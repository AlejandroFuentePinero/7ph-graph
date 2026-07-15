"""Render a Subgraph to a standalone interactive pyvis widget (HTML string).

vis.js assets are inlined so the returned HTML is self-contained and can be
dropped straight into a Gradio ``HTML`` component. Clicking a node shows its
details beside the graph; a deck's details link out to its Moxfield page.

This is thin glue over the tested query seam, not itself unit tested: pyvis's
generated HTML shape is deliberately not asserted (per the PRD testing plan).
"""

import json

from pyvis.network import Network

from graph7ph.query import Node, Subgraph

_COLOURS = {
    "Pilot": "#e15759",
    "Deck": "#4e79a7",
    "Card": "#59a14f",
    "Archetype": "#f28e2b",
    "Macro": "#edc948",
    "Event": "#b07aa1",
    "Placement": "#bab0ac",
}

# A head-to-head tints each player's chain instead of colouring by kind, so the
# two players read apart at a glance; up to two get these distinct hues.
_PLAYER_COLOURS = ["#4e79a7", "#f28e2b"]

# A deck's stable id is its Moxfield public id, so its authoritative list is a
# direct construction (confirmed against the source's own ``url`` field).
_MOXFIELD = "https://moxfield.com/decks/{}"


def _moxfield_url(node: Node) -> str | None:
    """The Moxfield page for a deck node, or ``None`` for any other kind."""
    if node.kind != "Deck":
        return None
    return _MOXFIELD.format(node.id.removeprefix("deck:"))


def render_subgraph(subgraph: Subgraph) -> str:
    net = Network(
        height="700px", width="100%", directed=True, cdn_resources="in_line"
    )
    # A stable colour per player group present, so the two chains stay distinct.
    groups = sorted({n.group for n in subgraph.nodes if n.group is not None})
    palette = {g: _PLAYER_COLOURS[i % len(_PLAYER_COLOURS)] for i, g in enumerate(groups)}
    group_by_id = {n.id: n.group for n in subgraph.nodes}
    for node in subgraph.nodes:
        # A weighted node is sized by its value; vis.js scales the values in the
        # graph between a min and max radius, so bigger weight reads as a bigger
        # node. Unweighted nodes render at the default size.
        weighted = {"value": node.weight} if node.weight is not None else {}
        # A grouped node takes its player colour; a plain node the kind colour.
        colour = palette[node.group] if node.group is not None else _COLOURS.get(node.kind)
        net.add_node(
            node.id,
            label=node.label,
            title=f"{node.kind}: {node.label}",
            color=colour,
            **weighted,
        )
    for edge in subgraph.edges:
        # Tint the edge to match its player so a chain reads as one colour; an
        # edge touching the neutral shared event takes the player on its other
        # end. The two players never share an edge (only the neutral event), so
        # source-or-target is unambiguous.
        player = group_by_id.get(edge.source) or group_by_id.get(edge.target)
        tint = {"color": palette[player]} if player is not None else {}
        net.add_edge(edge.source, edge.target, title=edge.label, **tint)

    meta = {
        node.id: {
            "kind": node.kind,
            "label": node.label,
            "moxfield": _moxfield_url(node),
        }
        for node in subgraph.nodes
    }
    return _with_details_panel(net.generate_html(notebook=False), meta)


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
