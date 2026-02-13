#!/usr/bin/env python3

"""
test_gnmi_connectivity.py
Utility script to test gNMI connectivity to SR Linux devices and explore available paths

Usage:
    python3 test_gnmi_connectivity.py -t <target> -u <username> -p <password>
"""

import sys
import json
import subprocess
import argparse


def test_connection(target, username, password):
    """Test basic gNMI connectivity"""
    print(f"\n{'='*60}")
    print(f"Testing gNMI connectivity to {target}")
    print(f"{'='*60}\n")
    
    cmd = [
        'gnmic',
        '-a', f'{target}:57400',
        '--skip-verify',
        '-u', username,
        '-p', password,
        '--encoding', 'json_ietf',
        'capabilities'
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            print("✓ Connection successful!")
            print("\nDevice capabilities:")
            print(result.stdout)
            return True
        else:
            print("✗ Connection failed!")
            print(f"Error: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        print("✗ Connection timeout!")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def get_network_instances(target, username, password):
    """List all network instances"""
    print(f"\n{'='*60}")
    print("Available Network Instances")
    print(f"{'='*60}\n")
    
    # Try a broader query first
    cmd = [
        'gnmic',
        '-a', f'{target}:57400',
        '--skip-verify',
        '-u', username,
        '-p', password,
        '--encoding', 'json_ietf',
        'get',
        '--path', '/network-instance/name',
        '--format', 'json'
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            data = json.loads(result.stdout)
            instances = set()  # Use set to avoid duplicates
            
            for item in data:
                if 'updates' in item:
                    for update in item['updates']:
                        if 'values' in update:
                            # Extract network instance names from the path or values
                            for key, value in update['values'].items():
                                if key:  # Filter out empty strings
                                    instances.add(key)
                                # Sometimes the name is in the value
                                if isinstance(value, dict) and 'name' in value:
                                    instances.add(value['name'])
            
            instances = sorted(list(instances))  # Convert to sorted list
            
            if instances:
                for ni in instances:
                    print(f"  - {ni}")
                return instances
            else:
                print("  Using default network instance")
                return ['default']
        else:
            print(f"  Could not query network instances: {result.stderr}")
            print("  Using 'default' as fallback")
            return ['default']
            
    except Exception as e:
        print(f"  Error: {e}")
        print("  Using 'default' as fallback")
        return ['default']


def get_bgp_neighbor_count(target, username, password, network_instance='default'):
    """Get count of BGP neighbors"""
    path = f'/network-instance[name={network_instance}]/protocols/bgp/neighbor'
    
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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            data = json.loads(result.stdout)
            neighbor_count = 0
            
            for item in data:
                if 'updates' in item:
                    for update in item['updates']:
                        if 'values' in update:
                            values = update['values']
                            
                            # SR Linux returns the full path structure
                            # Look for 'network-instance' key or nested structure
                            for key, value in values.items():
                                if isinstance(value, dict):
                                    # Check if this is the network-instance level
                                    if 'protocols' in value:
                                        if 'bgp' in value['protocols']:
                                            neighbors = value['protocols']['bgp'].get('neighbor', {})
                                            neighbor_count = len(neighbors)
                                    # Or check nested structure
                                    elif 'network-instance' in value:
                                        for ni_name, ni_data in value['network-instance'].items():
                                            if ni_name == network_instance and isinstance(ni_data, dict):
                                                if 'protocols' in ni_data and 'bgp' in ni_data['protocols']:
                                                    neighbors = ni_data['protocols']['bgp'].get('neighbor', {})
                                                    neighbor_count = len(neighbors)
            
            return neighbor_count
        else:
            # If the query fails, BGP might not be configured
            return 0
            
    except Exception:
        return 0


def get_bgp_statistics(target, username, password, network_instance='default', debug=False):
    """Get BGP statistics for a network instance"""
    print(f"\n{'='*60}")
    print(f"BGP Information for network-instance: {network_instance}")
    print(f"{'='*60}\n")
    
    # First check if BGP is configured by trying to query the BGP container
    bgp_path = f'/network-instance[name={network_instance}]/protocols/bgp'
    
    cmd = [
        'gnmic',
        '-a', f'{target}:57400',
        '--skip-verify',
        '-u', username,
        '-p', password,
        '--encoding', 'json_ietf',
        'get',
        '--path', bgp_path,
        '--format', 'json'
    ]
    
    bgp_configured = False
    neighbor_count = 0
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            data = json.loads(result.stdout)
            
            if debug:
                print("DEBUG: BGP query response:")
                print(json.dumps(data, indent=2))
            
            # Parse the response to check for BGP configuration
            for item in data:
                if 'updates' in item:
                    for update in item['updates']:
                        if 'values' in update and update['values']:
                            bgp_configured = True
                            values = update['values']
                            
                            # Try to count neighbors
                            for key, value in values.items():
                                if isinstance(value, dict):
                                    if 'protocols' in value and 'bgp' in value['protocols']:
                                        neighbors = value['protocols']['bgp'].get('neighbor', {})
                                        neighbor_count = len(neighbors)
                                    elif 'neighbor' in value:
                                        neighbor_count = len(value['neighbor'])
        else:
            if debug:
                print(f"DEBUG: BGP query failed: {result.stderr}")
                
    except Exception as e:
        if debug:
            print(f"DEBUG: Exception during BGP query: {e}")
    
    if not bgp_configured:
        print("  ℹ BGP is not configured in this network instance")
        print(f"\n  To configure BGP, use the SR Linux CLI:")
        print(f"    enter candidate")
        print(f"    /network-instance {network_instance} protocols bgp")
        print(f"    commit stay")
        return
    
    if neighbor_count > 0:
        print(f"  BGP Neighbors: {neighbor_count}")
    else:
        print("  BGP is configured but has no neighbors")
        print("  Route statistics require active BGP sessions")
    
    # Try to get route statistics from the afi-safi container
    print("\n  Querying AFI-SAFI statistics...")
    stats_found = False
    
    for key, value in values.items():
        if isinstance(value, dict):
            # Look for afi-safi in the BGP container
            if 'afi-safi' in value:
                afi_safis = value['afi-safi']
                
                for afi_safi in afi_safis:
                    afi_name = afi_safi.get('afi-safi-name', '')
                    
                    # Map SR Linux AFI-SAFI names to our tracking names
                    family_map = {
                        'srl_nokia-common:ipv4-unicast': 'ipv4-unicast',
                        'srl_nokia-common:ipv6-unicast': 'ipv6-unicast',
                        'srl_nokia-common:evpn': 'evpn'
                    }
                    
                    if afi_name in family_map:
                        family = family_map[afi_name]
                        active = afi_safi.get('active-routes', 0)
                        received = afi_safi.get('received-routes', 0)
                        
                        # Only show if there are routes
                        if int(active) > 0 or int(received) > 0:
                            if not stats_found:
                                print("\n  Address Family Statistics:")
                            stats_found = True
                            print(f"    {family.replace('-', ' ').title()}:")
                            print(f"      Active Routes: {active}")
                            print(f"      Received Routes: {received}")
            
            # Also check top-level statistics
            if 'statistics' in value:
                stats = value['statistics']
                total_active = stats.get('total-active-routes', 0)
                total_paths = stats.get('total-paths', 0)
                total_prefixes = stats.get('total-prefixes', 0)
                
                if int(total_active) > 0:
                    print(f"\n  Overall BGP Statistics:")
                    print(f"    Total Active Routes: {total_active}")
                    print(f"    Total Paths: {total_paths}")
                    print(f"    Total Prefixes: {total_prefixes}")
                    stats_found = True
    
    if not stats_found:
        print("\n  ℹ No route statistics available yet")
        print("    Statistics are populated when BGP sessions are established and routes are received")
        print("\n  Manual query example:")
        print(f"    gnmic -a {target}:57400 --skip-verify -u {username} -p *** \\")
        print(f"      --encoding json_ietf get --path '{bgp_path}' --format json | jq")


def show_example_paths(target, username, password):
    """Show example gNMI paths for manual testing"""
    print(f"\n{'='*60}")
    print("Example gNMI Paths for Manual Testing")
    print(f"{'='*60}\n")
    
    base_cmd = f"gnmic -a {target}:57400 --skip-verify -u {username} -p **** --encoding json_ietf"
    
    examples = [
        ("All Network Instances", "/network-instance/name"),
        ("BGP Neighbors (default)", "/network-instance[name=default]/protocols/bgp/neighbor"),
        ("BGP RIB (default)", "/network-instance[name=default]/protocols/bgp/rib"),
        ("BGP Statistics (default)", "/network-instance[name=default]/protocols/bgp/statistics"),
        ("Interface List", "/interface/name"),
        ("Interface Statistics", "/interface[name=*]/statistics"),
        ("System Info", "/system/information"),
    ]
    
    for desc, path in examples:
        print(f"{desc}:")
        print(f"  {base_cmd} get --path '{path}' --format json\n")
    
    print("For route counting in chartroutes, the tool will query:")
    print(f"  {base_cmd} get \\")
    print("    --path '/network-instance[name=default]/protocols/bgp/statistics/ipv4-unicast' --format json")
    print(f"  {base_cmd} get \\")
    print("    --path '/network-instance[name=default]/protocols/bgp/statistics/ipv6-unicast' --format json")


def main():
    parser = argparse.ArgumentParser(
        description='Test gNMI connectivity to Nokia SR Linux devices'
    )
    
    parser.add_argument('-t', '--target', required=True,
                       help='Device IP address or hostname')
    parser.add_argument('-u', '--username', required=True,
                       help='Username for device login')
    parser.add_argument('-p', '--password', required=True,
                       help='Password for device login')
    parser.add_argument('-n', '--network-instance', default='default',
                       help='Network instance to query (default: default)')
    parser.add_argument('-e', '--examples-only', action='store_true',
                       help='Only show example commands without testing')
    parser.add_argument('-d', '--debug', action='store_true',
                       help='Enable debug output to see raw gNMI responses')
    
    args = parser.parse_args()
    
    if args.examples_only:
        show_example_paths(args.target, args.username, args.password)
        return
    
    # Test basic connectivity
    if not test_connection(args.target, args.username, args.password):
        print("\n⚠ Basic connectivity test failed. Please check:")
        print("  1. Device IP/hostname is correct")
        print("  2. Port 57400 is accessible")
        print("  3. gNMI is enabled on the device")
        print("  4. Credentials are correct")
        print("\nTo enable gNMI on SR Linux:")
        print("  enter candidate")
        print("  /system gnmi-server admin-state enable")
        print("  /system gnmi-server network-instance mgmt admin-state enable")
        print("  commit now")
        sys.exit(1)
    
    # Get network instances
    instances = get_network_instances(args.target, args.username, args.password)
    
    # Get BGP statistics
    if args.network_instance in instances or args.network_instance == 'default':
        get_bgp_statistics(args.target, args.username, args.password, args.network_instance, args.debug)
    else:
        print(f"\n⚠ Network instance '{args.network_instance}' not found")
        if instances:
            print(f"Available instances: {', '.join(instances)}")
            print(f"\nTry using: -n {instances[0]}")
    
    # Show example paths
    show_example_paths(args.target, args.username, args.password)
    
    print(f"\n{'='*60}")
    print("✓ Connectivity test completed")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
