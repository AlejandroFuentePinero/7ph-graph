from graph7ph.query import Edge, Node, Subgraph
from graph7ph.render import render_subgraph
from graph7ph.serve import VIS_CSS_URL, VIS_JS_URL


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


def test_render_points_at_the_hosted_library_instead_of_carrying_it():
    """Every Explore result used to ship the whole vis.js library inlined, ~795 KB
    of it, on a stream that cannot be compressed or cached. It is now referenced by
    URL and served once by the app itself (issue #97), and still by the app itself
    rather than by a CDN, so the graph depends on nothing outside the deployment."""
    html = render_subgraph(Subgraph(nodes=[Node("deck:d1", "Grixis", "Deck")], edges=[]))

    assert VIS_JS_URL in html
    assert VIS_CSS_URL in html
    assert "cdnjs.cloudflare.com" not in html
    # The library alone is ~690 KB, so a result this size cannot be carrying it,
    # with room to spare for the largest graph the render threshold allows.
    assert len(html) < 50_000


def test_render_empty_subgraph_is_still_valid_html():
    html = render_subgraph(Subgraph(nodes=[], edges=[]))
    assert "<html" in html.lower()
