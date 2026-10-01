#!/usr/bin/env python3

"""
chartroutes_prometheus.py

Prometheus-backed sibling to chartroutes_srlinux.py. Same discrete-per-run
Excel output (one .xlsx per run, kept locally - not relying on a Prometheus
instance you may not control the retention/host of), but the data comes from
a Prometheus instance being continuously fed by a persistent gnmic
subscription, instead of chartroutes_srlinux.py's one-shot `gnmic get` RPC
polling the router directly once per second.

chartroutes_srlinux.py is intentionally left completely untouched as a
fallback - this is a new, separate tool, not a replacement.

--- Why this exists, and how it works ---
1. Auto-detects the live baseline via one Prometheus instant query (same
   asymmetric-margin logic as chartroutes_srlinux.py's run-ribin-test.sh
   wrapper: -s gets a margin above baseline to avoid false-early matches
   from ordinary jitter, -e never gets a margin below the true target,
   since undershooting it would mean declaring convergence during a real
   stall instead of capturing it).
2. Live-tails by polling Prometheus with a lightweight instant query every
   --poll-interval seconds (default 5s) until the target is reached or
   --max-wait elapses. This interval is deliberately decoupled from the
   underlying subscription/storage resolution (1s, matching SR Linux's own
   SAMPLE-mode floor) - the live-tail loop only needs to know "are we done
   yet", not resolve the actual curve shape, so it can check in coarsely
   without losing any resolution in what's actually stored.
3. Once done (or timed out), pulls ONE Prometheus range query at step=1s
   covering the whole window - this is the real, fully-resolved curve,
   independent of how coarse the live-tail polling was.
4. Writes the same Data + Graph sheet structure chartroutes_srlinux.py uses,
   via make_prometheus_spreadsheet() below - an adapted copy of
   chartroutes_srlinux.py's make_spreadsheet(), not an import of it. Reusing
   the original directly was tried first, but it hardcodes the Y-axis label
   to "Route Count" (reasonable when the tool only ever charted routes) -
   fine for the Routes tab, silently wrong on the CPU/Memory tabs this tool
   also produces. Found by the user actually looking at the file: all three
   tabs' axes read "Route Count" regardless of what was plotted. Copied
   instead of patched, since chartroutes_srlinux.py must stay untouched as
   the fallback.
5. Writes a Metadata sheet (also via make_prometheus_spreadsheet(), same
   xlsxwriter session as everything else - see its docstring for why this
   changed from an openpyxl post-process step) recording the exact
   start/end timestamps and the PromQL query used. That's the actual point
   of moving to Prometheus: any other metric (CPU, memory, BGP session
   state, whatever) can be pulled for that exact window later, even if
   nobody thought to capture it at test time - the "wish we'd captured that
   too" problem.

Convergence-calculation logic (threshold search, the single-sample-gap bound
handling, the asymmetric margin reasoning) is deliberately kept identical to
chartroutes_srlinux.py's - same lessons, same platform, just a different
data source underneath.
"""

import sys
import os
import re
import time
import argparse
from datetime import datetime, timezone

import requests
import xlsxwriter

# Fixed per-metric colors, not Excel's auto-assigned defaults - so a metric is
# always the same color everywhere it appears (single-metric tab AND the
# overlay chart), matching "color follows the entity, never its rank." Slots
# 1/2/3 from the dataviz skill's validated categorical palette
# (references/palette.md) - documented there as clearing the CVD/contrast
# floors together as a set, worst-case Delta E well above target in both
# light and dark mode.
SERIES_COLORS = {
    "Route Count": "#2a78d6",   # slot 1, blue
    "CPU Percent": "#eb6834",   # slot 2, orange
    "Memory MB": "#1baf7a",     # slot 3, aqua
}
GRIDLINE_COLOR = "#D9D9D9"  # light, recessive - not the heavy default black


def build_label_selector(labels):
    """dict -> PromQL {k="v",k2="v2"} selector string."""
    parts = [f'{k}="{v}"' for k, v in labels.items()]
    return "{" + ",".join(parts) + "}"


def prom_instant_query(prom_url, promql, timeout=10):
    """
    (prometheus_timestamp, value) for a PromQL expression, or (None, None) if
    no series matched. The timestamp is Prometheus's own evaluation time, not
    this script's local clock - deliberately, so that anything measuring an
    elapsed window is immune to clock skew between wherever this script
    happens to run and wherever Prometheus actually is. Don't discard it and
    fall back to time.time() - that reintroduces exactly the skew risk this
    is here to avoid.
    """
    resp = requests.get(f"{prom_url}/api/v1/query", params={"query": promql}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if data["status"] != "success":
        raise RuntimeError(f"Prometheus query failed: {data}")
    result = data["data"]["result"]
    if not result:
        return None, None
    ts, val = result[0]["value"]
    return float(ts), float(val)


def prom_range_query(prom_url, promql, start, end, step, timeout=30):
    """Full (timestamp, value) series for a PromQL expression over a window."""
    resp = requests.get(f"{prom_url}/api/v1/query_range", params={
        "query": promql, "start": start, "end": end, "step": step,
    }, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if data["status"] != "success":
        raise RuntimeError(f"Prometheus range query failed: {data}")
    result = data["data"]["result"]
    if not result:
        return []
    return result[0]["values"]


def make_prometheus_spreadsheet(sheetDataDict, excelFileName, metadata_rows=None, overlay=None):
    """
    Adapted copy of chartroutes_srlinux.py's make_spreadsheet() - same
    structure and behavior, with three real differences, all found live by
    the user actually opening the output files:

    1. The Y-axis label comes from each target's own 'yAxisLabel' key
       (defaulting to 'Value') instead of being hardcoded to 'Route Count' -
       fine in the original, which only ever charted route counts; wrong
       once this tool reuses the same sheet-writer for CPU%/Memory MB tabs
       too, where every tab's axis used to read "Route Count" regardless of
       what was actually plotted.
    2. Metadata is written here too (metadata_rows), as a plain xlsxwriter
       sheet, not appended afterward via openpyxl.load_workbook()+save(). It
       used to be appended via openpyxl, which - it turns out - corrupts the
       xlsxwriter-authored charts already in the file just by loading and
       re-saving it, even without touching the charts at all (openpyxl's
       chart round-tripping of charts it didn't create has real, documented
       fidelity limits). This silently broke every single Data/Graph chart
       in every run this tool ever produced, including the very first one -
       not just the runs with CPU/memory tabs.
    3. The Route-vs-CPU overlay chart (overlay=(key1, key2), dual Y-axis) is
       built here natively via xlsxwriter's own combine()/set_y2_axis()
       secondary-axis support, for the same reason - it used to be built via
       openpyxl after the fact, which is the other half of the same
       corruption bug.

    Net effect: this function is now the ONLY thing that ever touches the
    output file. It's written once, by one library, start to finish - no
    load/re-save round trip of a chart-bearing file, by any library, ever.

    Args:
        sheetDataDict: dict of {key: {sheetTitle, graphTitle, yAxisLabel, data}}
        excelFileName: output filename, without .xlsx extension
        metadata_rows: optional list of (field, value) tuples for a Metadata sheet
        overlay: optional (key1, key2) tuple - two sheetDataDict keys to combine
                 into one dual-Y-axis chart (key1's series on the primary axis,
                 key2's on the secondary)
    """
    for sheetData in sheetDataDict:
        if "data" not in sheetDataDict[sheetData]:
            raise Exception("make_prometheus_spreadsheet() ERROR: No data available to create worksheet")

    excelFile = excelFileName + ".xlsx"
    print(f"\nCreating file {excelFile} with the collected data")
    try:
        workbook = xlsxwriter.Workbook(excelFile)
    except Exception as err:
        print(f"Error creating Excel file: {err}")
        return False

    # Tracks each target's (sheetTitle, lastRow, timeCol, first data column
    # index) so the overlay chart (built after this loop) can reference the
    # right cell ranges without re-deriving them.
    sheet_layout = {}

    for sheetData in sheetDataDict:
        timeFormat = workbook.add_format()
        timeFormat.set_num_format("0.00")
        sheetFormat = workbook.add_format({"num_format": 0})

        sheetTitle = sheetDataDict[sheetData].get("sheetTitle", "Sheet1")
        ws = workbook.add_worksheet(sheetTitle)

        row, col = 0, 0
        lastRow = 1
        timeCol = -1

        for dataItem in sheetDataDict[sheetData]["data"]:
            ws.set_column(col, col, len(dataItem))
            ws.write(row, col, dataItem)
            row += 1

            for dataValue in range(len(sheetDataDict[sheetData]["data"][dataItem])):
                if not sheetDataDict[sheetData]["data"][dataItem][dataValue]:
                    sheetDataDict[sheetData]["data"][dataItem][dataValue] = 0

                if "Elapsed Time" in dataItem:
                    wsVal = round(float(str(sheetDataDict[sheetData]["data"][dataItem][dataValue])), 2)
                    ws.write(row, col, wsVal, timeFormat)
                else:
                    wsVal = float(str(sheetDataDict[sheetData]["data"][dataItem][dataValue]))
                    ws.write(row, col, wsVal, sheetFormat)
                row += 1
                lastRow = row

            if "Elapsed Time" in dataItem:
                timeCol = col

            row = 0
            col += 1

        sheet_layout[sheetData] = {"sheetTitle": sheetTitle, "lastRow": lastRow, "timeCol": timeCol}

        if "graphTitle" in sheetDataDict[sheetData]:
            wsGraph = workbook.add_worksheet(sheetDataDict[sheetData]["graphTitle"])
            chart = workbook.add_chart({"type": "line"})

            dataCol = 0
            series_count = 0
            for dataItem in sheetDataDict[sheetData]["data"]:
                if "Elapsed Time" not in dataItem:
                    color = SERIES_COLORS.get(dataItem)
                    chart.add_series({
                        "name": [sheetTitle, 0, dataCol],
                        "categories": [sheetTitle, 1, timeCol, lastRow - 1, timeCol],
                        "values": [sheetTitle, 1, dataCol, lastRow - 1, dataCol],
                        "line": {"color": color, "width": 1.75} if color else {"width": 1.75},
                        "smooth": False,  # the real curve has genuine sharp steps -
                                          # smoothing would visually misrepresent it
                    })
                    series_count += 1
                dataCol += 1

            chart.set_title({"name": sheetDataDict[sheetData].get("graphTitle", "Statistics")})
            chart.set_x_axis({"name": "Elapsed Time (seconds)"})
            chart.set_y_axis({
                "name": sheetDataDict[sheetData].get("yAxisLabel", "Value"),
                "major_gridlines": {"visible": True, "line": {"color": GRIDLINE_COLOR, "width": 0.75}},
            })
            chart.set_size({"width": 1200, "height": 600})
            if series_count <= 1:
                # A lone series doesn't need a legend - the chart title already
                # names it; a legend box here is pure clutter.
                chart.set_legend({"none": True})

            wsGraph.insert_chart("B2", chart)

    if metadata_rows:
        ws_meta = workbook.add_worksheet("Metadata")
        boldFormat = workbook.add_format({"bold": True})
        for r, (field, value) in enumerate(metadata_rows):
            ws_meta.write(r, 0, str(field), boldFormat if r == 0 else None)
            ws_meta.write(r, 1, str(value))
        ws_meta.set_column(0, 0, 26)
        ws_meta.set_column(1, 1, 90)

    if overlay and overlay[0] in sheet_layout and overlay[1] in sheet_layout:
        key1, key2 = overlay
        layout1, layout2 = sheet_layout[key1], sheet_layout[key2]
        label1 = [c for c in sheetDataDict[key1]["data"] if "Elapsed Time" not in c][0]
        label2 = [c for c in sheetDataDict[key2]["data"] if "Elapsed Time" not in c][0]

        primary = workbook.add_chart({"type": "line"})
        primary.add_series({
            "name": label1,
            "categories": [layout1["sheetTitle"], 1, layout1["timeCol"], layout1["lastRow"] - 1, layout1["timeCol"]],
            "values": [layout1["sheetTitle"], 1, 1, layout1["lastRow"] - 1, 1],
            "line": {"color": SERIES_COLORS.get(label1), "width": 1.75},
            "smooth": False,
        })
        primary.set_title({"name": f"Route Count vs {label2}"})
        primary.set_x_axis({"name": "Elapsed Time (seconds)"})
        primary.set_y_axis({
            "name": sheetDataDict[key1].get("yAxisLabel", label1),
            "major_gridlines": {"visible": True, "line": {"color": GRIDLINE_COLOR, "width": 0.75}},
        })
        primary.set_size({"width": 1200, "height": 600})
        primary.set_legend({"position": "bottom"})

        secondary = workbook.add_chart({"type": "line"})
        secondary.add_series({
            "name": label2,
            "categories": [layout2["sheetTitle"], 1, layout2["timeCol"], layout2["lastRow"] - 1, layout2["timeCol"]],
            "values": [layout2["sheetTitle"], 1, 1, layout2["lastRow"] - 1, 1],
            "y2_axis": True,
            "line": {"color": SERIES_COLORS.get(label2), "width": 1.75},
            "smooth": False,
        })
        secondary.set_y2_axis({"name": sheetDataDict[key2].get("yAxisLabel", label2)})

        primary.combine(secondary)

        ws_overlay = workbook.add_worksheet("Route+CPU Overlay")
        ws_overlay.insert_chart("B2", primary)

    workbook.close()
    print("Done.")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Pull RIB-in convergence data from Prometheus (fed by a persistent "
                    "gnmic subscription) instead of live-polling the router directly."
    )
    parser.add_argument("--prometheus-url", required=True,
                       help='e.g. http://172.30.30.33:9090')
    parser.add_argument("--metric", required=True,
                       help="Prometheus metric name, e.g. "
                            "network_instance_route_table_ipv4_unicast_statistics_active_routes")
    parser.add_argument("--label", action="append", default=[], metavar="KEY=VALUE",
                       help='Repeatable label filter, e.g. --label source=leaf1 '
                            '--label network_instance_name=default')
    parser.add_argument("--device-label", required=True,
                       help="Friendly name for sheet/graph titles, e.g. leaf1")
    parser.add_argument("--route-delta", type=int, required=True,
                       help="Number of routes being advertised (used with the "
                            "auto-detected baseline to compute the target)")
    parser.add_argument("--direction", choices=["up", "down"], default="up",
                        help="up (default): advertise, count rises by --route-delta. "
                             "down: withdraw, count falls by --route-delta")
    parser.add_argument("--start-margin", type=int, default=100,
                       help="Margin added above the detected baseline for the start "
                            "threshold (default: 100)")
    parser.add_argument("--poll-interval", type=float, default=5,
                       help="Seconds between live-tail checks (default: 5 - independent "
                            "of the underlying 1s storage resolution)")
    parser.add_argument("--max-wait", type=float, default=120,
                       help="Give up live-tailing after this many seconds (default: 120)")
    parser.add_argument("-o", "--output", default="route_stats",
                       help="Output filename prefix (default: route_stats)")
    parser.add_argument("--cpu-metric", default="platform_control_cpu_total_instant",
                       help="Prometheus metric for CPU%% (default: "
                            "platform_control_cpu_total_instant)")
    parser.add_argument("--memory-metric", default="platform_control_memory_reserved",
                       help="Prometheus metric for memory in bytes, converted to MB per "
                            "TestPlan.md's 'absolute MB, not percentage' spec (default: "
                            "platform_control_memory_reserved)")
    parser.add_argument("--control-slot", default="A",
                       help="control_slot label value for CPU/memory metrics (default: A)")
    parser.add_argument("--skip-system-metrics", action="store_true",
                       help="Collect route data only, skip CPU/memory and the overlay tab")
    parser.add_argument("--settle-window", type=int, default=3,
                       help="Number of consecutive CPU poll samples that must stay within "
                            "--settle-threshold of each other to count as settled (default: 3)")
    parser.add_argument("--settle-threshold", type=float, default=3.0,
                       help="Max allowed CPU-percent range (max-min) across --settle-window "
                            "samples to count as settled (default: 3.0 points) - deliberately "
                            "NOT a return-to-baseline check, since CPU may plateau at a new, "
                            "higher steady state after learning a large route set, not the "
                            "value it started at")
    parser.add_argument("--settle-max-above-baseline", type=float, default=None,
                       help="Also require the settled window to sit within this many CPU points of "
                            "the pre-test CPU baseline (read before the event). Off by default. "
                            "Flatness alone can fire on a busy plateau - e.g. SR Linux holds "
                            "~53-64%% flat for ~20s while programming a 1M-route FIB, and a "
                            "flat-only check declared 'settled' in the middle of it")
    parser.add_argument("--settle-max-wait", type=float, default=120,
                       help="Give up waiting for CPU to settle after this many seconds past "
                            "route convergence (default: 120) - a real safety ceiling, since "
                            "settling isn't guaranteed to happen at all")
    args = parser.parse_args()

    labels = {}
    for kv in args.label:
        if "=" not in kv:
            print(f"ERROR: --label must be KEY=VALUE, got: {kv}")
            sys.exit(1)
        k, v = kv.split("=", 1)
        labels[k] = v
    selector = build_label_selector(labels)
    promql = f"{args.metric}{selector}"

    print(f"Query: {promql}")
    test_start_time, baseline = prom_instant_query(args.prometheus_url, promql)
    if baseline is None:
        print("ERROR: no data returned for that metric/label selector - is gnmic "
              "actually scraping this target, and is the label selector right?")
        sys.exit(1)
    baseline = int(baseline)
    # test_start_time is Prometheus's own clock (from the response timestamp),
    # not this script's local time.time() - see prom_instant_query()'s
    # docstring for why. Everything that ends up in the range-query window or
    # the Metadata sheet is anchored to Prometheus's clock from here on;
    # test_end_time (below) is captured the same way. A purely local clock
    # (loop_start_local) is used ONLY for the max-wait timeout bookkeeping,
    # which is a local "how long has this script been waiting" concern and
    # deliberately doesn't need to agree with Prometheus's clock at all.

    # --direction down mirrors everything for withdraw measurement: the start
    # margin sits BELOW baseline (same jitter-absorbing purpose), and the end
    # target is baseline - route_delta with no margin above it - declaring a
    # withdraw complete while routes are still lingering would hide exactly
    # the tail behavior the measurement exists to capture.
    down = args.direction == "down"
    sign = -1 if down else 1
    start_val = baseline + sign * args.start_margin
    end_val = baseline + sign * args.route_delta

    def reached_end(count):
        return count <= end_val if down else count >= end_val

    def past_start(count):
        return count < start_val if down else count > start_val

    print(f"Baseline: {baseline}")
    cpu_baseline = None
    if not args.skip_system_metrics and args.settle_max_above_baseline is not None:
        _src = labels.get("source", args.device_label)
        _, cpu_baseline = prom_instant_query(
            args.prometheus_url,
            f'{args.cpu_metric}{{source="{_src}",control_slot="{args.control_slot}",cpu_index="all"}}')
        print(f"CPU baseline: {cpu_baseline}% (settled also requires <= "
              f"{cpu_baseline + args.settle_max_above_baseline if cpu_baseline is not None else '?'}%)")
    print(f"Using start={start_val} end={end_val} "
          f"(start-margin={args.start_margin}, end has no margin)\n")

    loop_start_local = time.time()
    print(f"Live-tailing (poll every {args.poll_interval}s, max wait {args.max_wait}s)...")

    converged = False
    test_end_time = test_start_time
    local_elapsed = 0.0
    while local_elapsed < args.max_wait:
        time.sleep(args.poll_interval)
        local_elapsed = time.time() - loop_start_local
        poll_ts, current = prom_instant_query(args.prometheus_url, promql)
        print(f"  [{local_elapsed:.1f}s] current={current}")
        if current is not None and reached_end(current):
            converged = True
            test_end_time = poll_ts
            break
        if poll_ts is not None:
            test_end_time = poll_ts

    if converged:
        print(f"\nConverged (live-tail resolution: {test_end_time - test_start_time:.2f}s - "
              f"coarse, see the pulled data below for the real curve).\n")
    else:
        print(f"\nWARNING: max-wait ({args.max_wait}s) exceeded without reaching target - "
              f"capturing what was collected anyway.\n")

    # --- CPU settle-detection phase: keeps polling PAST route convergence,
    # looking for CPU to flatline rather than return to its pre-test baseline.
    # This is deliberate, not an oversight: CPU may plateau at a new, higher
    # steady state after learning a large route set (more RIB/FIB bookkeeping
    # to maintain), so "did it get back to X%" is an unreliable stop
    # condition - "has it stopped moving" isn't, regardless of what level it
    # settles at. Settled = the last --settle-window CPU samples all fall
    # within --settle-threshold points of each other. Reports the START of
    # that stable window as the settle time, not the confirmation moment
    # (which is --settle-window samples later) - using the later time would
    # systematically overstate settle time by a fixed, avoidable amount, the
    # same class of bias already documented elsewhere in this tool. ---
    settle_promql = None
    settle_time_val = None
    settle_detected = False
    route_convergence_end = test_end_time
    final_data_end_time = test_end_time

    if not args.skip_system_metrics:
        source_val = labels.get("source", args.device_label)
        settle_promql = (f'{args.cpu_metric}{{source="{source_val}",'
                         f'control_slot="{args.control_slot}",cpu_index="all"}}')
        print(f"Watching CPU for settle (window={args.settle_window} samples, "
              f"threshold={args.settle_threshold} points, max wait {args.settle_max_wait}s)...")

        recent = []  # list of (timestamp, value)
        settle_loop_start_local = time.time()
        settle_local_elapsed = 0.0
        while settle_local_elapsed < args.settle_max_wait:
            time.sleep(args.poll_interval)
            settle_local_elapsed = time.time() - settle_loop_start_local
            poll_ts, cpu_now = prom_instant_query(args.prometheus_url, settle_promql)
            if cpu_now is None:
                continue
            recent.append((poll_ts, cpu_now))
            recent = recent[-args.settle_window:]
            print(f"  [+{settle_local_elapsed:.1f}s past convergence] CPU={cpu_now}")

            if len(recent) == args.settle_window:
                vals = [v for _, v in recent]
                low_enough = (cpu_baseline is None or
                              max(vals) <= cpu_baseline + args.settle_max_above_baseline)
                if max(vals) - min(vals) <= args.settle_threshold and low_enough:
                    settle_detected = True
                    final_data_end_time = recent[0][0]  # start of the stable window
                    settle_time_val = final_data_end_time - route_convergence_end
                    break
            final_data_end_time = poll_ts

        if settle_detected:
            print(f"\nCPU settled {settle_time_val:.2f}s after route convergence "
                  f"(final ~{recent[-1][1]}%).\n")
        else:
            print(f"\nWARNING: CPU never settled within --settle-max-wait "
                  f"({args.settle_max_wait}s) - capturing what was collected anyway.\n")

    # Small pre-roll before the trigger so the baseline shows in the graph too.
    range_start = int(test_start_time) - 3
    range_end = int(final_data_end_time) + 1
    print(f"Pulling full-resolution range: {range_start} to {range_end}, step=1s")
    raw_values = prom_range_query(args.prometheus_url, promql, range_start, range_end, "1s")

    if not raw_values:
        print("ERROR: range query returned no data.")
        sys.exit(1)

    t0 = raw_values[0][0]
    elapsed_times = [ts - t0 for ts, _ in raw_values]
    route_counts = [int(float(v)) for _, v in raw_values]

    # --- CPU/memory, pulled over the identical window so they line up on the
    # same elapsed-time axis as the route data. Best-effort: a missing series
    # (e.g. this target isn't in gnmic's srl-system-performance subscription)
    # degrades to an empty column rather than aborting the whole run - the
    # route data (the primary measurement) is never held hostage by this. ---
    cpu_times = cpu_values = mem_times = mem_values = []
    if not args.skip_system_metrics:
        source_val = labels.get("source", args.device_label)
        cpu_promql = (f'{args.cpu_metric}{{source="{source_val}",'
                      f'control_slot="{args.control_slot}",cpu_index="all"}}')
        mem_promql = f'{args.memory_metric}{{source="{source_val}",control_slot="{args.control_slot}"}}'

        cpu_raw = prom_range_query(args.prometheus_url, cpu_promql, range_start, range_end, "1s")
        mem_raw = prom_range_query(args.prometheus_url, mem_promql, range_start, range_end, "1s")

        if cpu_raw:
            cpu_times = [ts - t0 for ts, _ in cpu_raw]
            cpu_values = [float(v) for _, v in cpu_raw]
        else:
            print(f"WARNING: no CPU data for {cpu_promql} - CPU tab/overlay will be empty.")

        if mem_raw:
            mem_times = [ts - t0 for ts, _ in mem_raw]
            mem_values = [float(v) / 1_048_576 for _, v in mem_raw]  # bytes -> MB
        else:
            print(f"WARNING: no memory data for {mem_promql} - Memory tab will be empty.")

    # --- Total CPU Run Time: the full span CPU was doing something because of
    # this test, start to finish - complementary to Convergence Time (route
    # learning only) and Settle Time (the tail after routes finish). CPU can
    # start rising before route convergence completes, not only after, so
    # this is its own measurement, not just Convergence Time + Settle Time
    # added together. Start: first sample where CPU exceeds its own pre-test
    # baseline by more than --settle-threshold (same threshold used to
    # define "meaningful CPU movement" everywhere else in this tool, for
    # consistency), stepping back one sample per the same conservative
    # convention used for the route start_idx below. End: the settle-detected
    # stable window's start (or the last observed sample if it never
    # settled - labeled as a lower bound in that case, not an exact value). ---
    total_cpu_run_time_str = "N/A"
    if cpu_values:
        cpu_baseline = cpu_values[0]
        cpu_rise_idx = None
        for i, v in enumerate(cpu_values):
            if v > cpu_baseline + args.settle_threshold:
                cpu_rise_idx = max(0, i - 1)
                break

        settle_start_elapsed = final_data_end_time - t0
        if cpu_rise_idx is not None:
            cpu_rise_elapsed = cpu_times[cpu_rise_idx]
            total_cpu_run_time = settle_start_elapsed - cpu_rise_elapsed
            if settle_detected:
                total_cpu_run_time_str = f"{total_cpu_run_time:.2f}s"
            else:
                total_cpu_run_time_str = f">= {total_cpu_run_time:.2f}s (CPU had not settled by --settle-max-wait)"
            print(f"Total CPU Run Time: {total_cpu_run_time_str}")
        else:
            total_cpu_run_time_str = "N/A (CPU never rose meaningfully above its own baseline)"

    # --- Convergence calc: identical threshold-crossing/bound logic to
    # chartroutes_srlinux.py, same reasoning, different data source. ---
    start_idx = None
    end_idx = None
    for i, count in enumerate(route_counts):
        if past_start(count):
            start_idx = max(0, i - 1)
            break
    for i, count in enumerate(route_counts):
        if reached_end(count):
            end_idx = i
            break

    conv_time_str = "N/A - threshold not crossed in captured window"
    conv_rate_str = "N/A"
    if start_idx is not None and end_idx is not None:
        st, et = elapsed_times[start_idx], elapsed_times[end_idx]
        conv_time = et - st
        route_delta_actual = abs(end_val - start_val)
        conv_rate = route_delta_actual / conv_time if conv_time > 0 else 0

        if end_idx == start_idx + 1:
            # No intermediate sample - conv_time is an upper bound, conv_rate a
            # lower bound, not exact values. See chartroutes_srlinux.py's main()
            # for the full reasoning; kept identical here.
            print(f"Convergence time: <= {conv_time:.2f}s (resolution-limited - no "
                  f"intermediate sample fell within this gap)")
            print(f"Convergence rate: >= {conv_rate:.2f} routes/sec (lower bound, not exact)")
            conv_time_str = f"<= {conv_time:.2f}s (resolution-limited)"
            conv_rate_str = f">= {conv_rate:.2f} routes/sec (lower bound)"
        else:
            print(f"Convergence time: {conv_time:.2f}s")
            print(f"Convergence rate: {conv_rate:.2f} routes/sec")
            conv_time_str = f"{conv_time:.2f}s"
            conv_rate_str = f"{conv_rate:.2f} routes/sec"
    else:
        print("ERROR: could not find the start/end threshold crossing in the captured window.")

    # --- Write the Data + Graph sheets via make_prometheus_spreadsheet()
    # (see its docstring for why this is an adapted copy, not an import, of
    # chartroutes_srlinux.py's make_spreadsheet()). ---
    dir_path = os.path.dirname(os.path.realpath(__file__))
    chart_data_loc = os.path.join(dir_path, "data")
    os.makedirs(chart_data_loc, exist_ok=True)
    excel_file_name = os.path.join(chart_data_loc, f"{args.output}_{int(test_start_time)}")

    label_clean = re.sub(r"[:\\/?*\[\]]", "_", args.device_label)[:17]
    sheet_data = {
        "routes": {
            "sheetTitle": f"{label_clean} Routes Data",
            "graphTitle": f"{label_clean} Routes Graph",
            "yAxisLabel": "Route Count",
            "data": {
                "Elapsed Time": elapsed_times,
                "Route Count": route_counts,
            },
        }
    }
    if cpu_values:
        sheet_data["cpu"] = {
            "sheetTitle": f"{label_clean} CPU Data",
            "graphTitle": f"{label_clean} CPU Graph",
            "yAxisLabel": "CPU Percent",
            "data": {
                "Elapsed Time": cpu_times,
                "CPU Percent": cpu_values,
            },
        }
    if mem_values:
        sheet_data["memory"] = {
            "sheetTitle": f"{label_clean} Memory Data",
            "graphTitle": f"{label_clean} Memory Graph",
            "yAxisLabel": "Memory (MB)",
            "data": {
                "Elapsed Time": mem_times,
                "Memory MB": mem_values,
            },
        }
    settle_time_str = "N/A (--skip-system-metrics)" if args.skip_system_metrics else (
        f"{settle_time_val:.2f}s" if settle_detected else
        f"Not settled within {args.settle_max_wait:.0f}s"
    )

    metadata_rows = [
        ("Field", "Value"),
        ("Device", args.device_label),
        ("Prometheus URL", args.prometheus_url),
        ("PromQL query", promql),
        ("Test Start Time (UTC)", datetime.fromtimestamp(test_start_time, tz=timezone.utc).isoformat()),
        ("Route Convergence End (UTC)", datetime.fromtimestamp(route_convergence_end, tz=timezone.utc).isoformat()),
        ("Data Collection End (UTC)", datetime.fromtimestamp(final_data_end_time, tz=timezone.utc).isoformat()),
        ("Test Start Timestamp (Unix)", int(test_start_time)),
        ("Route Convergence End Timestamp (Unix)", int(route_convergence_end)),
        ("Data Collection End Timestamp (Unix)", int(final_data_end_time)),
        ("Baseline", baseline),
        ("Target (end value)", end_val),
        ("Converged before max-wait", converged),
        ("Convergence Time", conv_time_str),
        ("Convergence Rate", conv_rate_str),
        ("CPU Settled", settle_detected if not args.skip_system_metrics else "N/A"),
        ("CPU Settle Time (from route convergence end)", settle_time_str),
        ("Total CPU Run Time (first meaningful rise to settled)", total_cpu_run_time_str),
        ("Note", "CPU Settle Time is flatline-detection (stopped changing), NOT a return to "
                 "the pre-test baseline - CPU may plateau at a new, higher steady state after "
                 "learning a large route set. Total CPU Run Time covers the full CPU-elevated "
                 "window (which can start before route convergence finishes, not only after) - "
                 "it is not simply Convergence Time + Settle Time added together. Use the "
                 "timestamps above to query Prometheus for any other metric (memory, BGP "
                 "session state, ...) over this same window."),
    ]
    overlay = ("routes", "cpu") if cpu_values else None

    make_prometheus_spreadsheet(sheet_data, excel_file_name, metadata_rows=metadata_rows, overlay=overlay)

    print(f"\nSaved: {excel_file_name}.xlsx")


if __name__ == "__main__":
    main()
