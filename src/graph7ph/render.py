"""Render a Subgraph to a standalone interactive pyvis widget (HTML string).

vis.js assets are inlined so the returned HTML is self-contained and can be
dropped straight into a Gradio ``HTML`` component.
"""

from pyvis.network import Network

from graph7ph.query import Subgraph

_COLOURS = {
    "Pilot": "#e15759",
    "Deck": "#4e79a7",
    "Card": "#59a14f",
    "Archetype": "#f28e2b",
}


def render_subgraph(subgraph: Subgraph) -> str:
    net = Network(
        height="700px", width="100%", directed=True, cdn_resources="in_line"
    )
    for node in subgraph.nodes:
        net.add_node(
            node.id,
            label=node.label,
            title=f"{node.kind}: {node.label}",
            color=_COLOURS.get(node.kind),
        )
    for edge in subgraph.edges:
        net.add_edge(edge.source, edge.target, title=edge.label)

    return net.generate_html(notebook=False)
