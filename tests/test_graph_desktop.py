"""What the graph document actually draws at desktop width, measured on a real browser.

Issue #178: the graph is read on a desktop (ADR 0018), and at that width its labels
were merely small instead of absent: on a 1440 CSS px frame the pilot overview painted
its names at 6.9px, and the densest view the app draws (250 nodes) at 4.4px, a hair
above the 4px at which vis.js stops drawing a label at all. Nothing in the emitted
HTML says so. vis.js sizes a label in canvas units and multiplies by the zoom, so
whether a label reaches the reader is decided at paint time by numbers no string
assertion can see. These tests ask the running widget instead.

Chromium at a 1440x810 viewport, the interior of the graph frame on a 1440x900
desktop. Alongside legibility, these tests hold the three regressions ADR 0018 names
as the price #174 paid and #178 refuses: physics stays live, node positions are never
mutated, and edge quantities keep vis.js's own size, scaled by the zoom.

Skipped rather than failed where playwright or its browser is missing, the same call
``conftest.live_graph`` makes for the artifact: this is a browser harness the ordinary
suite does not carry, run with::

    uv run python -m pytest tests/test_graph_desktop.py

after a one-off ``uv run playwright install chromium``. ``playwright`` itself is an
ordinary dev dependency and CI installs that browser before it runs the suite, so a
skip here means this checkout is missing the download, not that the gate is optional
(issue #197).
"""

import http.server
import re
import threading

import pytest

from graph7ph import serve
from graph7ph.query import Edge, Node, Subgraph
from graph7ph.render import render_subgraph
from graph7ph.theme import build_css

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

# The interior of the graph frame on a 1440x900 desktop: the widget iframe spans the
# app's width, and `app._embed` sizes its height at `clamp(525px, 90vh, 950px)`, which
# on that viewport is 90vh = 810px.
DESKTOP = {"width": 1440, "height": 810}

# The theme's smallest type role, read off the stylesheet rather than restated: a label
# under it is not "small", it is below anything the design asks a reader to read at all.
THEME_MIN_PX = min(float(px) for px in re.findall(r"font-size:\s*([\d.]+)px", build_css()))

# vis.js's own default label size in canvas units, the size every node and edge in
# these documents is created at: pyvis passes no font, so this is what an untouched
# label measures against.
VIS_DEFAULT_PX = 14

# The size a node's name should reach the reader at: the colour key's chips
# (`.legend-chip`, 12px) in the same document, so a name is never smaller than the
# key it is read beside. Stated here as the number the design asks for, not imported
# from the renderer, so the test can disagree with it.
LEGIBLE_PX = 12


def _serve(doc: str):
    """Serve one rendered graph document, with the vis.js library it names.

    The library URLs are root-relative (``render._hosted_library``), so the document
    has to be fetched over HTTP from the root of an origin rather than opened as a
    file: served from a file it would draw with no library at all, and the tests
    below would measure an empty canvas.
    """
    class Handler(http.server.BaseHTTPRequestHandler):
        # The library is served exactly as the app serves it: the URL-to-file map is
        # `serve`'s own, so what these tests measure is drawn with the bytes
        # production ships rather than a hand-kept copy of that mapping.
        routes = {"/": (doc.encode(), "text/html")} | {
            url: (path.read_bytes(),
                  "application/javascript" if path.suffix == ".js" else "text/css")
            for url, path in serve._ASSETS.items()
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

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/"


# What the drawn graph reports about itself: the on-screen size of every node label,
# whether physics is still live, where every node sits, and what the visible edge
# labels are drawn at. Read off the live widget after it has settled, which is the
# only place these numbers exist.
_MEASURE = """
() => {
  const scale = network.getScale();
  const labels = [];
  for (const id of network.body.nodeIndices) {
    const node = network.body.nodes[id];
    labels.push({id, text: node.labelModule.elementOptions.label,
                 px: node.labelModule.fontOptions.size * scale});
  }
  const edgePx = Object.values(network.body.edges)
    .filter((edge) => edge.options.label)
    .map((edge) => edge.labelModule.fontOptions.size * scale);
  const at = network.getPositions();
  const physics = network.physics.options.enabled;
  // Whether the reader was left looking at a fitted graph: a fresh fit of what is
  // currently drawn should land on the zoom already in place. Read last, since the
  // probe itself moves the view.
  network.fit();
  const fitDrift = network.getScale() / scale;
  return {
    labels,
    edgePx,
    at,
    physics,
    fitDrift,
    zoom: scale,
    readerZoom: window._readerZoom === undefined ? null : window._readerZoom,
    preZoom: window._preZoom === undefined ? null : window._preZoom,
    preFrame: window._preFrame === undefined ? null : window._preFrame,
    frame: [network.canvas.frame.canvas.clientWidth,
            network.canvas.frame.canvas.clientHeight],
    dropFloor: network.nodesHandler.options.scaling.label.drawThreshold - 1,
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
    """Draw a subgraph at desktop width and hand back what the widget reports.

    Waited on by condition, not by clock: the settle's own ``settledFrame`` says when
    it has finished, so a slow stabilisation (the 250-node view is a thousand physics
    iterations) is waited out rather than raced. The two scenarios that assert an
    *absence* (nothing refits on a tab return or a node click) keep fixed graces
    instead, since an absence has no moment at which it announces itself.
    """
    def measure(subgraph: Subgraph, then_resize_to: dict | None = None,
                then_hide_and_show: bool = False, hidden_while_drawing: bool = False,
                then_reader_zooms_and_opens_details: bool = False) -> dict:
        server, url = _serve(render_subgraph(subgraph))
        page = None
        try:
            page = browser.new_page(viewport=DESKTOP)
            if hidden_while_drawing:
                # Hidden from the first frame, so the graph's own first settle is the one
                # that happens with no frame to settle against. Applied as a stylesheet on
                # ``DOMContentLoaded`` rather than after ``goto`` returns, because pyvis
                # draws from an inline script at the end of the body: by the time the page
                # has loaded the settle has already been scheduled, and hiding then is a
                # race this needs to win every run.
                page.add_init_script(_HIDE_FROM_THE_START)
            page.goto(url)
            if hidden_while_drawing:
                # With no frame there is no fit to wait for, only that the settle ran
                # and stopped short; the real settle comes once the frame is back.
                page.wait_for_function("() => hasSettled")
                page.evaluate("() => { document.getElementById('hide-frame').remove(); }")
            page.wait_for_function("() => settledFrame !== null")
            # A grace for the physics vis.js resumes after a settle whose layout had
            # iterations left: what is measured should be the graph at rest.
            page.wait_for_timeout(1000)
            if then_reader_zooms_and_opens_details:
                # A reader reading: a wheel zoom into the middle of the graph (vis's
                # own zoom, so the widget can know the view is now theirs), then a
                # node's details opened, which grows the panel under the canvas and
                # so shrinks the canvas: a frame change from inside the document.
                page.evaluate("() => { window._preZoom = network.getScale(); }")
                page.mouse.move(DESKTOP["width"] / 2, DESKTOP["height"] / 2)
                page.mouse.wheel(0, -240)
                page.wait_for_timeout(300)  # the wheel is dispatched asynchronously
                page.evaluate("() => { window._readerZoom = network.getScale();"
                              " const c = network.canvas.frame.canvas;"
                              " window._preFrame = [c.clientWidth, c.clientHeight];"
                              " showNode(Object.keys(NODE_META)[0]); }")
                page.wait_for_timeout(2500)  # vis notices the shrunken canvas on its poll
            if then_resize_to is not None:
                fitted = page.evaluate("() => settledFrame")
                page.set_viewport_size(then_resize_to)
                # vis.js notices the new size on its own poll; the settle marks the
                # new frame done by writing it over the old one.
                page.wait_for_function("prev => settledFrame !== prev", arg=fitted)
            if then_hide_and_show:
                # A reader who has zoomed in to read a corner, then switched tab: the
                # zoom is theirs, and the frame they come back to is the one the graph
                # already settled.
                page.evaluate("() => { network.moveTo({scale: network.getScale() * 2});"
                              " window._readerZoom = network.getScale(); }")
                # What a Gradio tab does to the panel it is hiding, which is where the
                # graph lives: display:none, so the canvas reports no size at all.
                page.evaluate("() => { document.body.style.display = 'none'; }")
                page.wait_for_timeout(2000)
                page.evaluate("() => { document.body.style.display = 'flex'; }")
                page.wait_for_timeout(2500)
            return page.evaluate(_MEASURE)
        finally:
            if page is not None:
                page.close()
            server.shutdown()
            server.server_close()

    return measure


def pilot_neighbourhood(events: int = 22) -> Subgraph:
    """A pilot neighbourhood the size and shape of the pilot overview.

    One pilot, and off them an event, a deck and a placement per entry: 67 nodes at 22
    events, which is Ariel M's record in the shipped corpus, and 250 at 83, which is
    the widest graph the app will draw before it refuses (``explore.assess``).

    Hand-authored rather than queried so the reproduction does not need the artifact,
    and the labels are real ones because their length is what a label's box is made of.
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


def head_to_head() -> Subgraph:
    """Two pilots' chains meeting at shared events, the head-to-head view's shape."""
    nodes = [Node("pilot:a", "Ariel M", "Pilot", group="pilot:a"),
             Node("pilot:b", "Ben F", "Pilot", group="pilot:b")]
    edges = []
    for i in range(8):
        event = f"event:e{i}"
        nodes.append(Node(event, f"SydneyShowdown{i}", "Event"))
        for who in ("a", "b"):
            deck, place = f"deck:{who}{i}", f"placement:{who}{i}"
            nodes += [Node(deck, "Jeskai Control", "Deck", group=f"pilot:{who}"),
                      Node(place, f"{i + 1}th", "Placement", group=f"pilot:{who}")]
            edges += [Edge(f"pilot:{who}", event, "PLAYED_AT"),
                      Edge(event, deck, "ENTERED"), Edge(deck, place, "PLACED")]
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


def hidden_gems() -> Subgraph:
    """Decks named by whole Moxfield titles, the hidden gems view's shape."""
    nodes = [Node(f"deck:d{i}", f"{i}51st Ben F - Academy Shops - WAC", "Deck")
             for i in range(20)]
    edges = [Edge("deck:d0", f"deck:d{i}", "SHARES") for i in range(1, 20)]
    return Subgraph(nodes=nodes, edges=edges)


@pytest.mark.parametrize("view", [
    pytest.param(pilot_neighbourhood(), id="pilot-overview-67"),
    pytest.param(pilot_neighbourhood(83), id="densest-assess-250"),
    pytest.param(head_to_head(), id="head-to-head"),
    pytest.param(hidden_gems(), id="hidden-gems"),
])
def test_every_node_carries_a_legible_label_at_desktop_width(drawn, view):
    """AC (#178): at desktop width every drawn node carries its label at a legible
    size, and physics is still live afterwards.

    Measured before the fix: the pilot overview painted at 6.9px and the 250-node
    ceiling at 4.4px, against a theme floor of 11 and a vis.js drop floor of 4. Held
    at the 250-node ceiling because the label size and the zoom are solved for each
    other a pass at a time and the loop is bounded: there it has the furthest to
    travel and the fewest passes to spare.

    Physics is asserted here, in the same breath, because it is the lever #174
    reached for and ADR 0018 forbids: a settle that buys its label size by freezing
    the simulation kills the drag interaction and strands the dynamic edges' support
    points, which is the whole of why that fix was reverted.
    """
    measured = drawn(view)

    assert measured["physics"], "the settle turned physics off (ADR 0018)"
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
        # loop has no other way of stating: it stops after eight passes whether the
        # zoom and the label size have finished agreeing or not.
        assert label["px"] == pytest.approx(LEGIBLE_PX, rel=0.05)


def test_the_composed_co_occurrence_view_is_labelled_without_being_touched(drawn):
    """AC (#178): the co-occurrence view carries legible labels too, and pays none of
    the #161 regressions for them.

    Its layout reaches the settle by the other route: every node is pinned, so physics
    never runs and the widget never announces a stabilised layout to wait for.

    Two of ADR 0018's regressions are held exactly here, where they are cheapest to
    see. The positions are a composition someone chose (``query``'s columns), so any
    settle that moves a node shows up against the pins it was given. And the
    percentages on the edges are quantities, not names: they keep vis.js's own size,
    multiplied by whatever zoom the settle lands on, rather than being solved up to
    the name size and shouting over the graph.
    """
    measured = drawn(card_cooccurrence())

    for label in measured["labels"]:
        assert label["px"] == pytest.approx(LEGIBLE_PX, rel=0.05), (
            f"{label['id']}'s label renders at {label['px']:.2f}px"
        )
    # Every node exactly where the composition pinned it: no post-layout mutation.
    at = measured["at"]
    assert (at["card:a"]["x"], at["card:a"]["y"]) == (-800, 150)
    assert (at["card:b"]["x"], at["card:b"]["y"]) == (-800, -150)
    assert (at["both:a|b"]["x"], at["both:a|b"]["y"]) == (-350, 0)
    for i in range(15):
        assert at[f"card:s{i}"]["x"] == 300
    # The quantities scale with the zoom from the size they were created at.
    assert measured["edgePx"], "no visible edge labels were drawn to measure"
    for px in measured["edgePx"]:
        assert px == pytest.approx(VIS_DEFAULT_PX * measured["zoom"], rel=0.01), (
            f"an edge quantity renders at {px:.2f}px at zoom {measured['zoom']:.3f}, "
            "so its size was solved instead of left to scale"
        )


def test_an_unweighted_node_among_weighted_ones_is_labelled_too(drawn):
    """The archetype affinity view sizes its archetypes and macros by how many events
    the pilot took them to, and leaves the pilot at the hub unweighted.

    A weighted node's radius goes through vis.js's scaling machinery and an unweighted
    one's does not, so this view is where a label size that reaches one population and
    misses the other would show: the reverted #174 recorded exactly that miss (its
    hub at 7px among 12px archetypes) on the way to its own fix. Under this settle the
    graph-level font measures as reaching both, and this test is what holds that.

    Held as one size across the graph and not only as a floor: on a sparse graph the
    zoom lands near 1, where the size a node was created at clears the floor by luck,
    and a miss would then show up only on the dense graphs that need the fix most.
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


def test_a_graph_that_settles_while_its_tab_is_hidden_still_draws(drawn):
    """The graph lives in a Gradio tab, and a hidden tab's frame reports 0 by 0.

    Stabilising a layout is a thousand iterations and seconds of work, and a reader
    who presses Draw and switches tabs while it runs leaves the graph to settle
    against a hidden frame: a fit to nothing lands on a zoom of nearly zero, and a
    label size solved by dividing by it is garbage that can still happen to look
    readable. So the settle has to wait for a frame that is there, and finish its
    work when the tab comes back.
    """
    measured = drawn(pilot_neighbourhood(), hidden_while_drawing=True)

    assert measured["physics"], "the settle turned physics off (ADR 0018)"
    assert measured["fitDrift"] == pytest.approx(1, rel=0.02), (
        f"a fresh fit moves the zoom by {measured['fitDrift']:.2f}x, so the tab came "
        "back to a graph that was never fitted to its frame"
    )
    for label in measured["labels"]:
        # Held at the size the settle aims for, not merely above the floor: settling
        # against no frame divides by a zoom of almost nothing, and what comes back
        # can be a wrong size that still happens to be a readable one.
        assert label["px"] == pytest.approx(LEGIBLE_PX, rel=0.05), (
            f"{label['id']}'s label came back at {label['px']:.2f}px, not {LEGIBLE_PX}px"
        )


def test_a_tab_switch_and_return_keeps_the_readers_zoom(drawn):
    """Coming back to the graph's tab is not a new frame, so nothing refits.

    Every tab's graph stays in the document: opening another tab hides this one's
    frame (0 by 0) and coming back restores it, and vis.js announces both as
    resizes. A settle that answered every announcement would throw away wherever
    the reader had panned or zoomed to before they left, for a frame the graph is
    already fitted to. The settle is owed to a frame, not to an event.
    """
    measured = drawn(pilot_neighbourhood(), then_hide_and_show=True)

    assert measured["physics"], "the settle turned physics off (ADR 0018)"
    assert measured["zoom"] == pytest.approx(measured["readerZoom"], rel=0.01), (
        f"the tab came back at zoom {measured['zoom']:.3f}, not the reader's "
        f"{measured['readerZoom']:.3f}: their view was refitted away"
    )
    for label in measured["labels"]:
        assert label["px"] >= THEME_MIN_PX, (
            f"{label['id']}'s label came back at {label['px']:.2f}px"
        )


def test_opening_a_nodes_details_does_not_refit_the_readers_view(drawn):
    """Clicking a node is not a new frame either, even though it resizes one.

    The details panel sits under the canvas in the same flex column, so filling it
    takes rows of height off the canvas, and vis.js announces that as a resize. A
    settle that answered it would refit the whole graph because the reader clicked
    a node: their wheel zoom thrown away by the very interaction the panel exists
    to serve. Once the reader has taken the view (a wheel zoom, a pan), no frame
    change refits it out from under them.
    """
    measured = drawn(pilot_neighbourhood(), then_reader_zooms_and_opens_details=True)

    # One wheel tick is a 1.1x zoom in vis.js, whatever the delta's magnitude.
    assert measured["readerZoom"] > measured["preZoom"] * 1.05, (
        "the wheel zoom never reached the canvas, so nothing is being held"
    )
    # vis.js itself keeps the formerly visible world in view when its canvas
    # shrinks, multiplying the zoom by the shrunken axis's ratio. That adjustment
    # is the widget's baseline behaviour and not the refit being forbidden here:
    # the reader's view survives up to exactly it, and no further.
    kept = measured["readerZoom"] * min(
        1,
        measured["frame"][0] / measured["preFrame"][0],
        measured["frame"][1] / measured["preFrame"][1],
    )
    assert measured["zoom"] == pytest.approx(kept, rel=0.01), (
        f"opening the details refitted the graph to zoom {measured['zoom']:.3f}, "
        f"throwing away the reader's {measured['readerZoom']:.3f} "
        f"(vis's own resize adjustment leaves {kept:.3f})"
    )


def test_a_resized_window_refits_and_keeps_its_labels_legible(drawn):
    """Legibility holds past the first paint: a desktop window gets dragged narrower.

    vis.js redraws a resized canvas at the zoom it already had and never refits, so
    without a settle of its own the graph would keep the frame it was born in: the
    labels solved for a 1440px fit, drawn into a 1000px window at the old zoom.

    This is also where the densest view falls off the cliff without the fix: at
    1000px its fitted zoom takes the default label under vis.js's 4px drop.
    """
    narrower = {"width": 1000, "height": DESKTOP["height"]}

    measured = drawn(pilot_neighbourhood(83), then_resize_to=narrower)

    assert measured["physics"], "the settle turned physics off (ADR 0018)"
    assert measured["fitDrift"] == pytest.approx(1, rel=0.02), (
        f"a fresh fit moves the zoom by {measured['fitDrift']:.2f}x, so the graph is "
        "still fitted to the frame it started in"
    )
    for label in measured["labels"]:
        assert label["px"] == pytest.approx(LEGIBLE_PX, rel=0.05), (
            f"{label['id']}'s label renders at {label['px']:.2f}px after the resize"
        )
