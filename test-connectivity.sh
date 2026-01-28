#!/bin/bash

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# All client IP addresses
declare -A CLIENT_IPS=(
    ["client1"]="10.255.10.11 10.255.20.11 10.255.254.11"
    ["client2"]="10.255.10.12 10.255.20.12 10.255.254.12"
    ["client3"]="10.255.10.13 10.255.20.13 10.255.254.13"
    ["client4"]="10.255.10.14 10.255.20.14 10.255.254.14"
    ["client5"]="10.255.30.15 10.255.40.15 10.255.254.15"
    ["client6"]="10.255.30.16 10.255.40.16 10.255.254.16"
    ["client7"]="10.255.70.17"
)

# Function to run a single ping test
run_ping_test() {
    local src_client="$1"
    local src_ip="$2"
    local target_ip="$3"
    
    docker exec clab-poc-$src_client ping -c 2 -W 2 -I $src_ip $target_ip > /dev/null 2>&1
}

# Function to check if lab is deployed
check_lab_deployed() {
    if ! containerlab inspect > /dev/null 2>&1; then
        echo -e "${RED}Error: Lab topology is not deployed. Run 'make deploy' first.${NC}"
        exit 1
    fi
}

# Function to get all client IPs matching a subnet
get_subnet_clients() {
    local subnet="$1"
    local -n result_array=$2
    
    for client in "${!CLIENT_IPS[@]}"; do
        for ip in ${CLIENT_IPS[$client]}; do
            if [[ "$ip" == "$subnet"* ]]; then
                result_array+=("$client:$ip")
            fi
        done
    done
}

# Function to run tests in parallel and collect results
run_parallel_tests() {
    local -n test_list_ref=$1
    local -n results_ref=$2
    local -a pids=()
    
    echo -e "${YELLOW}Running ${#test_list_ref[@]} connectivity tests in parallel...${NC}"
    
    # Start all tests in background
    for test_entry in "${test_list_ref[@]}"; do
        IFS=':' read -r src_client src_ip target_ip <<< "$test_entry"
        
        (
            if run_ping_test "$src_client" "$src_ip" "$target_ip"; then
                exit 0
            else
                exit 1
            fi
        ) &
        pids+=($!)
    done
    
    # Wait for all tests to complete and collect results
    for pid in "${pids[@]}"; do
        wait $pid
        results_ref+=($?)
    done
}

# Function to display test results with consistent formatting
display_results() {
    local -n test_info_ref=$1
    local -n results_ref=$2
    local group_by="${3:-none}"
    local -n passed_ref=$4
    local -n total_ref=$5
    
    local current_group=""
    passed_ref=0
    total_ref=${#test_info_ref[@]}
    
    for i in "${!test_info_ref[@]}"; do
        IFS=':' read -r src_client src_ip target_client target_ip <<< "${test_info_ref[i]}"
        
        # Determine grouping
        case "$group_by" in
            "source")
                local group_key="$src_client"
                local display_header="${YELLOW}From $src_client ($src_ip):${NC}"
                ;;
            "subnet")
                local subnet_prefix="$(echo $src_ip | cut -d. -f1-3)"
                local group_key="SUBNET$subnet_prefix"
                local display_header="${YELLOW}$subnet_prefix.0/24 tests:${NC}"
                ;;
            *)
                local group_key=""
                local display_header=""
                ;;
        esac
        
        # Print group header when it changes
        if [[ "$group_key" != "$current_group" && -n "$group_key" ]]; then
            if [[ -n "$current_group" ]]; then
                echo
            fi
            echo -e "$display_header"
            current_group="$group_key"
        fi
        
        # Display result
        if [[ ${results_ref[i]} -eq 0 ]]; then
            echo -e "  → $target_client ($src_ip → $target_ip): ${GREEN}✓ PASS${NC}"
            ((passed_ref++))
        else
            echo -e "  → $target_client ($src_ip → $target_ip): ${RED}✗ FAIL${NC}"
        fi
    done
    echo
}

# Function to display summary with consistent formatting
display_summary() {
    local test_name="$1"
    local passed_tests="$2"
    local total_tests="$3"
    
    local success_rate=$((passed_tests * 100 / total_tests))
    if [[ $passed_tests -eq $total_tests ]]; then
        echo -e "${GREEN}$test_name Summary: $passed_tests/$total_tests tests passed (${success_rate}% success)${NC}"
    else
        echo -e "${RED}$test_name Summary: $passed_tests/$total_tests tests passed (${success_rate}% success)${NC}"
    fi
    echo
    
    return $((total_tests - passed_tests))
}

test_connectivity() {
    local test_type="$1"
    local filter="$2"
    
    local -a test_list=()
    local -a test_info=()
    local -a results=()
    
    case "$test_type" in
        "subnet")
            # Test connectivity within a specific subnet
            echo -e "${BLUE}=== Testing $filter Subnet Connectivity Matrix ===${NC}"
            
            local -a subnet_clients=()
            get_subnet_clients "$filter" subnet_clients
            
            # Generate all source->target pairs within the subnet
            for src_entry in "${subnet_clients[@]}"; do
                IFS=':' read -r src_client src_ip <<< "$src_entry"
                
                for tgt_entry in "${subnet_clients[@]}"; do
                    IFS=':' read -r tgt_client tgt_ip <<< "$tgt_entry"
                    
                    if [[ "$src_client" != "$tgt_client" ]]; then
                        test_list+=("$src_client:$src_ip:$tgt_ip")
                        test_info+=("$src_client:$src_ip:$tgt_client:$tgt_ip")
                    fi
                done
            done
            ;;
            
        "comprehensive")
            # Test every client against every other client
            echo -e "${BLUE}=== Comprehensive Client-to-Client Connectivity Matrix ===${NC}"
            
            # Get all client IPs
            for src_client in "${!CLIENT_IPS[@]}"; do
                for src_ip in ${CLIENT_IPS[$src_client]}; do
                    # Test against all other clients
                    for tgt_client in "${!CLIENT_IPS[@]}"; do
                        for tgt_ip in ${CLIENT_IPS[$tgt_client]}; do
                            if [[ "$src_client" != "$tgt_client" ]]; then
                                test_list+=("$src_client:$src_ip:$tgt_ip")
                                test_info+=("$src_client:$src_ip:$tgt_client:$tgt_ip")
                            fi
                        done
                    done
                done
            done
            ;;
            
        *)
            echo -e "${RED}Unknown test type: $test_type${NC}"
            return 1
            ;;
    esac
    
    if [[ ${#test_list[@]} -eq 0 ]]; then
        echo -e "${YELLOW}No tests to run for the specified criteria.${NC}"
        return 0
    fi
    
    # Run all tests in parallel
    run_parallel_tests test_list results
    
    # Display results
    local passed_tests total_tests
    display_results test_info results "source" passed_tests total_tests
    
    # Display summary
    display_summary "$test_type Test" "$passed_tests" "$total_tests"
}

# Show help function
show_help() {
    echo "Usage: $0 [OPTION]"
    echo "DRY Refactored connectivity test for EVPN lab"
    echo
    echo "Subnet Tests:"
    echo "  matrix       Test all subnets with connectivity matrix (default)"
    echo "  10.255.10    Test 10.255.10.0/24 subnet connectivity"
    echo "  10.255.20    Test 10.255.20.0/24 subnet connectivity"
    echo "  10.255.30    Test 10.255.30.0/24 subnet connectivity"
    echo "  10.255.40    Test 10.255.40.0/24 subnet connectivity"
    echo "  10.255.70    Test 10.255.70.0/24 subnet connectivity"
    echo "  10.255.254   Test 10.255.254.0/24 subnet connectivity"
    echo
    echo "Comprehensive Tests:"
    echo "  comprehensive Test every client against every other client"
    echo
    echo "Other Options:"
    echo "  help         Show this help message"
    echo
}

# Main script execution
case "${1:-matrix}" in
    "10.255.10"|"10.255.20"|"10.255.30"|"10.255.40"|"10.255.70"|"10.255.254")
        check_lab_deployed
        test_connectivity "subnet" "$1"
        ;;
    "matrix")
        check_lab_deployed
        echo -e "${BLUE}Starting comprehensive subnet connectivity matrix tests...${NC}"
        echo
        
        total_failures=0
        subnets=("10.255.10" "10.255.20" "10.255.30" "10.255.40" "10.255.70" "10.255.254")
        
        for subnet in "${subnets[@]}"; do
            test_connectivity "subnet" "$subnet"
            total_failures=$((total_failures + $?))
        done
        
        echo -e "${BLUE}=== Overall Matrix Test Results ===${NC}"
        if [[ $total_failures -eq 0 ]]; then
            echo -e "${GREEN}All subnet connectivity tests passed! EVPN fabric is working correctly.${NC}"
        else
            echo -e "${RED}$total_failures connectivity tests failed. Check EVPN configuration.${NC}"
        fi
        ;;
    "comprehensive")
        check_lab_deployed
        test_connectivity "comprehensive" "all"
        ;;
    "help"|"-h"|"--help")
        show_help
        ;;
    *)
        echo -e "${RED}Invalid option: $1${NC}"
        echo "Use '$0 help' for usage information"
        exit 1
        ;;
esac