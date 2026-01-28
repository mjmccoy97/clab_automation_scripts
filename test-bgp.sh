#!/bin/bash

# BGP Session Test Script using JSON-RPC for EVPN Lab
# Tests and waits for all BGP neighbors to become established

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Configuration
DEFAULT_TIMEOUT=120
POLL_INTERVAL=5

# SR Linux devices (dynamically discovered)
DEVICES=()

# Function to show usage
show_usage() {
    echo "Usage: $0 [OPTIONS]"
    echo "Test and wait for BGP neighbors to become established using JSON-RPC"
    echo
    echo "Options:"
    echo "  -t, --timeout SECONDS    Timeout in seconds (default: ${DEFAULT_TIMEOUT})"
    echo "  -i, --interval SECONDS   Poll interval in seconds (default: ${POLL_INTERVAL})"
    echo "  -v, --verbose           Verbose output with session details"
    echo "  -h, --help              Show this help message"
    echo
    echo "Examples:"
    echo "  $0                      # Run with default settings"
    echo "  $0 -t 300 -v          # Wait up to 5 minutes with verbose output"
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

# Function to get BGP neighbors for a device using JSON-RPC
get_bgp_neighbors() {
    local device="$1"
    local url="http://clab-poc-${device}/jsonrpc"
    
    # JSON-RPC request to get BGP neighbor session states and descriptions
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
                        "path": "/network-instance[name=default]/protocols/bgp/neighbor[peer-address=*]",
                        "datastore": "state"
                    }
                ]
            }
        }' 2>/dev/null)
    
    if [[ -z "$response" ]]; then
        echo "ERROR_CONNECTION"
        return 1
    fi
    
    # Parse JSON response and extract neighbor info with descriptions
    echo "$response" | jq -r '
        if .result and .result[0] and .result[0].neighbor then
            .result[0].neighbor[] | "\(.["peer-address"]):\(.["session-state"]):\(.["description"] // "")"
        else
            empty
        end
    ' 2>/dev/null || echo "ERROR_PARSE"
}

# Function to check all BGP sessions
check_all_bgp_sessions() {
    local verbose="$1"
    
    local total_sessions=0
    local established_sessions=0
    local device_results=()
    
    if [[ "$verbose" == "true" ]]; then
        echo -e "${CYAN}Checking BGP sessions on all devices...${NC}" >&2
        echo >&2
    fi
    
    for device in "${DEVICES[@]}"; do
        local neighbors_info
        neighbors_info=$(get_bgp_neighbors "$device")
        
        if [[ "$neighbors_info" == "ERROR_CONNECTION" ]]; then
            if [[ "$verbose" == "true" ]]; then
                echo -e "${RED}$device: Connection failed${NC}" >&2
            fi
            device_results+=("$device:ERROR:0:0")
            continue
        elif [[ "$neighbors_info" == "ERROR_PARSE" ]]; then
            if [[ "$verbose" == "true" ]]; then
                echo -e "${RED}$device: Parse error${NC}" >&2
            fi
            device_results+=("$device:ERROR:0:0")
            continue
        fi
        
        local device_total=0
        local device_established=0
        local session_details=()
        
        if [[ -n "$neighbors_info" ]]; then
            while IFS= read -r neighbor_line; do
                if [[ -n "$neighbor_line" ]]; then
                    IFS=':' read -r peer_ip session_state description <<< "$neighbor_line"
                    ((device_total++))
                    ((total_sessions++))
                    
                    if [[ "$session_state" == "established" ]]; then
                        ((device_established++))
                        ((established_sessions++))
                        session_details+=("$peer_ip:✓:$description")
                    else
                        session_details+=("$peer_ip:✗($session_state):$description")
                    fi
                fi
            done <<< "$neighbors_info"
        fi
        
        device_results+=("$device:OK:$device_established:$device_total:$(IFS='|'; echo "${session_details[*]}")")
        
        if [[ "$verbose" == "true" ]]; then
            if [[ $device_established -eq $device_total ]] && [[ $device_total -gt 0 ]]; then
                echo -e "${GREEN}$device: ${device_established}/${device_total} sessions established${NC}" >&2
            else
                echo -e "${YELLOW}$device: ${device_established}/${device_total} sessions established${NC}" >&2
            fi
            
            for session in "${session_details[@]}"; do
                IFS=':' read -r peer status description <<< "$session"
                local peer_display
                if [[ -n "$description" ]]; then
                    peer_display="$peer ($description)"
                else
                    peer_display="$peer"
                fi
                
                if [[ "$status" == "✓" ]]; then
                    echo -e "  ${peer_display}: ${GREEN}established${NC}" >&2
                else
                    echo -e "  ${peer_display}: ${RED}${status#✗}${NC}" >&2
                fi
            done
            echo >&2
        fi
    done
    
    echo "$established_sessions:$total_sessions:$(IFS='~'; echo "${device_results[*]}")"
}

# Function to show detailed session status
show_session_summary() {
    local session_data="$1"
    
    IFS=':' read -r established total details <<< "$session_data"
    
    echo -e "${PURPLE}=== BGP Session Summary ===${NC}"
    echo -e "Sessions: ${GREEN}$established established${NC} / ${BLUE}$total total${NC}"
    
    if [[ $established -eq $total ]] && [[ $total -gt 0 ]]; then
        echo -e "Status: ${GREEN}All BGP sessions are established!${NC}"
    elif [[ $total -eq 0 ]]; then
        echo -e "Status: ${RED}No BGP sessions found${NC}"
    else
        echo -e "Status: ${YELLOW}$((total - established)) sessions not established${NC}"
    fi
    echo
}

# Function to show detailed peer status for each device
show_detailed_peer_status() {
    local session_data="$1"
    
    IFS=':' read -r established total details <<< "$session_data"
    
    echo -e "${PURPLE}=== Detailed BGP Peer Status ===${NC}"
    echo -e "Total sessions: ${BLUE}$total${NC}, Established: ${GREEN}$established${NC}, Not established: ${YELLOW}$((total - established))${NC}"
    echo
    
    # Parse device results
    IFS='~' read -r -a device_results <<< "$details"
    
    for device_result in "${device_results[@]}"; do
        if [[ -z "$device_result" ]]; then
            continue
        fi
        
        IFS=':' read -r device status device_est device_total session_list <<< "$device_result"
        
        if [[ "$status" == "ERROR" ]]; then
            echo -e "${RED}$device: ERROR (connection or parse error)${NC}"
            continue
        fi
        
        # Skip devices with no BGP peers configured
        if [[ $device_total -eq 0 ]]; then
            continue
        fi
        
        # Show device summary
        if [[ $device_est -eq $device_total ]]; then
            echo -e "${GREEN}$device: All $device_total sessions established${NC}"
        else
            echo -e "${YELLOW}$device: $device_est/$device_total sessions established${NC}"
        fi
        
        # Show individual peer states
        if [[ -n "$session_list" ]]; then
            IFS='|' read -r -a sessions <<< "$session_list"
            for session in "${sessions[@]}"; do
                if [[ -n "$session" ]]; then
                    IFS=':' read -r peer_ip status_symbol description <<< "$session"
                    
                    # Format peer display with device name if available
                    local peer_display
                    if [[ -n "$description" ]]; then
                        peer_display="$peer_ip ($description)"
                    else
                        peer_display="$peer_ip"
                    fi
                    
                    if [[ "$status_symbol" == "✓" ]]; then
                        echo -e "  ${peer_display}: ${GREEN}established${NC}"
                    else
                        # Remove the ✗ symbol and parentheses to show just the state
                        clean_status="${status_symbol#✗}"
                        clean_status="${clean_status#(}"
                        clean_status="${clean_status%)}"
                        echo -e "  ${peer_display}: ${RED}${clean_status}${NC}"
                    fi
                fi
            done
        fi
        echo
    done
}

# Function to wait for BGP sessions to be established
wait_for_bgp_sessions() {
    local timeout="$1"
    local poll_interval="$2"
    local verbose="$3"
    
    local start_time=$(date +%s)
    local end_time=$((start_time + timeout))
    
    echo -e "${BLUE}Waiting for BGP sessions to be established...${NC}"
    echo -e "${YELLOW}Timeout: ${timeout}s, Poll interval: ${poll_interval}s${NC}"
    echo
    
    while true; do
        local current_time=$(date +%s)
        local elapsed=$((current_time - start_time))
        
        if [[ $current_time -gt $end_time ]]; then
            echo -e "${RED}Timeout reached after ${elapsed}s${NC}"
            echo
            # Show detailed peer status on timeout
            local final_session_data
            final_session_data=$(check_all_bgp_sessions false)
            show_detailed_peer_status "$final_session_data"
            return 1
        fi
        
        local session_data
        session_data=$(check_all_bgp_sessions false)
        
        IFS=':' read -r established total _ <<< "$session_data"
        
        local remaining=$((end_time - current_time))
        echo -e "${BLUE}[${elapsed}s/${timeout}s]${NC} BGP Sessions: ${GREEN}$established${NC}/${BLUE}$total${NC} established (${remaining}s remaining)"
        
        if [[ $established -eq $total ]] && [[ $total -gt 0 ]]; then
            echo -e "${GREEN}All BGP sessions are established!${NC}"
            echo
            show_detailed_peer_status "$session_data"
            return 0
        fi
        
        if [[ "$verbose" == "true" ]]; then
            echo >&2
            show_session_summary "$session_data" >&2
        fi
        
        sleep "$poll_interval"
    done
}

# Parse command line arguments
TIMEOUT="$DEFAULT_TIMEOUT"
INTERVAL="$POLL_INTERVAL"
VERBOSE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -t|--timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        -i|--interval)
            INTERVAL="$2"
            shift 2
            ;;
        -v|--verbose)
            VERBOSE=true
            shift
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

# Validate arguments
if [[ ! "$TIMEOUT" =~ ^[0-9]+$ ]] || [[ $TIMEOUT -lt 1 ]]; then
    echo -e "${RED}Error: Timeout must be a positive integer${NC}"
    exit 1
fi

if [[ ! "$INTERVAL" =~ ^[0-9]+$ ]] || [[ $INTERVAL -lt 1 ]]; then
    echo -e "${RED}Error: Interval must be a positive integer${NC}"
    exit 1
fi

# Check if required tools are available
for tool in curl jq; do
    if ! command -v "$tool" &> /dev/null; then
        echo -e "${RED}Error: Required tool '$tool' is not installed${NC}"
        exit 1
    fi
done

# Check if lab is deployed
check_lab_deployed

# Show configuration
echo -e "${PURPLE}=== BGP Session Test Settings ===${NC}"
echo -e "Timeout: ${TIMEOUT}s"
echo -e "Poll interval: ${INTERVAL}s"
echo

# Start monitoring
if wait_for_bgp_sessions "$TIMEOUT" "$INTERVAL" "$VERBOSE"; then
    echo -e "${GREEN}Success: All BGP sessions are established!${NC}"
    exit 0
else
    echo -e "${RED}Failed: Not all BGP sessions became established within timeout${NC}"
    echo
    # Show detailed peer status
    session_data=$(check_all_bgp_sessions false)
    show_detailed_peer_status "$session_data"
    exit 1
fi