#!/bin/bash

# Multicast Traffic Verification Script for EVPN Lab
# Verifies that multicast traffic flows from client8 to client7

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Configuration
MCAST_GROUP="239.0.0.1"
MCAST_PORT="5000"
CLIENT7_CONTAINER="clab-poc-client7"
CLIENT8_CONTAINER="clab-poc-client8"
CLIENT8_IP="10.255.80.2"
TEST_DURATION=10  # seconds
PACKET_COUNT=10
VERBOSE=false

# Function to show usage
show_usage() {
    echo "Usage: $0 [OPTIONS]"
    echo "Verify multicast traffic flow from client8 to client7"
    echo
    echo "Options:"
    echo "  -v, --verbose           Verbose output with detailed information"
    echo "  -d, --duration SECONDS  Test duration in seconds (default: 10)"
    echo "  -g, --group ADDRESS     Multicast group address (default: 239.0.0.1)"
    echo "  -p, --port PORT         Multicast port (default: 5000)"
    echo "  -h, --help              Show this help message"
    echo
    echo "Examples:"
    echo "  $0                      # Run with default settings"
    echo "  $0 -v -d 20            # Run for 20 seconds with verbose output"
}

# Function to check if lab is deployed
check_lab_deployed() {
    if ! containerlab inspect > /dev/null 2>&1; then
        echo -e "${RED}Error: Lab topology is not deployed. Run 'make deploy' first.${NC}"
        exit 1
    fi
    
    # Check if client containers exist
    if ! docker ps --format '{{.Names}}' | grep -q "$CLIENT7_CONTAINER"; then
        echo -e "${RED}Error: $CLIENT7_CONTAINER not found${NC}"
        exit 1
    fi
    
    if ! docker ps --format '{{.Names}}' | grep -q "$CLIENT8_CONTAINER"; then
        echo -e "${RED}Error: $CLIENT8_CONTAINER not found${NC}"
        exit 1
    fi
    
    echo -e "${GREEN}✓ Lab is deployed and client containers are running${NC}"
}

# Function to verify client7 IGMP membership
verify_igmp_membership() {
    echo -e "${BLUE}Checking IGMP membership on client7...${NC}"
    
    # Check if client7 has joined the multicast group
    local mcast_groups
    mcast_groups=$(docker exec "$CLIENT7_CONTAINER" ip maddr show dev eth1 2>/dev/null | grep -o "$MCAST_GROUP" || true)
    
    if [[ -z "$mcast_groups" ]]; then
        echo -e "${RED}✗ Client7 has not joined multicast group $MCAST_GROUP${NC}"
        echo -e "${YELLOW}  Tip: Check if socat is running: docker exec $CLIENT7_CONTAINER ps aux | grep socat${NC}"
        return 1
    fi
    
    echo -e "${GREEN}✓ Client7 is a member of multicast group $MCAST_GROUP${NC}"
    
    # Verify socat process is running
    if docker exec "$CLIENT7_CONTAINER" ps aux | grep -q "[s]ocat.*$MCAST_GROUP"; then
        echo -e "${GREEN}✓ Socat receiver process is running${NC}"
    else
        echo -e "${RED}✗ Socat receiver process is not running${NC}"
        return 1
    fi
    
    return 0
}

# Function to verify SR Linux IGMP state
verify_srlinux_igmp() {
    echo -e "${BLUE}Checking SR Linux IGMP state...${NC}"
    
    # Check pod2leaf1a (client7's router)
    local router="clab-poc-pod2leaf1a"
    
    if ! docker ps --format '{{.Names}}' | grep -q "$router"; then
        echo -e "${YELLOW}  Warning: Cannot check $router - container not found${NC}"
        return 0
    fi
    
    # Check IGMP group membership
    local igmp_output
    igmp_output=$(docker exec "$router" sr_cli -d "info from state network-instance default protocols igmp interface ethernet-1/6.0 group-count" 2>/dev/null | grep -o '[0-9]\+' | head -1 || echo "0")
    
    if [[ "$igmp_output" -gt 0 ]]; then
        echo -e "${GREEN}✓ SR Linux has learned IGMP group (count: $igmp_output)${NC}"
    else
        echo -e "${YELLOW}  Warning: SR Linux shows no IGMP groups${NC}"
    fi
}

# Function to clear client7 log
clear_client7_log() {
    echo -e "${BLUE}Clearing client7 multicast log...${NC}"
    docker exec "$CLIENT7_CONTAINER" sh -c "> /tmp/mcast_client7.log" 2>/dev/null || true
    echo -e "${GREEN}✓ Log cleared${NC}"
}

# Function to start multicast traffic from client8
start_multicast_traffic() {
    local duration=$1
    local packet_count=$2
    
    echo -e "${BLUE}Starting multicast traffic from client8...${NC}"
    echo -e "  Source: $CLIENT8_IP"
    echo -e "  Destination: $MCAST_GROUP:$MCAST_PORT"
    echo -e "  Duration: ${duration}s"
    echo -e "  Packets: $packet_count"
    
    # Start Python multicast sender in background
    docker exec -d "$CLIENT8_CONTAINER" python3 -c "
import socket
import time

MCAST_GRP = '$MCAST_GROUP'
MCAST_PORT = $MCAST_PORT
SRC_IP = '$CLIENT8_IP'
TTL = 64
PACKET_COUNT = $packet_count
INTERVAL = $duration / float(PACKET_COUNT)

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, TTL)
sock.bind((SRC_IP, 0))

print(f'Sending {PACKET_COUNT} multicast packets from {SRC_IP} to {MCAST_GRP}:{MCAST_PORT} with TTL={TTL}')

for i in range(PACKET_COUNT):
    timestamp = time.strftime('%H:%M:%S')
    message = f'Multicast packet {i+1:03d}/{PACKET_COUNT} from client8 at {timestamp}'.encode()
    sock.sendto(message, (MCAST_GRP, MCAST_PORT))
    print(f'Sent packet {i+1}/{PACKET_COUNT}')
    time.sleep(INTERVAL)

sock.close()
print('Multicast transmission complete')
" > /tmp/mcast_sender.log 2>&1
    
    echo -e "${GREEN}✓ Multicast traffic started${NC}"
}

# Function to monitor client7 log for received packets
monitor_client7_traffic() {
    local duration=$1
    local expected_packets=$2
    
    echo -e "${BLUE}Monitoring client7 for received packets (${duration}s)...${NC}"
    
    # Wait for traffic to flow
    sleep 2
    
    # Monitor the log file
    local start_time=$(date +%s)
    local end_time=$((start_time + duration))
    local packet_count=0
    local last_count=0
    
    while [[ $(date +%s) -lt $end_time ]]; do
        # Count packets received - search for "Packet" pattern (handles concatenated log)
        packet_count=$(docker exec "$CLIENT7_CONTAINER" grep -o "packet" /tmp/mcast_client7.log 2>/dev/null | wc -l || echo "0")
        
        if [[ $packet_count -gt $last_count ]]; then
            echo -e "${GREEN}  Received $packet_count packets...${NC}"
            last_count=$packet_count
        fi
        
        sleep 2
    done
    
    # Final count
    packet_count=$(docker exec "$CLIENT7_CONTAINER" grep -o "packet" /tmp/mcast_client7.log 2>/dev/null | wc -l || echo "0")
    
    echo
    echo -e "${BLUE}Final Results:${NC}"
    echo -e "  Expected packets: $expected_packets"
    echo -e "  Received packets: $packet_count"
    
    if [[ $packet_count -gt 0 ]]; then
        local percentage=$((packet_count * 100 / expected_packets))
        echo -e "  Success rate: ${percentage}%"
        
        if [[ $packet_count -eq $expected_packets ]]; then
            echo -e "${GREEN}✓ All packets received successfully!${NC}"
            return 0
        elif [[ $packet_count -ge $((expected_packets * 8 / 10)) ]]; then
            echo -e "${GREEN}✓ Most packets received (acceptable)${NC}"
            return 0
        else
            echo -e "${YELLOW}⚠ Some packets lost${NC}"
            return 1
        fi
    else
        echo -e "${RED}✗ No packets received${NC}"
        return 1
    fi
}

# Function to stop multicast traffic
stop_multicast_traffic() {
    echo -e "${BLUE}Stopping multicast traffic...${NC}"
    
    # Kill any running Python processes on client8
    docker exec "$CLIENT8_CONTAINER" pkill -f "python3.*multicast" 2>/dev/null || true
    
    echo -e "${GREEN}✓ Multicast traffic stopped${NC}"
}

# Function to show traffic samples
show_traffic_samples() {
    echo -e "${BLUE}Sample received packets:${NC}"
    
    local log_content
    log_content=$(docker exec "$CLIENT7_CONTAINER" cat /tmp/mcast_client7.log 2>/dev/null || echo "")
    
    if [[ -n "$log_content" ]]; then
        # Extract first 5 packet entries using grep (handles concatenated log)
        echo "$log_content" | grep -oE "(Multicast packet|Packet) [0-9]{3}(/[0-9]+)? from client8 at [0-9:]+" | head -5 | while IFS= read -r line; do
            echo -e "  ${CYAN}$line${NC}"
        done
    else
        echo -e "  ${YELLOW}No packets captured${NC}"
    fi
}

# Function to verify PIM state
verify_pim_state() {
    echo -e "${BLUE}Checking PIM multicast forwarding state...${NC}"
    
    # Check pod1wan1 (RP) for (S,G) state
    local rp_router="clab-poc-pod1wan1"
    
    if ! docker ps --format '{{.Names}}' | grep -q "$rp_router"; then
        echo -e "${YELLOW}  Warning: Cannot check $rp_router - container not found${NC}"
        return 0
    fi
    
    # Check for forwarded packets
    local forwarded_packets
    forwarded_packets=$(docker exec "$rp_router" sr_cli -d "info from state network-instance default protocols pim database group $MCAST_GROUP source $CLIENT8_IP statistics forwarded-packets" 2>/dev/null | grep -o '[0-9]\+' | head -1 || echo "0")
    
    if [[ "$forwarded_packets" -gt 0 ]]; then
        echo -e "${GREEN}✓ PIM has forwarded $forwarded_packets packets from $CLIENT8_IP to group $MCAST_GROUP${NC}"
    else
        echo -e "${YELLOW}  Warning: PIM shows no forwarded packets${NC}"
    fi
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        -d|--duration)
            TEST_DURATION="$2"
            shift 2
            ;;
        -g|--group)
            MCAST_GROUP="$2"
            shift 2
            ;;
        -p|--port)
            MCAST_PORT="$2"
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
for tool in docker grep; do
    if ! command -v "$tool" &> /dev/null; then
        echo -e "${RED}Error: Required tool '$tool' is not installed${NC}"
        exit 1
    fi
done

# Main execution
echo -e "${PURPLE}=== Multicast Traffic Verification ===${NC}"
echo

# Step 1: Check lab deployment
check_lab_deployed
echo

# Step 2: Verify IGMP membership
if ! verify_igmp_membership; then
    echo -e "${RED}Failed: IGMP membership verification failed${NC}"
    exit 1
fi
echo

# Step 3: Verify SR Linux IGMP state (optional)
if [[ "$VERBOSE" == "true" ]]; then
    verify_srlinux_igmp
    echo
fi

# Step 4: Clear client7 log
clear_client7_log
echo

# Step 5: Start multicast traffic
start_multicast_traffic "$TEST_DURATION" "$PACKET_COUNT"
echo

# Step 6: Monitor traffic reception
if monitor_client7_traffic "$TEST_DURATION" "$PACKET_COUNT"; then
    VERIFICATION_PASSED=true
else
    VERIFICATION_PASSED=false
fi
echo

# Step 7: Show samples
if [[ "$VERBOSE" == "true" ]]; then
    show_traffic_samples
    echo
fi

# Step 8: Verify PIM state (optional)
if [[ "$VERBOSE" == "true" ]]; then
    verify_pim_state
    echo
fi

# Step 9: Stop traffic
stop_multicast_traffic
echo

# Summary
echo -e "${PURPLE}=== Verification Summary ===${NC}"
if [[ "$VERIFICATION_PASSED" == "true" ]]; then
    echo -e "${GREEN}✓ Multicast traffic verification PASSED${NC}"
    echo -e "${GREEN}  Client7 successfully received multicast traffic from client8${NC}"
    exit 0
else
    echo -e "${RED}✗ Multicast traffic verification FAILED${NC}"
    echo -e "${RED}  Client7 did not receive expected multicast traffic${NC}"
    echo
    echo -e "${YELLOW}Troubleshooting tips:${NC}"
    echo -e "  1. Check if socat is running: docker exec $CLIENT7_CONTAINER ps aux | grep socat"
    echo -e "  2. Check IGMP on SR Linux: docker exec clab-poc-pod2leaf1a sr_cli 'info from state network-instance default protocols igmp'"
    echo -e "  3. Check PIM state: docker exec clab-poc-pod1wan1 sr_cli 'show network-instance default multicast-forwarding-information-base ipv4-multicast'"
    echo -e "  4. Check client8 connectivity: docker exec $CLIENT8_CONTAINER ping -c 3 10.255.80.1"
    exit 1
fi
