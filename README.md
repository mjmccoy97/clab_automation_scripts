# clab_automation_scripts

A collection of Python and Bash scripts designed for managing and monitoring Nokia SR Linux fabrics in **Containerlab** environments.

## 🚀 Key Scripts

### 1. BGP Neighbor Reporter (`chk_bgp_nbrs.py`)
A gNMI-based tool that discovers all SR Linux nodes in a running Containerlab topology and reports their BGP peering states with color-coded status.

### 2. Grafana Flow-Panel Generator (`grafana/clab2flowpanel.py`)
Builds the topology diagram (SVG) and panel config for the `andrewbmchugh-flow-panel` Grafana plugin straight from a containerlab topology file, and can embed both into an existing dashboard JSON. No hand-drawing or hand-written cell IDs.

```bash
# write <lab>.svg and <lab>.flow_panel.yml, embed them in the dashboard, and render a PNG preview
python3 grafana/clab2flowpanel.py topology.clab.yml -o configs/grafana/flow_panels \
    --dashboard configs/grafana/dashboards/telemetry-dashboard.json --preview
```

- **Tiers** come from the graph: host nodes (`linux`, Ixia) on the bottom, everything else ranked by hop distance to the nearest host (leafs, then spines, then super-spines). Override with `--tier REGEX` (repeat, top to bottom) when a topology doesn't fit that shape.
- **Ports** are spread evenly along the side of each box facing the peer and ordered by the peer's position, so dots never overlap. Boxes widen to fit many ports, and rate labels alternate position so LAG members stay readable.
- **Hosts without telemetry** (Linux clients) are fed from the peer's counters: the client's port state is the leaf port's state, and client `out` is the leaf port's `in`.
- **Data refs** assume the usual legend formats (`oper-state:{{source}}:{{interface_name}}`, `{{source}}:{{interface_name}}:out|in`), with interface names matching the containerlab endpoint names (for SR Linux, gnmic renames `ethernet-1/1` to `e1-1`).
- The SVG and panel config are embedded inline in the dashboard, so the plugin never has to fetch them from the browser (avoids the CORS problem with bind-mounted files).
- `--include` / `--exclude` restrict which nodes are drawn; `--panel-title` targets one flow panel when a dashboard has several. Requires `pyyaml`.

## 🛠 Installation (Global/User Method)

Since this is a dedicated lab environment, we install dependencies globally for the user to simplify execution.

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/YOUR_USERNAME/clab_automation_scripts.git](https://github.com/YOUR_USERNAME/clab_automation_scripts.git)
   cd clab_automation_scripts