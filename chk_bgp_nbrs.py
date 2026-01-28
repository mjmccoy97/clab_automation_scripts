#!/usr/bin/env python3
# 01.21.26 M. McCoy Script to report the BGP neighbors in network-instances
# 01.22.26 Updated to discover SRLinux devices in Containerlab dynamically
# 01.28.26 Added ANSI color coding for BGP states and refined logic
# 01.28.26 REWRITE: Replaced pygnmi with gnmic subprocess for stability

import argparse
import json
import subprocess
import sys

# ANSI Color Codes
GREEN = "\033[92m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"

def discover_devices():
    """Discover SR Linux devices based on the specific JSON structure provided."""
    try:
        cmd = ["containerlab", "inspect", "--format", "json"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        data = json.loads(result.stdout)
        
        devices = []
        # Support both old and new clab JSON formats
        nodes_list = data if isinstance(data, list) else data.get('lab_nodes', [])
        if not nodes_list and isinstance(data, dict):
            for val in data.values():
                if isinstance(val, list):
                    nodes_list.extend(val)

        for node in nodes_list:
            if node.get('kind') == 'nokia_srlinux':
                devices.append(node['name'])
        return devices
    except Exception as e:
        print(f"Error parsing containerlab output: {e}")
        return []

def get_bgp_data_via_gnmic(router, user, pwd, port, ni_filter):
    """Fetches BGP neighbor data using gnmic CLI."""
    # Construct the path based on filter
    path = f"/network-instance[name={ni_filter}]/protocols/bgp/neighbor"
    
    cmd = [
        "gnmic", "-a", f"{router}:{port}",
        "-u", user, "-p", pwd,
        "--skip-verify", "get",
        "--path", path, "--format", "json"
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        # Check if the error is just a 'not found' or a connection issue
        if "NotFound" in e.stderr:
            return {}
        raise Exception(e.stderr.strip())

def main():
    # 1. CLI Setup
    parser = argparse.ArgumentParser(description='Obtain BGP peer state via gnmic.')
    parser.add_argument('-ni', type=str, default='*', 
                        help='Specific network-instance name (e.g., "default"). Use "*" for all.')
    parser.add_argument('-p', type=str, default='NokiaSrl1!', help='SRLinux password')
    parser.add_argument('-u', type=str, default='admin', help='SRLinux username')
    parser.add_argument('--port', type=int, default=57401, help='gNMI port')

    args = parser.parse_args()

    print(f"{BOLD}Discovering SRLinux Devices in Containerlab...{RESET}")
    routers = discover_devices()
    if not routers:
        print(f"{RED}No SRLinux devices found. Exiting.{RESET}")
        exit(1)
    print(f'{BOLD}Found {len(routers)} SRLinux Devices{RESET}')

    total_peers = 0
    total_est_peers = 0 

    for router in routers:
        print(f"\n{'*' * 60}\nRouter: {BOLD}{router}{RESET} (Filter: {args.ni})\n{'*' * 60}")

        try:
            raw_data = get_bgp_data_via_gnmic(router, args.u, args.p, args.port, args.ni)
            
            # 1. Access the first message in the list
            if not raw_data or not isinstance(raw_data, list):
                continue
            
            updates = raw_data[0].get('updates', [])
            for update in updates:
                # 2. Access the values, then the empty string key ""
                values_container = update.get('values', {}).get('', {})
                
                # 3. Find the network-instance key (ignoring prefixes)
                ni_key = next((k for k in values_container.keys() if 'network-instance' in k), None)
                if not ni_key: continue
                
                ni_list = values_container[ni_key]
                if not isinstance(ni_list, list): ni_list = [ni_list]

                for ni in ni_list:
                    ni_name = ni.get('name', 'unknown')
                    # 4. Dig into protocols -> bgp -> neighbor
                    protocols = ni.get('protocols', {})
                    bgp_key = next((k for k in protocols.keys() if 'bgp' in k), None)
                    if not bgp_key: continue
                    
                    bgp_data = protocols[bgp_key]
                    neighbors = bgp_data.get('neighbor', [])
                    
                    peer_count = 0
                    estab_count = 0
                    
                    for n in neighbors:
                        peer = n.get('peer-address', 'N/A')
                        state = n.get('session-state', 'N/A')
                        group = n.get('peer-group', 'N/A')
                        peer_type = n.get('peer-type', 'N/A')
                        
                        peer_count += 1
                        total_peers += 1

                        if state.lower() == 'established':
                            color = GREEN
                            estab_count += 1
                            total_est_peers += 1
                        else:
                            color = RED

                        print(f"  Instance: {ni_name:<12} | Peer: {peer:<15} | Group: {group:<15} | Type: {peer_type:<10} | State: {color}{state}{RESET}")
                    
                    if peer_count > 0:
                        print(f"  {'-' * 105}")
                        print(f"  Instance Summary: {ni_name:<12} | Total: {peer_count} | Up: {GREEN}{estab_count}{RESET}")

        except Exception as e:
            print(f"{RED}Error on {router}: {e}{RESET}")

    # Final Fabric Summary
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}FABRIC BGP HEALTH SUMMARY{RESET}")
    print(f"{'='*60}")
    print(f'Total SRLinux Devices Found: {len(routers)}')
    print(f"Total Peers found:           {total_peers}")
    print(f"Total Peers Established:     {GREEN}{total_est_peers}{RESET}")
    
    down_peers = total_peers - total_est_peers
    if down_peers > 0:
        print(f"Total Peers Down:            {RED}{down_peers}{RESET}")
    else:
        print(f"Total Peers Down:            {GREEN}0 (All Established!){RESET}")
    print(f"{BOLD}{'='*60}{RESET}\n")

if __name__ == "__main__":
    main()