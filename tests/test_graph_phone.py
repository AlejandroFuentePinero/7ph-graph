"""What the graph document actually draws at phone width, measured on a real browser.

Issue #161: at 390 CSS pixels the graph drew 67 bare coloured dots and not one
label, so a node's identity was carried by hue alone. Nothing in the emitted HTML
says so. vis.js sizes a label in canvas units and multiplies by the zoom, then drops
the label entirely once that product falls under a floor of its own, so whether a
label reaches the reader is decided at paint time by numbers no string assertion can
see. These tests ask the running widget instead.

Chromium at a 390-pixel viewport, the width the v1 PRD (#85) names as the primary
device. Skipped rather than failed where playwright or its browser is missing, the
same call ``conftest.live_graph`` makes for the artifact: this is a browser harness
the ordinary suite does not carry, run with::

    uv run --with playwright python -m pytest tests/test_graph_phone.py

after a one-off ``uv run --with playwright python -m playwright install chromium``.
"""

import http.server
import re
import socket
import threading
from pathlib import Path

import pytest

from graph7ph.query import Edge, Node, Subgraph
from graph7ph.render import LABEL_PX, render_subgraph
from graph7ph.serve import VIS_CSS_URL, VIS_JS_URL
from graph7ph.theme import TOKENS, build_css, contrast_ratio

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

import pyvis  # noqa: E402  (only reached once playwright is known to be installed)

_LIB = Path(pyvis.__file__).parent / "lib" / "vis-9.1.2"

# The interior of the graph frame on a 390x844 phone: `app._embed` sizes the iframe
# at `clamp(420px, 72vh, 760px)`, which on that viewport is 72vh = 608px.
PHONE = {"width": 390, "height": 608}

# The theme's smallest type role, read off the stylesheet rather than restated: a label
# under it is not "small", it is below anything the design asks a reader to read at all.
THEME_MIN_PX = min(float(px) for px in re.findall(r"font-size:\s*([\d.]+)px", build_css()))


def _serve(doc: str):
    """Serve one rendered graph document, with the vis.js library it names.

    The library URLs are root-relative (``render._hosted_library``), so the document
    has to be fetched over HTTP from the root of an origin rather than opened as a
    file: served from a file it would draw with no library at all, and the tests
    below would measure an empty canvas.
    """
    class Handler(http.server.BaseHTTPRequestHandler):
        routes = {
            "/": (doc.encode(), "text/html"),
            VIS_JS_URL: ((_LIB / "vis-network.min.js").read_bytes(), "application/javascript"),
            VIS_CSS_URL: ((_LIB / "vis-network.css").read_bytes(), "text/css"),
        }

        def log_message(self, *args):
            pass  # a passing test should say nothing

        def do_GET(self):
            found = self.routes.get(self.path)
            if found is None:
                self.send_error(404)
                return
            body, content_type = found
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{port}/"


# What the drawn graph reports about itself: the on-screen size of every node's label,
# the ink it is drawn in, and how much of the canvas the fitted layout covers. Read off
# the live widget after it has settled, which is the only place these numbers exist.
#
# The extent is walked here rather than asked of `render._SETTLE`'s own `drawnBox`, which
# computes the same thing: a measurement taken from the code under test can only ever
# agree with it, and what these tests are for is disagreeing with it.
_MEASURE = """
() => {
  const scale = network.getScale();
  const canvas = network.canvas.frame.canvas;
  const box = {left: Infinity, right: -Infinity, top: Infinity, bottom: -Infinity};
  const labels = [];
  for (const id of network.body.nodeIndices) {
    const node = network.body.nodes[id];
    const font = node.labelModule.fontOptions;
    labels.push({id, text: node.labelModule.elementOptions.label,
                 px: font.size * scale, colour: font.color});
    const bounds = node.shape.boundingBox;
    box.left = Math.min(box.left, bounds.left);
    box.right = Math.max(box.right, bounds.right);
    box.top = Math.min(box.top, bounds.top);
    box.bottom = Math.max(box.bottom, bounds.bottom);
  }
  const topLeft = network.canvasToDOM({x: box.left, y: box.top});
  const bottomRight = network.canvasToDOM({x: box.right, y: box.bottom});
  return {
    labels,
    at: network.getPositions(),
    saidTooNarrow: !document.getElementById("too-narrow").hidden,
    dropFloor: network.nodesHandler.options.scaling.label.drawThreshold - 1,
    widthUsed: (bottomRight.x - topLeft.x) / canvas.clientWidth,
    heightUsed: (bottomRight.y - topLeft.y) / canvas.clientHeight,
  };
}
"""


# What a Gradio tab does to the panel it is hiding, which is where the graph lives.
_HIDE_FROM_THE_START = """
document.addEventListener('DOMContentLoaded', () => {
  const rule = document.createElement('style');
  rule.id = 'hide-frame';
  rule.textContent = 'body { display: none !important; }';
  document.head.appendChild(rule);
});
"""


@pytest.fixture(scope="module")
def browser():
    """A Chromium, or a skip naming how to install one."""
    with sync_playwright() as play:
        try:
            engine = play.chromium.launch()
        except Exception as missing:  # noqa: BLE001 - any launch failure is the same skip
            pytest.skip(f"no Chromium to measure the graph on: {missing}")
        yield engine
        engine.close()


@pytest.fixture
def drawn(browser):
    """Draw a subgraph at phone width and hand back what the widget reports.

    The settle is generous on purpose: the physics layout runs to a thousand
    iterations before anything is fitted, and a measurement taken early would grade
    the pre-stabilisation scatter rather than the graph a reader sees.
    """
    def measure(subgraph: Subgraph, then_resize_to: dict | list | None = None,
                then_hide_and_show: bool = False, hidden_while_drawing: bool = False) -> dict:
        server, url = _serve(render_subgraph(subgraph))
        try:
            page = browser.new_page(viewport=PHONE)
            if hidden_while_drawing:
                # Hidden from the first frame, so the graph's own first settle is the one
                # that happens with no frame to settle against. Applied as a stylesheet on
                # ``DOMContentLoaded`` rather than after ``goto`` returns, because pyvis
                # draws from an inline script at the end of the body: by the time the page
                # has loaded the settle has already been scheduled, and hiding then is a
                # race this needs to win every run.
                page.add_init_script(_HIDE_FROM_THE_START)
            page.goto(url)
            page.wait_for_timeout(9000)
            if hidden_while_drawing:
                page.evaluate("() => { document.getElementById('hide-frame').remove(); }")
                page.wait_for_timeout(4000)
            sizes = [then_resize_to] if isinstance(then_resize_to, dict) else then_resize_to
            for size in sizes or []:
                page.set_viewport_size(size)
                page.wait_for_timeout(2500)  # vis.js notices a new size on its own
            if then_hide_and_show:
                # What a Gradio tab does to the panel it is hiding, which is where the
                # graph lives: display:none, so the canvas reports no size at all.
                page.evaluate("() => { document.body.style.display = 'none'; }")
                page.wait_for_timeout(2000)
                page.evaluate("() => { document.body.style.display = 'flex'; }")
                page.wait_for_timeout(2500)
            return page.evaluate(_MEASURE)
        finally:
            server.shutdown()

    return measure


def pilot_neighbourhood(events: int = 22) -> Subgraph:
    """A pilot neighbourhood the size and shape of the one #161 was filed against.

    One pilot, and off them an event, a deck and a placement per entry: 67 nodes at 22
    events, which is Ariel M's record in the shipped corpus, and 250 at 83, which is the
    widest graph the app will draw before it refuses (``explore.assess``).

    Hand-authored rather than queried so the reproduction does not need the artifact, and
    the labels are real ones because their length is what decides whether a label fits
    beside its node. A deck here is "Grixis", not a Moxfield title: the pilot views label
    a deck by ``deckName``, its own short name, where the gems view uses ``name``, the
    whole title. That difference is the whole of why one view can be named at this width
    and the other cannot.
    """
    nodes = [Node("pilot:ariel", "Ariel M", "Pilot")]
    edges = []
    names = ("Grixis", "Jeskai Control", "Kiki Pod", "Mono Red", "4c Aristocrats")
    for i in range(events):
        event, deck, place = f"event:e{i}", f"deck:d{i}", f"placement:d{i}"
        nodes += [
            Node(event, f"SydneyShowdown{i}", "Event"),
            Node(deck, names[i % len(names)], "Deck"),
            Node(place, f"{i + 1}th", "Placement"),
        ]
        edges += [
            Edge("pilot:ariel", event, "PLAYED_AT"),
            Edge(event, deck, "ENTERED"),
            Edge(deck, place, "PLACED"),
        ]
    return Subgraph(nodes=nodes, edges=edges)


def card_cooccurrence(shared: int = 15) -> Subgraph:
    """The two-seed co-occurrence view's composed layout, at the size it ships at.

    The other shape a graph view comes in, and the one the settle has to treat
    differently: every node is pinned, so physics never runs and never announces
    itself stabilised, and the hub is a ``circle`` whose size follows its label. The
    coordinates are ``query``'s own (``_SEED_X`` and friends), since it is that
    composition, not a physics cloud, that the settle must leave alone.
    """
    nodes = [
        Node("card:a", "Snapcaster Mage", "Card", group="seed:a", pin=(-800.0, 150.0)),
        Node("card:b", "Lightning Bolt", "Card", group="seed:b", pin=(-800.0, -150.0)),
        Node("both:a|b", "Both · 214 decks", "Intersection", group="cooccur",
             shape="circle", pin=(-350.0, 0.0)),
    ]
    edges = [Edge("card:a", "both:a|b", "31%", visible=True),
             Edge("card:b", "both:a|b", "44%", visible=True)]
    for i in range(shared):
        nodes.append(Node(f"card:s{i}", f"Shared Card {i}", "Card", group="cooccur",
                          pin=(300.0, (i - (shared - 1) / 2) * 80.0)))
        edges.append(Edge("both:a|b", f"card:s{i}", "62%", visible=True))
    return Subgraph(nodes=nodes, edges=edges)


@pytest.mark.parametrize("events, nodes", [(22, 67), (83, 250)])
def test_every_node_carries_a_legible_label_at_phone_width(drawn, events, nodes):
    """AC (#161): at 390 CSS px every drawn node carries its label, and a label is
    legible: it clears the theme's smallest type size rather than merely clearing
    vis.js's own floor for dropping one.

    The bug: a 67-node graph fits at a zoom of 0.23, which took the 14px label to
    3.3px on screen, under the 4px at which vis.js stops drawing labels at all. Every
    node came out a bare coloured dot.

    Held at both ends of the range a graph can be drawn at, because the label size and
    the zoom are solved for each other a pass at a time and the loop is bounded: at the
    250-node ceiling it has the furthest to travel and the fewest passes to spare.
    """
    measured = drawn(pilot_neighbourhood(events))

    assert len(measured["labels"]) == nodes
    for label in measured["labels"]:
        assert label["text"], f"{label['id']} has no label to draw"
        assert label["px"] > measured["dropFloor"], (
            f"{label['id']}'s label renders at {label['px']:.2f}px, under the "
            f"{measured['dropFloor']}px at which vis.js stops drawing it"
        )
        assert label["px"] >= THEME_MIN_PX, (
            f"{label['id']}'s label renders at {label['px']:.2f}px, under the "
            f"theme's smallest type size ({THEME_MIN_PX}px)"
        )
        # And at the size asked for, which is the post-condition the settle's bounded
        # loop has no other way of stating: it stops after eight passes whether the zoom
        # and the label size have finished agreeing or not.
        assert label["px"] == pytest.approx(LABEL_PX, rel=0.05)


def test_the_composed_co_occurrence_view_is_labelled_without_being_reshaped(drawn):
    """AC (#161): co-occurrence carries its labels at 390 CSS px too.

    Its layout reaches the settle by the other route: nothing is left for physics to
    solve, so the widget never announces a stabilised layout to wait for.

    Its positions are also a composition someone chose rather than one physics landed
    on, so what is held here is the composition and not the coordinates. Filling the
    frame stretches one axis, which moves the rows apart without disturbing the three
    columns, their order, or the symmetry of the two seeds about the hub, and buying
    the frame with any of those would be the wrong trade.
    """
    measured = drawn(card_cooccurrence())
    at = measured["at"]

    for label in measured["labels"]:
        assert label["px"] >= THEME_MIN_PX, (
            f"{label['id']}'s label renders at {label['px']:.2f}px"
        )
    # The three columns, in their composed order, each still a column.
    assert at["card:a"]["x"] == at["card:b"]["x"] == -800
    assert at["both:a|b"]["x"] == -350
    rows = [at[f"card:s{i}"] for i in range(15)]
    assert {row["x"] for row in rows} == {300}
    # The hub still sits level with the two seeds it joins, and the shared cards still
    # run down their column in order, evenly spaced. Within a pixel, since vis.js holds
    # a position as a whole number.
    seeds = (at["card:a"]["y"] + at["card:b"]["y"]) / 2
    assert at["both:a|b"]["y"] == pytest.approx(seeds, abs=1)
    gaps = [b["y"] - a["y"] for a, b in zip(rows, rows[1:])]
    assert all(gap == pytest.approx(gaps[0], abs=1) and gap > 0 for gap in gaps)


def test_an_unweighted_node_among_weighted_ones_is_labelled_too(drawn):
    """The archetype affinity view sizes its archetypes and macros by how many events
    the pilot took them to, and leaves the pilot at the hub unweighted.

    vis.js restores an unweighted node's font to the size it was created at whenever
    any node in the graph carries a weight, so a label size applied to the graph as a
    whole reaches every weighted node and silently misses the rest. On this view that
    is the one node the whole graph is about, drawn at 7px beside its 12px neighbours.

    Held as one size across the graph and not only as a floor: on a sparse graph the
    zoom lands near 1, where the size a node was created at clears the floor by luck,
    and the miss would then show up only on the dense graphs that need the fix most.
    """
    hub = Node("pilot:ada", "Ada Lovelace", "Pilot")
    macros = [Node(f"macro:m{i}", f"Jeskai Midrange {i}", "Macro", weight=i + 1)
              for i in range(19)]
    weighted = Subgraph(
        nodes=[hub, *macros],
        edges=[Edge(hub.id, macro.id, "PLAYED") for macro in macros],
    )

    labels = {label["id"]: label["px"] for label in drawn(weighted)["labels"]}

    assert labels["pilot:ada"] >= THEME_MIN_PX
    assert labels["pilot:ada"] == pytest.approx(max(labels.values()), rel=0.02), (
        f"the unweighted hub is drawn at {labels['pilot:ada']:.1f}px among labels of "
        f"{max(labels.values()):.1f}px"
    )


def test_a_view_that_cannot_name_its_nodes_says_so_instead_of_smearing(drawn):
    """AC (#161): where a view genuinely cannot label every node at phone width, the
    fallback is stated on screen rather than left silent.

    The hidden gems view names each node with a whole deck title ("051st Ben F -
    Academy Shops - WAC"), which at 390 CSS px is 61 percent of the frame for one name
    where every other view's is 10 to 21. Drawn anyway, its titles lie on top of each
    other and leave the graph less readable than the bare dots #161 started from: a name
    that wide cannot go beside anything, so the view says so and sends the reader to the
    details panel, which answers for one node at a time.
    """
    titled = Subgraph(
        nodes=[Node(f"deck:d{i}", f"{i}51st Ben F - Academy Shops - WAC", "Deck")
               for i in range(20)],
        edges=[Edge("deck:d0", f"deck:d{i}", "SHARES") for i in range(1, 20)],
    )

    measured = drawn(titled)

    assert measured["saidTooNarrow"], "unreadable titles were drawn with nothing said"
    assert all(label["px"] == 0 for label in measured["labels"]), (
        "the fallback was stated but the smear was drawn under it anyway"
    )


def test_a_rotated_phone_refills_its_new_frame(drawn):
    """AC (#161) holds past the first paint: a link shared into a chat is opened on a
    phone that gets turned sideways.

    vis.js redraws a resized canvas at the zoom it already had and never refits, so the
    graph would keep the shape of the frame it was born in: fitted to a tall frame and
    then turned on its side, it would sit in a band across the middle of a wide one.
    """
    landscape = {"width": 844, "height": 390}

    measured = drawn(pilot_neighbourhood(), then_resize_to=landscape)

    assert measured["heightUsed"] >= 0.8, (
        f"after the turn the graph covers {measured['heightUsed']:.0%} of its frame's "
        "height, so it is still fitted to the frame it started in"
    )
    assert measured["widthUsed"] >= 0.8
    assert all(label["px"] >= THEME_MIN_PX for label in measured["labels"])


def test_a_graph_on_a_hidden_tab_survives_being_shown_again(drawn):
    """Every tab's graph stays in the document, so opening another tab hides this one's
    frame rather than closing it, and coming back shows it.

    A hidden frame has no size, and a graph settled against one has no size to fit to:
    a zoom of zero, a label sized by dividing by it, and a stretch onto a frame whose
    proportions are zero over zero. So the settle waits for a frame that is there.
    """
    measured = drawn(pilot_neighbourhood(), then_hide_and_show=True)

    for label in measured["labels"]:
        assert label["px"] >= THEME_MIN_PX, (
            f"{label['id']}'s label came back at {label['px']:.2f}px"
        )
    for id_, at in measured["at"].items():
        assert at["x"] == at["x"] and at["y"] == at["y"], f"{id_} was moved to {at}"
    assert measured["heightUsed"] >= 0.8 and measured["widthUsed"] >= 0.8


def test_a_graph_that_settles_while_its_tab_is_hidden_still_draws(drawn):
    """The other way a graph meets a frame of no size, and the one that ruins it.

    Stabilising a layout is a thousand iterations and seconds of work, and a reader who
    presses Draw and switches tabs while it runs leaves the graph to settle against a
    hidden frame. Fitting to nothing gives a zoom of nearly zero, so the label size
    divides by it, and asking what shape a 0 by 0 frame is gives NaN, which the stretch
    then writes into every node's position. That one is permanent: the tab comes back to
    a graph with no nodes left to draw, and no later resize can recover them.
    """
    measured = drawn(pilot_neighbourhood(), hidden_while_drawing=True)

    for id_, at in measured["at"].items():
        assert at["x"] == at["x"] and at["y"] == at["y"], f"{id_} was left at {at}"
    for label in measured["labels"]:
        # Held at the size the renderer asks for, not merely above the floor: settling
        # against no frame divides by a zoom of almost nothing, and what comes back can
        # be a wrong size that still happens to be a readable one.
        assert label["px"] == pytest.approx(LABEL_PX, rel=0.05), (
            f"{label['id']}'s label came back at {label['px']:.2f}px, not {LABEL_PX}px"
        )
    assert measured["heightUsed"] >= 0.8 and measured["widthUsed"] >= 0.8


def test_the_stated_fallback_does_not_set_the_settle_off_again(drawn):
    """Stating the fallback changes the frame, and the frame changing is what asks for
    another settle: the notice is a row in the same flex column as the canvas, so showing
    it takes ~34px off the graph's height and hiding it gives them back.

    vis.js resizes its canvas at the end of every ``setOptions`` and announces it there
    and then, so a settle that shows the notice announces a new frame from inside itself,
    and the next settle hides the notice and announces another. Left alone the two take
    turns forever, eight fits at a time, until the tab stops responding.
    """
    titled = Subgraph(
        nodes=[Node(f"deck:d{i}", f"{i}51st Ben F - Academy Shops - WAC", "Deck")
               for i in range(20)],
        edges=[Edge("deck:d0", f"deck:d{i}", "SHARES") for i in range(1, 20)],
    )

    # Taller, not wider: the frame has to stay one the names do not fit in, or the
    # fallback would rightly go away and there would be no second settle to take turns
    # with the first.
    measured = drawn(titled, then_resize_to={"width": PHONE["width"], "height": 500})

    assert measured["saidTooNarrow"]
    assert all(label["px"] == 0 for label in measured["labels"])


def test_a_view_that_can_name_its_nodes_says_nothing(drawn):
    """The fallback stays out of the way of the views that do fit their names, which is
    every other one: no notice, and the labels drawn."""
    measured = drawn(pilot_neighbourhood())

    assert not measured["saidTooNarrow"]


def test_a_graph_that_is_a_line_is_not_fanned_out_to_fill_the_frame(drawn):
    """Filling the frame is bounded: a layout is never pulled further from its own
    shape than the frame is out of square.

    A pilot with one event is four nodes in a chain, and physics lays a chain out as a
    line. Stretched without a bound to a portrait frame's proportions, such a graph
    would be fanned into a frame-shaped cloud: structure invented out of what is only
    the jitter in where physics dropped each node. Ground to spare is the honest answer
    there, and this holds the graph to it: a line drawn nearly flat stays nearly flat.

    Turned twice on the way, because the bound has to be on the layout that was solved
    and not on the one the last settle left behind. Spent afresh on each new frame it
    would buy the same stretch again every time, and a chain would be fanned out a
    little further with every rotation until it was the cloud.
    """
    drawn_at = 20.0  # the chain is laid out at y = +/- 10, so its own spread is 20
    chain = Subgraph(
        nodes=[Node(f"n{i}", f"Node {i}", "Deck", pin=(i * 100.0, 10.0 if i % 2 else -10.0))
               for i in range(10)],
        edges=[Edge(f"n{i}", f"n{i + 1}", "NEXT") for i in range(9)],
    )

    measured = drawn(chain, then_resize_to=[{"width": 844, "height": 390}, PHONE])

    assert measured["widthUsed"] >= 0.8  # it fills the axis it genuinely spans
    assert measured["heightUsed"] < 0.5, (
        f"a flat chain was fanned out over {measured['heightUsed']:.0%} of the frame's "
        "height, inventing structure out of layout jitter"
    )
    # Measured against the layout it was given rather than against the frame, because
    # the frame hides the compounding: three settles stretching a chain five times over
    # still leave a fitted graph too flat to fail the check above. A phone frame is under
    # twice as tall as it is wide, so one capped stretch cannot double this spread and
    # nothing here should have applied more than one.
    spread = max(at["y"] for at in measured["at"].values()) - min(
        at["y"] for at in measured["at"].values())
    assert spread <= 2 * drawn_at, (
        f"two turns of the phone pulled the chain from a spread of {drawn_at:.0f} to "
        f"{spread:.0f}, so each frame is spending the cap again on the last one's work"
    )


def test_a_label_clears_wcag_aa_against_the_graph_ground(drawn):
    """AC (#161): a label at phone width clears WCAG AA against the graph ground.

    Measured on the ink vis.js actually draws with rather than the token the renderer
    passed, and against the AA floor for ordinary text: `LABEL_PX` is well under the
    18.66px at which the 3:1 large-text floor would apply, so a label restored at that
    size has to clear 4.5:1 to have been worth restoring.
    """
    for label in drawn(pilot_neighbourhood())["labels"]:
        ratio = contrast_ratio(label["colour"], TOKENS["surface"])
        assert ratio >= 4.5, f"{label['id']}'s label is {ratio:.1f}:1 on the graph ground"


def test_the_fitted_graph_uses_the_whole_frame_not_its_middle(drawn):
    """AC (#161): the fitted graph uses its frame's height rather than clustering into
    the middle third of it, with empty ground above and below.

    A phone frame is portrait and a force layout comes out round, so a fit that
    preserves the layout's own aspect can only ever fill the narrower axis: on the
    graph #161 was filed against that was 65 percent of the height. Both axes are held
    here, since filling one by starving the other is the same fault turned sideways.
    """
    measured = drawn(pilot_neighbourhood())

    assert measured["heightUsed"] >= 0.8, (
        f"the drawn graph covers {measured['heightUsed']:.0%} of the frame's height"
    )
    assert measured["widthUsed"] >= 0.8, (
        f"the drawn graph covers {measured['widthUsed']:.0%} of the frame's width"
    )
