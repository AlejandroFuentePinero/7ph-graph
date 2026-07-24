"""Render a Subgraph to an interactive pyvis widget (HTML string).

The widget names the vis.js library the app serves itself rather than carrying a
copy, so a result is a few KB and the library is downloaded once (issue #97).
The document lays out as a colour key, the graph filling the middle, and a details
panel below it; clicking a node fills that panel, and a deck's details link out to
its Moxfield page.

pyvis's own generated graph internals (its node/edge JS, its layout) are deliberately
not asserted, per the PRD testing plan. What this module *injects onto* that document
is: the tests hold the shared-palette node colours, the on-screen colour key, the dark
ground, and the structured details panel, since those are this module's contribution
and the visual-direction contract (§7) they answer to.
"""

import html
import json
import re

from pyvis.network import Network

from graph7ph.palette import CATEGORICAL, assign
from graph7ph.query import Node, Subgraph
from graph7ph.serve import VIS_CSS_URL, VIS_JS_URL
from graph7ph.theme import FONT_STACK, TOKENS

# §7: the eight node kinds take the eight palette slots in a fixed by-kind order,
# so the graph draws its colour from the one shared vocabulary (`assign`) the
# charts also use. Deck leads, so it lands on slot 1 (blue), the one worked example
# §5 names ("a Deck is the same blue as a dot or a line"). Colour is a secondary
# cue: the node label plus the on-screen key carry identity, never hue alone, since
# any two kinds can sit adjacent.
_KIND_ORDER = (
    "Deck", "Pilot", "Card", "Archetype", "Macro", "Event", "Placement", "Intersection",
)
_KIND_COLOURS = assign(_KIND_ORDER)

# Grouped views colour by group instead of by kind, so the groups read apart at
# a glance: a head-to-head tints each player's chain, and card co-occurrence
# tints each seed card and its shared partners. Slots 1-3 of the shared set, the
# trio that stays distinct under adjacency (§7); up to three groups present (two
# seeds and their partners).
_GROUP_COLOURS = CATEGORICAL[:3]

# A deck's stable id is its Moxfield public id, so its authoritative list is a
# direct construction (confirmed against the source's own ``url`` field).
_MOXFIELD = "https://moxfield.com/decks/{}"


def _moxfield_url(node: Node) -> str | None:
    """The Moxfield page for a deck node, or ``None`` for any other kind."""
    if node.kind != "Deck":
        return None
    return _MOXFIELD.format(node.id.removeprefix("deck:"))


def _hosted_library(doc: str) -> str:
    """Point the widget's two library tags at the copy the app serves.

    pyvis offers its library inlined or from a CDN and nothing in between, so the
    widget is generated in its CDN shape and the tags are rewritten here. They are
    rebuilt rather than their URLs substituted, because pyvis's carry a Subresource
    Integrity hash for the CDN's bytes that a browser would then check ours against.

    The URLs are root-relative, which holds the app to being served at the root of
    its origin. It is, on all three ways it runs: the Space, ``graph7ph app``, and
    Colab, where ``proxyPort`` hands the notebook a per-port hostname of its own.
    Mounting it under a path (a ``root_path``, or ``mount_gradio_app`` inside a
    larger API) would break this and nothing else, silently: the widget is embedded
    through an iframe ``srcdoc``, which has no URL of its own, so these resolve
    against the parent document, and a prefix the parent carries is not in them.
    """
    tags = (
        (r"<link[^>]*cdnjs[^>]*vis-network[^>]*>",
         f'<link rel="stylesheet" href="{VIS_CSS_URL}"/>'),
        (r"<script[^>]*cdnjs[^>]*vis-network[^>]*></script>",
         f'<script src="{VIS_JS_URL}"></script>'),
    )
    for pattern, tag in tags:
        doc, found = re.subn(pattern, tag, doc)
        if found != 1:
            raise RuntimeError(
                f"pyvis emitted {found} tags matching {pattern}, expected 1: its "
                "template has changed, and the graph would draw with no library."
            )
    return doc


def render_subgraph(subgraph: Subgraph) -> str:
    net = Network(
        height="700px", width="100%", directed=True, cdn_resources="remote",
        bgcolor=TOKENS["surface"], font_color=TOKENS["text"],
    )
    # A stable colour per player group present, so the two chains stay distinct.
    groups = sorted({n.group for n in subgraph.nodes if n.group is not None})
    palette = {g: _GROUP_COLOURS[i % len(_GROUP_COLOURS)] for i, g in enumerate(groups)}
    group_by_id = {n.id: n.group for n in subgraph.nodes}
    for node in subgraph.nodes:
        # A weighted node is sized by its value; vis.js scales the values in the
        # graph between a min and max radius, so bigger weight reads as a bigger
        # node. Unweighted nodes render at the default size.
        weighted = {"value": node.weight} if node.weight is not None else {}
        # A grouped node takes its player colour; a plain node the kind colour.
        colour = palette[node.group] if node.group is not None else _KIND_COLOURS.get(node.kind)
        # A shape override (e.g. "circle") draws the label inside the node; vis.js
        # then sizes it to the text, so a shaped node ignores any weight.
        shaped = {"shape": node.shape} if node.shape is not None else {}
        # A pinned node holds a fixed position with physics off, so a deterministic
        # layout stays put instead of being pulled into a hairball.
        pinned = (
            {"x": node.pin[0], "y": node.pin[1], "fixed": True, "physics": False}
            if node.pin is not None
            else {}
        )
        net.add_node(
            node.id,
            label=node.label,
            title=f"{node.kind}: {node.label}",
            color=colour,
            **weighted,
            **shaped,
            **pinned,
        )
    for edge in subgraph.edges:
        # Tint the edge to match its player so a chain reads as one colour; an
        # edge touching the neutral shared event takes the player on its other
        # end. The two players never share an edge (only the neutral event), so
        # source-or-target is unambiguous.
        player = group_by_id.get(edge.source) or group_by_id.get(edge.target)
        # §7: an edge is a hairline on `--border` when neutral, the group's tint
        # in a grouped view, so neither reads as the vis.js default grey slab line.
        tint = {"color": palette[player] if player is not None else TOKENS["border"]}
        # A visible label is drawn on the edge; otherwise it is a hover tooltip.
        text = {"label": edge.label} if edge.visible else {"title": edge.label}
        net.add_edge(edge.source, edge.target, **text, **tint)

    # A fully pinned graph (the two-seed co-occurrence layout) has nothing for
    # physics to solve, so turn it off: the fixed positions render as-is with no
    # stabilisation jitter.
    if subgraph.nodes and all(n.pin is not None for n in subgraph.nodes):
        net.toggle_physics(False)

    meta = {
        node.id: {
            "kind": node.kind,
            "label": node.label,
            "moxfield": _moxfield_url(node),
        }
        for node in subgraph.nodes
    }
    # The colour key names what colour actually encodes in this render: the groups
    # in a grouped view, otherwise the kinds present, each beside its swatch. Only
    # what is actually drawn is keyed, in the fixed slot order.
    if groups:
        # A group's value is an internal id; it is named in the key by its anchor
        # node (the member whose id is the group value, e.g. the pilot in a
        # head-to-head), whose label is the friendly name. A group with no such
        # anchor (the co-occurrence "seed:"/"cooccur" buckets, whose card ids never
        # equal the group value) is left out rather than surfaced as a raw id; its
        # seeds already carry their names on the nodes.
        label_by_id = {n.id: n.label for n in subgraph.nodes}
        legend = [(label_by_id[g], palette[g]) for g in groups if g in label_by_id]
    else:
        kinds_present = {n.kind for n in subgraph.nodes}
        legend = [(k, _KIND_COLOURS[k]) for k in _KIND_ORDER if k in kinds_present]
    doc = _hosted_library(net.generate_html(notebook=False))
    return _compose(doc, meta, _legend(legend))


_PROMPT = "Click a node to see its details."

# The iframe is an isolated document, so the parent's `:root` tokens do not reach
# it: the theme is carried in as literal token values here. It lays the doc out as
# a flex column (key on top, graph filling the middle, details at the bottom), so
# the height is whatever the embedding iframe gives it and the details stay visible
# without scrolling. It also retires pyvis/bootstrap's own light chrome: the white
# `.card`, the `lightgray` graph border, and the empty centred heading.
_DOC_STYLE = f"""<style>
  html, body {{ height: 100%; margin: 0; }}
  body {{
    display: flex; flex-direction: column;
    background: {TOKENS['surface']}; color: {TOKENS['text']}; font-family: {FONT_STACK};
  }}
  center {{ display: none; }}
  .card {{
    flex: 1 1 auto; display: flex; min-height: 0;
    margin: 0; border: none !important; background: transparent !important;
  }}
  #mynetwork {{
    flex: 1 1 auto; height: auto !important; min-height: 0;
    float: none !important; border: none !important; background: {TOKENS['surface']} !important;
  }}
  #graph-legend {{
    flex: 0 0 auto; display: flex; flex-wrap: wrap; gap: 0.85rem;
    padding: 0.55rem 0.85rem; border-bottom: 1px solid {TOKENS['border']};
  }}
  .legend-chip {{
    display: inline-flex; align-items: center; gap: 0.4rem;
    font-size: 12px; color: {TOKENS['text-dim']};
  }}
  .legend-dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
  .vis-tooltip {{
    background: {TOKENS['surface-2']} !important; color: {TOKENS['text']} !important;
    border: 1px solid {TOKENS['border']} !important; border-radius: 4px !important;
    font-family: {FONT_STACK} !important; font-size: 12px !important; padding: 4px 8px !important;
  }}
  #node-details {{
    flex: 0 0 auto; padding: 0.7rem 0.85rem;
    border-top: 1px solid {TOKENS['border']}; font-size: 14px;
  }}
  .nd-row {{ display: flex; gap: 0.85rem; padding: 0.12rem 0; }}
  .nd-label {{
    flex: 0 0 3.25rem; color: {TOKENS['text-mute']};
    text-transform: uppercase; font-size: 11px; letter-spacing: 0.06em; line-height: 1.6;
  }}
  .nd-value {{ color: {TOKENS['text']}; }}
  .nd-prompt {{ margin: 0; color: {TOKENS['text-mute']}; }}
  .nd-link {{ color: {TOKENS['accent-bright']}; text-decoration: none; }}
  .nd-link:hover {{ text-decoration: underline; }}
</style>"""

# A details panel keyed on the vis node id: on click it shows the selected node's
# kind and name as labelled field rows, and for a deck its Moxfield page as a link
# affordance. The script runs after pyvis's own ``drawGraph()`` has assigned the
# global ``network``. The label is untrusted (a display name recovered from a
# Moxfield deck title), so it is written through textContent and a built <a>
# element, never innerHTML.
_PANEL = """
<div id="node-details"><p class="nd-prompt">__PROMPT__</p></div>
<script>
  const NODE_META = __META__;
  const panel = document.getElementById("node-details");
  function row(labelText, value) {
    const r = document.createElement("div"); r.className = "nd-row";
    const k = document.createElement("span"); k.className = "nd-label"; k.textContent = labelText;
    const v = document.createElement("span"); v.className = "nd-value";
    if (typeof value === "string") { v.textContent = value; } else { v.appendChild(value); }
    r.appendChild(k); r.appendChild(v); return r;
  }
  function showNode(id) {
    const m = NODE_META[id];
    if (!m) return;
    panel.replaceChildren(row("Kind", m.kind), row("Name", m.label));
    if (m.moxfield) {
      const a = document.createElement("a");
      a.href = m.moxfield;
      a.target = "_blank";
      a.rel = "noopener";
      a.className = "nd-link";
      a.textContent = "Open on Moxfield \\u2197";
      panel.appendChild(row("Deck", a));
    }
  }
  function reset() {
    const p = document.createElement("p"); p.className = "nd-prompt"; p.textContent = "__PROMPT__";
    panel.replaceChildren(p);
  }
  network.on("selectNode", (params) => showNode(params.nodes[0]));
  network.on("deselectNode", reset);
</script>
"""


def _legend(pairs: list[tuple[str, str]]) -> str:
    """The on-screen colour key: one chip per drawn kind or group, a swatch beside
    its name, so identity is carried by the label and not by hue alone (§7).

    Labels can be untrusted (a group is a player or card name), so each is escaped;
    the swatch colour is one of our own palette hexes.
    """
    if not pairs:
        return ""
    chips = "".join(
        f'<span class="legend-chip">'
        f'<span class="legend-dot" style="background:{colour}"></span>'
        f"{html.escape(label)}</span>"
        for label, colour in pairs
    )
    return f'<div id="graph-legend">{chips}</div>'


def _compose(doc: str, meta: dict, legend: str) -> str:
    """Bring the standalone pyvis document onto the theme: inject the dark doc-level
    style, the colour key at the top of the body, and the details panel at the end.

    The node metadata is embedded in an inline ``<script>``, so ``<``/``>``/``&``
    are unicode-escaped to stop a label ever closing the tag (``</script>``).
    """
    payload = (
        json.dumps(meta)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    panel = _PANEL.replace("__META__", payload).replace("__PROMPT__", _PROMPT)
    doc = _inject(doc, "</head>", _DOC_STYLE + "</head>")
    doc = _inject(doc, "<body>", "<body>" + legend)
    return _inject(doc, "</body>", panel + "</body>")


def _inject(doc: str, anchor: str, replacement: str) -> str:
    """Replace ``anchor``'s first occurrence, failing loudly if it is absent.

    Unlike a bare ``str.replace``, a missing anchor is an error rather than a silent
    no-op: pyvis's template shifting (an attributed ``<body ...>``, a renamed tag)
    would otherwise drop the theme and draw the graph on pyvis's white chrome with
    no signal, the same failure :func:`_hosted_library` guards for the library tags.
    """
    if anchor not in doc:
        raise RuntimeError(
            f"pyvis emitted no {anchor!r}: its template has changed, and the graph "
            "would draw un-themed."
        )
    return doc.replace(anchor, replacement, 1)
