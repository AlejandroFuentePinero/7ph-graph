"""What the graph document actually draws at desktop width, measured on a real browser.

Issue #178: the graph is read on a desktop (ADR 0018), and at that width its labels
were merely small instead of absent: on a 1440 CSS px frame the pilot overview painted
its names at 6.9px, and the densest view the app draws (250 nodes) at 4.4px, a hair
above the 4px at which vis.js stops drawing a label at all. Nothing in the emitted
HTML says so. vis.js sizes a label in canvas units and multiplies by the zoom, so
whether a label reaches the reader is decided at paint time by numbers no string
assertion can see. These tests ask the running widget instead.

Chromium at a 1440x810 viewport, the interior of the graph frame on a 1440x900
desktop when the graph had the card's whole width. Since #330 the graph sits beside a
panel in a frame about two thirds as wide (`BESIDE_PANEL`), which is measured too:
the narrower frame is the harder fit. Alongside legibility, these tests hold the three regressions ADR 0018 names
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

import contextlib
import http.server
import re
import threading

import pytest

from graph7ph import serve
from graph7ph.query import Edge, Node, Subgraph
from graph7ph.render import render_subgraph
from graph7ph.theme import FONTS, TOKENS, build_css, contrast_ratio

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

# The interior of the graph frame on a 1440x900 desktop while the graph had the card
# to itself: the frame spanned the app's width at `clamp(525px, 90vh, 950px)` tall,
# which on that viewport is 90vh = 810px. Kept as the wider of the two frames these
# documents are measured in.
DESKTOP = {"width": 1440, "height": 810}

# The frame a graph is drawn in beside its panel (#330), on that same desktop: the
# graph's share of a 1150px card, at 78vh. Read off the running page.
BESIDE_PANEL = {"width": 791, "height": 702}

# The theme's smallest type role, read off the stylesheet rather than restated: a label
# under it is not "small", it is below anything the design asks a reader to read at all.
THEME_MIN_PX = min(float(px) for px in re.findall(r"font-size:\s*([\d.]+)px", build_css()))

# vis.js's own default label size in canvas units, the size every node and edge in
# these documents is created at: pyvis passes no font, so this is what an untouched
# label measures against.
VIS_DEFAULT_PX = 14

# The size a node's name should reach the reader at: the colour key's chips
# (`.key-chip`, 12px) in the panel beside the graph, so a name is never smaller than
# the key it is read beside. Stated here as the number the design asks for, not imported
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
    *absence* (nothing refits on a tab return, or under a reader who has zoomed) keep
    fixed graces instead, since an absence has no moment at which it announces itself.
    """
    def measure(subgraph: Subgraph, then_resize_to: dict | None = None,
                then_hide_and_show: bool = False, hidden_while_drawing: bool = False,
                then_reader_zooms: bool = False, frame: dict = DESKTOP) -> dict:
        server, url = _serve(render_subgraph(subgraph))
        page = None
        try:
            page = browser.new_page(viewport=frame)
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
            if then_reader_zooms:
                # A reader reading: a wheel zoom into the middle of the graph (vis's
                # own zoom, so the widget can know the view is now theirs).
                page.evaluate("() => { window._preZoom = network.getScale(); }")
                page.mouse.move(frame["width"] / 2, frame["height"] / 2)
                page.mouse.wheel(0, -240)
                page.wait_for_timeout(300)  # the wheel is dispatched asynchronously
                page.evaluate("() => { window._readerZoom = network.getScale();"
                              " const c = network.canvas.frame.canvas;"
                              " window._preFrame = [c.clientWidth, c.clientHeight]; }")
            if then_resize_to is not None and then_reader_zooms:
                # Nothing announces a settle that is not owed, so this waits out the
                # one that would come: vis notices the new size on its poll, and the
                # settle is held back 150ms behind that.
                page.set_viewport_size(then_resize_to)
                page.wait_for_timeout(2500)
            elif then_resize_to is not None:
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


def test_drawing_a_graph_asks_no_other_host_for_anything(browser):
    """Every request the drawn document makes goes to the app (issue #312). Asked of the
    browser, not read off the markup: what a document fetches is decided when it runs."""
    server, url = _serve(render_subgraph(head_to_head()))
    page = browser.new_page(viewport=DESKTOP)
    try:
        asked = []
        page.on("request", lambda request: asked.append(request.url))
        page.goto(url)
        page.wait_for_function("() => settledFrame !== null")
        assert url in asked
        assert [address for address in asked if not address.startswith(url)] == []
    finally:
        page.close()
        server.shutdown()
        server.server_close()


# Notes, the moment the graph says it has settled, whether the app's face was in.
_NOTE_THE_FACE_AT_THE_SETTLE = """
const watch = setInterval(() => {
  if (typeof settledFrame === 'undefined' || settledFrame === null) return;
  window._faceAtTheSettle = document.fonts.check("12px '__FACE__'");
  clearInterval(watch);
}, 5);
"""


def test_labels_are_drawn_in_the_apps_face_and_measured_only_once_it_is_in(browser):
    """Issue #330: the graph is a document of its own and loaded no face, so every
    label was drawn in Arial beside a page set in the app's own.

    A label's box is measured when the graph settles, and the fit is solved from
    those boxes, so a face that arrives afterwards leaves the graph fitted to the
    widths of another face's letters. The face is held back for a second here, as a
    first visit on a slow line holds it, on the view that would otherwise settle at
    once (every node pinned, nothing to stabilise): the settle has to wait for it.
    """
    face = FONTS["body"]["family"]
    server, url = _serve(render_subgraph(card_cooccurrence()))
    page = browser.new_page(viewport=DESKTOP)
    try:
        page.add_init_script(_NOTE_THE_FACE_AT_THE_SETTLE.replace("__FACE__", face))
        page.route("**/*.woff2", lambda route: (page.wait_for_timeout(1000), route.continue_()))
        page.goto(url)
        page.wait_for_function("() => window._faceAtTheSettle !== undefined")
        read = page.evaluate("""() => {
          const faces = (things) => [...new Set(Object.values(things)
            .filter((thing) => thing.options.label)
            .map((thing) => thing.labelModule.fontOptions.face))];
          return {
            inAtTheSettle: window._faceAtTheSettle,
            loaded: [...document.fonts].filter((f) => f.status === 'loaded')
              .map((f) => f.family.replace(/['"]/g, '')),
            nodes: faces(network.body.nodes),
            edges: faces(network.body.edges),
          };
        }""")
    finally:
        page.close()
        server.shutdown()
        server.server_close()

    assert read["inAtTheSettle"], "the graph settled before the face was in"
    assert read["loaded"] == [face]
    for drawn_in in (*read["nodes"], *read["edges"]):
        assert drawn_in.strip("'\"").startswith(face), drawn_in
    assert read["nodes"] and read["edges"]


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


def test_a_frame_that_changes_under_a_reader_who_has_zoomed_does_not_refit(drawn):
    """Once the reader has taken the view, no change of frame takes it back.

    A frame that changes size is a different frame to fit, and the settle answers it
    (the test below). Not under a reader who has zoomed in to read a corner: a refit
    then throws their zoom away for a frame they never asked to be fitted to. This
    was first held against the details strip that used to sit under the canvas in
    this document, whose filling on a node click shrank the canvas and so refitted
    the graph because the reader clicked a node. That strip is the page's now (#330),
    and the frame changes under a reader the way it still can: the window narrows.
    """
    narrower = {"width": 1000, "height": DESKTOP["height"]}

    measured = drawn(pilot_neighbourhood(), then_reader_zooms=True, then_resize_to=narrower)

    # One wheel tick is a 1.1x zoom in vis.js, whatever the delta's magnitude.
    assert measured["readerZoom"] > measured["preZoom"] * 1.05, (
        "the wheel zoom never reached the canvas, so nothing is being held"
    )
    assert measured["frame"][0] < measured["preFrame"][0], "the frame never changed"
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
        f"the narrowed window refitted the graph to zoom {measured['zoom']:.3f}, "
        f"throwing away the reader's {measured['readerZoom']:.3f} "
        f"(vis's own resize adjustment leaves {kept:.3f})"
    )


# What the widget would paint for a node and for an edge right now, in CSS pixels:
# vis.js works both out when it draws, from the selection as it stands.
_PAINTED = """
(ids) => {
  const scale = network.getScale();
  const node = (id) => { const v = network.body.nodes[id].getFormattingValues();
    return {ring: v.borderWidth * scale, ringColour: v.borderColor, fill: v.color}; };
  const edge = (from, to) => {
    const found = Object.values(network.body.edges)
      .find((e) => e.fromId === from && e.toId === to).getFormattingValues();
    return {width: found.width * scale, colour: found.color}; };
  return {hub: node(ids.hub), other: node(ids.other),
          reached: edge(ids.hub, ids.reached), apart: edge(ids.reached, ids.other)};
}
"""


@pytest.mark.parametrize("events", [pytest.param(22, id="pilot-overview-67"),
                                    pytest.param(83, id="densest-assess-250")])
def test_a_selected_node_and_the_edges_it_reaches_cannot_be_missed(browser, events):
    """After the maintainer's look (#330): a click on a node, or on its row in the
    panel, selected it in the graph, and the selection was hard to find.

    vis.js rings a selected node in a shade of its own fill and thickens its edges by
    one canvas unit, which at the zoom a hundred nodes are fitted at is under half a
    pixel. The selected node takes a ring in the text colour, and every edge it
    reaches is drawn in that colour, both sized on screen and not in canvas units,
    so they hold on the densest graph the app draws. What it does not reach steps
    back (#331), where it was once drawn as it had been: a dot dimmed toward the
    ground, and an edge dimmed and no thicker.
    """
    ids = {"hub": "pilot:ariel", "reached": "event:e3", "other": "deck:d3"}
    server, url = _serve(render_subgraph(pilot_neighbourhood(events)))
    page = browser.new_page(viewport=BESIDE_PANEL)
    try:
        page.goto(url)
        page.wait_for_function("() => settledFrame !== null")
        page.wait_for_timeout(1000)
        before = page.evaluate(_PAINTED, ids)
        page.evaluate("id => network.selectNodes([id])", ids["hub"])
        after = page.evaluate(_PAINTED, ids)
    finally:
        page.close()
        server.shutdown()
        server.server_close()

    bright = TOKENS["text"]
    assert before["hub"]["ringColour"] != bright and before["reached"]["width"] < 1
    # The selected node: a ring a reader cannot miss, in the brightest ink the app has.
    assert after["hub"]["ringColour"] == bright
    assert after["hub"]["ring"] == pytest.approx(3, abs=0.1)
    # The edges it reaches: the same ink, as thick as a label's stroke.
    assert after["reached"]["colour"] == bright
    assert after["reached"]["width"] >= 2.5
    # And what it does not reach steps back, with no ring and no added width.
    assert _contrast(after["other"]["fill"]) < RECEDED < _contrast(before["other"]["fill"])
    assert after["other"]["ring"] == before["other"]["ring"]
    assert _contrast(after["apart"]["colour"]) < RECEDED < _contrast(before["apart"]["colour"])
    assert after["apart"]["width"] == before["apart"]["width"]


@pytest.mark.parametrize("view", [
    pytest.param(pilot_neighbourhood(), id="pilot-overview-67"),
    pytest.param(pilot_neighbourhood(83), id="densest-assess-250"),
    pytest.param(card_cooccurrence(), id="co-occurrence"),
])
def test_every_label_is_legible_in_the_frame_beside_the_panel(drawn, view):
    """Issue #330: the graph is drawn in a frame about two thirds as wide as it was,
    beside the panel that answers it. A narrower frame is a smaller fitted zoom, which
    is the direction the label size and the zoom have furthest to travel to agree in,
    so the size asked for is held there too, on the densest view the app draws."""
    measured = drawn(view, frame=BESIDE_PANEL)

    assert measured["frame"] == [BESIDE_PANEL["width"], BESIDE_PANEL["height"]]
    for label in measured["labels"]:
        assert label["px"] == pytest.approx(LEGIBLE_PX, rel=0.05), (
            f"{label['id']}'s label renders at {label['px']:.2f}px"
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


# --- The subject stands out, and a node brings its neighbourhood forward (issue #331) ---


@contextlib.contextmanager
def _settled(browser, doc: str, frame: dict = BESIDE_PANEL):
    """A graph document drawn in the frame it has beside its panel, settled and at rest."""
    server, url = _serve(doc)
    page = browser.new_page(viewport=frame)
    try:
        page.goto(url)
        page.wait_for_function("() => settledFrame !== null")
        page.wait_for_timeout(1000)
        yield page
    finally:
        page.close()
        server.shutdown()
        server.server_close()


# Each node as a reader sees it: its dot's radius and its name's size, on screen.
_DRAWN_AS = """
() => {
  const scale = network.getScale(), seen = {};
  for (const id of network.body.nodeIndices) {
    const node = network.body.nodes[id];
    seen[id] = {dot: node.shape.radius * scale,
                name: node.labelModule.fontOptions.size * scale};
  }
  return seen;
}
"""


def test_the_node_a_graph_was_asked_about_has_the_largest_dot_and_the_largest_name(browser):
    """Issue #331: a card's usage graph drew all 106 of its nodes one size, and the
    card the reader asked about was a small dot in the middle of them.

    The subject of a graph is drawn as a larger dot with a larger name: 15px, a step
    up from the 12px every other name keeps. Every other node is drawn as it was.
    """
    doc = render_subgraph(pilot_neighbourhood(), subjects=["pilot:ariel"])
    with _settled(browser, doc) as page:
        seen = page.evaluate(_DRAWN_AS)

    subject = seen.pop("pilot:ariel")
    assert subject["name"] == pytest.approx(15, rel=0.05)
    for node, other in seen.items():
        assert subject["dot"] >= 1.5 * other["dot"], node
        assert other["name"] == pytest.approx(LEGIBLE_PX, rel=0.05), node


# How far apart two nodes' dots are on screen, edge to edge, beside the wider dot's
# diameter: under zero the two overlap.
_GAP = """
([a, b]) => {
  const scale = network.getScale(), at = network.getPositions([a, b]);
  const [one, other] = [network.body.nodes[a], network.body.nodes[b]];
  const centres = Math.hypot(at[a].x - at[b].x, at[a].y - at[b].y);
  return {gap: (centres - one.shape.radius - other.shape.radius) * scale,
          dot: 2 * Math.max(one.shape.radius, other.shape.radius) * scale,
          physics: network.physics.options.enabled,
          pinned: [one, other].some((n) => n.options.fixed.x || n.options.fixed.y)};
}
"""


def test_the_two_pilots_of_a_head_to_head_rest_clear_of_one_another(browser):
    """Issue #331: the two pilots of a head-to-head came to rest 27px apart at the
    centre, one dot lying over the other, because every event they share pulls both
    to the same point.

    They rest with clear ground between them, at least a dot's width of it. Neither
    is pinned and physics is live (ADR 0018), so each still answers a drag.
    """
    pilots = ["pilot:a", "pilot:b"]
    with _settled(browser, render_subgraph(head_to_head(), subjects=pilots)) as page:
        seen = page.evaluate(_GAP, pilots)

    assert seen["gap"] >= seen["dot"], (
        f"the two pilots rest {seen['gap']:.0f}px apart, edge to edge, and a dot is "
        f"{seen['dot']:.0f}px wide"
    )
    assert seen["physics"], "physics was turned off to hold them apart (ADR 0018)"
    assert not seen["pinned"], "a pilot was pinned to hold the two apart"


# Everything the widget would paint right now: each node's fill and the ink of its
# name, each edge's colour and its width on screen. vis.js works these out as it
# draws, from what is selected and hovered at that moment.
_PAINTED_ALL = """
() => {
  const scale = network.getScale(), nodes = {}, edges = {};
  for (const id of network.body.nodeIndices) {
    const node = network.body.nodes[id];
    nodes[id] = {fill: node.getFormattingValues().color,
                 name: node.labelModule.fontOptions.color};
  }
  for (const edge of Object.values(network.body.edges)) {
    const drawn = edge.getFormattingValues();
    edges[edge.fromId + " > " + edge.toId] = {colour: drawn.color, width: drawn.width * scale};
  }
  return {nodes, edges};
}
"""


def _contrast(colour: str) -> float:
    """The contrast of a drawn colour against the ground the graph is drawn on."""
    return contrast_ratio(colour, TOKENS["surface"])


# What a drawn thing has to stay under, against the ground, to have stepped back: half
# of the 3:1 a graphical object that carries meaning is asked to reach.
RECEDED = 1.5


def _assert_only_the_neighbourhood_is_lit(
    painted: dict, rest: dict, focus: str, near: set, reached: set, selected: str | None = None
):
    """``focus`` is the node in focus, ``near`` the nodes it reaches and ``reached``
    the edges that touch it. The node in focus is held only to its name: its dot is
    vis.js's to draw, in the shade it gives a selected node. ``selected`` is a
    selected node the focus has moved away from, whose dot is vis.js's too."""
    assert painted["nodes"][focus]["name"] == rest["nodes"][focus]["name"]
    for node, drawn in painted["nodes"].items():
        if node == focus:
            continue
        if node in near:
            assert drawn == rest["nodes"][node], f"{node} is in the neighbourhood and changed"
        else:
            if node != selected:
                assert _contrast(drawn["fill"]) < RECEDED, f"{node}'s dot has not stepped back"
            assert _contrast(drawn["name"]) < _contrast(rest["nodes"][node]["name"]) / 2, (
                f"{node}'s name has not stepped back")
    for edge, drawn in painted["edges"].items():
        if edge in reached:
            assert drawn["colour"] == TOKENS["text"], f"{edge} is reached and not in the ink"
            assert drawn["width"] >= 2.5, f"{edge} is reached and {drawn['width']:.1f}px wide"
        else:
            assert _contrast(drawn["colour"]) < RECEDED, f"{edge} has not stepped back"
            assert drawn["width"] == rest["edges"][edge]["width"], edge


def test_a_selected_node_brings_its_neighbourhood_forward_and_letting_go_puts_it_back(browser):
    """Issue #331: on a dense graph a selected node's ring and bright edges were lit
    among a hundred nodes as bright as they were.

    Selecting a node dims everything outside its neighbourhood, dots, names and
    edges alike, and leaves the node, the nodes it reaches and the edges between
    them as they were or brighter. Nothing changes at rest, and letting the
    selection go puts every node and edge back exactly as it was drawn.
    """
    near = {"pilot:ariel", "deck:d3"}
    reached = {"pilot:ariel > event:e3", "event:e3 > deck:d3"}
    with _settled(browser, render_subgraph(pilot_neighbourhood())) as page:
        rest = page.evaluate(_PAINTED_ALL)
        page.evaluate("() => network.selectNodes(['event:e3'])")
        selected = page.evaluate(_PAINTED_ALL)
        page.evaluate("() => network.unselectAll()")
        let_go = page.evaluate(_PAINTED_ALL)

    assert len(rest["nodes"]) == 67 and len(rest["edges"]) == 66
    _assert_only_the_neighbourhood_is_lit(selected, rest, "event:e3", near, reached)
    assert let_go == rest


_WHERE = """(id) => { const at = network.canvasToDOM(network.getPositions([id])[id]);
                      return [at.x, at.y]; }"""


def test_the_node_under_the_pointer_is_the_focus_and_the_selection_takes_it_back(browser):
    """Issue #331: a reader finding their way round a dense graph asks "what does
    this one touch" of node after node, and should not have to click each to see.

    The node under the pointer brings its neighbourhood forward as a selected one
    does, and comes first while it is there: with one node selected, the pointer over
    another lights that other's neighbourhood. When the pointer leaves, the focus goes
    back to the selected node.
    """
    with _settled(browser, render_subgraph(pilot_neighbourhood())) as page:
        rest = page.evaluate(_PAINTED_ALL)
        page.evaluate("() => network.selectNodes(['event:e7'])")
        page.mouse.move(*page.evaluate(_WHERE, "event:e3"))
        page.wait_for_timeout(300)
        under_the_pointer = page.evaluate(_PAINTED_ALL)
        page.mouse.move(3, 3)
        page.wait_for_timeout(300)
        pointer_gone = page.evaluate(_PAINTED_ALL)

    # The selected node is outside this neighbourhood, and its name steps back with
    # the rest. Its dot does not: it keeps the ring and shade of a selected node,
    # since the panel beside the graph is still showing its facts.
    _assert_only_the_neighbourhood_is_lit(
        under_the_pointer, rest, "event:e3", {"pilot:ariel", "deck:d3"},
        {"pilot:ariel > event:e3", "event:e3 > deck:d3"}, selected="event:e7")
    assert _contrast(under_the_pointer["nodes"]["event:e7"]["fill"]) > 3
    # Under the pointer and not selected, the node keeps the dot it has at rest.
    assert under_the_pointer["nodes"]["event:e3"] == rest["nodes"]["event:e3"]
    _assert_only_the_neighbourhood_is_lit(
        pointer_gone, rest, "event:e7", {"pilot:ariel", "deck:d7"},
        {"pilot:ariel > event:e7", "event:e7 > deck:d7"})


# Each drawn edge quantity: the ink it is in and its size on screen.
_QUANTITIES = """
() => {
  const scale = network.getScale(), seen = {};
  for (const edge of Object.values(network.body.edges)) {
    if (!edge.options.label) continue;
    const font = edge.labelModule.fontOptions;
    seen[edge.fromId + " > " + edge.toId] = {ink: font.color, px: font.size * scale};
  }
  return seen;
}
"""


def test_a_node_in_focus_states_its_own_quantities_at_the_size_of_a_name(browser):
    """Issue #331: an edge's quantity is drawn at vis.js's own size scaled by the
    zoom (ADR 0018), which on a fitted graph is too small to read, and a hub's
    quantities sit among those of every other hub.

    The edges a node in focus reaches state their quantities in the text colour at
    the size of a name. The other quantities are not drawn while there is a focus.
    At rest, and once the focus is gone, every quantity is as vis.js draws it.
    """
    reached = "both:a|b > card:s3"
    with _settled(browser, render_subgraph(card_cooccurrence())) as page:
        zoom = page.evaluate("() => network.getScale()")
        rest = page.evaluate(_QUANTITIES)
        page.evaluate("() => network.selectNodes(['card:s3'])")
        focused = page.evaluate(_QUANTITIES)
        page.evaluate("() => network.unselectAll()")
        let_go = page.evaluate(_QUANTITIES)

    assert len(rest) == 17
    for quantity in rest.values():
        assert quantity["px"] == pytest.approx(VIS_DEFAULT_PX * zoom, rel=0.01)
    assert focused[reached]["ink"] == TOKENS["text"]
    assert focused[reached]["px"] == pytest.approx(LEGIBLE_PX, rel=0.05)
    for edge, quantity in focused.items():
        if edge != reached:
            assert quantity["ink"] == "rgba(0,0,0,0)", f"{edge}'s quantity is still drawn"
    assert let_go == rest


# What is on the canvas, read off its pixels after a draw: the colour at a point, and
# the brightest pixel in the box a label is drawn in. Points and boxes are in the
# graph's own coordinates. The ground is the page's, behind a clear canvas, so a
# pixel nothing was drawn on reads as black.
_ON_THE_CANVAS = """
({points, names, quantities}) => {
  network.redraw();
  const canvas = network.canvas.frame.canvas, ctx = canvas.getContext('2d');
  const ratio = canvas.width / canvas.clientWidth;
  const px = (at) => { const p = network.canvasToDOM(at);
                       return [Math.round(p.x * ratio), Math.round(p.y * ratio)]; };
  const colour = (at) => '#' + [...ctx.getImageData(...px(at), 1, 1).data.slice(0, 3)]
    .map((v) => v.toString(16).padStart(2, '0')).join('');
  const brightest = (box) => {
    const [left, top] = px({x: box.left, y: box.top});
    const [right, bottom] = px({x: box.left + box.width, y: box.top + box.height});
    const data = ctx.getImageData(left, top, right - left, bottom - top).data;
    let most = 0;
    for (let i = 0; i < data.length; i += 4) {
      most = Math.max(most, (data[i] + data[i + 1] + data[i + 2]) / 3);
    }
    return most;
  };
  const edge = (ends) => Object.values(network.body.edges)
    .find((e) => e.fromId + ' > ' + e.toId === ends);
  return {
    points: Object.fromEntries(Object.entries(points).map(([k, at]) => [k, colour(at)])),
    names: Object.fromEntries(names.map(
      (id) => [id, brightest(network.body.nodes[id].labelModule.size)])),
    quantities: Object.fromEntries(quantities.map(
      (ends) => [ends, brightest(edge(ends).labelModule.size)])),
  };
}
"""


def _brightness(colour: str) -> float:
    return sum(int(colour[i:i + 2], 16) for i in (1, 3, 5)) / 3


def test_what_the_focus_changes_is_what_reaches_the_canvas(browser):
    """The focus is held above by what vis.js says it would paint. This reads the
    canvas itself, on a graph composed so that every claim has a pixel to stand on:
    a node lying across the subject, a node lying across one in the neighbourhood,
    and a quantity inside the focus and one outside it.

    A subject is painted over a node the layout has left lying across it. With a
    node in focus, a node it reaches is painted over one it does not, in its own
    colour, and the one it does not is dimmed. A name outside the neighbourhood is
    painted dimmer than it was. A reached edge's quantity is painted in the text
    colour, and another edge's is not painted at all.
    """
    card, archetype = "#199e70", "#c98500"  # the palette's slots for the two kinds
    composed = Subgraph(
        nodes=[
            Node("card:s", "Brainstorm", "Card", pin=(0.0, 0.0)),
            Node("arch:over-s", "Oracle", "Archetype", pin=(15.0, 0.0)),
            Node("macro:a", "tempo", "Macro", pin=(300.0, 0.0)),
            Node("arch:n", "Grixis", "Archetype", pin=(300.0, 200.0)),
            Node("arch:over-n", "Dimir", "Archetype", pin=(315.0, 200.0)),
            Node("arch:far", "Breach", "Archetype", pin=(600.0, -200.0)),
        ],
        edges=[Edge("macro:a", "arch:n", "62%", visible=True),
               Edge("card:s", "arch:far", "31%", visible=True)],
    )
    asked = {
        "points": {"subject": {"x": 0, "y": 0}, "reached": {"x": 300, "y": 200},
                   "lying across it": {"x": 337, "y": 200}},
        "names": ["arch:n", "arch:far"],
        "quantities": ["macro:a > arch:n", "card:s > arch:far"],
    }
    with _settled(browser, render_subgraph(composed, subjects=["card:s"])) as page:
        rest = page.evaluate(_ON_THE_CANVAS, asked)
        page.evaluate("() => network.selectNodes(['macro:a'])")
        focused = page.evaluate(_ON_THE_CANVAS, asked)

    ink = _brightness(TOKENS["text"])
    # At rest: the subject over the node across it, every name in the ink, and each
    # quantity painted, in vis.js's own white-edged grey.
    assert rest["points"]["subject"] == card
    assert rest["points"]["lying across it"] == archetype
    assert rest["names"] == {"arch:n": pytest.approx(ink, abs=2),
                             "arch:far": pytest.approx(ink, abs=2)}
    assert min(rest["quantities"].values()) > 200
    # In focus: the reached node over the one across it, which is dimmed.
    assert focused["points"]["subject"] == card
    assert focused["points"]["reached"] == archetype
    assert _contrast(focused["points"]["lying across it"]) < RECEDED
    assert focused["names"]["arch:n"] == pytest.approx(ink, abs=2)
    assert focused["names"]["arch:far"] < ink / 2
    assert focused["quantities"]["macro:a > arch:n"] == pytest.approx(ink, abs=2)
    assert focused["quantities"]["card:s > arch:far"] < 100


def test_two_cards_that_share_no_deck_are_not_thrown_apart(browser):
    """Two cards that never share a deck are drawn as two nodes with nothing between
    them. The weight that parts two pilots is for subjects the layout pulls to one
    point, through what both are tied to; with no tie, two heavy nodes only push
    each other away, and the pair was fitted as two specks 600px and more apart.

    They are drawn as they were before either was marked: close, and at full size.
    """
    seeds = ["card:a", "card:b"]
    apart = Subgraph(nodes=[Node("card:a", "Snapcaster Mage", "Card", group="seed:a"),
                            Node("card:b", "Lightning Bolt", "Card", group="seed:b")],
                     edges=[])
    with _settled(browser, render_subgraph(apart, subjects=seeds)) as page:
        seen = page.evaluate(_GAP, seeds)

    assert seen["dot"] >= 40, f"a subject's dot is fitted {seen['dot']:.0f}px wide"
    assert seen["gap"] < 200, f"the two cards rest {seen['gap']:.0f}px apart"


def test_an_edge_selected_on_its_own_is_still_lit(browser):
    """A click on an edge selects it with no node selected, and since #330 the
    selected edge has been drawn in the text colour, 2.5px wider. The focus is a
    node's, so an edge alone dims nothing, and it keeps the look it had.
    """
    with _settled(browser, render_subgraph(pilot_neighbourhood())) as page:
        rest = page.evaluate(_PAINTED_ALL)
        page.evaluate("""() => network.selectEdges([Object.values(network.body.edges)
          .find((e) => e.fromId === 'event:e3' && e.toId === 'deck:d3').id])""")
        selected = page.evaluate(_PAINTED_ALL)
        page.evaluate("() => network.unselectAll()")
        let_go = page.evaluate(_PAINTED_ALL)

    edge = selected["edges"].pop("event:e3 > deck:d3")
    assert edge["colour"] == TOKENS["text"]
    assert edge["width"] >= 2.5
    del rest["edges"]["event:e3 > deck:d3"]
    assert selected == rest, "an edge selected alone changed something else"
    assert let_go["edges"]["event:e3 > deck:d3"]["colour"] != TOKENS["text"]
