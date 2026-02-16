#!/bin/bash

# LLDP Neighbor Verification Script for EVPN Lab
# Verifies that LLDP neighbors match the expected topology cutsheet

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Configuration
CUTSHEET_FILE="cutsheet.csv"
VERBOSE=false

# SR Linux devices (dynamically discovered)
DEVICES=()

# Function to show usage
show_usage() {
    echo "Usage: $0 [OPTIONS]"
    echo "Verify LLDP neighbors against expected topology cutsheet"
    echo
    echo "Options:"
    echo "  -v, --verbose           Verbose output with detailed neighbor info"
    echo "  -c, --cutsheet FILE     Cutsheet CSV file (default: cutsheet.csv)"
    echo "  -h, --help              Show this help message"
    echo
    echo "Examples:"
    echo "  $0                      # Run with default settings"
    echo "  $0 -v                   # Run with verbose output"
}

# Function to discover SR Linux devices dynamically
discover_srlinux_devices() {
    local devices_json
    devices_json=$(containerlab inspect --format json 2>/dev/null | jq -r '.poc[] | select(.kind == "nokia_srlinux") | .name' | sed 's/^clab-poc-//')
    
    if [[ -z "$devices_json" ]]; then
        echo -e "${RED}Error: No SR Linux devices found in the topology${NC}"
        exit 1
    fi
    
    # Convert to array
    readarray -t DEVICES <<< "$devices_json"
    
    echo -e "${CYAN}Discovered ${#DEVICES[@]} SR Linux devices: ${DEVICES[*]}${NC}"
}

# Function to check if lab is deployed and discover devices
check_lab_deployed() {
    if ! containerlab inspect > /dev/null 2>&1; then
        echo -e "${RED}Error: Lab topology is not deployed. Run 'make deploy' first.${NC}"
        exit 1
    fi
    
    discover_srlinux_devices
}

# Function to load expected neighbors from cutsheet
load_cutsheet() {
    local cutsheet_file="$1"
    
    if [[ ! -f "$cutsheet_file" ]]; then
        echo -e "${RED}Error: Cutsheet file '$cutsheet_file' not found${NC}"
        exit 1
    fi
    
    # Read cutsheet and build associative array of expected neighbors
    # Format: device:interface -> remote_device:remote_interface
    declare -g -A EXPECTED_NEIGHBORS
    
    while IFS=',' read -r local_device local_role local_interface remote_device remote_role remote_interface; do
        # Skip header line
        if [[ "$local_device" == "local_device" ]]; then
            continue
        fi
        
        # Skip any connections involving client devices
        if [[ "$local_role" == "client" ]] || [[ "$remote_role" == "client" ]]; then
            continue
        fi
        
        # Only process non-client connections
        EXPECTED_NEIGHBORS["$local_device:$local_interface"]="$remote_device:$remote_interface"
    done < "$cutsheet_file"
    
    if [[ "$VERBOSE" == "true" ]]; then
        echo -e "${BLUE}Loaded ${#EXPECTED_NEIGHBORS[@]} expected neighbor relationships${NC}"
    fi
}

# Function to get LLDP neighbors for a device using JSON-RPC
get_lldp_neighbors() {
    local device="$1"
    local url="http://clab-poc-${device}/jsonrpc"
    
    # JSON-RPC request to get LLDP neighbors (excluding mgmt0)
    local response
    response=$(curl -s -u admin:NokiaSrl1! "$url" \
        -H "Content-Type: application/json" \
        -d '{
            "jsonrpc": "2.0",
            "id": 0,
            "method": "get",
            "params": {
                "commands": [
                    {
                        "path": "/system/lldp",
                        "datastore": "state"
                    }
                ]
            }
        }' 2>/dev/null)
    
    if [[ -z "$response" ]]; then
        echo "ERROR_CONNECTION"
        return 1
    fi
    
    # Parse JSON response and extract LLDP neighbor info
    # Format: interface:remote_system_name:remote_port_id
    echo "$response" | jq -r '
        if .result and .result[0] and .result[0].interface then
            .result[0].interface[] |
            select(.name != "mgmt0") |
            if .neighbor then
                .neighbor[] as $neighbor |
                "\(.name):\($neighbor["system-name"] // "unknown"):\($neighbor["port-id"] // "unknown")"
            else
                empty
            end
        else
            empty
        end
    ' 2>/dev/null || echo "ERROR_PARSE"
}

# Function to verify LLDP neighbors for all devices
verify_lldp_neighbors() {
    local total_checks=0
    local passed_checks=0
    local failed_checks=0
    local missing_checks=0
    
    echo -e "${PURPLE}=== LLDP Neighbor Verification ===${NC}"
    echo
    
    for device in "${DEVICES[@]}"; do
        echo -e "${BLUE}Checking $device...${NC}"
        
        local neighbors_info
        neighbors_info=$(get_lldp_neighbors "$device")
        
        if [[ "$neighbors_info" == "ERROR_CONNECTION" ]]; then
            echo -e "${RED}  Connection failed${NC}"
            continue
        elif [[ "$neighbors_info" == "ERROR_PARSE" ]]; then
            echo -e "${RED}  Parse error${NC}"
            continue
        fi
        
        # Track which expected neighbors we've seen for this device
        declare -A seen_neighbors
        
        # Process actual LLDP neighbors
        if [[ -n "$neighbors_info" ]]; then
            while IFS= read -r neighbor_line; do
                if [[ -n "$neighbor_line" ]]; then
                    IFS=':' read -r interface remote_system remote_port <<< "$neighbor_line"
                    
                    # Look up expected neighbor for this interface
                    local expected="${EXPECTED_NEIGHBORS["$device:$interface"]}"
                    
                    if [[ -n "$expected" ]]; then
                        IFS=':' read -r expected_device expected_interface <<< "$expected"
                        
                        # Mark this expected neighbor as seen
                        seen_neighbors["$device:$interface"]="seen"
                        
                        # Verify the neighbor matches expectation
                        if [[ "$remote_system" == "$expected_device" ]]; then
                            echo -e "  ${GREEN}✓ $interface → $remote_system:$remote_port (expected: $expected)${NC}"
                            ((passed_checks++))
                        else
                            echo -e "  ${RED}✗ $interface → $remote_system:$remote_port (expected: $expected)${NC}"
                            ((failed_checks++))
                        fi
                    else
                        # Unexpected neighbor (not in cutsheet)
                        echo -e "  ${YELLOW}? $interface → $remote_system:$remote_port (not in cutsheet)${NC}"
                    fi
                    
                    ((total_checks++))
                fi
            done <<< "$neighbors_info"
        fi
        
        # Check for missing expected neighbors
        for expected_key in "${!EXPECTED_NEIGHBORS[@]}"; do
            IFS=':' read -r exp_device exp_interface <<< "$expected_key"
            
            if [[ "$exp_device" == "$device" ]] && [[ -z "${seen_neighbors[$expected_key]}" ]]; then
                local expected_neighbor="${EXPECTED_NEIGHBORS[$expected_key]}"
                echo -e "  ${RED}✗ $exp_interface → MISSING (expected: $expected_neighbor)${NC}"
                ((missing_checks++))
                ((total_checks++))
            fi
        done
        
        echo
    done
    
    # Summary
    echo -e "${PURPLE}=== Verification Summary ===${NC}"
    echo -e "Total checks: ${BLUE}$total_checks${NC}"
    echo -e "Passed: ${GREEN}$passed_checks${NC}"
    echo -e "Failed: ${RED}$failed_checks${NC}"
    echo -e "Missing: ${RED}$missing_checks${NC}"
    
    if [[ $failed_checks -eq 0 ]] && [[ $missing_checks -eq 0 ]]; then
        echo -e "${GREEN}All LLDP neighbors match the expected topology!${NC}"
        return 0
    else
        echo -e "${RED}LLDP verification failed - topology mismatch detected${NC}"
        return 1
    fi
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        -c|--cutsheet)
            CUTSHEET_FILE="$2"
            shift 2
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            show_usage
            exit 1
            ;;
    esac
done

# Check if required tools are available
for tool in curl jq; do
    if ! command -v "$tool" &> /dev/null; then
        echo -e "${RED}Error: Required tool '$tool' is not installed${NC}"
        exit 1
    fi
done

# Check if lab is deployed
check_lab_deployed

# Load cutsheet
load_cutsheet "$CUTSHEET_FILE"

# Show configuration
echo -e "${PURPLE}=== LLDP Verification Settings ===${NC}"
echo -e "Cutsheet file: $CUTSHEET_FILE"
echo

# Start verification
if verify_lldp_neighbors; then
    echo -e "${GREEN}Success: All LLDP neighbors verified!${NC}"
    exit 0
else
    echo -e "${RED}Failed: LLDP verification detected issues${NC}"
    exit 1
fi