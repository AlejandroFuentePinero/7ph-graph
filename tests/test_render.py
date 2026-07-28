import json
import re

from graph7ph.palette import CATEGORICAL
from graph7ph import render
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
    html = render_subgraph(sub)
    assert "Sol Ring</span>" in html  # the seed names its own bucket
    assert "Played alongside</span>" in html  # the partner set is named for what it is
    # and no internal group value reaches the screen as a chip
    assert "seed:sol ring</span>" not in html and "cooccur</span>" not in html


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
    html = render_subgraph(sub)
    assert "Ada Lovelace</span>" in html
    assert "mystery</span>" not in html


def test_the_pyvis_document_takes_its_height_from_its_frame():
    # AC (#85, criterion 12): no fixed 700/760px letterbox. The frame around this
    # document is a responsive clamp (`app._embed`), which only works if the document
    # stretches to it, so pyvis is told 100% and no 700px survives to fight it.
    html = render_subgraph(Subgraph(nodes=[Node("deck:d1", "Grixis", "Deck")], edges=[]))
    assert "700px" not in html


def test_details_panel_has_field_labels_and_the_moxfield_affordance():
    # §7 / AC: the details readout is structured (field labels, hierarchy), not
    # debug output, and a deck carries its Moxfield page as a link affordance.
    html = render_subgraph(Subgraph(nodes=[Node("deck:abc123", "Grixis", "Deck")], edges=[]))
    assert "nd-label" in html and "nd-value" in html  # structured field rows
    assert "Kind" in html and "Name" in html  # the field labels
    assert "moxfield.com/decks/abc123" in html  # the deck's page
    assert "Open on Moxfield" in html  # as a link affordance, not a raw url


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
