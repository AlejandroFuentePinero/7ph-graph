"""Render a Subgraph to an interactive pyvis widget (HTML string).

The widget names the vis.js library the app serves itself rather than carrying a
copy, so a result is a few KB and the library is downloaded once (issue #97).
Clicking a node shows its details beside the graph; a deck's details link out to
its Moxfield page.

This is thin glue over the tested query seam, not itself unit tested: pyvis's
generated HTML shape is deliberately not asserted (per the PRD testing plan),
beyond the two library tags this rewrites.
"""

import json
import re

from pyvis.network import Network

from graph7ph.query import Node, Subgraph
from graph7ph.serve import VIS_CSS_URL, VIS_JS_URL

_COLOURS = {
    "Pilot": "#e15759",
    "Deck": "#4e79a7",
    "Card": "#59a14f",
    "Archetype": "#f28e2b",
    "Macro": "#edc948",
    "Event": "#b07aa1",
    "Placement": "#bab0ac",
    "Intersection": "#9c755f",
}

# Grouped views colour by group instead of by kind, so the groups read apart at
# a glance: a head-to-head tints each player's chain, and card co-occurrence
# tints each seed card and its shared partners. One hue per group present, so up
# to three (two seeds and their partners) stay distinct.
_GROUP_COLOURS = ["#4e79a7", "#f28e2b", "#76b7b2"]

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
    net = Network(height="700px", width="100%", directed=True, cdn_resources="remote")
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
        colour = palette[node.group] if node.group is not None else _COLOURS.get(node.kind)
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
        tint = {"color": palette[player]} if player is not None else {}
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
    doc = _hosted_library(net.generate_html(notebook=False))
    return _with_details_panel(doc, meta)


_PROMPT = "Click a node to see its details."

# A details panel keyed on the vis node id: on click it shows the selected
# node's kind and label, and for a deck a link to its Moxfield list. The script
# runs after pyvis's own ``drawGraph()`` has assigned the global ``network``. The
# label is untrusted (a display name recovered from a Moxfield deck title), so it
# is written through textContent and a built <a> element, never innerHTML.
_PANEL = """
<div id="node-details" style="padding:0.75rem;font-family:sans-serif;color:#333">
  __PROMPT__
</div>
<script>
  const NODE_META = __META__;
  const panel = document.getElementById("node-details");
  function showNode(id) {
    const m = NODE_META[id];
    if (!m) return;
    panel.textContent = m.kind + ": " + m.label;
    if (m.moxfield) {
      panel.appendChild(document.createTextNode(" \\u00b7 "));
      const a = document.createElement("a");
      a.href = m.moxfield;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = "Open on Moxfield";
      panel.appendChild(a);
    }
  }
  network.on("selectNode", (params) => showNode(params.nodes[0]));
  network.on("deselectNode", () => { panel.textContent = "__PROMPT__"; });
</script>
"""


def _with_details_panel(doc: str, meta: dict) -> str:
    """Inject the click-to-details panel just before the document's body ends.

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
    return doc.replace("</body>", panel + "</body>", 1)
