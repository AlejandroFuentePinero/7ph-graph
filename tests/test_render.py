from graph7ph.palette import CATEGORICAL
from graph7ph.query import Edge, Node, Subgraph
from graph7ph.render import render_subgraph
from graph7ph.serve import VIS_CSS_URL, VIS_JS_URL
from graph7ph.theme import TOKENS


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


def test_nodes_draw_from_the_shared_categorical_palette():
    # §7: node kinds take their colour from the one eight-hue set the charts also
    # draw from, in a fixed by-kind order, so a Deck is the same slot everywhere.
    # The retired Tableau set that used to live in render.py is gone.
    html = render_subgraph(Subgraph(nodes=[Node("deck:d1", "Grixis", "Deck")], edges=[]))
    assert CATEGORICAL[0] in html  # Deck -> slot 1 (blue), the §5 worked example
    assert "#4e79a7" not in html  # the retired Tableau blue is no longer used


def test_graph_ground_is_the_dark_surface_not_a_white_slab():
    # §7: the graph sits on the app's own dark surface, retiring the white vis.js
    # slab and the details panel's `#333` ink from the light-background era (§1).
    html = render_subgraph(Subgraph(nodes=[Node("deck:d1", "Grixis", "Deck")], edges=[]))
    assert TOKENS["surface"] in html  # the ground is the surface token
    assert "#333" not in html  # the panel's hardcoded dark-on-white ink is gone
    assert "vis-tooltip" in html  # the vis.js hover tooltip is themed off its white default


def test_a_node_kind_colour_key_names_the_kinds_present():
    # §7 / AC: kind is not encoded by colour alone. An on-screen key names each
    # kind present beside its swatch; a kind absent from the graph is not keyed.
    sub = Subgraph(
        nodes=[Node("pilot:p", "Jordan", "Pilot"), Node("deck:d1", "Grixis", "Deck")],
        edges=[],
    )
    html = render_subgraph(sub)
    assert "graph-legend" in html
    assert "Pilot" in html and "Deck" in html  # both kinds present are named
    assert "Archetype" not in html  # a kind absent from the graph is not keyed


def test_a_grouped_view_keys_the_groups_by_friendly_name_not_raw_id():
    # §7: grouped views (head-to-head) tint by group with slots 1-3, and the key
    # names the groups so the colour reads. A group's value is an internal node id
    # (the pilot's, here `pilot:ada`), so the key must name it by its anchor node's
    # friendly label, never surface the raw id as a debug-looking chip.
    sub = Subgraph(
        nodes=[
            Node("pilot:ada", "Ada Lovelace", "Pilot", group="pilot:ada"),
            Node("deck:a", "Grixis", "Deck", group="pilot:ada"),
            Node("pilot:bob", "Bob Carter", "Pilot", group="pilot:bob"),
            Node("deck:b", "Boros", "Deck", group="pilot:bob"),
        ],
        edges=[],
    )
    html = render_subgraph(sub)
    assert CATEGORICAL[0] in html and CATEGORICAL[1] in html  # slots 1-2 for the groups
    # the chip label is the friendly name, never the raw group id
    assert "Ada Lovelace</span>" in html and "Bob Carter</span>" in html
    assert "pilot:ada</span>" not in html and "pilot:bob</span>" not in html


def test_details_panel_has_field_labels_and_the_moxfield_affordance():
    # §7 / AC: the details readout is structured (field labels, hierarchy), not
    # debug output, and a deck carries its Moxfield page as a link affordance.
    html = render_subgraph(Subgraph(nodes=[Node("deck:abc123", "Grixis", "Deck")], edges=[]))
    assert "nd-label" in html and "nd-value" in html  # structured field rows
    assert "Kind" in html and "Name" in html  # the field labels
    assert "moxfield.com/decks/abc123" in html  # the deck's page
    assert "Open on Moxfield" in html  # as a link affordance, not a raw url
