"""
viz.py -- render the stitched AWS Config graph to a single self-contained HTML file.

Loads the normalized model (model.load_model) and inferred cross-account edges
(stitch.load_cross_account_edges), builds a pyvis / vis.js network, then injects a
custom control panel (layer toggles, account legend, search, org/detail drill-down)
into the generated HTML. Output is fully self-contained (vis.js inlined) so it opens
with no server and no AWS access.
"""

import argparse
import json
import re
from pathlib import Path

from pyvis.network import Network

from model import assign_layer, load_model
from stitch import load_cross_account_edges

OUTPUT_DIR = Path(__file__).parent / "output"

# Account id -> human name (per LukeD Instructions.md; one account still unknown).
ACCOUNT_NAMES = {
    "124074140119": "ALS",
    "162521700530": "Unknown",
    "387075078863": "CCCI",
    "886031930818": "Engineering dev",
    "954475336656": "shared-services",
}

# Account id -> node color (colorblind-friendly Tableau 10 subset).
ACCOUNT_COLORS = {
    "124074140119": "#4e79a7",
    "162521700530": "#f28e2b",
    "387075078863": "#59a14f",
    "886031930818": "#e15759",
    "954475336656": "#b07aa1",
}

# Layer -> vis.js node shape (gives each layer a distinct silhouette).
LAYER_SHAPES = {
    "networking": "dot",
    "compute": "square",
    "storage": "database",
    "iam": "triangle",
    "other": "ellipse",
}

# Per-resource-type shape/size overrides. These win over LAYER_SHAPES so that a
# few important resource kinds get an instantly recognizable silhouette. Size is
# optional; when omitted the default node size (14) is used.
# vis.js shapes: dot, circle, ellipse, database, box, diamond, star, triangle,
# triangleDown, hexagon, square, text.
TYPE_SHAPES: dict[str, str] = {
    "AWS::EC2::VPC":                     "dot",       # big circle -> the container
    "AWS::EC2::Subnet":                  "hexagon",
    "AWS::EC2::InternetGateway":         "star",
    "AWS::EC2::NatGateway":              "star",
    "AWS::EC2::TransitGateway":          "star",
    "AWS::EC2::TransitGatewayAttachment": "star",
    "AWS::EC2::Instance":                "box",
    "AWS::ElasticLoadBalancingV2::LoadBalancer": "diamond",
    "AWS::IAM::Role":                    "triangle",
    "AWS::S3::Bucket":                   "database",
}
TYPE_SIZES: dict[str, int] = {
    "AWS::EC2::VPC":     34,   # noticeably larger than the default 14
    "AWS::EC2::Subnet":  20,
}

ALL_LAYERS = ["networking", "storage", "iam", "compute", "other"]
# 'other' is noisy (Config recorders, alarms, compliance) -> off by default.
DEFAULT_LAYERS = ["networking", "storage", "iam", "compute"]

PLACEHOLDER_COLOR = "#5a5f66"  # faded grey for referenced-but-unrecorded resources
INTRA_EDGE_COLOR = "#8a8f98"
CROSS_EDGE_COLOR = "#ff8c1a"

# Connection (edge) categories. Config relationship labels follow a small set of
# verb patterns ("Contains X", "Is contained in X", "Is attached to X",
# "Is associated with X"); we bucket each edge into one category and give the
# category a color. Cross-account edges are always their own category. This
# powers both the color-coordinate-connectors toggle and the connection filters.
# Order matters: first substring match wins.
EDGE_CATEGORIES: list[tuple[str, str, str]] = [
    # key,            match substring (lowercased),   color
    ("contains",      "contains",            "#59a14f"),   # green
    ("contained_in",  "is contained in",     "#4e79a7"),   # blue
    ("attached",      "is attached to",      "#edc948"),   # yellow
    ("associated",    "is associated with",  "#b07aa1"),   # purple
]
EDGE_OTHER_COLOR = "#8a8f98"   # grey, for intra edges matching no category
# Cross-account edges reuse CROSS_EDGE_COLOR and the category key "cross".


def _edge_category(relationship_name: str) -> str:
    """Bucket a Config relationship label into an edge category key."""
    text = (relationship_name or "").lower()
    for key, needle, _color in EDGE_CATEGORIES:
        if needle in text:
            return key
    return "other"


def _category_color(key: str) -> str:
    for k, _needle, color in EDGE_CATEGORIES:
        if k == key:
            return color
    return EDGE_OTHER_COLOR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _node_id(account: str, resource_id: str) -> str:
    """Namespace resource ids by account (ids are only unique within an account)."""
    return f"{account}::{resource_id}"


def _account_label(account: str) -> str:
    name = ACCOUNT_NAMES.get(account, account)
    return f"{name} ({account})"


def _short(text: str, limit: int = 60) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _build_title(resource: dict) -> str:
    """Plain-text multi-line hover tooltip for a real resource."""
    lines = [
        resource.get("resourceType", ""),
        resource.get("name", "") or resource.get("resourceId", ""),
        f"id: {resource.get('resourceId', '')}",
        f"account: {_account_label(resource.get('account', ''))}",
        f"layer: {resource.get('layer', '')}",
    ]
    if resource.get("region"):
        lines.append(f"region: {resource['region']}")

    cfg = resource.get("configFields") or {}
    if isinstance(cfg, dict) and cfg:
        lines.append("--- config ---")
        for k, v in list(cfg.items())[:6]:
            lines.append(f"{k}: {_short(v)}")

    tags = resource.get("tags") or {}
    if isinstance(tags, dict) and tags:
        lines.append("--- tags ---")
        for k, v in list(tags.items())[:6]:
            lines.append(f"{k}={_short(v)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Network construction
# ---------------------------------------------------------------------------

def _build_network(resources: list[dict], intra_edges: list[dict],
                   cross_edges: list[dict], accounts: set[str] | None) -> Network:
    net = Network(
        height="100vh",
        width="100%",
        bgcolor="#1a1d21",
        font_color="#e8eaed",
        directed=True,
        cdn_resources="in_line",  # inline vis.js -> self-contained, no network calls
    )

    # Index real resources by (account, resourceId) so edges can find endpoints.
    resource_index: dict[str, dict] = {}
    added: set[str] = set()

    for r in resources:
        acct = r.get("account", "")
        if accounts and acct not in accounts:
            continue
        rid = r.get("resourceId", "")
        nid = _node_id(acct, rid)
        resource_index[nid] = r

        rtype = r.get("resourceType", "")
        net.add_node(
            nid,
            label=_short(r.get("name") or rid, 28),
            shape=TYPE_SHAPES.get(rtype, LAYER_SHAPES.get(r.get("layer", "other"), "ellipse")),
            title=_build_title(r),
            color=ACCOUNT_COLORS.get(acct, "#9aa0a6"),
            account=acct,
            layer=r.get("layer", "other"),
            kind="real",
            rtype=rtype,
            size=TYPE_SIZES.get(rtype, 14),
            # Full node config, embedded so right-click "Export config" works
            # offline (no re-fetch). Connected edges are gathered client-side.
            cfg={
                "resourceType": rtype,
                "resourceId":   rid,
                "name":         r.get("name", ""),
                "account":      acct,
                "region":       r.get("region", ""),
                "layer":        r.get("layer", "other"),
                "configFields": r.get("configFields") or {},
                "tags":         r.get("tags") or {},
            },
        )
        added.add(nid)

    def ensure_placeholder(acct: str, resource_id: str, rtype: str) -> str | None:
        """Create a faded node for an edge target that has no recorded resource."""
        if accounts and acct not in accounts:
            return None
        nid = _node_id(acct, resource_id)
        if nid in added:
            return nid
        layer = assign_layer(rtype) if rtype else "other"
        net.add_node(
            nid,
            label=_short(resource_id, 24),
            shape="diamond",
            title=f"{rtype or 'referenced resource'}\nid: {resource_id}\n"
                  f"account: {_account_label(acct)}\n(referenced but not recorded in Config)",
            color={"background": PLACEHOLDER_COLOR, "border": "#3a3f45"},
            account=acct,
            layer=layer,
            kind="placeholder",
            rtype=rtype,
            size=9,
            opacity=0.55,
            cfg={
                "resourceType": rtype or "",
                "resourceId":   resource_id,
                "account":      acct,
                "note":         "referenced but not recorded in Config",
            },
        )
        added.add(nid)
        return nid

    # Intra-account edges.
    for e in intra_edges:
        acct = e.get("account", "")
        if accounts and acct not in accounts:
            continue
        src = _node_id(acct, e.get("srcId", ""))
        if src not in added:
            continue  # src should always be a real recorded resource
        dst = ensure_placeholder(acct, e.get("dstId", ""), e.get("dstType", ""))
        if dst is None:
            continue
        rel = e.get("relationshipName", "")
        cat = _edge_category(rel)
        net.add_edge(
            src, dst,
            title=rel,
            color=_category_color(cat),
            width=1,
            relcat=cat,       # relationship category (contains / attached / ...)
            ecls="intra",     # edge class: within a single account
            basecolor=INTRA_EDGE_COLOR,  # color to use when coloring is toggled off
        )

    # Cross-account edges (styled distinctly).
    for e in cross_edges:
        sacct = e.get("srcAccount", "")
        dacct = e.get("dstAccount", "")
        if accounts and (sacct not in accounts or dacct not in accounts):
            continue
        src = ensure_placeholder(sacct, e.get("srcId", ""), "")
        dst = ensure_placeholder(dacct, e.get("dstId", ""), "")
        if src is None or dst is None:
            continue
        net.add_edge(
            src, dst,
            title=e.get("stitchReason", "") + ": " + e.get("relationshipName", ""),
            color=CROSS_EDGE_COLOR,
            width=3,
            dashes=True,
            relcat="cross",     # own category; always styled distinctly
            ecls="cross",       # edge class: spans two accounts
            basecolor=CROSS_EDGE_COLOR,
        )

    net.set_options(json.dumps(_VIS_OPTIONS))
    return net


_VIS_OPTIONS = {
    "interaction": {"hover": True, "tooltipDelay": 120, "navigationButtons": True,
                    "keyboard": {"enabled": True}},
    "physics": {
        "solver": "barnesHut",
        # Stronger repulsion + longer springs + max avoidOverlap keep nodes from
        # piling on top of each other. avoidOverlap=1 makes vis.js treat each node
        # as a solid disc during layout so they push apart instead of stacking.
        "barnesHut": {"gravitationalConstant": -26000, "springLength": 170,
                      "springConstant": 0.02, "damping": 0.4, "avoidOverlap": 1.0},
        "stabilization": {"enabled": True, "iterations": 600, "updateInterval": 50},
        "minVelocity": 0.75,
    },
    "nodes": {"borderWidth": 1, "font": {"color": "#e8eaed", "size": 12}},
    # "continuous" smoothing computes the curve analytically. "dynamic" (the
    # pyvis default) instead spawns one invisible support node per edge; those
    # get orphaned when clustering / re-laying-out and render as stray thin
    # ovals. continuous keeps the curved look with no support nodes -> no
    # artifacts, and is faster + better-behaved under clustering.
    "edges": {"smooth": {"enabled": True, "type": "continuous", "roundness": 0.5},
              "arrows": {"to": {"enabled": True, "scaleFactor": 0.5}}},
}


# ---------------------------------------------------------------------------
# Control-panel injection
# ---------------------------------------------------------------------------

def _strip_cdn_bootstrap(html: str) -> str:
    """Remove pyvis's CDN Bootstrap tags (unused; would fail to load offline)."""
    html = re.sub(r'<link[^>]*bootstrap[^>]*>', "", html, flags=re.IGNORECASE)
    html = re.sub(r'<script[^>]*bootstrap[^>]*></script>', "", html, flags=re.IGNORECASE)
    return html


def _control_panel(default_layers: list[str]) -> str:
    # Edge categories for the connector legend / color toggle / filters.
    edge_categories = [
        {"key": key, "label": key.replace("_", " "), "color": color}
        for key, _needle, color in EDGE_CATEGORIES
    ]
    edge_categories.append({"key": "other", "label": "other", "color": EDGE_OTHER_COLOR})
    edge_categories.append({"key": "cross", "label": "cross-account", "color": CROSS_EDGE_COLOR})

    cfg = {
        "accountNames": ACCOUNT_NAMES,
        "accountColors": ACCOUNT_COLORS,
        "allLayers": ALL_LAYERS,
        "defaultLayers": default_layers,
        "layerShapes": LAYER_SHAPES,
        "crossEdgeColor": CROSS_EDGE_COLOR,
        "edgeCategories": edge_categories,
    }
    return _PANEL_CSS + '<script type="text/javascript">\nconst VIZCFG = ' \
        + json.dumps(cfg) + ";\n" + _PANEL_JS + "\n</script>\n"


_PANEL_CSS = """
<style>
#viz-panel {
  position: fixed; top: 12px; left: 12px; z-index: 1000;
  width: 240px; max-height: 92vh; overflow-y: auto;
  background: rgba(28,31,35,0.94); color: #e8eaed;
  border: 1px solid #3a3f45; border-radius: 8px; padding: 12px 14px;
  font-family: -apple-system, Segoe UI, Roboto, sans-serif; font-size: 12px;
  box-shadow: 0 4px 16px rgba(0,0,0,0.5);
}
#viz-panel h1 { font-size: 14px; margin: 0 0 8px; font-weight: 600; }
#viz-panel h2 { font-size: 11px; margin: 12px 0 6px; text-transform: uppercase;
  letter-spacing: .05em; color: #9aa0a6; }
#viz-panel .btn { display: inline-block; padding: 4px 9px; margin: 2px 3px 2px 0;
  border: 1px solid #4a4f55; border-radius: 5px; cursor: pointer;
  background: #2a2e33; user-select: none; }
#viz-panel .btn.off { opacity: .4; text-decoration: line-through; }
#viz-panel .legend-row { display: flex; align-items: center; margin: 3px 0;
  cursor: pointer; user-select: none; }
#viz-panel .legend-row.off { opacity: .4; }
#viz-panel .swatch { width: 12px; height: 12px; border-radius: 3px; margin-right: 7px;
  flex: 0 0 auto; }
#viz-panel input[type=text] { width: 100%; box-sizing: border-box; padding: 5px 7px;
  border: 1px solid #4a4f55; border-radius: 5px; background: #14171a; color: #e8eaed; }
#viz-panel .hint { color: #9aa0a6; font-size: 10px; margin-top: 4px; }
#viz-search-count { color: #9aa0a6; font-size: 10px; margin-top: 3px; min-height: 12px; }

/* Layer rows with an expandable per-resource-type sub-filter list. */
#viz-panel .layer-row { display: flex; align-items: center; margin: 3px 0; }
#viz-panel .caret { width: 16px; text-align: center; cursor: pointer;
  user-select: none; color: #9aa0a6; flex: 0 0 auto; font-family: monospace; }
#viz-panel .caret:hover { color: #e8eaed; }
#viz-panel .subtypes { margin: 0 0 4px 20px; display: none; }
#viz-panel .subtypes.open { display: block; }
#viz-panel .subtype { display: block; padding: 2px 6px; margin: 2px 0; cursor: pointer;
  border-radius: 4px; user-select: none; color: #cfd3d8; font-size: 11px; }
#viz-panel .subtype:hover { background: #2a2e33; }
#viz-panel .subtype.off { opacity: .4; text-decoration: line-through; }
#viz-panel .subtype .cnt { color: #9aa0a6; }

/* Right-click context menu. */
#viz-ctx { position: fixed; z-index: 2000; min-width: 168px; display: none;
  background: rgba(28,31,35,0.98); color: #e8eaed;
  border: 1px solid #4a4f55; border-radius: 6px; padding: 4px;
  font-family: -apple-system, Segoe UI, Roboto, sans-serif; font-size: 12px;
  box-shadow: 0 6px 20px rgba(0,0,0,0.6); }
#viz-ctx .ctx-item { padding: 6px 10px; border-radius: 4px; cursor: pointer;
  white-space: nowrap; }
#viz-ctx .ctx-item:hover { background: #3a4048; }
#viz-ctx .ctx-item.disabled { color: #6b7079; cursor: default; }
#viz-ctx .ctx-item.disabled:hover { background: transparent; }
#viz-ctx .ctx-head { padding: 4px 10px 6px; color: #9aa0a6; font-size: 10px;
  text-transform: uppercase; letter-spacing: .05em; border-bottom: 1px solid #3a3f45;
  margin-bottom: 4px; max-width: 220px; overflow: hidden; text-overflow: ellipsis; }
#viz-ctx .ctx-sep { height: 1px; background: #3a3f45; margin: 4px 0; }
</style>
"""

_PANEL_JS = r"""
function vizInit() {
  if (typeof network === "undefined" || typeof nodes === "undefined"
      || typeof edges === "undefined") {
    return setTimeout(vizInit, 120);
  }
  var allNodesArr = nodes.get();
  var allEdgesArr = edges.get();
  var enabledLayers = new Set(VIZCFG.defaultLayers);
  var enabledAccounts = new Set(Object.keys(VIZCFG.accountNames));
  var enabledEdgeCats = new Set(VIZCFG.edgeCategories.map(function (c) { return c.key; }));
  var colorConnectors = true;   // color-coordinate connectors on by default
  var showPlaceholders = true;
  var clustered = false;
  var focusFilter = null;       // Set of node ids to isolate (right-click focus), or null

  // Resource types present, grouped by layer, with per-type node counts. Powers
  // the expandable per-resource-type sub-filters under each layer. Every present
  // type starts enabled; the layer button is the master switch, a type toggle
  // narrows within an (enabled) layer.
  var typesByLayer = {};
  var enabledTypes = new Set();
  allNodesArr.forEach(function (n) {
    if (!n.rtype) return;
    typesByLayer[n.layer] = typesByLayer[n.layer] || {};
    typesByLayer[n.layer][n.rtype] = (typesByLayer[n.layer][n.rtype] || 0) + 1;
    enabledTypes.add(n.rtype);
  });

  // Which edge categories actually occur, in the declared order (for the legend).
  var presentEdgeCats = VIZCFG.edgeCategories.filter(function (c) {
    return allEdgesArr.some(function (e) { return (e.relcat || "other") === c.key; });
  });

  function edgeCatColor(key) {
    var c = VIZCFG.edgeCategories.find(function (x) { return x.key === key; });
    return c ? c.color : "#8a8f98";
  }

  // Recolor every edge: category color when coloring is on, its stored base
  // color (plain grey / cross-orange) when off.
  function applyEdgeColors() {
    var updates = allEdgesArr.map(function (e) {
      var col = colorConnectors ? edgeCatColor(e.relcat || "other")
                                : (e.basecolor || "#8a8f98");
      return { id: e.id, color: col };
    });
    edges.update(updates);
  }

  // Hide edges whose relationship category is filtered off.
  function applyEdgeVisibility() {
    var updates = allEdgesArr.map(function (e) {
      return { id: e.id, hidden: !enabledEdgeCats.has(e.relcat || "other") };
    });
    edges.update(updates);
  }

  // Freeze physics once the layout settles so the graph stops drifting.
  // Nodes stay draggable; they just won't wander on their own. We also collapse
  // to the org overview on first load (after the detail layout has settled, so
  // the saved positions used by "Expand to detail" are the good spread-out ones).
  function freezePhysics() { network.setOptions({ physics: false }); }
  var didStabilize = false;
  function onStabilized() {
    if (didStabilize) return;
    didStabilize = true;
    freezePhysics();
    collapseToOrg();  // open in org overview by default
  }
  network.once("stabilizationIterationsDone", onStabilized);
  setTimeout(onStabilized, 6000);  // fallback if the event already fired

  var presentLayers = VIZCFG.allLayers.filter(function (l) {
    return allNodesArr.some(function (n) { return n.layer === l; });
  });
  var presentAccounts = Object.keys(VIZCFG.accountNames).filter(function (a) {
    return allNodesArr.some(function (n) { return n.account === a; });
  });

  function applyVisibility() {
    var updates = allNodesArr.map(function (n) {
      var typeOn = n.rtype ? enabledTypes.has(n.rtype) : true;
      var vis = enabledLayers.has(n.layer)
        && typeOn
        && enabledAccounts.has(n.account)
        && (n.kind === "real" || showPlaceholders)
        && (focusFilter === null || focusFilter.has(n.id));
      return { id: n.id, hidden: !vis };
    });
    nodes.update(updates);
  }

  // --- panel scaffold ---
  var panel = document.createElement("div");
  panel.id = "viz-panel";
  panel.innerHTML =
    '<h1>AWS Config Explorer</h1>' +
    '<input id="viz-search" type="text" placeholder="Search name / id / type…" />' +
    '<div id="viz-search-count"></div>' +
    '<h2>Zoom</h2><div><span id="viz-zoom" class="btn">Collapse to org overview</span></div>' +
    '<h2>Layers</h2><div id="viz-layers"></div>' +
    '<h2>Accounts</h2><div id="viz-accounts"></div>' +
    '<h2>Connections</h2>' +
    '<div><span id="viz-edgecolor" class="btn">Color connectors: on</span></div>' +
    '<div id="viz-edgecats"></div>' +
    '<h2>Options</h2>' +
    '<div><span id="viz-ph" class="btn">Placeholders: on</span></div>' +
    '<div><span id="viz-unstack" class="btn">Unstack / re-layout</span></div>' +
    '<div class="hint">Click [+] beside a layer to filter its resource types. ' +
    'Double-click an account bubble to expand it. Right-click a node to export ' +
    'its config or filter to what it connects to.</div>';
  document.body.appendChild(panel);

  // --- layer buttons, each expandable to per-resource-type sub-filters ---
  var layerBox = panel.querySelector("#viz-layers");
  function shortType(t) { return t.replace(/^AWS::/, ""); }

  presentLayers.forEach(function (layer) {
    var wrap = document.createElement("div");
    var types = Object.keys(typesByLayer[layer] || {}).sort();

    var row = document.createElement("div");
    row.className = "layer-row";

    var caret = document.createElement("span");
    caret.className = "caret";
    caret.textContent = types.length ? "+" : "";

    var b = document.createElement("span");
    b.className = "btn" + (enabledLayers.has(layer) ? "" : " off");
    b.textContent = layer;
    b.onclick = function () {
      if (enabledLayers.has(layer)) { enabledLayers.delete(layer); b.classList.add("off"); }
      else { enabledLayers.add(layer); b.classList.remove("off"); }
      applyVisibility();
    };

    var sub = document.createElement("div");
    sub.className = "subtypes";
    caret.onclick = function () {
      if (!types.length) return;
      var open = sub.classList.toggle("open");
      caret.textContent = open ? "-" : "+";
    };

    row.appendChild(caret);
    row.appendChild(b);
    wrap.appendChild(row);

    types.forEach(function (t) {
      var item = document.createElement("span");
      item.className = "subtype" + (enabledTypes.has(t) ? "" : " off");
      item.textContent = shortType(t) + " ";
      var cnt = document.createElement("span");
      cnt.className = "cnt";
      cnt.textContent = "(" + typesByLayer[layer][t] + ")";
      item.appendChild(cnt);
      item.onclick = function () {
        if (enabledTypes.has(t)) { enabledTypes.delete(t); item.classList.add("off"); }
        else { enabledTypes.add(t); item.classList.remove("off"); }
        applyVisibility();
      };
      sub.appendChild(item);
    });

    wrap.appendChild(sub);
    layerBox.appendChild(wrap);
  });

  // --- account legend / toggles ---
  var acctBox = panel.querySelector("#viz-accounts");
  presentAccounts.forEach(function (acc) {
    var row = document.createElement("div");
    row.className = "legend-row";
    var sw = document.createElement("span");
    sw.className = "swatch";
    sw.style.background = VIZCFG.accountColors[acc] || "#9aa0a6";
    var lbl = document.createElement("span");
    lbl.textContent = VIZCFG.accountNames[acc] + " (" + acc + ")";
    row.appendChild(sw); row.appendChild(lbl);
    row.onclick = function () {
      if (enabledAccounts.has(acc)) { enabledAccounts.delete(acc); row.classList.add("off"); }
      else { enabledAccounts.add(acc); row.classList.remove("off"); }
      applyVisibility();
    };
    acctBox.appendChild(row);
  });

  // --- connector color toggle ---
  var edgeColorBtn = panel.querySelector("#viz-edgecolor");
  edgeColorBtn.onclick = function () {
    colorConnectors = !colorConnectors;
    edgeColorBtn.textContent = "Color connectors: " + (colorConnectors ? "on" : "off");
    edgeColorBtn.classList.toggle("off", !colorConnectors);
    applyEdgeColors();
  };

  // --- connection category legend + filters ---
  var edgeCatBox = panel.querySelector("#viz-edgecats");
  presentEdgeCats.forEach(function (cat) {
    var row = document.createElement("div");
    row.className = "legend-row";
    var sw = document.createElement("span");
    sw.className = "swatch";
    sw.style.background = cat.color;
    var lbl = document.createElement("span");
    lbl.textContent = cat.label;
    row.appendChild(sw); row.appendChild(lbl);
    row.onclick = function () {
      if (enabledEdgeCats.has(cat.key)) { enabledEdgeCats.delete(cat.key); row.classList.add("off"); }
      else { enabledEdgeCats.add(cat.key); row.classList.remove("off"); }
      applyEdgeVisibility();
    };
    edgeCatBox.appendChild(row);
  });

  // --- placeholder toggle ---
  var phBtn = panel.querySelector("#viz-ph");
  phBtn.onclick = function () {
    showPlaceholders = !showPlaceholders;
    phBtn.textContent = "Placeholders: " + (showPlaceholders ? "on" : "off");
    phBtn.classList.toggle("off", !showPlaceholders);
    applyVisibility();
  };

  // --- unstack / re-layout ---
  // Physics is frozen after load, so dragged or freshly-uncollapsed nodes can end
  // up piled together. This gives a short physics burst with maximum overlap
  // avoidance to spread them apart, then re-freezes so the graph stays put.
  var unstackBtn = panel.querySelector("#viz-unstack");
  unstackBtn.onclick = function () {
    unstackBtn.classList.add("off");
    unstackBtn.textContent = "Unstacking…";
    network.setOptions({ physics: {
      enabled: true, solver: "barnesHut",
      barnesHut: { gravitationalConstant: -30000, springLength: 180,
                   springConstant: 0.02, damping: 0.5, avoidOverlap: 1.0 },
      minVelocity: 1.0,
    }});
    setTimeout(function () {
      network.setOptions({ physics: false });
      unstackBtn.classList.remove("off");
      unstackBtn.textContent = "Unstack / re-layout";
    }, 2500);
  };

  // --- clustering (org overview <-> detail) ---
  // Physics is frozen after stabilization, so when a cluster is opened vis.js
  // drops every released child node at the cluster's single coordinate and has
  // no forces to spread them back out -> they stack. We snapshot positions
  // before clustering and restore them after opening so the layout is preserved.
  var savedPositions = {};
  function restorePositions() {
    Object.keys(savedPositions).forEach(function (id) {
      if (network.findNode(id).length) {
        var p = savedPositions[id];
        network.moveNode(id, p.x, p.y);
      }
    });
  }
  function clusterByAccount() {
    savedPositions = network.getPositions();
    presentAccounts.forEach(function (acc) {
      network.cluster({
        joinCondition: function (opts) { return opts.account === acc; },
        clusterNodeProperties: {
          id: "cluster:" + acc,
          label: VIZCFG.accountNames[acc] + "\n(" + acc + ")",
          shape: "database",
          color: VIZCFG.accountColors[acc],
          font: { size: 26, color: "#ffffff" },
          borderWidth: 3, size: 40,
        },
      });
    });
  }
  function openAllClusters() {
    presentAccounts.forEach(function (acc) {
      if (network.isCluster("cluster:" + acc)) network.openCluster("cluster:" + acc);
    });
    restorePositions();
  }
  // Physics is frozen, so cluster nodes land wherever their members happened to
  // sit and can overlap. Spread the account bubbles evenly around a circle so the
  // org overview is always legible.
  function layoutClusters() {
    var ids = presentAccounts
      .map(function (a) { return "cluster:" + a; })
      .filter(function (id) { return network.isCluster(id); });
    var n = ids.length;
    if (!n) return;
    var radius = n === 1 ? 0 : 180 + n * 60;
    ids.forEach(function (id, i) {
      var ang = (2 * Math.PI * i) / n - Math.PI / 2;
      network.moveNode(id, radius * Math.cos(ang), radius * Math.sin(ang));
    });
  }

  var zoomBtn = panel.querySelector("#viz-zoom");
  function collapseToOrg() {
    clusterByAccount();
    layoutClusters();
    clustered = true;
    zoomBtn.textContent = "Expand to detail";
    network.fit({ animation: false });
  }
  function expandToDetail() {
    openAllClusters();
    clustered = false;
    zoomBtn.textContent = "Collapse to org overview";
    network.fit({ animation: false });
  }
  zoomBtn.onclick = function () {
    if (clustered) { expandToDetail(); } else { collapseToOrg(); }
  };
  network.on("doubleClick", function (p) {
    if (p.nodes.length === 1 && network.isCluster(p.nodes[0])) {
      network.openCluster(p.nodes[0]);
      restorePositions();
    }
  });

  // --- search ---
  var searchBox = panel.querySelector("#viz-search");
  var countEl = panel.querySelector("#viz-search-count");
  searchBox.addEventListener("keyup", function () {
    var q = searchBox.value.trim().toLowerCase();
    if (!q) { network.unselectAll(); countEl.textContent = ""; return; }
    var hits = allNodesArr.filter(function (n) {
      return (n.label || "").toLowerCase().indexOf(q) >= 0
        || String(n.id || "").toLowerCase().indexOf(q) >= 0
        || (n.rtype || "").toLowerCase().indexOf(q) >= 0;
    }).map(function (n) { return n.id; });
    // don't try to select nodes hidden inside a cluster
    var selectable = hits.filter(function (id) { return network.findNode(id).length; });
    network.selectNodes(selectable);
    countEl.textContent = hits.length + " match" + (hits.length === 1 ? "" : "es");
    if (selectable.length) network.focus(selectable[0], { scale: 1.0, animation: true });
  });

  // --- right-click context menu ---
  var ctx = document.createElement("div");
  ctx.id = "viz-ctx";
  document.body.appendChild(ctx);

  function hideCtx() { ctx.style.display = "none"; }

  function safeName(s) { return String(s).replace(/[^A-Za-z0-9._-]+/g, "_").slice(0, 120); }

  function downloadJSON(filename, obj) {
    var blob = new Blob([JSON.stringify(obj, null, 2)], { type: "application/json" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  // Export a node's embedded config plus its connected edges, as a .json file.
  function exportNode(nodeId) {
    var n = nodes.get(nodeId);
    if (!n) return;
    var conns = network.getConnectedEdges(nodeId).map(function (eid) {
      var e = edges.get(eid);
      if (!e) return null;
      var otherId = (e.from === nodeId) ? e.to : e.from;
      var other = nodes.get(otherId);
      return {
        relationship: e.title || "",
        category: e.relcat || "",
        direction: (e.from === nodeId) ? "out" : "in",
        target: otherId,
        targetName: other ? (other.label || "") : "",
      };
    }).filter(Boolean);
    var out = Object.assign({}, n.cfg || { id: nodeId }, { connections: conns });
    var base = (n.cfg && n.cfg.resourceId) ? n.cfg.resourceId : nodeId;
    downloadJSON(safeName(base) + ".json", out);
  }

  // Isolate a node and its directly-connected (1-hop) neighbors.
  function focusOnNode(nodeId) {
    focusFilter = new Set(network.getConnectedNodes(nodeId));
    focusFilter.add(nodeId);
    applyVisibility();
    if (network.findNode(nodeId).length) {
      network.focus(nodeId, { scale: 1.0, animation: true });
    }
  }

  function clearFocus() {
    if (focusFilter === null) return;
    focusFilter = null;
    applyVisibility();
  }

  function addCtxItem(label, fn, enabled) {
    var it = document.createElement("div");
    it.className = "ctx-item" + (enabled === false ? " disabled" : "");
    it.textContent = label;
    if (enabled !== false) { it.onclick = function () { hideCtx(); fn(); }; }
    ctx.appendChild(it);
  }

  function buildCtx(nodeId) {
    ctx.innerHTML = "";
    if (nodeId) {
      var n = nodes.get(nodeId);
      var head = document.createElement("div");
      head.className = "ctx-head";
      head.textContent = (n && n.label) ? n.label : nodeId;
      ctx.appendChild(head);
      addCtxItem("Export config", function () { exportNode(nodeId); });
      addCtxItem("Filter to connected", function () { focusOnNode(nodeId); });
      var sep = document.createElement("div"); sep.className = "ctx-sep"; ctx.appendChild(sep);
    }
    addCtxItem("Clear filter", clearFocus, focusFilter !== null);
  }

  network.on("oncontext", function (params) {
    params.event.preventDefault();
    var nodeId = network.getNodeAt(params.pointer.DOM);
    buildCtx(nodeId || null);
    ctx.style.display = "block";  // display first so we can measure to keep it on-screen
    var rect = ctx.getBoundingClientRect();
    var x = params.event.clientX, y = params.event.clientY;
    if (x + rect.width > window.innerWidth) x = window.innerWidth - rect.width - 6;
    if (y + rect.height > window.innerHeight) y = window.innerHeight - rect.height - 6;
    ctx.style.left = x + "px";
    ctx.style.top = y + "px";
  });

  document.addEventListener("click", function (e) { if (!ctx.contains(e.target)) hideCtx(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") hideCtx(); });

  applyVisibility();      // apply default layer set ('other' hidden)
  applyEdgeColors();      // color-coordinate connectors (on by default)
  applyEdgeVisibility();  // all connection categories visible by default
}
vizInit();
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render(accounts: list[str] | None = None, layers: list[str] | None = None,
           out_path: Path | None = None) -> Path:
    resources, intra_edges = load_model()
    try:
        cross_edges = load_cross_account_edges()
    except FileNotFoundError:
        cross_edges = []
        print("  warn  no cross-account edges found; run 'stitch' first")

    acct_set = set(accounts) if accounts else None
    default_layers = layers if layers else DEFAULT_LAYERS

    net = _build_network(resources, intra_edges, cross_edges, acct_set)
    html = net.generate_html(notebook=False)
    html = _strip_cdn_bootstrap(html)
    html = html.replace("</body>", _control_panel(default_layers) + "</body>")

    OUTPUT_DIR.mkdir(exist_ok=True)
    if out_path is None:
        if accounts and len(accounts) == 1:
            out_path = OUTPUT_DIR / f"aws-config-{accounts[0]}.html"
        else:
            out_path = OUTPUT_DIR / "aws-config-all.html"

    out_path.write_text(html, encoding="utf-8")
    n_nodes = len(net.get_nodes())
    n_edges = len(net.get_edges())
    print(f"Rendered {n_nodes} nodes, {n_edges} edges -> {out_path}")
    return out_path


def main() -> None:
    p = argparse.ArgumentParser(description="Render the AWS Config graph to HTML")
    p.add_argument("--account", action="append",
                   help="Render only this account id (repeatable). Default: all.")
    p.add_argument("--layers",
                   help="Comma-separated layers visible on load "
                        "(networking,storage,iam,compute,other).")
    p.add_argument("--out", help="Output HTML path.")
    args = p.parse_args()

    layers = [x.strip() for x in args.layers.split(",")] if args.layers else None
    out = Path(args.out) if args.out else None
    render(accounts=args.account, layers=layers, out_path=out)


if __name__ == "__main__":
    main()
