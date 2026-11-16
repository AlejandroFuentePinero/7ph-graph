import json
import re

import pytest
from pyvis.network import Network

from graph7ph.palette import CATEGORICAL
from graph7ph import render
from graph7ph.query import Edge, Node, Subgraph
from graph7ph.render import colour_key, node_colours, render_subgraph
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


def test_a_graph_document_loads_nothing_from_outside_the_app():
    """pyvis's template also asks a CDN for Bootstrap, a stylesheet and a script the
    graph uses three rules of (issue #312). A visitor's browser fetched both for every
    graph drawn, from a host the deployment does not control. A Deck is drawn because
    its Moxfield address is the one outside address a graph leads to, and that is a
    link in the panel beside the graph (issue #330), no longer anything the document
    itself names."""
    html = render_subgraph(Subgraph(nodes=[Node("deck:d1", "Grixis", "Deck")], edges=[]))

    loaded = re.findall(r'<(?:link|script)\b[^>]*\b(?:href|src)\s*=\s*"((?:https?:)?//[^"]*)"', html)
    assert loaded == []
    assert "moxfield.com" not in html


def test_a_pyvis_template_that_loads_another_outside_file_fails_loudly(monkeypatch):
    """The tags are removed by name, so a pyvis that started loading something new
    would bring a third party back with every test above still green. The render
    refuses the document instead, naming what it found."""
    generate = Network.generate_html

    def with_one_more(self, **kwargs):
        return generate(self, **kwargs).replace(
            "</head>", '<script src="https://cdn.example/extra.js"></script></head>', 1)

    monkeypatch.setattr(Network, "generate_html", with_one_more)

    with pytest.raises(RuntimeError, match="cdn.example/extra.js"):
        render_subgraph(Subgraph(nodes=[Node("deck:d1", "Grixis", "Deck")], edges=[]))


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
    # Both kinds present are named, in the fixed slot order, each with the colour
    # its nodes are drawn in; a kind absent from the graph is not keyed.
    assert colour_key(sub) == [("Deck", CATEGORICAL[0]), ("Pilot", CATEGORICAL[1])]
    assert node_colours(sub) == {"pilot:p": CATEGORICAL[1], "deck:d1": CATEGORICAL[0]}


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
    # Slots 1-2 for the groups, each named by its pilot, never by the raw group id.
    assert colour_key(sub) == [
        ("Ada Lovelace", CATEGORICAL[0]), ("Bob Carter", CATEGORICAL[1])]
    # And every node of a chain is drawn in its pilot's colour, whatever its kind.
    assert node_colours(sub) == {
        "pilot:ada": CATEGORICAL[0], "deck:a": CATEGORICAL[0],
        "pilot:bob": CATEGORICAL[1], "deck:b": CATEGORICAL[1],
    }


def test_the_co_occurrence_view_is_keyed_even_though_no_node_names_its_groups():
    # AC (#85, criterion 11): "the graph has a colour key and node kind is not encoded
    # by colour alone". The co-occurrence views are grouped, so colour there carries
    # the group rather than the kind (every node is a Card) -- and they were the one
    # place that drew no key at all, because the key named a group only by its anchor
    # node and these groups have none: a card's id is never `seed:...` or `cooccur`.
    # A `seed:` bucket holds exactly the seed card, so the card names it; the partner
    # bucket is a set, so it is named for what the set means.
    sub = Subgraph(
        nodes=[
            Node("card:sol ring", "Sol Ring", "Card", group="seed:sol ring"),
            Node("card:mana crypt", "Mana Crypt", "Card", group="cooccur"),
            Node("card:mox diamond", "Mox Diamond", "Card", group="cooccur"),
        ],
        edges=[],
    )
    # The partner set is named for what it is and the seed names its own bucket, in
    # group order, so no internal group value reaches the screen as a chip.
    assert [name for name, _ in colour_key(sub)] == ["Played alongside", "Sol Ring"]


def test_a_grouped_key_leaves_out_a_group_it_cannot_name():
    # The naming rule refuses rather than guesses: a multi-member group with no anchor
    # node and no written name has nothing honest to call itself, so it is dropped from
    # the key instead of surfacing a raw id. The groups that *can* be named still are.
    sub = Subgraph(
        nodes=[
            Node("pilot:ada", "Ada Lovelace", "Pilot", group="pilot:ada"),
            Node("deck:x", "Grixis", "Deck", group="mystery"),
            Node("deck:y", "Boros", "Deck", group="mystery"),
        ],
        edges=[],
    )
    assert [name for name, _ in colour_key(sub)] == ["Ada Lovelace"]


def test_the_pyvis_document_takes_its_height_from_its_frame():
    # AC (#85, criterion 12): no fixed 700/760px letterbox. The frame around this
    # document is a responsive clamp (`app._embed`), which only works if the document
    # stretches to it, so pyvis is told 100% and no 700px survives to fight it.
    html = render_subgraph(Subgraph(nodes=[Node("deck:d1", "Grixis", "Deck")], edges=[]))
    assert "700px" not in html


def test_the_document_is_the_graph_alone():
    # Issue #330: the key sat over the graph and the answer to a click under it, at
    # the far edge of a frame taller than most windows. Both are the page's now, in
    # the panel beside the frame, so the document holds neither and every pixel of
    # the frame is the graph's. What a node answers with is the panel's to say; the
    # document only says which node was clicked.
    html = render_subgraph(Subgraph(
        nodes=[Node("deck:abc123", "Grixis", "Deck", event="PogMay26")], edges=[]))
    assert "graph-legend" not in html and "legend-chip" not in html
    assert "node-details" not in html and "PogMay26" not in html
    assert ".graph-panel" in html  # where it looks for the panel that answers


def test_a_visible_rate_edge_hovers_its_own_counts():
    # #211: the usage archetype tier keeps every slice, however thin, because an
    # adoption rate is exact at any denominator; what the reader must not be denied
    # is the denominator itself. A drawn "100%" over 1 deck and one over 386 are
    # identical pixels, so a visible rate edge carries its counts as the hover, in
    # the one sample-size form (numfmt.count_of), the way the trend chart's hover
    # carries each cell's n (ADR 0013, #145 amendment).
    sub = Subgraph(
        nodes=[
            Node("card:troll", "Troll of Khazad-dûm", "Card"),
            Node("macro:midrange", "midrange", "Macro"),
            Node("arch:golgari", "Golgari", "Archetype"),
        ],
        edges=[
            Edge("card:troll", "macro:midrange", "12%", visible=True, decks=120, total_decks=1000),
            Edge("macro:midrange", "arch:golgari", "88%", visible=True, decks=7, total_decks=8),
        ],
    )
    html = render_subgraph(sub)
    drawn = json.loads(re.search(r"edges = new vis\.DataSet\((\[.*?\])\)", html, re.S).group(1))
    by_target = {edge["to"]: edge for edge in drawn}
    assert by_target["arch:golgari"]["label"] == "88%"  # the drawn percent is untouched
    assert by_target["arch:golgari"]["title"] == "7 / 8 decks"
    assert by_target["macro:midrange"]["title"] == "120 / 1,000 decks"


def test_the_count_hover_rides_only_a_visible_edge_with_a_ratio_behind_it():
    # The hover rides only where both halves hold. An invisible edge's label is
    # already its hover (a co-occurrence percent), so its counts stay numbers for
    # a consumer, and a visible edge with no ratio behind it has nothing to add.
    sub = Subgraph(
        nodes=[Node("card:a", "Sol Ring", "Card"), Node("card:b", "Mana Crypt", "Card")],
        edges=[
            Edge("card:a", "card:b", "34%", decks=12, total_decks=35),
            Edge("card:b", "card:a", "OWNS", visible=True),
        ],
    )
    html = render_subgraph(sub)
    drawn = json.loads(re.search(r"edges = new vis\.DataSet\((\[.*?\])\)", html, re.S).group(1))
    invisible, visible = drawn
    assert invisible["title"] == "34%" and "label" not in invisible
    assert visible["label"] == "OWNS" and "title" not in visible


def test_the_settle_ships_in_the_document_with_its_substitutions_resolved():
    # The fit-and-label settle (#178) runs only on a real browser, which CI does not
    # carry (tests/test_graph_desktop.py skips without playwright), so this holds the
    # string half: the script is injected and both its placeholders are resolved. A
    # physics view waits for stabilisation; a fully pinned view, which never
    # stabilises, settles immediately.
    physics = render_subgraph(Subgraph(nodes=[Node("deck:d1", "Grixis", "Deck")], edges=[]))
    pinned = render_subgraph(Subgraph(
        nodes=[Node("deck:d1", "Grixis", "Deck", pin=(0.0, 0.0))], edges=[],
    ))

    for html in (physics, pinned):
        assert "const LABEL_PX = 12;" in html
        assert "__LABEL_PX__" not in html and "__PINNED__" not in html
    assert "const PINNED = false;" in physics
    assert "const PINNED = true;" in pinned


def test_a_label_is_haloed_in_the_ground_so_crossed_labels_stay_readable():
    # A dense cluster draws its labels over one another, and two names in the same
    # colour on the same ground melt into one unreadable smear. A halo in the ground's
    # own colour cuts each label out of whatever is behind it, so the top one reads and
    # the layering is visible. It is drawn, not laid out: no node moves and physics is
    # never consulted (ADR 0018), so it costs no relayout on any view.
    html = render_subgraph(Subgraph(nodes=[Node("deck:d1", "Grixis", "Deck")], edges=[]))

    assert f"const HALO_PX = {render.HALO_PX};" in html
    assert "__HALO_PX__" not in html
    # Sized off the same zoom as the label it surrounds: vis.js measures both in canvas
    # units, so a halo left at a fixed width would thicken as a graph zoomed out and
    # swallow the very labels it is there to separate.
    assert "strokeWidth: HALO_PX / zoom" in html
    assert f"strokeColor: '{TOKENS['surface']}'" in html


def test_a_label_drawn_inside_its_node_takes_no_halo():
    # The halo is the ground's colour, which is only what is behind a label drawn ON
    # the ground. A shape override draws the label inside the node instead, over a
    # categorical fill, where a ground-coloured stroke outlines the text in something
    # that is not behind it. So a shaped node carries its own `strokeWidth: 0`: vis
    # takes each font key from the most specific source that sets it, so the node wins
    # the stroke while its size still follows the zoom the settle sets globally.
    html = render_subgraph(Subgraph(
        nodes=[
            Node("card:a", "Sol Ring", "Card"),
            Node("both:a|b", "Both · 9 decks", "Card", shape="circle"),
        ],
        edges=[],
    ))

    drawn = json.loads(re.search(r"new vis\.DataSet\((\[.*?\])\)", html, re.S).group(1))
    haloed = {node["id"]: node.get("font", {}).get("strokeWidth") for node in drawn}
    assert haloed == {"card:a": None, "both:a|b": 0}
