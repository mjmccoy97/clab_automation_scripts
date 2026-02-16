#!/usr/bin/env python3
# 01.21.26 M. McCoy Script to report the BGP neighbors in network-instances
# 01.22.26 Updated to discover SRLinux devices in Containerlab dynamically
# 01.28.26 Added ANSI color coding for BGP states and refined logic

import argparse
import json
import subprocess
from pygnmi.client import gNMIclient

# ANSI Color Codes
GREEN = "\033[92m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"

def discover_devices():
    """Discover SR Linux devices based on the specific JSON structure provided."""
    try:
        cmd = "containerlab inspect --format json"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        data = json.loads(result.stdout)
        
        devices = []
        for lab_nodes in data.values():
            for node in lab_nodes:
                if node.get('kind') == 'nokia_srlinux':
                    devices.append(node['name'])
        return devices
    except Exception as e:
        print(f"Error parsing containerlab output: {e}")
        return []

def get_prefixed_key(data_dict, base_name):
    """Finds a key in a dict that ends with the base_name."""
    if not isinstance(data_dict, dict): return {}
    for key in data_dict.keys():
        if key == base_name or key.endswith(f":{base_name}"):
            return data_dict[key]
    return {}

def main():
    # 1. CLI Setup
    parser = argparse.ArgumentParser(description='Obtain BGP peer state via gNMI.')
    parser.add_argument('-ni', type=str, default='*', 
                        help='Specific network-instance name (e.g., "default"). Use "*" for all.')
    parser.add_argument('-p', type=str, default='admin', help='SRLinux password')
    parser.add_argument('-u', type=str, default='admin', help='SRLinux username')
    parser.add_argument('--port', type=int, default=57401, help='gNMI port')

    args = parser.parse_args()

    print(f"{BOLD}Discovering SRLinux Devices in Containerlab...{RESET}")
    routers = discover_devices()
    if not routers:
        print(f"{RED}No SRLinux devices found. Exiting.{RESET}")
        exit(1)
    print(f'{BOLD}Found {len(routers)} SRLinux Devices')

    path = [f'network-instance[name={args.ni}]/protocols/bgp/neighbor']
    total_peers = 0
    total_est_peers = 0 

    for router in routers:
        target_params = {
            'target': (router, args.port),
            'username': args.u,
            'password': args.p,
            'insecure': True,
            'timeout': 10
        }   

        print(f"\n{'*' * 60}\nRouter: {BOLD}{router}{RESET} (Filter: {args.ni})\n{'*' * 60}")

        try:
            with gNMIclient(**target_params) as gc:
                result = gc.get(path=path, datatype='state')
                updates = result.get('notification', [{}])[0].get('update', [])

                for update in updates:
                    val = update.get('val', {})
                    raw_ni_data = get_prefixed_key(val, 'network-instance')
                    ni_list = raw_ni_data if isinstance(raw_ni_data, list) else [raw_ni_data]

                    for ni in ni_list:
                        if not ni and args.ni == '*': continue
                        ni_name = ni.get('name', args.ni)
                        
                        protocols = ni.get('protocols', ni)
                        bgp_container = get_prefixed_key(protocols, 'bgp')
                        neighbor_list = bgp_container.get('neighbor', [])
                        
                        if not neighbor_list and 'neighbor' in val:
                            neighbor_list = val['neighbor']

                        if isinstance(neighbor_list, dict):
                            neighbor_list = [neighbor_list]

                        peer_count = 0
                        estab_count = 0
                        for n in neighbor_list:
                            peer = n.get('peer-address', 'N/A')
                            state = n.get('session-state', 'N/A')
                            group = n.get('peer-group', 'N/A')
                            peer_type = n.get('peer-type', 'N/A')
                            
                            peer_count += 1
                            total_peers += 1

                            # Apply Color Coding
                            if state.lower() == 'established':
                                color = GREEN
                                estab_count += 1
                                total_est_peers += 1
                            else:
                                color = RED

                            print(f"  Instance: {ni_name:<12} | Peer: {peer:<15} | Group: {group:<15} | Type: {peer_type:<10} | State: {color}{state}{RESET}")
                        
                        # Print instance summary
                        if peer_count > 0:
                            print(f"  {'-' * 110}")
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