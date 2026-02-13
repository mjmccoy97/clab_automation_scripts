# Quick Start Guide - Chartroutes for SR Linux

This guide will get you collecting route statistics in 5 minutes.

## 1. Prerequisites Check

```bash
# Check Python version (need 3.7+)
python3 --version

# Check gNMIc is installed
gnmic version

# Install Python dependencies
pip3 install xlsxwriter
```

## 2. First Test - Verify Connectivity

Before running chartroutes, verify you can connect to your SR Linux device:

```bash
chmod +x test_gnmi_connectivity.py

python3 test_gnmi_connectivity.py \
  -t 192.168.1.10 \
  -u admin \
  -p admin
```

Expected output:
```
============================================================
Testing gNMI connectivity to 192.168.1.10
============================================================

✓ Connection successful!

Device capabilities:
...

============================================================
Available Network Instances
============================================================

  - default
  - mgmt

============================================================
BGP Statistics for network-instance: default
============================================================

IPv4 Unicast Statistics:
  Total Paths: 100
  Active Routes: 100
  Total Routes: 100
...
```

## 3. First Collection - Basic Test

Run a simple 60-second collection:

```bash
chmod +x chartroutes_srlinux.py

python3 chartroutes_srlinux.py \
  -t 192.168.1.10 \
  -u admin \
  -p admin \
  -d 60 \
  -o my_first_test
```

You should see:
```
Gathering BGP route statistics for families ipv4-unicast,ipv6-unicast
from network-instance 'default' on 1 device(s)
Collection duration: 60 seconds

Starting data collection at Thu Feb 12 20:15:35 2026

Press ENTER any time to stop data collection.
```

Wait 60 seconds or press ENTER to stop early.

## 4. View Results

After collection completes:

```bash
cd data
ls -ltr  # Find your most recent file

# Open the Excel file
# On macOS:
open my_first_test_*.xlsx
# On Linux with LibreOffice:
libreoffice my_first_test_*.xlsx
# On Windows:
start my_first_test_*.xlsx
```

You'll see:
- A "Data" sheet with route counts over time
- A "Graph" sheet with a line chart

## 5. Common Scenarios

### Scenario A: Monitor Route Injection

You're adding 10,000 new BGP routes and want to measure convergence.

**Step 1**: Start monitoring (before injection)
```bash
python3 chartroutes_srlinux.py \
  -t 192.168.1.10 \
  -u admin \
  -p admin \
  -d 600 \
  -o route_injection_test
```

**Step 2**: In another terminal, inject the routes
```bash
# Your route injection script here
./inject_10k_routes.sh
```

**Step 3**: Watch the collection complete or press ENTER when done

**Step 4**: Review the Excel graph to see the route increase

### Scenario B: Measure Convergence Time

You know routes will go from 1000 to 11000. Calculate exact convergence.

```bash
python3 chartroutes_srlinux.py \
  -t 192.168.1.10 \
  -u admin \
  -p admin \
  -d 600 \
  -f ipv4-unicast \
  -s 1000 \
  -e 11000 \
  -o convergence_measurement
```

Output will include:
```
Calculating convergence time for BGP

Convergence times for 192.168.1.10:

  ipv4-unicast: Route count increasing from 1000 to 11000
    Start time: 45.23s
    End time: 51.89s
    Convergence time: 6.66s
    Convergence rate: 1501.50 routes/sec
```

### Scenario C: Multi-Router Comparison

Compare convergence across 3 routers simultaneously:

```bash
python3 chartroutes_srlinux.py \
  -t 192.168.1.10,192.168.1.11,192.168.1.12 \
  -u admin \
  -p admin \
  -d 600 \
  -o three_router_test
```

Each router gets its own sheet in the Excel file. You can:
- Compare convergence times
- Identify which router is slowest
- Verify all routers converged to same final count

### Scenario D: IPv6 Only Testing

Testing IPv6-only network:

```bash
python3 chartroutes_srlinux.py \
  -t 2001:db8::1 \
  -u admin \
  -p admin \
  -f ipv6-unicast \
  -d 300 \
  -o ipv6_test
```

### Scenario E: Long-Duration Stability Test

Monitor for 2 hours to verify route stability:

```bash
# Run in background
nohup python3 chartroutes_srlinux.py \
  -t 192.168.1.10 \
  -u admin \
  -p admin \
  -d 7200 \
  -o stability_2hr > stability.log 2>&1 &

# Check status
tail -f stability.log

# When done
fg  # Bring to foreground
# Press ENTER to stop
```

## 6. Interpreting Results

### Reading the Graphs

**X-axis**: Elapsed time in seconds
**Y-axis**: Route count

Look for:
- **Sharp increases**: Routes being added (convergence starting)
- **Flat lines**: Stable state (convergence complete)
- **Oscillations**: Route instability (investigate!)
- **Steps**: Routes added in batches

### Good vs. Bad Patterns

**Good** - Clean convergence:
```
Routes
  │     ┌─────────
  │    ╱
  │   ╱
  │  ╱
  └──────────────── Time
```

**Bad** - Oscillating routes:
```
Routes
  │  ╱\  ╱\  ╱\
  │ ╱  ╲╱  ╲╱  ╲
  │╱
  └──────────────── Time
```

**Bad** - Slow convergence:
```
Routes
  │         ┌──────
  │       ╱
  │     ╱
  │   ╱
  │ ╱
  └──────────────── Time
     (too slow)
```

## 7. Troubleshooting Quick Fixes

### "gNMIc command failed"
```bash
# Test manually
gnmic -a 192.168.1.10:57400 --insecure -u admin -p admin capabilities
```

### "No data collected"
```bash
# Enable debug
python3 chartroutes_srlinux.py ... -x

# Check you have routes
gnmic -a 192.168.1.10:57400 --insecure -u admin -p admin \
  get --path /network-instance[name=default]/protocols/bgp/statistics
```

### "Cannot find starting value"
Your start value might be wrong. Check actual route count:
```bash
python3 test_gnmi_connectivity.py -t 192.168.1.10 -u admin -p admin
```

## 8. Next Steps

Once you're comfortable with basics:

1. **Automate testing**: Create scripts that trigger network events and collect data
2. **CI/CD integration**: Use convergence calculations in automated tests
3. **Scheduled monitoring**: Run periodic collections via cron
4. **Custom protocols**: Extend the script for ISIS/OSPF (see README.md)

## Common Commands Reference

```bash
# Basic test (1 minute)
python3 chartroutes_srlinux.py -t <IP> -u <USER> -p <PASS> -d 60 -o test1

# With convergence calculation
python3 chartroutes_srlinux.py -t <IP> -u <USER> -p <PASS> -d 600 \
  -s <START> -e <END> -o conv_test

# Multiple devices
python3 chartroutes_srlinux.py -t <IP1>,<IP2>,<IP3> -u <USER> -p <PASS> \
  -d 300 -o multi_device

# IPv4 only
python3 chartroutes_srlinux.py -t <IP> -u <USER> -p <PASS> -d 300 \
  -f ipv4-unicast -o ipv4_only

# Different VRF
python3 chartroutes_srlinux.py -t <IP> -u <USER> -p <PASS> -d 300 \
  -n vrf-customer -o customer_vrf

# Debug mode
python3 chartroutes_srlinux.py -t <IP> -u <USER> -p <PASS> -d 60 -x
```

## Tips for Best Results

1. **Start collection before the event**: Begin collecting 30-60 seconds before triggering route changes
2. **Allow time after**: Let collection run 60-120 seconds after expected convergence
3. **Name files descriptively**: Use `-o` with meaningful names like "bgp_failover_test1"
4. **Keep duration reasonable**: 5-15 minutes is usually sufficient for most tests
5. **Save your data**: Excel files are timestamped, keep them for comparison
6. **Test connectivity first**: Always run `test_gnmi_connectivity.py` before production tests

Happy testing! 🚀
