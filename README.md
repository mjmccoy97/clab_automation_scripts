# clab_automation_scripts

A collection of Python and Bash scripts designed for managing and monitoring Nokia SR Linux fabrics in **Containerlab** environments.

## 🚀 Key Scripts

### 1. BGP Neighbor Reporter (`chk_bgp_nbrs.py`)
A gNMI-based tool that discovers all SR Linux nodes in a running Containerlab topology and reports their BGP peering states with color-coded status.

## 🛠 Installation (Global/User Method)

Since this is a dedicated lab environment, we install dependencies globally for the user to simplify execution.

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/YOUR_USERNAME/clab_automation_scripts.git](https://github.com/YOUR_USERNAME/clab_automation_scripts.git)
   cd clab_automation_scripts