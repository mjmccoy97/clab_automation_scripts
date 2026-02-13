#!/usr/bin/env python3

"""
chartroutes_srlinux.py
Script to collect active/total route count for specified protocols from Nokia SR Linux routers
via gNMIc and graph them in an Excel spreadsheet

Modernized version adapted from original Juniper PyEz chartroutes.py
Author: Adapted for SR Linux - 2026

Original concept: M. McCoy 2017
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


def get_route_summary(target, username, password, network_instance='default', protocol='bgp'):
    """
    Get route summary statistics from SR Linux
    
    Args:
        target: IP address or hostname
        username: SSH username
        password: SSH password
        network_instance: Network instance name (default: 'default')
        protocol: Routing protocol (bgp, isis, ospf, static, etc.)
        
    Returns:
        Dictionary with total and active route counts, or None on error
    """
    
    if protocol.lower() == 'bgp':
        # Query the entire BGP container to get afi-safi statistics
        bgp_path = f'/network-instance[name={network_instance}]/protocols/bgp/afi-safi'
        
        stats = {'total': 0, 'active': 0}
        
        bgp_data = execute_gnmic_command(target, username, password, bgp_path)
        if bgp_data:
            try:
                # Navigate the gNMI response structure
                for item in bgp_data:
                    if 'updates' in item:
                        for update in item['updates']:
                            if 'values' in update:
                                values = update['values']
                                
                                # SR Linux returns the full path in the key
                                for key, value in values.items():
                                    if isinstance(value, list):
                                        # afi-safi is a list
                                        for afi_safi in value:
                                            active = afi_safi.get('active-routes', 0)
                                            received = afi_safi.get('received-routes', 0)
                                            
                                            # Convert string numbers to int
                                            if isinstance(active, str):
                                                active = int(active) if active.isdigit() else 0
                                            if isinstance(received, str):
                                                received = int(received) if received.isdigit() else 0
                                            
                                            stats['active'] += active
                                            stats['total'] += received
                                    
                                    elif isinstance(value, dict):
                                        # Sometimes nested differently
                                        if 'afi-safi' in value:
                                            for afi_safi in value['afi-safi']:
                                                active = afi_safi.get('active-routes', 0)
                                                received = afi_safi.get('received-routes', 0)
                                                
                                                if isinstance(active, str):
                                                    active = int(active) if active.isdigit() else 0
                                                if isinstance(received, str):
                                                    received = int(received) if received.isdigit() else 0
                                                
                                                stats['active'] += active
                                                stats['total'] += received
                                
            except Exception as e:
                print(f"Error parsing BGP stats: {e}")
                return None
                
        return stats
        
    else:
        # For other protocols, implement similar logic
        print(f"Protocol {protocol} not yet implemented")
        return None


def get_protocol_stats_by_family(target, username, password, network_instance, protocol, families):
    """
    Get route statistics for specific address families
    
    Args:
        target: Device IP/hostname
        username: Username
        password: Password
        network_instance: Network instance name
        protocol: Protocol name (bgp, isis, etc.)
        families: List of address families (e.g., ['ipv4-unicast', 'ipv6-unicast'])
        
    Returns:
        Dictionary with stats per family: {family: {'total': X, 'active': Y}}
    """
    results = {}
    
    # Map common family names to SR Linux's naming
    family_map = {
        'ipv4-unicast': 'srl_nokia-common:ipv4-unicast',
        'ipv6-unicast': 'srl_nokia-common:ipv6-unicast',
        'evpn': 'srl_nokia-common:evpn'
    }
    
    if protocol.lower() == 'bgp':
        # Query the BGP afi-safi container
        path = f'/network-instance[name={network_instance}]/protocols/bgp/afi-safi'
        
        data = execute_gnmic_command(target, username, password, path)
        
        if data:
            try:
                for item in data:
                    if 'updates' in item:
                        for update in item['updates']:
                            if 'values' in update:
                                values = update['values']
                                
                                # Navigate the response
                                for key, value in values.items():
                                    afi_safi_list = []
                                    
                                    if isinstance(value, list):
                                        afi_safi_list = value
                                    elif isinstance(value, dict) and 'afi-safi' in value:
                                        afi_safi_list = value['afi-safi']
                                    
                                    # Process each AFI-SAFI
                                    for afi_safi in afi_safi_list:
                                        afi_name = afi_safi.get('afi-safi-name', '')
                                        
                                        # Check if this is one of the families we're interested in
                                        for requested_family in families:
                                            sr_linux_name = family_map.get(requested_family, requested_family)
                                            
                                            if afi_name == sr_linux_name:
                                                active = afi_safi.get('active-routes', 0)
                                                received = afi_safi.get('received-routes', 0)
                                                
                                                # Convert strings to integers
                                                if isinstance(active, str):
                                                    active = int(active) if active.isdigit() else 0
                                                if isinstance(received, str):
                                                    received = int(received) if received.isdigit() else 0
                                                
                                                results[requested_family] = {
                                                    'total': received,
                                                    'active': active
                                                }
                
                # Fill in zeros for families not found
                for family in families:
                    if family not in results:
                        results[family] = {'total': 0, 'active': 0}
                        
            except Exception as e:
                print(f"Error parsing AFI-SAFI stats: {e}")
                for family in families:
                    results[family] = {'total': 0, 'active': 0}
        else:
            for family in families:
                results[family] = {'total': 0, 'active': 0}
    else:
        # Other protocols not implemented
        for family in families:
            results[family] = {'total': 0, 'active': 0}
            
    return results


def collect_route_data(target, username, password, network_instance, protocol, families, 
                       duration, data_dict, device_queue, debug=False):
    """
    Thread function to collect route statistics from a device
    
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
            
            # Get stats for each address family
            family_stats = get_protocol_stats_by_family(
                target, username, password, network_instance, protocol, families
            )
            
            if family_stats:
                # Record the elapsed time
                data_dict[target]['routeStats']['Elapsed Time'].append(elapsed)
                
                # Record stats for each family
                for family, stats in family_stats.items():
                    total_key = f"{family} {protocol.upper()} Total Routes"
                    active_key = f"{family} {protocol.upper()} Active Routes"
                    
                    data_dict[target]['routeStats'][total_key].append(stats['total'])
                    data_dict[target]['routeStats'][active_key].append(stats['active'])
                
                sample_count += 1
                
                if debug:
                    print(f"[{target}] Sample {sample_count}: {family_stats}")
            
            # Sleep for 1 second between samples (adjust as needed)
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
                       help='Comma-separated list of device IPs/hostnames')
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
    
    # Parse inputs
    targets = [t.strip() for t in args.targets.split(',')]
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
    
    # Wait for duration or user interrupt
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
    
    # Calculate convergence if requested
    if starting_values and ending_values:
        print(f"\nCalculating convergence time for {args.protocol.upper()}")
        for target in valid_targets:
            print(f"\nConvergence times for {target}:")
            
            for idx, family in enumerate(families):
                total_key = f"{family} {args.protocol.upper()} Total Routes"
                
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
                    
                    # Find start point
                    for i, count in enumerate(route_counts):
                        if count > starting_values[idx]:
                            start_idx = max(0, i - 1)
                            break
                    
                    # Find end point
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
                    print(f"    Convergence time: {conv_time:.2f}s")
                    print(f"    Convergence rate: {conv_rate:.2f} routes/sec")
                else:
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
        sheetData[target] = {
            'sheetTitle': f"{target} Data",
            'graphTitle': f"{target} Graph",
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
