# clab_automation_scripts

A collection of Python and Bash scripts designed for managing and monitoring Nokia SR Linux fabrics, specifically optimized for **Containerlab** environments.

## 🚀 Key Scripts

### 1. BGP Neighbor Reporter (`chk_bgp_nbrs.py`)
A gNMI-based tool that discovers all SR Linux nodes in a running Containerlab topology and reports their BGP peering states with color-coded status.

**Features:**
* **Dynamic Discovery:** Automatically finds nodes using `containerlab inspect`.
* **Flexible Filtering:** Target specific network-instances or scan the whole node.
* **Color Coding:** Green for `Established`, Red for any other state.
* **Fabric Summary:** Provides a high-level health count of all peers across the network.

## 🛠 Installation

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/YOUR_USERNAME/clab_automation_scripts.git](https://github.com/YOUR_USERNAME/clab_automation_scripts.git)
   cd clab_automation_scripts