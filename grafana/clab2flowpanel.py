#!/usr/bin/env python3
# 09.28.26 M. McCoy Generate a Grafana flow-panel topology (SVG + panel config)
#          straight from a containerlab topology file, and optionally embed it
#          into an existing dashboard JSON.
"""
Builds a tiered topology diagram for the andrewbmchugh-flow-panel plugin:

  * one oper-state port cell per link endpoint   ("<node>:<iface>")
  * two traffic halves per link, one per direction ("link_id:<a>:<ai>:<b>:<bi>")
  * a rate label on each half                      ("link_id:...:label")

Data refs follow the usual dashboard legend formats:
  oper-state:{{source}}:{{interface_name}}
  {{source}}:{{interface_name}}:out  /  {{source}}:{{interface_name}}:in

so interface names must match the clab endpoint names as they appear in
Prometheus (for SR Linux that means gnmic renames ethernet-1/1 -> e1-1).

Nodes without telemetry (linux clients etc.) have no series of their own, so
their side of each link is fed from the peer's counters: the client's port
state is the peer port's state, and client "out" is the peer port's "in".

Examples:
  clab2flowpanel.py topology.clab.yml -o configs/grafana/flow_panels
  clab2flowpanel.py topology.clab.yml -o flow --dashboard configs/grafana/dashboards/telemetry-dashboard.json
  clab2flowpanel.py topology.clab.yml -o flow --tier 'spine' --tier 'leaf' --tier '.*' --preview

Tiers are derived from the graph by default: host nodes (linux, ixia, ...) on
the bottom, every other node ranked by its hop distance to the nearest host
(leafs 1, spines 2, super-spines 3). Use --tier regexes to override.
"""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict

import yaml

DEFAULT_TELEMETRY_KINDS = ["nokia_srlinux", "nokia_srsim", "nokia_sros", "vr-sros"]
DEFAULT_HOST_KINDS = ["linux", "keysight_ixia-c-one", "ixia-c-one", "host"]

BOX_W, BOX_H = 150, 40
H_GAP = 40           # minimum horizontal gap between boxes in a tier
V_GAP = 180          # vertical distance between tiers
PORT_MARGIN = 6      # distance of the port dot outside the box edge
DOT = 8              # port dot size
PORT_PITCH = DOT + 6  # minimum spacing between port dots along a box side
LABEL_POS = (0.3, 0.55)  # rate label position along each half, from the port end; alternates between
                         # neighbouring ports so labels on parallel links (LAG members) don't overlap

NODE_FILL = "#005aff"
PASSIVE_FILL = "#5b6770"


def natural_key(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def load_topology(path):
    with open(path) as f:
        topo = yaml.safe_load(f)
    t = topo.get("topology", {})
    nodes = t.get("nodes", {}) or {}
    kinds = t.get("kinds", {}) or {}
    default_kind = (t.get("defaults") or {}).get("kind")
    links = []
    for link in t.get("links", []) or []:
        eps = link.get("endpoints")
        if not eps or len(eps) != 2:
            continue  # only point-to-point veth links are drawn
        (a, ai), (b, bi) = (e.split(":", 1) for e in eps)
        links.append((a, ai, b, bi))
    return topo.get("name", "topology"), nodes, kinds, default_kind, links


def node_kind(name, nodes, default_kind):
    return (nodes.get(name) or {}).get("kind") or default_kind or "linux"


def node_subtitle(name, nodes, kinds, default_kind):
    n = nodes.get(name) or {}
    kind = node_kind(name, nodes, default_kind)
    t = n.get("type") or (kinds.get(kind) or {}).get("type")
    if t:
        return str(t).upper()
    image = n.get("image") or (kinds.get(kind) or {}).get("image") or kind
    return image.rsplit("/", 1)[-1].split(":")[0]


def assign_tiers(names, tier_patterns):
    tiers = [[] for _ in tier_patterns]
    for n in names:
        for i, pat in enumerate(tier_patterns):
            if re.search(pat, n):
                tiers[i].append(n)
                break
    return [sorted(t, key=natural_key) for t in tiers if t]


def graph_tiers(names, adjacency, is_host):
    """Hosts on the bottom tier; other nodes ranked by hop distance to the
    nearest host, farthest on top. Nodes with no path to a host go on top."""
    dist = {n: 0 for n in names if is_host(n)}
    frontier = list(dist)
    while frontier:
        nxt = []
        for n in frontier:
            for p in adjacency[n]:
                if p not in dist and not is_host(p):
                    dist[p] = dist[n] + 1
                    nxt.append(p)
        frontier = nxt
    top = max(dist.values(), default=0) + 1
    for n in names:
        dist.setdefault(n, top)
    levels = sorted(set(dist.values()), reverse=True)
    return [sorted([n for n in names if dist[n] == lvl], key=natural_key) for lvl in levels]


def layout(tiers, adjacency, spacing):
    """Place tiers top to bottom. Each tier after the first is ordered by the
    mean x of its neighbours in the tiers above (barycenter), so clients land
    under the leaf they hang off."""
    widest = max(len(t) for t in tiers)
    width = max(widest, 2) * spacing
    pos = {}
    for level, tier in enumerate(tiers):
        if level > 0:
            def bary(n):
                xs = [pos[p][0] for p in adjacency[n] if p in pos]
                return sum(xs) / len(xs) if xs else float("inf")
            tier.sort(key=lambda n: (bary(n), natural_key(n)))
        step = width / len(tier)
        for i, n in enumerate(tier):
            pos[n] = (step * (i + 0.5), level * V_GAP)
    return pos, width, (len(tiers) - 1) * V_GAP


def port_sides(links, tier_of, order_of):
    """Which side of its box each link endpoint leaves from: toward the peer's
    tier (top/bottom), or left/right for links within a tier."""
    sides = defaultdict(list)  # (node, side) -> [(link idx, end)]
    for idx, (a, ai, b, bi) in enumerate(links):
        for end, (node, peer) in enumerate(((a, b), (b, a))):
            if tier_of[peer] < tier_of[node]:
                side = "top"
            elif tier_of[peer] > tier_of[node]:
                side = "bottom"
            else:
                side = "right" if order_of[peer] > order_of[node] else "left"
            sides[(node, side)].append((idx, end))
    return sides


def box_widths(names, sides):
    widths = {}
    for n in names:
        most = max([len(sides.get((n, s), [])) for s in ("top", "bottom")] + [0])
        widths[n] = max(BOX_W, most * PORT_PITCH + 24)
    return widths


def place_ports(links, pos, sides, widths):
    """Spread each side's ports evenly along it, ordered by the peer's position
    so lines fan out without crossing at the box."""
    xy = {}
    for (node, side), ends in sides.items():
        x, y = pos[node]
        hw, hh = widths[node] / 2, BOX_H / 2

        def peer_key(item):
            idx, end = item
            a, ai, b, bi = links[idx]
            peer = b if end == 0 else a
            return (pos[peer][0], idx)

        ends = sorted(ends, key=peer_key)
        n = len(ends)
        for i, (idx, end) in enumerate(ends):
            frac = (i + 1) / (n + 1)
            if side in ("top", "bottom"):
                px = x - hw + 12 + frac * (2 * hw - 24)
                py = y - hh - PORT_MARGIN if side == "top" else y + hh + PORT_MARGIN
            else:
                px = x + hw + PORT_MARGIN if side == "right" else x - hw - PORT_MARGIN
                py = y - hh + frac * 2 * hh
            xy[(idx, end)] = (px, py, i)
    return xy


def build(links, xy, has_telemetry):
    edges_svg, ports_svg, cells, warnings = [], [], {}, []
    port_xy = defaultdict(list)

    for idx, (a, ai, b, bi) in enumerate(links):
        pa, pb = xy[(idx, 0)], xy[(idx, 1)]
        mid = ((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2)

        group = ['<g class="export-edge">']
        for node, iface, peer, peer_if, start in ((a, ai, b, bi, pa), (b, bi, a, ai, pb)):
            cid = f"link_id:{node}:{iface}:{peer}:{peer_if}"
            lp = LABEL_POS[start[2] % 2]
            lx = start[0] + (mid[0] - start[0]) * lp
            ly = start[1] + (mid[1] - start[1]) * lp
            group.append(
                f'<g class="grafana-traffic-half" id="cell-{cid}" data-cell-id="{cid}">'
                f'<path d="M {start[0]:.1f} {start[1]:.1f} L {mid[0]:.1f} {mid[1]:.1f}" '
                f'fill="none" stroke="gray" stroke-width="3" opacity="0.6"/>'
                f'<text x="{lx:.1f}" y="{ly:.1f}" font-size="10" font-family="Helvetica, Arial, sans-serif" '
                f'id="cell-{cid}:label" data-cell-id="{cid}:label" fill="currentColor" text-anchor="middle" '
                f'dominant-baseline="middle" style="color: rgb(255, 255, 255); '
                f'filter: drop-shadow(rgba(0, 0, 0, 0.95) 0px 0px 1px);">rate</text></g>')

            if has_telemetry(node):
                oper, rate = f"oper-state:{node}:{iface}", f"{node}:{iface}:out"
            elif has_telemetry(peer):
                oper, rate = f"oper-state:{peer}:{peer_if}", f"{peer}:{peer_if}:in"
            else:
                oper, rate = f"oper-state:{node}:{iface}", f"{node}:{iface}:out"
                warnings.append(f"{node}:{iface} <-> {peer}:{peer_if}: neither end has telemetry, cells will stay empty")
            cells[f"{node}:{iface}"] = ("oper", oper)
            cells[cid] = ("rate", rate)
            cells[f"{cid}:label"] = ("label", rate)

            port_xy[node].append((iface, start))
            ports_svg.append(
                f'<g class="export-edge grafana-operstate-cell" id="cell-{node}:{iface}" data-cell-id="{node}:{iface}">'
                f'<rect x="{start[0] - DOT / 2:.1f}" y="{start[1] - DOT / 2:.1f}" width="{DOT}" height="{DOT}" '
                f'rx="1.5" ry="1.5" fill="transparent" stroke="none"/></g>')
        group.append("</g>")
        edges_svg.append("".join(group))

    # Overlapping port dots on one node hide each other in the panel, so flag them.
    for node, plist in port_xy.items():
        for i in range(len(plist)):
            for j in range(i + 1, len(plist)):
                (i1, p1), (i2, p2) = plist[i], plist[j]
                if math.hypot(p1[0] - p2[0], p1[1] - p2[1]) < DOT:
                    warnings.append(f"{node}: port dots {i1} and {i2} overlap")
    return edges_svg, ports_svg, cells, warnings


def render_svg(pos, widths, width, height, edges_svg, ports_svg, subtitles, passive):
    nodes_svg = []
    for node, (x, y) in pos.items():
        fill = PASSIVE_FILL if node in passive else NODE_FILL
        bw = widths[node]
        nodes_svg.append(
            f'<g class="export-node topology-node" data-id="{node}">'
            f'<rect x="{x - bw / 2}" y="{y - BOX_H / 2}" width="{bw}" height="{BOX_H}" rx="6" ry="6" fill="{fill}"/>'
            f'<text x="{x}" y="{y - 4}" font-size="13" font-weight="600" '
            f'font-family="system-ui, -apple-system, sans-serif" fill="#FFFFFF" text-anchor="middle">{node}</text>'
            f'<text x="{x}" y="{y + 11}" font-size="9" font-family="system-ui, -apple-system, sans-serif" '
            f'fill="#dbe4ff" text-anchor="middle">{subtitles[node]}</text></g>')
    pad = 20
    left = min(x - widths[n] / 2 for n, (x, _) in pos.items()) - pad
    right = max(x + widths[n] / 2 for n, (x, _) in pos.items()) + pad
    vb = f"{left:.0f} {-BOX_H / 2 - pad} {right - left:.0f} {height + BOX_H + 2 * pad}"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="100%" viewBox="{vb}" '
            f'preserveAspectRatio="xMidYMid meet"><g class="export-graph-layer">'
            + "".join(edges_svg) + "".join(ports_svg) + "".join(nodes_svg) + "</g></svg>")


def render_panel_config(cells):
    lines = [
        "---",
        "anchors:",
        "  thresholds-operstate: &thresholds-operstate",
        '    - { color: "red", level: 0 }',
        '    - { color: "green", level: 1 }',
        "  thresholds-traffic: &thresholds-traffic",
        '    - { color: "gray", level: 0 }',
        '    - { color: "green", level: 199 }',
        '    - { color: "yellow", level: 500 }',
        '    - { color: "orange", level: 1000 }',
        '    - { color: "red", level: 5000 }',
        "  thresholds-rate-label: &thresholds-rate-label",
        '    - { color: "white", level: 0 }',
        "  label-config: &label-config",
        '    separator: "replace"',
        '    units: "bps"',
        "    decimalPoints: 1",
        "    valueMappings:",
        '      - { valueMax: 199999, text: "\\u200B" }',
        'cellIdPreamble: "cell-"',
        "tagConfig:",
        '  legend: ["hide-rates"]',
        "  lowlightAlphaFactor: 0",
        "  highlightRgbFactor: 1",
        "cells:",
    ]
    for cid, (kind, ref) in cells.items():
        lines += [f'  "{cid}":', f'    dataRef: "{ref}"']
        if kind == "oper":
            lines += ["    fillColor:", "      thresholds: *thresholds-operstate", '    tags: ["hide-rates"]']
        elif kind == "rate":
            lines += ["    strokeColor:", "      thresholds: *thresholds-traffic", '    tags: ["hide-rates"]']
        else:
            lines += ["    label: *label-config", "    labelColor:", "      thresholds: *thresholds-rate-label"]
    return "\n".join(lines) + "\n"


def embed_in_dashboard(path, svg, panel_config, panel_title):
    with open(path) as f:
        dash = json.load(f)
    updated = []

    def walk(panels):
        for p in panels:
            if p.get("type") == "andrewbmchugh-flow-panel" and (panel_title is None or p.get("title") == panel_title):
                p.setdefault("options", {}).update(svg=svg, panelConfig=panel_config, siteConfig=panel_config)
                updated.append(p.get("title") or "(untitled)")
            walk(p.get("panels", []))

    walk(dash.get("panels", []))
    if not updated:
        sys.exit(f"No flow panel{' titled ' + repr(panel_title) if panel_title else ''} found in {path}")
    with open(path, "w") as f:
        json.dump(dash, f, indent=2)
    return updated


def write_preview(svg, out_dir, base, width, height):
    """Render a PNG with the port dots made visible, for checking placement."""
    html = os.path.join(out_dir, f"{base}.preview.html")
    png = os.path.join(out_dir, f"{base}.preview.png")
    visible = svg.replace('fill="transparent"', 'fill="#2ecc71"')
    w, h = int(width + 200), int(height + 200)
    with open(html, "w") as f:
        f.write(f'<html><body style="margin:0;background:#181b1f;width:{w}px;height:{h}px">{visible}</body></html>')
    chrome = next((c for c in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
                   if shutil.which(c)), None)
    if not chrome:
        return html
    subprocess.run([chrome, "--headless", "--disable-gpu", "--no-sandbox", f"--window-size={w},{h}",
                    f"--screenshot={os.path.abspath(png)}", f"file://{os.path.abspath(html)}"],
                   capture_output=True)
    os.remove(html)
    return png


def main():
    ap = argparse.ArgumentParser(description="Generate a Grafana flow-panel topology from a containerlab topology file.")
    ap.add_argument("topology", help="containerlab topology file")
    ap.add_argument("-o", "--out-dir", default=".", help="directory for the .svg and .flow_panel.yml (default: .)")
    ap.add_argument("--tier", action="append", metavar="REGEX",
                    help="node-name regex for one tier, top to bottom, first match wins; repeat per tier "
                         "(default: tiers derived from hop distance to the host nodes)")
    ap.add_argument("--include", metavar="REGEX", help="only draw nodes whose name matches")
    ap.add_argument("--exclude", metavar="REGEX", help="don't draw nodes whose name matches")
    ap.add_argument("--telemetry-kinds", default=",".join(DEFAULT_TELEMETRY_KINDS),
                    help="comma-separated kinds that stream telemetry (default: %(default)s)")
    ap.add_argument("--host-kinds", default=",".join(DEFAULT_HOST_KINDS),
                    help="comma-separated kinds drawn as hosts on the bottom tier (default: %(default)s)")
    ap.add_argument("--dashboard", help="dashboard JSON to embed the result into (edited in place)")
    ap.add_argument("--panel-title", help="only update the flow panel with this title")
    ap.add_argument("--preview", action="store_true", help="also render a PNG preview (needs Chrome/Chromium)")
    args = ap.parse_args()

    name, nodes, kinds, default_kind, links = load_topology(args.topology)
    keep = lambda n: (not args.include or re.search(args.include, n)) and not (args.exclude and re.search(args.exclude, n))
    links = [l for l in links if keep(l[0]) and keep(l[2])]
    if not links:
        sys.exit("No links to draw.")

    linked = sorted({l[0] for l in links} | {l[2] for l in links}, key=natural_key)
    adjacency = defaultdict(set)
    for a, _, b, _ in links:
        adjacency[a].add(b)
        adjacency[b].add(a)

    telemetry_kinds = set(args.telemetry_kinds.split(","))
    has_telemetry = lambda n: node_kind(n, nodes, default_kind) in telemetry_kinds
    host_kinds = set(args.host_kinds.split(","))
    is_host = lambda n: node_kind(n, nodes, default_kind) in host_kinds
    passive = {n for n in linked if not has_telemetry(n)}

    if args.tier:
        tiers = assign_tiers(linked, args.tier)
    else:
        tiers = graph_tiers(linked, adjacency, is_host)
    tier_of = {n: i for i, t in enumerate(tiers) for n in t}
    # box widths depend only on port counts per side, which only need tier membership
    provisional_order = {n: i for t in tiers for i, n in enumerate(t)}
    widths = box_widths(linked, port_sides(links, tier_of, provisional_order))
    pos, width, height = layout(tiers, adjacency, max(widths.values()) + H_GAP)
    order_of = {n: pos[n][0] for n in linked}
    sides = port_sides(links, tier_of, order_of)
    xy = place_ports(links, pos, sides, widths)
    edges_svg, ports_svg, cells, warnings = build(links, xy, has_telemetry)
    subtitles = {n: node_subtitle(n, nodes, kinds, default_kind) for n in linked}
    svg = render_svg(pos, widths, width, height, edges_svg, ports_svg, subtitles, passive)
    panel_config = render_panel_config(cells)

    os.makedirs(args.out_dir, exist_ok=True)
    svg_path = os.path.join(args.out_dir, f"{name}.svg")
    pc_path = os.path.join(args.out_dir, f"{name}.flow_panel.yml")
    with open(svg_path, "w") as f:
        f.write(svg)
    with open(pc_path, "w") as f:
        f.write(panel_config)

    print(f"Tiers: " + " | ".join(", ".join(t) for t in tiers))
    print(f"{len(links)} links, {len(cells)} cells")
    print(f"Wrote {svg_path}")
    print(f"Wrote {pc_path}")
    if args.dashboard:
        updated = embed_in_dashboard(args.dashboard, svg, panel_config, args.panel_title)
        print(f"Embedded into {args.dashboard}: {', '.join(updated)}")
    if args.preview:
        print(f"Preview: {write_preview(svg, args.out_dir, name, width, height)}")
    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)


if __name__ == "__main__":
    main()
