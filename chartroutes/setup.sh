#!/bin/bash

# Setup script for Chartroutes SR Linux
# This script checks prerequisites and sets up the environment

set -e

echo "========================================"
echo "Chartroutes SR Linux - Setup"
echo "========================================"
echo ""

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}✓${NC} $2"
    else
        echo -e "${RED}✗${NC} $2"
    fi
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

# Check Python 3
echo "Checking Python 3..."
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
    PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d'.' -f1)
    PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d'.' -f2)
    
    if [ "$PYTHON_MAJOR" -ge 3 ] && [ "$PYTHON_MINOR" -ge 7 ]; then
        print_status 0 "Python $PYTHON_VERSION found"
    else
        print_status 1 "Python $PYTHON_VERSION found but need 3.7+"
        echo "Please upgrade Python to version 3.7 or higher"
        exit 1
    fi
else
    print_status 1 "Python 3 not found"
    echo "Please install Python 3.7 or higher"
    exit 1
fi

# Check pip3
echo ""
echo "Checking pip3..."
if command -v pip3 &> /dev/null; then
    print_status 0 "pip3 found"
else
    print_status 1 "pip3 not found"
    echo "Please install pip3"
    exit 1
fi

# Check gNMIc
echo ""
echo "Checking gNMIc..."
if command -v gnmic &> /dev/null; then
    GNMIC_VERSION=$(gnmic version 2>&1 | head -n1 || echo "version unknown")
    print_status 0 "gNMIc found ($GNMIC_VERSION)"
else
    print_status 1 "gNMIc not found"
    echo ""
    echo "gNMIc is required but not installed."
    echo "Install it from: https://gnmic.openconfig.net/install/"
    echo ""
    echo "Quick install options:"
    echo "  curl -sL https://github.com/openconfig/gnmic/raw/main/install.sh | sudo bash"
    echo "  or"
    echo "  brew install gnmic  (macOS)"
    echo ""
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Install Python dependencies
echo ""
echo "Installing Python dependencies..."
if pip3 install -r requirements.txt; then
    print_status 0 "Python dependencies installed"
else
    print_status 1 "Failed to install Python dependencies"
    exit 1
fi

# Create data directory
echo ""
echo "Creating data directory..."
if [ ! -d "data" ]; then
    mkdir -p data
    print_status 0 "Created ./data directory"
else
    print_status 0 "./data directory already exists"
fi

# Make scripts executable
echo ""
echo "Making scripts executable..."
chmod +x chartroutes_srlinux.py
chmod +x test_gnmi_connectivity.py
print_status 0 "Scripts are now executable"

# Check network connectivity (optional)
echo ""
read -p "Do you want to test connectivity to an SR Linux device? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo ""
    read -p "Enter device IP/hostname: " DEVICE_IP
    read -p "Enter username [admin]: " USERNAME
    USERNAME=${USERNAME:-admin}
    read -sp "Enter password: " PASSWORD
    echo ""
    
    echo ""
    echo "Testing connectivity to $DEVICE_IP..."
    
    if python3 test_gnmi_connectivity.py -t "$DEVICE_IP" -u "$USERNAME" -p "$PASSWORD"; then
        echo ""
        print_status 0 "Successfully connected to $DEVICE_IP"
    else
        echo ""
        print_status 1 "Failed to connect to $DEVICE_IP"
        print_warning "Check device IP, credentials, and ensure gNMI is enabled"
    fi
fi

# Summary
echo ""
echo "========================================"
echo "Setup Complete!"
echo "========================================"
echo ""
echo "Next steps:"
echo "  1. Review the README.md for full documentation"
echo "  2. Check QUICKSTART.md for common usage examples"
echo "  3. Test with: python3 chartroutes_srlinux.py --help"
echo ""
echo "Example usage:"
echo "  python3 chartroutes_srlinux.py \\"
echo "    -t 192.168.1.10 \\"
echo "    -u admin \\"
echo "    -p admin \\"
echo "    -d 60 \\"
echo "    -o my_test"
echo ""
echo "Test connectivity first:"
echo "  python3 test_gnmi_connectivity.py \\"
echo "    -t 192.168.1.10 \\"
echo "    -u admin \\"
echo "    -p admin"
echo ""
