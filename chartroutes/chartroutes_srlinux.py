#!/usr/bin/env python3

"""
chartroutes_srlinux.py
Script to collect active/total route count for specified protocols from Nokia SR Linux routers
via gNMIc and graph them in an Excel spreadsheet

Modernized version adapted from original Juniper PyEz chartroutes.py
Author: Adapted for SR Linux - 2026

Original concept: M. McCoy 2017

--- How this works, top to bottom ---
1. main() parses args, spawns one collect_route_data() thread per target device.
2. Each thread polls its device once per second (execute_gnmic_command -> gnmic get,
   NOT a gNMI streaming subscription) and appends a (elapsed_time, total, active) sample
   to a shared dict. 1-second polling isn't a laziness shortcut: SR Linux's own gNMI
   server enforces a hard 1s floor on SAMPLE-mode subscriptions (verified against
   SR Linux 26.7.1 - a 100ms subscribe request is flatly rejected server-side with
   "Minimum supported sample interval is 1000000000 ns"), so a real streaming
   subscription would be no faster than this polling loop anyway on this platform.
3. Collection stops after --duration seconds (or an ENTER keypress on stdin).
4. If -s/-e were given, main() scans the collected samples for where the route count
   crossed those thresholds and prints a convergence-time/rate summary.
5. make_spreadsheet() writes everything to one .xlsx per run: a data sheet (raw
   time-series) and a graph sheet (line chart) per device.
"""

import sys
import json
import subprocess
import time
from time import strftime
from datetime import datetime
import re
import collections
import select
import argparse
import os
import threading
import queue
import xlsxwriter
from decimal import Decimal

# Global flag to stop threads
stop_threads = False

def execute_gnmic_command(target, username, password, path, timeout=10):
    """
    Execute a gNMIc get command and return the parsed JSON response

    Args:
        target: IP address or hostname of the SR Linux device
        username: SSH username
        password: SSH password
        path: gNMI path to query
        timeout: Command timeout in seconds

    Returns:
        Parsed JSON response or None on error
    """
    # This is a one-shot `gnmic get`, run fresh every call - not a persistent
    # subscription. --skip-verify means "use TLS but don't check the cert"
    # (SR Linux's gNMI server requires TLS; gnmic's --insecure flag means
    # plaintext/no-TLS instead and will fail against it with a confusing
    # "error reading server preface: EOF").
    cmd = [
        'gnmic',
        '-a', f'{target}:57400',
        '--skip-verify',
        '-u', username,
        '-p', password,
        '--encoding', 'json_ietf',
        'get',
        '--path', path,
        '--format', 'json'
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        if result.returncode != 0:
            print(f"gNMIc command failed for {target}: {result.stderr}")
            return None

        # Parse the JSON response
        response = json.loads(result.stdout)
        return response

    except subprocess.TimeoutExpired:
        print(f"gNMIc command timed out for {target}")
        return None
    except json.JSONDecodeError as e:
        print(f"Failed to parse gNMIc JSON response for {target}: {e}")
        return None
    except Exception as e:
        print(f"Error executing gNMIc command for {target}: {e}")
        return None


def get_route_table_stats(target, username, password, network_instance, families):
    """
    Get the network-instance ROUTE TABLE (RIB) counters for each address family.
    This is the counter that actually drives every measurement in this tool.

    Reads /network-instance[name=<ni>]/route-table/<family>/statistics - the actual
    RIB for that network-instance, aggregated across EVERY protocol contributing to
    it (BGP, static, connected, ISIS, ...), not just BGP's own internal bookkeeping.

    An earlier version of this tool instead (or also) read BGP's own view of what it
    holds as best-path per AFI-SAFI (/protocols/bgp/afi-safi, via a now-removed
    get_protocol_stats_by_family() function). That counter was found (2026-09-02) to
    be a periodically-refreshed rollup, not a real-time one - on a 250,000-route
    advertisement it lagged the real RIB by several seconds, producing convergence
    numbers that looked either impossibly fast or showed a mysterious multi-second
    stall, neither of which reflected reality. It's no longer read at all - use this
    RIB counter for anything timing-sensitive on SR Linux.

    Note this is still RIB, not FIB - it's the routes selected into this
    network-instance's route table, not what's actually been programmed into the
    forwarding ASIC. See TestPlan.md's RIB vs FIB distinction for that further step.

    Args:
        target: Device IP/hostname
        username: Username
        password: Password
        network_instance: Network instance name
        families: List of address families (e.g., ['ipv4-unicast', 'ipv6-unicast'])

    Returns:
        Dictionary with stats per family: {family: {'total': X, 'active': Y}}
    """
    results = {}

    for family in families:
        # Only ipv4-unicast/ipv6-unicast have a route-table tree; anything else
        # (evpn, etc.) has no equivalent path and is reported as zero.
        if family not in ('ipv4-unicast', 'ipv6-unicast'):
            results[family] = {'total': 0, 'active': 0}
            continue

        path = f'/network-instance[name={network_instance}]/route-table/{family}/statistics'
        data = execute_gnmic_command(target, username, password, path)

        stats = {'total': 0, 'active': 0}
        if data:
            try:
                for item in data:
                    if 'updates' in item:
                        for update in item['updates']:
                            if 'values' in update:
                                values = update['values']
                                container = next(iter(values.values()), {})

                                active = container.get('active-routes', 0)
                                total = container.get('total-routes', 0)

                                # This path mixes types - active-routes comes back as
                                # a JSON number, total-routes as a string. Handle both.
                                if isinstance(active, str):
                                    active = int(active) if active.isdigit() else 0
                                if isinstance(total, str):
                                    total = int(total) if total.isdigit() else 0

                                stats = {'total': total, 'active': active}
            except Exception as e:
                print(f"Error parsing route-table stats for {family}: {e}")

        results[family] = stats

    return results


def collect_route_data(target, username, password, network_instance, protocol, families,
                       duration, data_dict, device_queue, debug=False):
    """
    Thread function to collect route statistics from a device.

    One of these runs per target, in parallel (threading, not multiprocessing - fine
    here since each iteration is dominated by waiting on the gnmic subprocess/network
    round-trip, not CPU work). Polls once a second until `duration` elapses or
    stop_threads flips true (set by main() on an ENTER keypress or timeout).

    Args:
        target: Device IP/hostname
        username: Username
        password: Password
        network_instance: Network instance name
        protocol: Protocol to monitor
        families: List of address families
        duration: How long to collect data (seconds)
        data_dict: Shared dictionary to store results
        device_queue: Queue for thread status updates
        debug: Enable debug output
    """
    global stop_threads

    start_time = time.time()

    # Initialize data structure
    data_dict[target] = {
        'routeStats': collections.defaultdict(list)
    }

    # Add elapsed time tracking
    data_dict[target]['routeStats']['Elapsed Time'] = []

    try:
        sample_count = 0
        while not stop_threads and (time.time() - start_time) < duration:
            current_time = time.time()
            elapsed = current_time - start_time

            # Get RIB stats for each family - the network-instance route-table view,
            # aggregated across every protocol contributing to it, not just BGP. This
            # used to also poll BGP's own /protocols/bgp/afi-safi view alongside this,
            # but that counter was found (2026-09-02) to be a periodically-refreshed
            # rollup that can lag the real RIB by several seconds - it was producing
            # misleading convergence numbers, not just a redundant second series, so
            # it's no longer collected at all. See get_route_table_stats()'s docstring
            # and the "SR Linux Operational Lessons" note for the full finding.
            rib_stats = get_route_table_stats(
                target, username, password, network_instance, families
            )

            if rib_stats:
                # Record the elapsed time
                data_dict[target]['routeStats']['Elapsed Time'].append(elapsed)

                # Record network-instance route-table (RIB) stats for each family
                for family, stats in rib_stats.items():
                    total_key = f"{family} RIB Total Routes"
                    active_key = f"{family} RIB Active Routes"

                    data_dict[target]['routeStats'][total_key].append(stats['total'])
                    data_dict[target]['routeStats'][active_key].append(stats['active'])

                sample_count += 1

                if debug:
                    print(f"[{target}] Sample {sample_count}: RIB={rib_stats}")

            # Sleep for 1 second between samples. This is also the practical floor:
            # SR Linux's gNMI server rejects any SAMPLE-mode interval below 1s outright,
            # so there's no streaming-subscription version of this loop that would run
            # meaningfully faster on this platform - see the module docstring.
            time.sleep(1)

        device_queue.put({target: 'Done'})

    except Exception as e:
        print(f"Error collecting data from {target}: {e}")
        device_queue.put({target: f'Error: {e}'})


def make_spreadsheet(sheetDataDict, excelFileName):
    """
    Create an Excel spreadsheet with graphs from collected data

    Args:
        sheetDataDict: Dictionary of sheet data
        excelFileName: Output filename (without .xlsx extension)
    """

    # Validate that there is data to work on
    for sheetData in sheetDataDict:
        if 'data' not in sheetDataDict[sheetData]:
            raise Exception('make_spreadsheet() ERROR: No data available to create worksheet')

    # Create the Excel File
    if excelFileName:
        excelFile = excelFileName + ".xlsx"
    else:
        excelFile = "temp_" + str(int(time.time())) + ".xlsx"

    print(f"\nCreating file {excelFile} with the collected data")
    try:
        workbook = xlsxwriter.Workbook(excelFile)
    except Exception as err:
        print(f"Error creating Excel file: {err}")
        return False

    # One iteration of this outer loop = one target device = one data sheet + one graph sheet.
    for sheetData in sheetDataDict:
        # Define formatting
        timeFormat = workbook.add_format()
        timeFormat.set_num_format('0.00')
        sheetFormat = workbook.add_format({'num_format': 0})

        # Define the title of the worksheet
        if 'sheetTitle' in sheetDataDict[sheetData]:
            if 'debug' in sheetDataDict[sheetData]:
                print(f"\nAdding worksheet {sheetDataDict[sheetData]['sheetTitle']}")
            ws = workbook.add_worksheet(sheetDataDict[sheetData]['sheetTitle'])
        else:
            ws = workbook.add_worksheet('Sheet1')

        row, col = 0, 0
        lastRow = 1
        timeCol = -1

        # Data is written column-by-column (one dataItem = one column: "Elapsed Time",
        # "ipv4-unicast RIB Total Routes", "ipv4-unicast RIB Active Routes", ...),
        # header in row 0, values below it - this is why row gets reset to 0 and col
        # incremented at the bottom of this inner loop, once per column.
        for dataItem in sheetDataDict[sheetData]['data']:
            if 'debug' in sheetDataDict[sheetData]:
                print(f"  Processing Data Item {dataItem}")
                print(f"  Length: {len(sheetDataDict[sheetData]['data'][dataItem])}")

            # The first row contains the headers
            ws.set_column(col, col, len(dataItem))
            ws.write(row, col, dataItem)
            row += 1

            for dataValue in range(len(sheetDataDict[sheetData]['data'][dataItem])):
                # If no value exists, set it to 0
                if not sheetDataDict[sheetData]['data'][dataItem][dataValue]:
                    sheetDataDict[sheetData]['data'][dataItem][dataValue] = 0

                if 'Elapsed Time' in dataItem:
                    wsVal = round(float(str(sheetDataDict[sheetData]['data'][dataItem][dataValue])), 2)
                    ws.write(row, col, wsVal, timeFormat)
                else:
                    # Write numeric values
                    wsVal = float(str(sheetDataDict[sheetData]['data'][dataItem][dataValue]))
                    ws.write(row, col, wsVal, sheetFormat)
                row += 1
                lastRow = row

            if 'Elapsed Time' in dataItem:
                timeCol = col

            row = 0
            col += 1

        # Create the graph
        if 'graphTitle' in sheetDataDict[sheetData]:
            wsGraph = workbook.add_worksheet(sheetDataDict[sheetData]['graphTitle'])
            chart = workbook.add_chart({'type': 'line'})

            # Add series for each data column (except time)
            dataCol = 0
            for dataItem in sheetDataDict[sheetData]['data']:
                if 'Elapsed Time' not in dataItem:
                    chart.add_series({
                        'name': [sheetDataDict[sheetData]['sheetTitle'], 0, dataCol],
                        'categories': [sheetDataDict[sheetData]['sheetTitle'], 1, timeCol, lastRow - 1, timeCol],
                        'values': [sheetDataDict[sheetData]['sheetTitle'], 1, dataCol, lastRow - 1, dataCol],
                    })
                dataCol += 1

            # Configure chart
            chart.set_title({'name': sheetDataDict[sheetData].get('graphTitle', 'Route Statistics')})
            chart.set_x_axis({'name': 'Elapsed Time (seconds)'})
            chart.set_y_axis({'name': 'Route Count'})
            chart.set_size({'width': 1200, 'height': 600})

            wsGraph.insert_chart('B2', chart)

    workbook.close()
    print("Done.")
    return True


def main():
    global stop_threads

    parser = argparse.ArgumentParser(
        description='Collect and graph route statistics from Nokia SR Linux routers'
    )

    parser.add_argument('-t', '--targets', required=True,
                       help='Comma-separated list of device IPs/hostnames. Each entry may be '
                            '"address=label" (e.g. 172.30.30.21=leaf1) to use a friendly name '
                            'in sheet/graph titles instead of the connection address.')
    parser.add_argument('-u', '--username', required=True,
                       help='Username for device login')
    parser.add_argument('-p', '--password', required=True,
                       help='Password for device login')
    parser.add_argument('-n', '--network-instance', default='default',
                       help='Network instance name (default: default)')
    parser.add_argument('-P', '--protocol', default='bgp',
                       help='Protocol to monitor (default: bgp)')
    parser.add_argument('-f', '--families', default='ipv4-unicast,ipv6-unicast',
                       help='Comma-separated address families (default: ipv4-unicast,ipv6-unicast)')
    parser.add_argument('-d', '--duration', type=int, required=True,
                       help='Data collection duration in seconds')
    parser.add_argument('-o', '--output', default='route_stats',
                       help='Output filename prefix (default: route_stats)')
    parser.add_argument('-s', '--start-values',
                       help='Comma-separated starting route values for convergence calculation (one per family)')
    parser.add_argument('-e', '--end-values',
                       help='Comma-separated ending route values for convergence calculation (one per family)')
    parser.add_argument('-x', '--debug', action='store_true',
                       help='Enable debug output')

    args = parser.parse_args()

    # Parse inputs. target_labels lets sheet/graph titles show a friendly hostname
    # ("leaf1") instead of the raw connection address ("172.30.30.21") - `targets`
    # (the connection addresses) stays the dict key used everywhere else below, so
    # this is purely a display-layer substitution.
    target_labels = {}
    targets = []
    for t in args.targets.split(','):
        t = t.strip()
        if '=' in t:
            address, label = t.split('=', 1)
            address = address.strip()
            target_labels[address] = label.strip()
            targets.append(address)
        else:
            target_labels[t] = t
            targets.append(t)
    families = [f.strip() for f in args.families.split(',')]

    starting_values = None
    ending_values = None
    if args.start_values and args.end_values:
        starting_values = [int(v.strip()) for v in args.start_values.split(',')]
        ending_values = [int(v.strip()) for v in args.end_values.split(',')]

        if len(starting_values) != len(families) or len(ending_values) != len(families):
            print("ERROR: Number of start/end values must match number of address families")
            sys.exit(1)

    print(f"\nGathering {args.protocol.upper()} route statistics for families {args.families}")
    print(f"from network-instance '{args.network_instance}' on {len(targets)} device(s)")
    print(f"Collection duration: {args.duration} seconds\n")

    startTime = time.time()
    endTime = startTime + args.duration

    print(f"Starting data collection at {time.asctime(time.localtime(startTime))}")
    print("\nPress ENTER any time to stop data collection.\n")

    # Shared data structures
    rtrData = {}
    device_queue = queue.Queue()
    threads = []
    stop_threads = False

    # Start collection thread for each target
    for target in targets:
        t = threading.Thread(
            target=collect_route_data,
            args=(target, args.username, args.password, args.network_instance,
                  args.protocol, families, args.duration, rtrData, device_queue, args.debug)
        )
        threads.append(t)
        t.daemon = True

    for t in threads:
        t.start()

    # Wait for duration or user interrupt. select.select() on stdin with a 0 timeout
    # is a non-blocking "is there input waiting?" check - this only works if stdin is
    # a real terminal or an open pipe that nothing has closed yet. Piping stdin from
    # something that's already at EOF (e.g. /dev/null) makes select() report "ready"
    # immediately and this loop exits on its very first iteration - if running this
    # unattended (nohup, over ssh, etc.) with no real stdin to hold open, redirect
    # stdin from something that blocks forever and never EOFs, e.g. `tail -f /dev/null |`,
    # or collection stops instantly instead of running for `duration`.
    while time.time() < (endTime + 5):
        if select.select([sys.stdin], [], [], 0)[0]:
            elapsedTime = time.time() - startTime
            print(f"Data collection stopped by user after {elapsedTime:.2f} seconds")
            break

    # Stop threads
    stop_threads = True
    for t in threads:
        t.join(timeout=10)

    endTime = time.asctime(time.localtime(time.time()))
    print(f"Ending data collection at {endTime}\n")

    # Check for successful data collection
    valid_targets = []
    for target in targets:
        if target in rtrData and len(rtrData[target]['routeStats']) > 1:
            valid_targets.append(target)
        else:
            print(f"WARNING: No data collected from {target}")

    if not valid_targets:
        print("No data collected from any device. Exiting.")
        sys.exit(1)

    # Calculate convergence if requested.
    #
    # IMPORTANT - how -s/-e should be chosen, and why:
    #   This is a THRESHOLD CROSSING search, not a "measure from route 0" tool. The
    #   route count is essentially never exactly 0 - there's always some baseline
    #   (underlay routes, other peers, etc.), so -s should be that live baseline PLUS
    #   a safety margin (e.g. baseline+100), not 0 and not the exact baseline value.
    #   Passing 0 (or the exact live baseline) makes "count > starting_values[idx]"
    #   true on the very first sample, before the real event happens at all, and the
    #   reported start time comes out as 0.00s - this looks like a bug but is really
    #   just the wrong parameter. A margin above baseline that's small relative to the
    #   route delta being tested (a few hundred routes when advertising 250k) avoids
    #   this without meaningfully distorting the measured window.
    #
    #   -e must NEVER be set below the true final target count. The `>=` comparison
    #   below already tolerates overshoot (extra ECMP/multipath bookkeeping routes),
    #   so -e needs no margin on that side - and subtracting one is actively wrong:
    #   it means declaring convergence before every route has actually arrived. If a
    #   peer is slow to send its last few routes or its End-of-RIB marker, the count
    #   can plateau just short of the true target for a real, meaningful stretch of
    #   time - that stall is exactly what a convergence test is trying to capture, and
    #   an undershot -e would report "converged" before that stall shows up at all.
    #
    #   Even with -s/-e chosen correctly, this method has a structural, ONE-DIRECTIONAL
    #   BIAS: start_idx is taken as the sample BEFORE the count first exceeds -s (so
    #   the reported start is always <= the true crossing), and end_idx is the first
    #   sample that reaches -e (so the reported end is always >= the true crossing).
    #   Every run therefore overestimates the true convergence time by up to ~1 sample
    #   interval (~1s here, since SR Linux's gNMI server won't sample faster than
    #   that - see the module docstring). Averaging repeated runs shrinks the RANDOM
    #   part of this (exactly where within a sample window the true crossing falls
    #   varies run to run) but does NOT correct the bias itself, since it pushes the
    #   same direction on every run - a tight standard deviation across N runs is not
    #   evidence that the bias isn't there.
    if starting_values and ending_values:
        print(f"\nCalculating convergence time for {args.protocol.upper()}")
        for target in valid_targets:
            print(f"\nConvergence times for {target_labels[target]} ({target}):")

            for idx, family in enumerate(families):
                # Measured against the RIB (route-table) counter. This tool used to
                # also read BGP's own "/protocols/bgp/afi-safi" aggregate; found live
                # on this lab (2026-09-02) to be a periodically-refreshed rollup with
                # real lag baked in (observed ~8s behind the actual RIB at 250k
                # routes), completely separate from gNMI polling interval. That lag is
                # what earlier looked like both an "impossibly fast" single-sample
                # jump (the BGP stat catching up all at once on its own refresh cycle)
                # and a multi-second "stall" (the BGP stat sitting mid-update while
                # real convergence had already finished) - see the "SR Linux
                # Operational Lessons" note for the full writeup. That counter is no
                # longer read at all; /network-instance/route-table/<family>/statistics
                # is the only signal this tool measures against now.
                total_key = f"{family} RIB Total Routes"

                if total_key not in rtrData[target]['routeStats']:
                    print(f"  No data for {family}")
                    continue

                route_counts = rtrData[target]['routeStats'][total_key]
                time_values = rtrData[target]['routeStats']['Elapsed Time']

                start_idx = None
                end_idx = None

                # Determine if routes are increasing or decreasing
                if starting_values[idx] < ending_values[idx]:
                    print(f"  {family}: Route count increasing from {starting_values[idx]} to {ending_values[idx]}")

                    # Find start point: the sample just before the count first climbs
                    # past -s (see the bias note above for why this rounds early).
                    for i, count in enumerate(route_counts):
                        if count > starting_values[idx]:
                            start_idx = max(0, i - 1)
                            break

                    # Find end point: the first sample that reaches or exceeds -e.
                    for i, count in enumerate(route_counts):
                        if count >= ending_values[idx]:
                            end_idx = i
                            break
                else:
                    print(f"  {family}: Route count decreasing from {starting_values[idx]} to {ending_values[idx]}")

                    # Find start point
                    for i, count in enumerate(route_counts):
                        if count < starting_values[idx]:
                            start_idx = max(0, i - 1)
                            break

                    # Find end point
                    for i, count in enumerate(route_counts):
                        if count <= ending_values[idx]:
                            end_idx = i
                            break

                if start_idx is not None and end_idx is not None:
                    start_time = time_values[start_idx]
                    end_time = time_values[end_idx]
                    conv_time = end_time - start_time

                    route_delta = abs(ending_values[idx] - starting_values[idx])
                    conv_rate = route_delta / conv_time if conv_time > 0 else 0

                    print(f"    Start time: {start_time:.2f}s")
                    print(f"    End time: {end_time:.2f}s")

                    if end_idx == start_idx + 1:
                        # The whole event happened between two adjacent samples - there
                        # is no intermediate data point showing it in progress. conv_time
                        # here is just however long that particular poll cycle happened
                        # to take (varies run to run from gnmic subprocess/network
                        # overhead alone), not a measurement of anything the router did.
                        # Still report a number - never refuse outright. A genuine RIB-in
                        # stall (e.g. a slow End-of-RIB) is a real thing that can happen
                        # on real hardware too, and if it does, it's exactly what this
                        # tool exists to catch - it just shouldn't be assumed away. What
                        # this data actually supports is a BOUND, not an exact value: the
                        # event finished in AT MOST conv_time seconds, so the true rate
                        # was AT LEAST conv_rate routes/sec (could be far higher). Report
                        # it as a bound, not as false five-decimal precision, and point at
                        # the graph - a real multi-second stall shows up there as an
                        # actual flat plateau across several samples; an instant
                        # single-gap transition like this one won't.
                        print(f"    Convergence time: <= {conv_time:.2f}s (resolution-limited - no "
                              f"intermediate sample fell within this gap; check the graph sheet - a real "
                              f"stall shows as a flat plateau across several samples, this is one gap only)")
                        print(f"    Convergence rate: >= {conv_rate:.2f} routes/sec (lower bound, not exact)")
                    else:
                        print(f"    Convergence time: {conv_time:.2f}s")
                        print(f"    Convergence rate: {conv_rate:.2f} routes/sec")
                else:
                    # start_idx/end_idx stay None if the threshold was never crossed
                    # within the collection window at all - e.g. -e set higher than
                    # anything actually observed (duration too short, or -s/-e were
                    # computed from a different counter than get_route_table_stats()
                    # actually measures - see run-ribin-test.sh, which must stay
                    # matched to this).
                    if start_idx is None:
                        print(f"    ERROR: Could not find starting value {starting_values[idx]}")
                    if end_idx is None:
                        print(f"    ERROR: Could not find ending value {ending_values[idx]}")

    # Create Excel output
    dir_path = os.path.dirname(os.path.realpath(__file__))
    chartDataLoc = os.path.join(dir_path, "data")
    if not os.path.exists(chartDataLoc):
        print(f"Creating directory {chartDataLoc} for chart file storage")
        os.makedirs(chartDataLoc)

    sheetData = collections.defaultdict(dict)
    excelFileName = os.path.join(chartDataLoc, f"{args.output}_{int(startTime)}")

    for target in valid_targets:
        # Excel worksheet names: max 31 chars, no : \ / ? * [ ]. Truncated to 17 chars
        # here (not 25) since " Learning Rate" (14 chars) is longer than " Graph" was.
        label = re.sub(r'[:\\/?*\[\]]', '_', target_labels[target])[:17]
        sheetData[target] = {
            'sheetTitle': f"{label} Data",
            'graphTitle': f"{label} Learning Rate",
            'data': rtrData[target]['routeStats'],
            'format': 'general',
            'timeFormat': 'elapsed'
        }

    if args.debug:
        print("\n!!! Debug enabled. Dumping collected data:")
        for target in valid_targets:
            sheetData[target]['debug'] = True
        print(sheetData)

    try:
        make_spreadsheet(sheetData, excelFileName)
    except Exception as err:
        print(f"Error creating spreadsheet: {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
