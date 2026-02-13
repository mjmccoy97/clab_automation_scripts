# Chartroutes for Nokia SR Linux

A Python tool to collect and graph BGP route convergence statistics from Nokia SR Linux routers using gNMIc.

## Overview

This tool is a modernized adaptation of the original Juniper PyEz-based chartroutes script. It collects route statistics from Nokia SR Linux routers via gNMI and creates Excel spreadsheets with graphs showing route convergence over time.

### Key Features

- **Multi-device support**: Collect data from multiple SR Linux routers simultaneously
- **Address family support**: Monitor IPv4 and IPv6 routes separately
- **Convergence calculations**: Automatically calculate convergence time and rate
- **Excel output**: Generate professional graphs and data in Excel format
- **Real-time collection**: Monitor route changes during network events
- **Thread-based**: Parallel data collection from multiple devices

## Prerequisites

### 1. System Requirements

- Python 3.7 or higher
- gNMIc CLI tool installed and in PATH
- Network connectivity to SR Linux devices on port 57400 (gNMI)

### 2. Install Python Dependencies

```bash
pip3 install xlsxwriter
```

### 3. Install gNMIc

Follow the installation instructions at: https://gnmic.openconfig.net/install/

Quick install on Linux:
```bash
bash -c "$(curl -sL https://get-gnmic.openconfig.net)"
```

Or via package manager:
```bash
# Debian/Ubuntu
sudo apt-get update
sudo apt-get install gnmic

# macOS
brew install gnmic
```

### 4. Enable gNMI on SR Linux Devices

Ensure gNMI is enabled on your SR Linux routers:

```
--{ + running }--[  ]--
A:srl1# enter candidate

--{ + candidate shared default }--[  ]--
A:srl1# system gnmi-server admin-state enable

--{ +* candidate shared default }--[  ]--
A:srl1# system gnmi-server network-instance mgmt admin-state enable

--{ +* candidate shared default }--[  ]--
A:srl1# commit now
```

## Installation

1. Clone or download the script:
```bash
mkdir ~/chartroutes
cd ~/chartroutes
# Copy chartroutes_srlinux.py to this directory
chmod +x chartroutes_srlinux.py
```

2. Create a data directory (will be created automatically if it doesn't exist):
```bash
mkdir data
```

## Usage

### Basic Syntax

```bash
python3 chartroutes_srlinux.py \
  -t <targets> \
  -u <username> \
  -p <password> \
  -d <duration> \
  [options]
```

### Required Arguments

- `-t, --targets`: Comma-separated list of device IPs/hostnames
- `-u, --username`: Username for device authentication
- `-p, --password`: Password for device authentication
- `-d, --duration`: Data collection duration in seconds

### Optional Arguments

- `-n, --network-instance`: Network instance name (default: `default`)
- `-P, --protocol`: Protocol to monitor (default: `bgp`)
- `-f, --families`: Address families to monitor (default: `ipv4-unicast,ipv6-unicast`)
- `-o, --output`: Output filename prefix (default: `route_stats`)
- `-s, --start-values`: Starting route values for convergence calculation
- `-e, --end-values`: Ending route values for convergence calculation
- `-x, --debug`: Enable debug output

## Examples

### Example 1: Basic Route Collection

Collect BGP route statistics from a single device for 5 minutes:

```bash
python3 chartroutes_srlinux.py \
  -t 192.168.1.10 \
  -u admin \
  -p admin \
  -d 300 \
  -o bgp_baseline
```

This will:
- Connect to 192.168.1.10
- Collect BGP IPv4 and IPv6 route counts every second
- Run for 300 seconds (5 minutes)
- Output: `data/bgp_baseline_<timestamp>.xlsx`

### Example 2: Multiple Devices

Monitor route convergence on multiple routers:

```bash
python3 chartroutes_srlinux.py \
  -t 10.0.0.1,10.0.0.2,10.0.0.3 \
  -u admin \
  -p admin \
  -d 600 \
  -o multi_router_test
```

### Example 3: IPv4 Only Monitoring

Monitor only IPv4 unicast routes:

```bash
python3 chartroutes_srlinux.py \
  -t 192.168.1.10 \
  -u admin \
  -p admin \
  -d 300 \
  -f ipv4-unicast \
  -o ipv4_only
```

### Example 4: Convergence Calculation

Measure convergence time when BGP routes increase from 10,000 to 50,000 (IPv4) and 5,000 to 25,000 (IPv6):

```bash
python3 chartroutes_srlinux.py \
  -t 192.168.1.10 \
  -u admin \
  -p admin \
  -d 900 \
  -f ipv4-unicast,ipv6-unicast \
  -s 10000,5000 \
  -e 50000,25000 \
  -o convergence_test
```

Output will include:
```
Calculating convergence time for BGP

Convergence times for 192.168.1.10:

  ipv4-unicast: Route count increasing from 10000 to 50000
    Start time: 25.49s
    End time: 32.14s
    Convergence time: 6.65s
    Convergence rate: 6015.04 routes/sec

  ipv6-unicast: Route count increasing from 5000 to 25000
    Start time: 26.12s
    End time: 31.54s
    Convergence time: 5.42s
    Convergence rate: 3690.04 routes/sec
```

### Example 5: Non-Default Network Instance

Monitor routes in a specific network instance:

```bash
python3 chartroutes_srlinux.py \
  -t 192.168.1.10 \
  -u admin \
  -p admin \
  -n vrf-customer-a \
  -d 300 \
  -o customer_vrf_test
```

### Example 6: Debug Mode

Enable debug output to see detailed gNMI interactions:

```bash
python3 chartroutes_srlinux.py \
  -t 192.168.1.10 \
  -u admin \
  -p admin \
  -d 60 \
  -x
```

## Typical Test Workflow

### Scenario: BGP Route Convergence Testing

1. **Establish Baseline**
   ```bash
   # Start collection before the test
   python3 chartroutes_srlinux.py \
     -t 10.0.0.1,10.0.0.2 \
     -u admin \
     -p admin \
     -d 1800 \
     -o bgp_convergence_test &
   ```

2. **Trigger Network Event**
   - Add new BGP peers
   - Advertise new prefixes
   - Fail over a link
   - Perform maintenance

3. **Monitor Progress**
   - The script runs in the background
   - Press ENTER to stop early if needed
   - Watch the terminal for status updates

4. **Review Results**
   - Excel file is created in `./data/` directory
   - Each device has its own sheet with data and graphs
   - Review graphs to identify convergence patterns

## Understanding the Output

### Excel Spreadsheet Structure

Each device gets two worksheets:

1. **Data Sheet** (`<device> Data`)
   - Columns for each address family's total and active routes
   - Elapsed time column
   - Raw data values

2. **Graph Sheet** (`<device> Graph`)
   - Line chart showing route counts over time
   - Separate lines for total and active routes per family
   - X-axis: Elapsed time (seconds)
   - Y-axis: Route count

### Interpreting Convergence Results

- **Convergence Time**: Time between route count reaching start and end values
- **Convergence Rate**: Routes per second = (end_value - start_value) / convergence_time
- Faster convergence = better network performance
- Look for:
  - Stable convergence times across tests
  - Consistent rates between IPv4 and IPv6 (if similar route counts)
  - No unexpected plateaus or drops in the graphs

## Troubleshooting

### gNMIc Connection Issues

**Error**: `gNMIc command failed: connection timeout`

**Solutions**:
- Verify network connectivity: `ping <device-ip>`
- Check gNMI port is accessible: `telnet <device-ip> 57400`
- Verify gNMI is enabled on the device
- Check firewall rules

### Authentication Failures

**Error**: `gNMIc command failed: authentication failed`

**Solutions**:
- Verify username and password
- Check user has appropriate permissions on SR Linux
- Ensure the user has gNMI access rights

### No Data Collected

**Warning**: `No data collected from <device>`

**Solutions**:
- Enable debug mode (`-x`) to see detailed errors
- Verify the network instance name is correct
- Check that the protocol has active routes
- Ensure address family names match SR Linux nomenclature

### Empty Graphs

**Issue**: Excel file created but graphs are empty

**Causes**:
- Collection duration too short
- No route changes during collection
- All routes already converged before collection started

**Solutions**:
- Increase duration (`-d`)
- Time the collection to coincide with network events
- Verify routes exist: `gnmic -a <device>:57400 --insecure -u admin -p admin get --path /network-instance[name=default]/protocols/bgp/statistics`

## Advanced Usage

### Scripting with Chartroutes

Create a test automation script:

```bash
#!/bin/bash

# bgp_test.sh - Automated BGP convergence test

DEVICES="10.0.0.1,10.0.0.2,10.0.0.3"
USERNAME="admin"
PASSWORD="admin"
DURATION=900

# Start monitoring
python3 chartroutes_srlinux.py \
  -t $DEVICES \
  -u $USERNAME \
  -p $PASSWORD \
  -d $DURATION \
  -s 10000,5000 \
  -e 100000,50000 \
  -o automated_test_$(date +%Y%m%d_%H%M%S) &

PID=$!

# Wait for baseline (30 seconds)
sleep 30

# Trigger network event
echo "Triggering route injection..."
./inject_routes.sh

# Wait for collection to complete
wait $PID

echo "Test complete. Check ./data/ for results."
```

### Integration with CI/CD

Use the convergence calculation output for automated testing:

```bash
# Extract convergence rate and fail if below threshold
RESULT=$(python3 chartroutes_srlinux.py ... | grep "Convergence rate")
RATE=$(echo $RESULT | awk '{print $3}')

if (( $(echo "$RATE < 5000" | bc -l) )); then
  echo "FAIL: Convergence rate below 5000 routes/sec"
  exit 1
fi
```

## Comparison with Original Juniper Version

| Feature | Juniper (PyEz) | SR Linux (gNMIc) |
|---------|----------------|------------------|
| Connection | NETCONF/SSH | gNMI |
| Protocol | PyEz library | gNMIc CLI |
| Python | 2.7 | 3.7+ |
| Device regex | Supported | Manual list |
| Database file | .xlsx topology | None (direct IPs) |
| Protocols | Multiple | BGP (extensible) |

## Known Limitations

1. **Protocol Support**: Currently only BGP is fully implemented. Other protocols (ISIS, OSPF) can be added by extending the `get_route_summary()` function.

2. **No Hostname Regex**: Unlike the original, you must specify exact IP addresses or hostnames (comma-separated). No regex pattern matching.

3. **No Topology Database**: The original used an Excel file for device inventory. This version takes devices directly from command line.

4. **gNMIc Dependency**: Requires gNMIc CLI to be installed. Cannot use Python gNMI libraries due to dependency issues mentioned by the user.

## Future Enhancements

Potential improvements:
- Add support for ISIS and OSPF statistics
- Implement device inventory file support
- Add support for other SR Linux features (MAC table, EVPN, etc.)
- Create real-time terminal dashboard
- Add CSV export option
- Support for gNMI subscriptions (streaming telemetry)

## License

This tool is provided as-is for network testing and monitoring purposes.

## Credits

Original concept and Juniper implementation: M. McCoy (2017)
SR Linux adaptation: 2026

## Support

For issues or questions:
1. Verify prerequisites are installed
2. Run with debug flag (`-x`)
3. Check gNMI connectivity manually with gNMIc
4. Review SR Linux gNMI documentation
