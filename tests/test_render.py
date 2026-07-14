from graph7ph.query import Edge, Node, Subgraph
from graph7ph.render import render_subgraph


def test_render_produces_html_embedding_the_nodes():
    sub = Subgraph(
        nodes=[
            Node("pilot:Jordan C", "Jordan C", "Pilot"),
            Node("deck:d1", "Grixis", "Deck"),
            Node("card:arid mesa", "Arid Mesa", "Card"),
        ],
        edges=[
            Edge("deck:d1", "pilot:Jordan C", "PILOTED_BY"),
            Edge("deck:d1", "card:arid mesa", "CONTAINS:Main"),
        ],
    )

    html = render_subgraph(sub)

    assert "<html" in html.lower()
    assert "vis-network" in html  # the interactive pyvis/vis.js widget
    for label in ("Jordan C", "Grixis", "Arid Mesa"):
        assert label in html


def test_render_empty_subgraph_is_still_valid_html():
    html = render_subgraph(Subgraph(nodes=[], edges=[]))
    assert "<html" in html.lower()
