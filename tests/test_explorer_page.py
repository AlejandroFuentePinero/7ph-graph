"""What the explorer draws round its results, measured on a real browser.

Issue #324: Gradio draws a group as a surface of its own, filled with the border
colour so the gaps between its children read as dividers. Inside a card or the
control panel the app answers for that surface. The groups that only hold a view's
results did not, so on Cards and Pilots the border colour showed as a strip behind
the subject line, between the cards and under the last one. Which element carries
the fill is Gradio's markup and not the app's, so no test that reads the stylesheet
or the component tree can see it. These ask the running page.

The explorer itself, over the suite's small snapshot, since the groups under test
are the ones it nests and shows on a pick. It is driven with the acceptance
screenshot script's own steps (``scripts/acceptance_shots.py``), so the script's
reading of Gradio's controls is held here too: on the move to Gradio 6 it stopped at
its first dropdown, and nothing said so until someone next ran it.

Skipped where playwright or its browser is missing, as ``tests/test_page_style.py`` is.
"""

import importlib.util
import re
from contextlib import contextmanager
from pathlib import Path

import pytest

from graph7ph.app import build_app, launch_kwargs
from graph7ph.build import build_graph
from graph7ph.db import artifact_path, database_path
from graph7ph.models import load_snapshot
from graph7ph.provenance import staleness
from graph7ph.theme import FONTS, TOKENS, contrast_ratio

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright


def _acceptance_script():
    """``scripts/acceptance_shots.py`` as a module: ``scripts`` is not a package."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "acceptance_shots.py"
    spec = importlib.util.spec_from_file_location("acceptance_shots", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shots = _acceptance_script()

# A card of the suite's snapshot and its two pilots, who met at one event, so every
# view has something to draw.
CARD = "Brainstorm"
PILOT = "Jordan C"
PILOT_B = "Tom S"


def _rgb(hex_: str) -> str:
    """A ``#rrggbb`` token as the browser reports a computed colour."""
    value = hex_.lstrip("#")
    return "rgb({}, {}, {})".format(*(int(value[i:i + 2], 16) for i in (0, 2, 4)))


@pytest.fixture(scope="module")
def chromium():
    """A Chromium to load the explorer in, or a skip naming what is missing."""
    with sync_playwright() as play:
        try:
            engine = play.chromium.launch()
        except Exception as missing:  # noqa: BLE001 - any launch failure is the same skip
            pytest.skip(f"no Chromium to load the explorer in: {missing}")
        yield engine
        engine.close()


@contextmanager
def _explorer_over(artifact: Path):
    """The address of the explorer over ``artifact``, launched as every real launch is."""
    demo = build_app(artifact)
    _, url, _ = demo.launch(prevent_thread_lock=True, **launch_kwargs())
    try:
        yield url
    finally:
        demo.close()


def _loaded(chromium, url: str, width: str = "desktop"):
    """The explorer freshly loaded at one of the acceptance script's three widths."""
    tab = chromium.new_page(**shots.VIEWPORTS[width])
    tab.goto(url, wait_until="networkidle")
    tab.get_by_role("tab", name="Meta", exact=True).wait_for()
    return tab


@pytest.fixture(scope="module")
def explorer(tmp_path_factory, snapshot_dir):
    """The explorer over the suite's snapshot."""
    artifact = tmp_path_factory.mktemp("explorer") / "graph"
    build_graph(load_snapshot(snapshot_dir), artifact)
    with _explorer_over(artifact) as url:
        yield url


@pytest.fixture(scope="module")
def record():
    """The explorer over the real record, for what the suite's snapshot cannot draw.

    Skipped where the record is absent or built from other sources, as
    `conftest.live_graph` is.
    """
    artifact = artifact_path()
    if not database_path(artifact).exists():
        pytest.skip(f"no graph artifact at {artifact}; build one with `uv run graph7ph build`")
    if staleness(artifact):
        pytest.skip(f"cannot grade the real record: {staleness(artifact)}")
    with _explorer_over(artifact) as url:
        yield url


@pytest.fixture
def page(chromium, explorer):
    """A fresh load for each test, so none inherits a Draw."""
    tab = _loaded(chromium, explorer)
    yield tab
    tab.close()


# The tab on screen, as a JavaScript expression: every tab's content stays in the
# document, and only the open one takes any room.
_OPEN_TAB = (
    "[...document.querySelectorAll('.tabitem')]"
    ".find(tab => tab.getBoundingClientRect().height > 0)"
)


def _filled_with_the_border_colour(page) -> list[str]:
    """Every box on the open tab painted in the border colour, outside a card or the
    control panel, as ``tag.classes WxH``."""
    return page.evaluate(
        "(border) => { const open = " + _OPEN_TAB + """;
             return [...open.querySelectorAll('*')].filter(el => {
               const box = el.getBoundingClientRect();
               return box.width > 0 && box.height > 0
                 && getComputedStyle(el).backgroundColor === border
                 && !el.closest('.insight-card, .control-panel');
             }).map(el => {
               const box = el.getBoundingClientRect();
               return el.tagName.toLowerCase() + '.' + [...el.classList].join('.')
                 + ' ' + Math.round(box.width) + 'x' + Math.round(box.height);
             });
           }""",
        _rgb(TOKENS["border"]),
    )


@pytest.mark.parametrize(
    ("tab", "view", "picks"),
    [
        ("Cards", None, [("Card", CARD)]),
        ("Cards", "Co-occurrence", [("Card", CARD)]),
        ("Pilots", None, [("Pilot", PILOT)]),
        ("Pilots", "Head-to-head", [("Pilot", PILOT), ("Second pilot (required)", PILOT_B)]),
    ],
    ids=["card overview", "card co-occurrence", "pilot overview", "pilot head-to-head"],
)
def test_only_a_card_or_the_control_panel_is_filled_with_the_border_colour(
    page, tab, view, picks
):
    shots.tab(page, tab)
    if view:
        shots.chip(page, "View", view)

    # Before a pick the view's group is empty, and should be no more than that.
    assert _filled_with_the_border_colour(page) == []

    for label, name in picks:
        shots.pick(page, label, name)
    # A pick draws; the head-to-head alone waits for its Draw.
    if page.get_by_role("button", name="Draw", exact=True).locator("visible=true").count():
        shots.draw(page, settle=500)
    # The results are on the page, so the second reading is of a drawn view.
    page.locator(".results-stack .insight-card").locator("visible=true").first.wait_for()
    page.wait_for_timeout(500)
    assert _filled_with_the_border_colour(page) == []


def test_the_hidden_gems_are_drawn_as_one_card(page):
    # The gem view was the one result set as a card by classing the HTML block itself.
    # Gradio gives a block's classes to the block and to the content inside it, so it
    # drew a bordered card inset in a padded surface of the same fill.
    shots.tab(page, "Hidden gems")

    padded_surfaces_round_each_card = page.evaluate(
        "([border, surface]) => { const open = " + _OPEN_TAB + """;
             const cards = [...open.querySelectorAll('*')].filter(el => {
               const style = getComputedStyle(el);
               return el.getBoundingClientRect().height > 0
                 && style.borderTopWidth === '1px' && style.borderTopColor === border
                 && style.borderTopLeftRadius === '12px';
             });
             return cards.map(card => {
               const round = [];
               for (let el = card.parentElement; el !== open; el = el.parentElement) {
                 const style = getComputedStyle(el);
                 if (style.backgroundColor === surface && parseFloat(style.paddingLeft) > 0)
                   round.push(el.tagName.toLowerCase() + '.' + [...el.classList].join('.'));
               }
               return round;
             });
           }""",
        [_rgb(TOKENS["border"]), _rgb(TOKENS["surface"])],
    )

    # One card on the tab, and no surface of its own fill padded round it.
    assert padded_surfaces_round_each_card == [[]]


def test_the_standings_and_the_gems_draw_no_table_frame_on_the_real_record(chromium):
    # The app's own two tables, where they are drawn: the suite's snapshot has no
    # race and no gem, so its explorer draws neither. What the stylesheet makes of
    # the table's markup is held everywhere on a stand-in
    # (`tests/test_page_style.py`); this holds the tables the app really builds and
    # the blocks it really sets them in. Graded on the real record and skipped where
    # it is absent or built from other sources, as `conftest.live_graph` is.
    artifact = artifact_path()
    if not database_path(artifact).exists():
        pytest.skip(f"no graph artifact at {artifact}; build one with `uv run graph7ph build`")
    if staleness(artifact):
        pytest.skip(f"cannot grade the real record: {staleness(artifact)}")

    with _explorer_over(artifact) as url:
        page = _loaded(chromium, url)
        for tab in ("Player leaderboard", "Hidden gems"):
            shots.tab(page, tab)
            table = page.evaluate(
                "() => { const table = " + _OPEN_TAB + """.querySelector('table.data-table');
                     const cells = [...table.querySelectorAll('th, td')];
                     const width = (el, side) => getComputedStyle(el)['border' + side + 'Width'];
                     return {
                       cells: cells.length,
                       // No line either side of a cell or over it, and none round a
                       // row or the table: all that is left is the hairline under a cell.
                       lines: [...new Set([
                         ...cells.flatMap(el => ['Top', 'Right', 'Left'].map(s => width(el, s))),
                         ...[table, ...table.querySelectorAll('tr')].flatMap(
                           el => ['Top', 'Right', 'Bottom', 'Left'].map(s => width(el, s))),
                       ])],
                     }; }"""
            )
            assert table["cells"] > 0, tab
            assert table["lines"] == ["0px"], tab


# Every tab, the picks that put its charts on screen, and how many charts that is.
# Cards is walked twice, since its two views each draw a chart of their own. The
# suite's snapshot draws the two on Meta and a card's adoption in both views; it
# has no archetype with a timeline, no pilot with a year to average or a rivalry
# to chart, and no race.
SUITE_WALK = [
    ("Meta", [("Or focus on specific archetypes", "Grixis")], 2),
    ("Archetypes", [], 0),
    ("Cards", [("Card", CARD)], 1),
    ("Cards", [("View", "Co-occurrence"), ("Card", CARD)], 1),
    ("Hidden gems", [], 0),
    ("Pilots", [("Pilot", PILOT)], 0),
    ("Player leaderboard", [], 0),
    ("FAQ", [], 0),
]


def _toolbar_buttons_on_show(page) -> list[list[int]]:
    """For each chart on the open tab, how many of the chart library's toolbar buttons
    a reader can see, with the pointer off the chart and then on it."""
    seen = []
    drawn = page.locator(".js-plotly-plot").locator("visible=true")
    for i in range(drawn.count()):
        chart = drawn.nth(i)

        def on_show():
            return chart.evaluate(
                "chart => [...chart.querySelectorAll('.modebar-btn')].filter(button =>"
                " button.checkVisibility({opacityProperty: true, visibilityProperty: true})"
                ").length"
            )

        page.mouse.move(0, 0)
        off = on_show()
        chart.hover()
        # The library fades its toolbar in over 300ms once the pointer is on the chart.
        page.wait_for_timeout(500)
        seen.append([off, on_show()])
    return seen


def _walked(page, walk) -> list:
    """Each tab of ``walk`` in turn with its charts drawn, and the toolbar buttons on
    show on each chart."""
    found = []
    for name, picks, charts in walk:
        shots.tab(page, name)
        for label, text in picks:
            # The view is a row of chips; everything else picked here is a search.
            (shots.chip if label == "View" else shots.pick)(page, label, text)
        # A multiselect stays open after a pick, with its list over the chart below it.
        page.keyboard.press("Escape")
        if picks and page.get_by_role("button", name="Draw", exact=True).locator(
                "visible=true").count():
            shots.draw(page, settle=500)
        if charts:
            page.locator(".js-plotly-plot").locator("visible=true").nth(charts - 1).wait_for(
                timeout=60_000)
            page.wait_for_timeout(500)
        found.append((name, _toolbar_buttons_on_show(page)))
    return found


@pytest.mark.parametrize("width", list(shots.VIEWPORTS))
def test_no_chart_shows_the_chart_librarys_toolbar(chromium, explorer, width):
    # Issue #325: the chart library draws a row of its own icons (camera, zoom, pan,
    # select, reset) over the top right of every chart, 272 by 24px. On a desktop it
    # fades in under the pointer; on a phone it is always there and covers the top
    # axis label. Nothing on it is needed to read a chart.
    page = _loaded(chromium, explorer, width)
    try:
        found = _walked(page, SUITE_WALK)
    finally:
        page.close()

    assert found == [(name, [[0, 0]] * charts) for name, _, charts in SUITE_WALK]


# The same walk over the real record, which draws the charts the suite's snapshot
# cannot: the landscape and an archetype's timeline, a pilot's performance, a
# rivalry, and the race. Pilots is walked twice as Cards is, so the walk covers all
# nine of the app's plots. The picks are the acceptance script's own.
RECORD_WALK = [
    ("Meta", [("Or focus on specific archetypes", shots.ARCHETYPE)], 2),
    ("Archetypes", [("Archetype", shots.ARCHETYPE)], 2),
    ("Cards", [("Card", shots.CARD)], 1),
    ("Cards", [("View", "Co-occurrence"), ("Card", shots.CARD),
               ("Second card (optional)", shots.CARD_B)], 1),
    ("Hidden gems", [], 0),
    ("Pilots", [("Pilot", shots.PILOT)], 1),
    ("Pilots", [("View", "Head-to-head"), ("Pilot", shots.PILOT_RIVAL),
                ("Second pilot (required)", shots.PILOT_B)], 1),
    ("Player leaderboard", [], 1),
    ("FAQ", [], 0),
]


@pytest.mark.parametrize("width", list(shots.VIEWPORTS))
def test_no_chart_shows_the_chart_librarys_toolbar_on_the_real_record(chromium, record, width):
    page = _loaded(chromium, record, width)
    try:
        found = _walked(page, RECORD_WALK)
    finally:
        page.close()

    assert found == [(name, [[0, 0]] * charts) for name, _, charts in RECORD_WALK]


def test_the_box_over_a_data_point_is_set_in_the_apps_face_and_tokens(page):
    # Issue #325: the box the chart library draws over a hovered point was never
    # styled, so it took the library's own: Arial, on a fill taken from the trace
    # under the pointer, outlined and lettered in whichever of dark grey and white
    # contrasts with it (white on both, over this point). Read off the box as the
    # browser drew it, over a real point of the chart Meta opens on.
    point = page.locator(".js-plotly-plot .scatterlayer path.point").locator(
        "visible=true").first.bounding_box()
    x, y = point["x"] + point["width"] / 2, point["y"] + point["height"] / 2
    # Moved onto the point rather than placed on it: the library answers movement.
    page.mouse.move(x - 40, y - 40)
    page.mouse.move(x, y, steps=5)
    box = page.locator(".js-plotly-plot .hoverlayer .hovertext")
    box.wait_for()

    drawn = box.evaluate(
        """box => { const frame = getComputedStyle(box.querySelector('path'));
             const text = getComputedStyle(box.querySelector('text'));
             return {
               says: box.textContent,
               face: text.fontFamily.split(',')[0].replaceAll(/['"]/g, '').trim(),
               text: text.fill, surface: frame.fill, border: frame.stroke,
             }; }"""
    )

    assert "Grixis" in drawn.pop("says")
    assert drawn == {
        "face": FONTS["body"]["family"],
        "text": _rgb(TOKENS["text"]),
        "surface": _rgb(TOKENS["surface"]),
        "border": _rgb(TOKENS["border"]),
    }


def _picked_by_typing(page, tab: str, label: str, text: str) -> list[str]:
    """What a picker holds once ``text`` is typed into it and Enter is pressed.

    Keystrokes, as ``shots.pick`` types them, and then Enter where the script clicks
    an option. A single-select shows its pick as the field's text; a multiselect
    empties the field and adds the pick as a token beside it.
    """
    shots.tab(page, tab)
    box = shots.field(page, label)
    box.click()
    box.press("ControlOrMeta+a")
    box.press_sequentially(text)
    shots.wait(page, 500)
    box.press("Enter")
    shots.wait(page, 400)
    tokens = box.evaluate(
        "box => [...box.closest('.wrap').querySelectorAll('.token > span')]"
        ".map(token => token.textContent)"
    )
    return tokens or [box.input_value()]


def test_typing_a_full_name_and_pressing_enter_picks_that_name(page):
    # Issue #326: a picker keeps every option that holds the typed text anywhere and
    # Enter takes the first one kept, so "Swamp" then Enter picked "Snow-Covered
    # Swamp", which the suite's snapshot lists ahead of it by name and by decks alike.
    # Which option Enter lands on is Gradio's, so only a real page can say.
    assert _picked_by_typing(page, "Cards", "Card", "Swamp") == ["Swamp"]


@pytest.mark.parametrize("name", ["Grixis", "Oracle"])
def test_typing_an_archetype_and_pressing_enter_picks_it_on_the_real_record(
    chromium, record, name
):
    # The two the issue measured: "Grixis" picked "Bus Stop Grixis" and "Oracle"
    # picked "Noracle", in the Meta focus list and in the Archetypes picker alike.
    page = _loaded(chromium, record)
    try:
        focus = _picked_by_typing(page, "Meta", "Or focus on specific archetypes", name)
        (timeline,) = _picked_by_typing(page, "Archetypes", "Archetype", name)
    finally:
        page.close()

    assert focus == [name]
    assert re.fullmatch(rf"{name} \(\d+ events\)", timeline)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_a_first_visit_fetches_the_page_once_and_ends_dark(chromium, explorer, scheme):
    # Issue #327: every visit downloaded the page twice. It arrived, found no dark
    # marker in its address, and sent itself to the same address with one, with a
    # blank flash between: about 1.5s more from the live site. The page is counted
    # as the browser fetches it, under each setting an operating system can hand it,
    # at an address that carries a query of the visitor's own.
    context = chromium.new_context(color_scheme=scheme)
    page = context.new_page()
    documents = []
    page.on("request", lambda request: request.resource_type == "document"
            and documents.append(request.url))
    try:
        page.goto(explorer + "?from=a-link&year=2025", wait_until="networkidle")
        page.get_by_role("tab", name="Meta", exact=True).wait_for()
        # Long enough for a page that means to send itself elsewhere to have done so.
        page.wait_for_timeout(1000)
        dark, ground, query = page.evaluate(
            "() => [document.body.classList.contains('dark'),"
            " getComputedStyle(document.body).backgroundColor, location.search]"
        )
    finally:
        context.close()

    assert len(documents) == 1
    # Gradio's own dark rules are the ones in force, on the app's ground.
    assert dark
    assert ground == _rgb(TOKENS["bg"])
    # And the address is the one the visitor arrived at.
    assert query == "?from=a-link&year=2025"


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_page_as_served_is_dark_before_any_script_runs(chromium, explorer, scheme):
    # Issue #327: the stylesheet arrives with the page's script, so until that runs
    # the page is whatever the document itself paints, which was nothing: a white
    # screen ahead of a dark app. Loaded with scripts off, so what is measured is
    # the document as served and nothing a script did to it.
    context = chromium.new_context(color_scheme=scheme, java_script_enabled=False)
    page = context.new_page()
    try:
        page.goto(explorer, wait_until="load")
        painted = page.evaluate(
            """() => { for (const el of [document.body, document.documentElement]) {
                 const fill = getComputedStyle(el).backgroundColor;
                 if (fill !== 'rgba(0, 0, 0, 0)') return fill;
               } return 'nothing'; }"""
        )
    finally:
        context.close()

    assert painted == _rgb(TOKENS["bg"])


def test_the_page_is_one_capped_column_centred_on_a_wide_screen(chromium, explorer):
    # Issue #327: the page ran the width of the window, 1,856px of content at 1920,
    # and an FAQ answer ran the width of it. It is held to the column a 1440 window
    # already gave it, and that column sits in the middle.
    page = _loaded(chromium, explorer, "wide")
    try:
        left, right, window = page.evaluate(
            "() => { const box = " + _OPEN_TAB + ".getBoundingClientRect();"
            " return [box.left, box.right, document.documentElement.clientWidth]; }"
        )
    finally:
        page.close()

    assert window == 1920
    assert 1200 <= right - left <= 1280
    assert abs(left - (window - right)) <= 1


def _header_parts(page) -> dict[str, dict]:
    """The box of each part of the header, as the browser laid it out."""
    return page.evaluate(
        """() => Object.fromEntries(Object.entries({
             mark: '.brand-mark svg', name: '.brand-name',
             tabs: '[role=tablist]', freshness: '.freshness',
           }).map(([part, selector]) => {
             const box = document.querySelector(selector).getBoundingClientRect();
             return [part, {left: box.left, right: box.right, top: box.top,
                            bottom: box.bottom}];
           }))"""
    )


def test_one_slim_row_carries_the_mark_the_name_the_tabs_and_the_freshness(page):
    # Issue #327: the title, the tab strip and a heading repeating the open tab took
    # three rows, and the first result on Meta started 454px down a 900px window.
    # Where each part lands is the browser's answer to Gradio's markup and the
    # stylesheet together, so it is read off the running page.
    parts = _header_parts(page)
    order = [parts[part] for part in ("mark", "name", "tabs", "freshness")]

    # One row: there is a line across the page that passes through all four.
    assert max(box["top"] for box in order) < min(box["bottom"] for box in order)
    # Side by side in reading order, none over another.
    for before, after in zip(order, order[1:]):
        assert before["right"] <= after["left"]
    # And slim: from the top of the tallest part to the foot of the lowest.
    assert max(b["bottom"] for b in order) - min(b["top"] for b in order) <= 48

    assert page.locator(".brand-name").inner_text() == "7PH Graph"
    assert re.fullmatch(r"\d+ events(, updated \d{1,2} [A-Z][a-z]{2} \d{4})?",
                        page.locator(".freshness").inner_text())
    # Every tab is on the strip, with none scrolled out of it at this width.
    strip = page.locator("[role=tablist]")
    assert strip.get_by_role("tab").count() == 7
    assert strip.evaluate("strip => strip.scrollWidth <= strip.clientWidth")


def test_the_first_result_on_meta_starts_high_on_a_desktop(page):
    # AC (#328): at 1440 the first result card on Meta starts at or above 240px.
    # Under the three-row header it was 454px; the slim header (#327) brought it to
    # 363px; the rest was the control panel, a stack of full-width rows 205px tall,
    # now one row of 89px.
    #
    # The same AC asks for 320px at 390, or the position it does reach and what
    # holds it lower. It reaches 372px (from 477px), which the design doc records
    # with the reason, and which is not pinned here: a bound read off the page is
    # the page grading itself.
    top = page.evaluate(
        "() => " + _OPEN_TAB + ".querySelector('.insight-card').getBoundingClientRect().top"
    )

    assert top <= 240


@pytest.mark.parametrize("tab", ["Pilots", "Player leaderboard"])
def test_the_privacy_line_is_one_line_whose_link_opens_the_note_in_full(page, tab):
    # Issue #327: the note in full stood above the picker on the two tabs that name
    # people, 384px of a phone's 844. It is one line there now, and the note is the
    # FAQ's answer, on another tab: so the line's link has to open that tab and bring
    # the answer into view, which only a click on the running page can show.
    shots.tab(page, tab)
    line = page.locator(".privacy-line .t-body").locator("visible=true")

    height, one_line, drawn_below = line.evaluate(
        "line => [line.getBoundingClientRect().height,"
        " parseFloat(getComputedStyle(line).lineHeight),"
        " " + _OPEN_TAB + ".querySelector('.control-panel, .insight-card')"
        ".getBoundingClientRect().top - line.getBoundingClientRect().bottom]"
    )
    assert height == pytest.approx(one_line, abs=1)
    # Above the picker on Pilots, and above the chart on the leaderboard.
    assert drawn_below >= 0

    line.get_by_role("link").click()
    # Gradio draws a group as two nested elements and gives both its id; the outer
    # one is the box.
    note = page.locator("#faq-privacy").first
    note.wait_for()
    assert page.get_by_role("tab", name="FAQ", exact=True).get_attribute(
        "aria-selected") == "true"
    assert "I'll pull your identifiable records in the next update" in note.inner_text()
    # In view, not merely on the open tab: the FAQ is several screens long.
    assert 0 <= note.bounding_box()["y"] < page.viewport_size["height"] / 2
    # And the address is as it was, since nothing here is reached by one.
    assert page.evaluate("() => location.hash") == ""


def test_a_phone_gets_tabs_a_finger_can_hit_and_a_chart_with_room(chromium, explorer):
    # Issue #327, the phone floor. Tab buttons were 31px tall, under the 44px a
    # finger needs. And of a 390px screen a chart's plot area had 217px, the rest
    # going to the page's margin, the card's and the axis labels: the first two are
    # tightened, which is worth 48px to the chart.
    page = _loaded(chromium, explorer, "phone")
    try:
        parts = _header_parts(page)
        tabs = page.evaluate(
            "() => [...document.querySelectorAll('[role=tablist] [role=tab]')]"
            ".map(tab => tab.getBoundingClientRect().height)"
        )
        # How much of a tab's label shows at the strip's right edge, for the one tab
        # the edge cuts through: its text starts inside the strip and ends outside.
        cut_label_showing = page.evaluate(
            """() => { const edge = document.querySelector('[role=tablist]')
                 .getBoundingClientRect().right;
               return [...document.querySelectorAll('[role=tablist] [role=tab]')].map(tab => {
                 const text = document.createRange(); text.selectNodeContents(tab);
                 const box = text.getBoundingClientRect();
                 return box.left < edge && box.right > edge ? edge - box.left : 0;
               }).filter(Boolean); }"""
        )
        plot_area = page.evaluate(
            "() => " + _OPEN_TAB + ".querySelector('.js-plotly-plot .nsewdrag')"
            ".getBoundingClientRect().width"
        )
    finally:
        page.close()

    assert len(tabs) == 7 and min(tabs) >= 44
    # No room for one row here: the name and the freshness share a line, clear of
    # each other, and the strip has the full width of the line below.
    assert parts["name"]["right"] < parts["freshness"]["left"]
    assert parts["tabs"]["top"] >= max(parts["name"]["bottom"], parts["freshness"]["bottom"])
    # The strip scrolls sideways (#172), and what says so is a tab cut at its edge.
    # The tighter margin moved that edge into the gap between two tabs, where nothing
    # showed, so the tabs sit a little closer here: a label is cut, with enough of it
    # on screen to read as one.
    assert len(cut_label_showing) == 1 and cut_label_showing[0] >= 20
    assert plot_area >= 260


def _results_on_show(page):
    """The cards of a drawn view on the open tab."""
    return page.locator(".results-stack .insight-card").locator("visible=true")


def _draw_buttons(page) -> int:
    """How many Draw buttons the open tab shows."""
    return page.get_by_role("button", name="Draw", exact=True).locator("visible=true").count()


def test_picking_a_card_draws_it_with_no_button_press(page):
    # Issue #328: on Meta and Archetypes a pick redrew at once, and on Cards nothing
    # happened until a full-width Draw was pressed. The pick is the whole gesture
    # now, so there is no button on the tab to press.
    shots.tab(page, "Cards")
    assert _draw_buttons(page) == 0

    shots.pick(page, "Card", CARD)

    _results_on_show(page).first.wait_for(timeout=20_000)
    assert page.locator(".page-name").locator("visible=true").inner_text() == CARD


def test_picking_a_pilot_draws_it_with_no_button_press(page):
    # Issue #328, the other tab that waited on a Draw. One pilot is one subject, so
    # the overview has no button either.
    shots.tab(page, "Pilots")
    assert _draw_buttons(page) == 0

    shots.pick(page, "Pilot", PILOT)

    _results_on_show(page).first.wait_for(timeout=20_000)
    assert page.locator(".page-name").locator("visible=true").inner_text() == PILOT


def _chips(page) -> dict[str, list[str]]:
    """Each chip selector the open tab shows: its label, and the chips it offers."""
    return page.evaluate(
        "() => Object.fromEntries([..." + _OPEN_TAB + """.querySelectorAll('fieldset')]
             .filter(group => group.getBoundingClientRect().height > 0
                              && group.querySelector('input[type=radio]'))
             .map(group => [
               group.querySelector('[data-testid=block-info]').textContent.trim(),
               [...group.querySelectorAll('label')].map(chip => chip.textContent.trim()),
             ]))"""
    )


def _dropdown_labels(page) -> list[str]:
    """The label of every dropdown the open tab shows."""
    return page.evaluate(
        "() => [..." + _OPEN_TAB + """.querySelectorAll('input[role=combobox]')]
             .filter(box => box.getBoundingClientRect().height > 0)
             .map(box => box.closest('.block').querySelector('[data-testid=block-info]')
                            .textContent.trim())"""
    )


def test_the_small_options_are_chips_and_a_chip_redraws(page):
    # Issue #328: View, Board, Year and Cards to show were each a dropdown on a row of
    # its own, two clicks to change one of three values. Each is a row of chips, as
    # the Meta cut always was, and only a search is left as a dropdown.
    assert list(_chips(page).values()) == [["Top 25%", "Top 50%", "Top 75%"]]
    assert _dropdown_labels(page) == ["Or focus on specific archetypes"]

    shots.tab(page, "Archetypes")
    (year, years), = _chips(page).items()
    assert year.startswith("Year") and years and all(y.isdigit() for y in years)
    assert _dropdown_labels(page) == ["Archetype", "Second archetype (optional)"]

    shots.tab(page, "Pilots")
    assert _chips(page) == {"View": ["Pilot overview", "Head-to-head"]}
    assert _dropdown_labels(page) == ["Pilot"]

    shots.tab(page, "Cards")
    assert _chips(page) == {
        "View": ["Card overview", "Co-occurrence"],
        "Board": ["Main or side", "Main", "Side"],
    }
    assert _dropdown_labels(page) == ["Card"]

    # With no button, a chip has to redraw what it changes: a filter, then the view.
    captions = page.locator(".results-stack .caption").locator("visible=true")
    shots.pick(page, "Card", CARD)
    _results_on_show(page).first.wait_for(timeout=20_000)
    shots.chip(page, "Board", "Side")
    captions.filter(has_text="on the side board").first.wait_for(timeout=20_000)
    shots.chip(page, "View", "Co-occurrence")
    page.locator(".results-stack .t-result-title", has_text="Co-occurrence").locator(
        "visible=true").wait_for(timeout=20_000)
    shots.chip(page, "Cards to show", "5")
    captions.filter(has_text="The 5 cards").wait_for(timeout=20_000)
    assert _chips(page) == {
        "View": ["Card overview", "Co-occurrence"],
        "Cards to show": ["5", "15", "25"],
    }
    assert _dropdown_labels(page) == ["Card", "Second card (optional)"]


def test_the_head_to_head_keeps_one_draw_that_waits_for_both_pilots(page):
    # Issue #328: a rivalry needs two pilots, so the first pick cannot draw it and
    # this is the one view that keeps a Draw. It used to take a press with a pilot
    # missing, and answered with nothing; now it takes none until both are chosen.
    shots.tab(page, "Pilots")
    shots.chip(page, "View", "Head-to-head")
    button = page.get_by_role("button", name="Draw", exact=True).locator("visible=true")
    assert button.count() == 1 and button.is_disabled()

    shots.pick(page, "Pilot", PILOT)
    shots.wait(page, 1500)
    assert button.is_disabled()
    assert _results_on_show(page).count() == 0

    shots.pick(page, "Second pilot (required)", PILOT_B)
    shots.wait(page, 500)
    assert button.is_enabled()
    button.click()
    _results_on_show(page).first.wait_for(timeout=20_000)
    assert page.locator(".subject-line").locator("visible=true").inner_text() == (
        f"Head-to-head {PILOT} vs {PILOT_B}"
    )

    # Taking a pilot away takes the rivalry down and closes the Draw again.
    page.locator("button.clear-btn").locator("visible=true").last.click()
    shots.wait(page, 1000)
    assert button.is_disabled()
    assert _results_on_show(page).count() == 0


def _placeholders(page) -> dict[str, str]:
    """The placeholder of every search the open tab shows, by the search's label."""
    return page.evaluate(
        "() => Object.fromEntries([..." + _OPEN_TAB + """.querySelectorAll('input[role=combobox]')]
             .filter(box => box.getBoundingClientRect().height > 0)
             .map(box => [box.closest('.block').querySelector('[data-testid=block-info]')
                             .textContent.trim(), box.placeholder]))"""
    )


# Every search in the app: its tab, the chip that brings it on screen where it is
# not there on open, its label, and what it searches.
SEARCHES = [
    ("Meta", None, "Or focus on specific archetypes", "archetypes"),
    ("Archetypes", None, "Archetype", "archetypes"),
    ("Archetypes", None, "Second archetype (optional)", "archetypes"),
    ("Cards", None, "Card", "cards"),
    ("Cards", "Co-occurrence", "Second card (optional)", "cards"),
    ("Pilots", None, "Pilot", "pilots"),
    ("Pilots", "Head-to-head", "Second pilot (required)", "pilots"),
]


def test_every_search_says_what_it_searches_and_how_many(page):
    # Issue #328: no search field said anything, so an empty box was all that
    # invited a first pick. Each says what it holds and how many. The count is
    # graded against what the field itself offers, read off the page's own
    # description of its controls. And the words are all there is: no placeholder
    # names a pilot, since nobody should open the page to find themselves on it.
    offered = page.evaluate(
        "() => Object.fromEntries(window.gradio_config.components"
        ".filter(control => control.type === 'dropdown')"
        ".map(control => [control.props.label, control.props.choices.length]))"
    )

    found = {}
    for tab, view, label, _ in SEARCHES:
        shots.tab(page, tab)
        if view:
            shots.chip(page, "View", view)
        found[label] = _placeholders(page)[label]

    assert found == {
        label: f"Search {offered[label]:,} {things}" for _, _, label, things in SEARCHES
    }


def _example_chips(page):
    """The example chips the open tab offers."""
    return page.locator(".examples button").locator("visible=true")


def test_cards_opens_on_examples_that_draw_in_one_click_and_pilots_offers_none(page):
    # Issue #328: Cards opened as an empty box over empty ground. It offers two or
    # three cards to try, and one click on a chip is a pick like any other. Pilots
    # offers none, because an example there is a person put on show unasked.
    shots.tab(page, "Cards")
    names = _example_chips(page).all_inner_texts()
    assert 2 <= len(names) <= 3

    _example_chips(page).first.click()
    _results_on_show(page).first.wait_for(timeout=20_000)
    assert page.locator(".page-name").locator("visible=true").inner_text() == names[0]
    assert shots.field(page, "Card").input_value() == names[0]
    # An invitation to a first pick, so it leaves once there is one, and is back
    # when the card is cleared.
    assert _example_chips(page).count() == 0
    page.locator("button.clear-btn").locator("visible=true").first.click()
    _example_chips(page).first.wait_for()
    assert _example_chips(page).all_inner_texts() == names

    shots.tab(page, "Pilots")
    assert _example_chips(page).count() == 0
    assert page.locator(".examples").locator("visible=true").count() == 0


def _controls(page) -> list[dict]:
    """The box of every control in the open tab's control panel, in page order: a
    field with its label, or a button that acts. A field's clear button is part of
    the field."""
    return page.evaluate(
        "() => [..." + _OPEN_TAB + """.querySelector('.control-panel')
               .querySelectorAll('.block, button.primary')]
             .filter(control => control.getBoundingClientRect().height > 0)
             .map(control => { const box = control.getBoundingClientRect();
               const label = control.querySelector('[data-testid=block-info]');
               return {name: (label || control).textContent.trim(),
                       search: !!control.querySelector('input[role=combobox]'),
                       label: label ? label.getBoundingClientRect().top : null,
                       top: box.top, bottom: box.bottom, left: box.left,
                       right: box.right, width: box.width}; })"""
    )


def _panel(page) -> dict:
    """The open tab's control panel: its height, and how far short of the row's far
    edge its last control stops."""
    return page.evaluate(
        "() => { const panel = " + _OPEN_TAB + """.querySelector('.control-panel');
            const row = panel.querySelector('.styler').getBoundingClientRect();
            const ends = [...panel.querySelectorAll('.block, button')]
              .map(control => control.getBoundingClientRect())
              .filter(box => box.height > 0).map(box => box.right);
            return {height: panel.getBoundingClientRect().height,
                    short: row.right - Math.max(...ends)}; }"""
    )


def _label_lines(controls: list[dict]) -> set[float]:
    """The lines the controls' labels are set on, as tops to a tenth of a pixel. A
    button and the one checkbox carry no label."""
    return {round(c["label"], 1) for c in controls if c["label"] is not None}


# Every state of every control panel: the tab, and the chip that turns it to its
# other view.
PANELS = [
    ("Meta", None), ("Archetypes", None),
    ("Cards", None), ("Cards", "Co-occurrence"),
    ("Pilots", None), ("Pilots", "Head-to-head"),
]


def _assert_every_panel_is_one_row(page) -> None:
    """Every state of every control panel, as one row: side by side on one line,
    the subject's search the widest thing in it, a button the width of its label."""
    for tab, view in PANELS:
        shots.tab(page, tab)
        if view:
            shots.chip(page, "View", view)
        controls = _controls(page)
        state = f"{tab} {view or ''}"

        assert len(controls) >= 2, state
        # One row: a line across the page passes through every control.
        assert max(c["top"] for c in controls) < min(c["bottom"] for c in controls), state
        # Side by side, none over another.
        for before, after in zip(controls, controls[1:]):
            assert before["right"] <= after["left"], state
        # To within half a pixel: two searches on even shares come out a sixty-fourth
        # of a pixel apart where the browser's layout rounds them so.
        subject = next(c for c in controls if c["search"])
        assert subject["width"] == pytest.approx(
            max(c["width"] for c in controls), abs=0.5), state
        # The labels on one line, and the row run out to the panel's far edge: a
        # search takes the room that is left, so no row stops short of it.
        assert len(_label_lines(controls)) == 1, state
        assert _panel(page)["short"] <= 1, state
        for button in (c for c in controls if c["name"] == "Draw"):
            assert button["width"] <= 120, state


@pytest.mark.parametrize("width", ["desktop", "wide"])
def test_each_tabs_controls_are_one_row_on_a_desktop(chromium, explorer, width):
    # Issue #328: the control panel was a stack of full-width rows, 334px tall on
    # Cards, with a Draw 1,174px wide at 1440. Each tab's controls are one row: the
    # subject's search the widest thing in it, chips at the width of their words,
    # and a button the width of its label.
    page = _loaded(chromium, explorer, width)
    try:
        _assert_every_panel_is_one_row(page)
    finally:
        page.close()


@pytest.mark.parametrize("width", ["desktop", "wide"])
def test_each_tabs_controls_are_one_row_on_the_real_record(chromium, record, width):
    # The same row with the record's own words in it: four years of chips, and a
    # cut that names its year.
    page = _loaded(chromium, record, width)
    try:
        _assert_every_panel_is_one_row(page)
    finally:
        page.close()


def test_focusing_an_archetype_leaves_the_meta_row_on_its_line(page):
    # The maintainer's look at #328: the focus search was 1.6px taller than the cut's
    # chips and 9px taller once it held a pick, and the row lined its controls up on
    # their bottoms, so the search's label sat above the chips' and the panel grew
    # under the reader on a first pick.
    empty = _panel(page)["height"]

    shots.pick(page, "Or focus on specific archetypes", "Grixis")
    page.keyboard.press("Escape")
    _card_titled(page, "Selected archetypes").wait_for(timeout=30_000)

    assert len(_label_lines(_controls(page))) == 1
    assert _panel(page)["height"] == empty


def test_a_head_to_head_searches_its_two_pilots_in_fields_of_one_size(page):
    # The maintainer's look at #328: the first pilot's search was 448px and the
    # second's 266px. The two pilots of a rivalry are peers, and between them they
    # still take the room the row has left.
    shots.tab(page, "Pilots")
    shots.chip(page, "View", "Head-to-head")

    first, second = (c["width"] for c in _controls(page) if c["search"])
    assert first == pytest.approx(second, abs=0.5)


def test_each_tabs_controls_stack_on_a_phone(chromium, explorer):
    # The same row at 390px, where there is no room for it: one control under
    # another, each the width of the panel.
    page = _loaded(chromium, explorer, "phone")
    try:
        for tab, view in PANELS:
            shots.tab(page, tab)
            if view:
                shots.chip(page, "View", view)
            controls = _controls(page)
            state = f"{tab} {view or ''}"

            assert len(controls) >= 2, state
            for before, after in zip(controls, controls[1:]):
                assert before["bottom"] <= after["top"], state
            assert max(c["right"] for c in controls) <= 390, state
    finally:
        page.close()


def _settled_top(page, result) -> float:
    """Where the top of ``result`` comes to rest in the window, once it is drawn
    and the page has stopped moving."""
    result.wait_for(timeout=30_000)
    last = None
    for _ in range(40):
        page.wait_for_timeout(150)
        top = result.bounding_box()["y"]
        if top == last:
            return top
        last = top
    raise AssertionError("the page never came to rest")


def _card_titled(page, title: str):
    """The card on show whose title is ``title``. Gradio draws a group as two nested
    elements; the outer one is the card."""
    return page.locator(".insight-card", has=page.locator(
        ".t-result-title", has_text=title)).locator("visible=true").first


def _in_view(page, top: float) -> bool:
    """No lower than the middle of the window, give or take a pixel of rounding: a
    result whose first pixel is at the window's foot is on screen and shows nothing."""
    return 0 <= top <= page.viewport_size["height"] / 2 + 1


def test_a_pick_brings_its_result_into_view(page):
    # Issue #328, at 1440 by 900: a focus archetype on Meta drew its chart 965px
    # down a 900px window, so the pick looked like it had done nothing. The page
    # brings what a pick drew into view.
    assert page.viewport_size == {"width": 1440, "height": 900}

    shots.pick(page, "Or focus on specific archetypes", "Grixis")
    page.keyboard.press("Escape")
    focus = _card_titled(page, "Selected archetypes")
    assert _in_view(page, _settled_top(page, focus))

    subject = page.locator(".view-body", has=page.locator(".page-head")).locator(
        "visible=true").first
    shots.tab(page, "Cards")
    shots.pick(page, "Card", CARD)
    assert _in_view(page, _settled_top(page, subject))

    shots.tab(page, "Pilots")
    shots.pick(page, "Pilot", PILOT)
    assert _in_view(page, _settled_top(page, subject))


def test_a_pick_brings_its_result_into_view_on_the_real_record(chromium, record):
    # The picks the suite's snapshot cannot draw: an archetype's timeline, which
    # landed 1,330px down a 900px window, and a rivalry.
    page = _loaded(chromium, record)
    try:
        shots.tab(page, "Archetypes")
        shots.pick(page, "Archetype", shots.ARCHETYPE)
        timeline = _settled_top(page, _card_titled(page, "Finishes over time"))

        shots.tab(page, "Pilots")
        shots.chip(page, "View", "Head-to-head")
        shots.pick(page, "Pilot", shots.PILOT_RIVAL)
        shots.pick(page, "Second pilot (required)", shots.PILOT_B)
        page.get_by_role("button", name="Draw", exact=True).locator("visible=true").click()
        rivalry = _settled_top(page, page.locator(
            ".view-body", has=page.locator(".subject-line")).locator("visible=true").first)
        in_view = [_in_view(page, timeline), _in_view(page, rivalry)]
    finally:
        page.close()

    assert in_view == [True, True]


def test_a_result_brought_into_view_on_a_phone_leaves_its_controls_on_screen(
    chromium, explorer
):
    # Issue #328: at 390px the controls stack, so a card's result starts below the
    # middle of the window and the page moves to show it. It moves the least that
    # does: put at the top of the window, the result took the controls off the
    # screen while the reader was still using them.
    page = _loaded(chromium, explorer, "phone")
    try:
        shots.tab(page, "Cards")
        shots.pick(page, "Card", CARD)
        result = page.locator(".view-body", has=page.locator(".page-head")).locator(
            "visible=true").first
        top = _settled_top(page, result)
        in_view, moved = _in_view(page, top), page.evaluate("() => window.scrollY")
        controls = _controls(page)
    finally:
        page.close()

    # The page did move, and the result and every control are on the screen.
    assert moved > 0
    assert in_view
    assert min(c["top"] for c in controls) >= 0


def test_a_pick_says_it_is_drawing_while_its_query_runs(page):
    # Issue #328: the Draw button used to say "Drawing…" while its query ran, and a
    # pick has no button. The results are hidden until they are ready, so the word
    # is a line of its own where they will land. Held open with a second of latency
    # on the page's requests, as the acceptance script's mid-query shot is: a local
    # query answers faster than the line can be seen.
    shots.tab(page, "Pilots")
    line = page.locator(".t-state", has_text="Drawing…").locator("visible=true")
    assert line.count() == 0

    shots.throttle(page, 1000)
    try:
        shots.pick(page, "Pilot", PILOT)
        line.wait_for(timeout=10_000)
        assert _results_on_show(page).count() == 0
    finally:
        shots.throttle(page, 0)

    _results_on_show(page).first.wait_for(timeout=20_000)
    line.wait_for(state="hidden", timeout=10_000)


def _channels(colour: str) -> list[float]:
    """A computed ``rgb()`` or ``rgba()`` colour as its numbers."""
    return [float(n) for n in re.findall(r"[\d.]+", colour)]


def _over(colour: str, ground: str) -> str:
    """``colour`` as it is seen on an opaque ``ground``, as ``#rrggbb``."""
    red, green, blue, *alpha = _channels(colour)
    alpha = alpha[0] if alpha else 1.0
    return "#" + "".join(
        f"{round(top * alpha + under * (1 - alpha)):02x}"
        for top, under in zip((red, green, blue), _channels(ground))
    )


@pytest.mark.parametrize("tab", ["Meta", "Archetypes", "Cards", "Pilots"])
def test_what_sits_on_the_control_panel_is_legible_there(page, tab):
    # Issue #328: a control used to carry a surface of its own, and its label and
    # its clear button were read against that. In the row they sit on the panel,
    # which is a step lighter: the label's muted ink measured 4.23 there and the
    # clear button's 4.29, both under the 4.5 of WCAG AA. Read off the running page,
    # since the ground a text ends up on is Gradio's markup as much as the app's.
    shots.tab(page, tab)
    panel, label, clear, clear_fill = page.evaluate(
        "() => { const panel = " + _OPEN_TAB + """.querySelector('.control-panel');
             const style = selector => getComputedStyle(panel.querySelector(selector));
             return [getComputedStyle(panel).backgroundColor,
                     style('[data-testid=block-info]').color,
                     style('.clear-btn').color, style('.clear-btn').backgroundColor]; }"""
    )

    assert panel == _rgb(TOKENS["surface-2"])
    assert contrast_ratio(_over(label, panel), _over(panel, panel)) >= 4.5
    # The clear button's fill is a tint, so its ground is that tint over the panel.
    assert contrast_ratio(_over(clear, panel), _over(clear_fill, panel)) >= 4.5


# --- The pilot and card pages (issue #329) ---


# What the open tab draws from the top down: the page head's three parts, then each
# card by its title.
_PAGE_FROM_THE_TOP = "() => { const open = " + _OPEN_TAB + """;
    const shown = el => el.getBoundingClientRect().height > 0;
    const parts = [...open.querySelectorAll(
      '.page-name, .page-descriptor, .tiles, .insight-card .t-result-title')].filter(shown);
    return {
      order: parts.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top)
        .map(el => el.classList.contains('t-result-title') ? el.innerText : el.className),
      tiles: [...open.querySelectorAll('.tile')].filter(shown).length,
      face: getComputedStyle(open.querySelector('.page-name')).fontFamily,
      rows: [...open.querySelectorAll('table.data-table')].filter(shown)
        .map(table => table.querySelectorAll('tbody tr').length),
    }; }"""

_HEAD = ["page-name", "page-descriptor", "tiles"]


def _display_face(read: dict) -> bool:
    return read["face"].strip("'\"").startswith(FONTS["display"]["family"])


def test_a_drawn_pilot_page_runs_head_then_graphs_then_record(page):
    # Issue #329: a pilot's page was graphs first with no table. It opens with the
    # name in the display face, a descriptor and four tiles, and closes with the
    # whole record. The suite's pilot has no year to average, so no chart is drawn.
    shots.tab(page, "Pilots")
    shots.pick(page, "Pilot", PILOT)
    _results_on_show(page).first.wait_for(timeout=20_000)

    read = page.evaluate(_PAGE_FROM_THE_TOP)

    assert read["order"] == [*_HEAD, "Neighbourhood", "Archetype affinity", "Record"]
    assert read["tiles"] == 4 and _display_face(read)
    assert read["rows"] == [2]  # the pilot's two results


def test_a_drawn_pilot_page_puts_the_record_after_the_chart_on_the_real_record(
    chromium, record
):
    page = _loaded(chromium, record)
    try:
        shots.tab(page, "Pilots")
        shots.pick(page, "Pilot", shots.PILOT)
        _card_titled(page, "Performance over time").wait_for(timeout=30_000)
        read = page.evaluate(_PAGE_FROM_THE_TOP)
    finally:
        page.close()

    assert read["order"] == [*_HEAD, "Neighbourhood", "Archetype affinity",
                             "Performance over time", "Record"]
    assert read["tiles"] == 4 and read["rows"][0] > 2


def test_a_drawn_card_page_opens_with_its_head_and_carries_a_list_on_each_view(page):
    # Issue #329: the usage graph's archetypes as a table grouped by broad class, and
    # on the co-occurrence view the cards drawn beside the subject, ranked.
    shots.tab(page, "Cards")
    shots.pick(page, "Card", CARD)
    _card_titled(page, "Archetypes").wait_for(timeout=20_000)
    overview = page.evaluate(_PAGE_FROM_THE_TOP)

    shots.chip(page, "View", "Co-occurrence")
    _card_titled(page, "Played with").wait_for(timeout=20_000)
    cooccurrence = page.evaluate(_PAGE_FROM_THE_TOP)

    assert overview["order"] == [*_HEAD, "Usage", "Adoption over time", "Archetypes"]
    assert overview["tiles"] == 4 and _display_face(overview)
    # Brainstorm is in all three decks: two broad classes, each heading one archetype.
    assert overview["rows"] == [4]
    assert cooccurrence["order"] == [
        *_HEAD, "Co-occurrence", "Adoption over time", "Played with"]
    assert cooccurrence["tiles"] == 4 and cooccurrence["rows"] == [15]


# Every table on show on the open tab: how far it runs past the inside of its card
# (in CSS pixels, 0 where it fits), and how its rows and numbers are set.
_TABLES = "(accent) => { const open = " + _OPEN_TAB + """;
    return [...open.querySelectorAll('table.data-table')]
      .filter(table => table.getBoundingClientRect().height > 0).map(table => {
        const card = table.closest('.insight-card');
        const box = card.getBoundingClientRect(), style = getComputedStyle(card);
        const inside = {
          left: box.left + parseFloat(style.paddingLeft) + parseFloat(style.borderLeftWidth),
          right: box.right - parseFloat(style.paddingRight) - parseFloat(style.borderRightWidth),
        };
        const rect = table.getBoundingClientRect();
        const set = (els, property) => [...new Set(els.map(el => getComputedStyle(el)[property]))];
        const numbers = [...table.querySelectorAll('.num')];
        return {
          past: Math.max(0, rect.right - inside.right, inside.left - rect.left),
          fills: set([...table.querySelectorAll('tr, th, td')], 'backgroundColor'),
          numbers: numbers.length,
          aligned: set(numbers, 'textAlign'),
          figures: set(numbers, 'fontVariantNumeric'),
          // What is drawn in the accent, by tag: only a link may be.
          accented: [...new Set([...table.querySelectorAll('*')]
            .filter(el => accent.includes(getComputedStyle(el).color))
            .map(el => el.tagName.toLowerCase()))],
        }; }); }"""

_ACCENTS = [_rgb(TOKENS["accent"]), _rgb(TOKENS["accent-bright"])]


def _sideways_scroll(page) -> int:
    return page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")


def _assert_tables_fit_and_are_plain(page, where: str) -> None:
    tables = page.evaluate(_TABLES, _ACCENTS)
    assert tables, where
    for table in tables:
        assert table["past"] <= 0.5, (where, table)
        # Hairline rows only: no row, header or cell is filled.
        assert table["fills"] == ["rgba(0, 0, 0, 0)"], (where, table)
        assert table["numbers"] > 0, where
        assert table["aligned"] == ["right"], (where, table)
        assert table["figures"] == ["tabular-nums"], (where, table)
        assert set(table["accented"]) <= {"a"}, (where, table)
    assert _sideways_scroll(page) <= 0, where


@pytest.mark.parametrize("width", list(shots.VIEWPORTS))
def test_no_table_runs_past_its_card_and_each_is_hairline_rows_and_set_numbers(
    chromium, explorer, width
):
    # Issue #329: one table for every page, which fits a phone. Text breaks wherever
    # it must and a number never does, so a table is as wide as its card and no wider.
    page = _loaded(chromium, explorer, width)
    try:
        shots.tab(page, "Pilots")
        shots.pick(page, "Pilot", PILOT)
        _card_titled(page, "Record").wait_for(timeout=20_000)
        _assert_tables_fit_and_are_plain(page, "Pilots")

        shots.tab(page, "Cards")
        shots.pick(page, "Card", CARD)
        _card_titled(page, "Archetypes").wait_for(timeout=20_000)
        _assert_tables_fit_and_are_plain(page, "Cards, overview")
        shots.chip(page, "View", "Co-occurrence")
        _card_titled(page, "Played with").wait_for(timeout=20_000)
        _assert_tables_fit_and_are_plain(page, "Cards, co-occurrence")
    finally:
        page.close()


@pytest.mark.parametrize("width", list(shots.VIEWPORTS))
def test_no_table_runs_past_its_card_on_any_tab_of_the_real_record(chromium, record, width):
    # The tables the suite's snapshot cannot fill or draw at all. At 390px the
    # standings were 306px wide in a 284px card and the gems table ran 125 to 157px
    # past its card (#314); a pilot's record here runs to dozens of events with names
    # as long as TabernacleGamesHighlanderChampionship2023, and a staple card's
    # archetypes to over a hundred rows.
    page = _loaded(chromium, record, width)
    try:
        for tab in ("Hidden gems", "Player leaderboard"):
            shots.tab(page, tab)
            shots.wait(page, 500)
            _assert_tables_fit_and_are_plain(page, tab)

        shots.tab(page, "Pilots")
        shots.pick(page, "Pilot", shots.PILOT)
        _card_titled(page, "Record").wait_for(timeout=30_000)
        _assert_tables_fit_and_are_plain(page, "Pilots")

        shots.tab(page, "Cards")
        shots.pick(page, "Card", shots.CARD_B)
        _card_titled(page, "Archetypes").wait_for(timeout=30_000)
        _assert_tables_fit_and_are_plain(page, "Cards, overview")
        shots.chip(page, "View", "Co-occurrence")
        _card_titled(page, "Played with").wait_for(timeout=30_000)
        _assert_tables_fit_and_are_plain(page, "Cards, co-occurrence")
    finally:
        page.close()


# Every caption on show on the open tab: its colour, how many figures it sets apart,
# and whether any card still carries a caption in one of the two older roles.
_CAPTIONS = "() => { const open = " + _OPEN_TAB + """;
    const shown = el => el.getBoundingClientRect().height > 0;
    return {
      captions: [...open.querySelectorAll('.caption')].filter(shown).map(el => ({
        said: el.innerText, colour: getComputedStyle(el).color,
        figures: el.querySelectorAll('.figure').length,
      })),
      older: [...open.querySelectorAll('.insight-card .t-caption, .insight-card .t-fieldstat')]
        .filter(shown).map(el => el.innerText),
    }; }"""


def test_a_caption_on_the_pilot_and_card_pages_is_text_with_at_most_one_figure(page):
    # The caption rule (#329): a caption was set in the accent, the colour of what can
    # be clicked. It is ordinary text in the text colour, with at most one figure
    # set apart, on every card of both tabs.
    read = []
    shots.tab(page, "Pilots")
    shots.pick(page, "Pilot", PILOT)
    _card_titled(page, "Record").wait_for(timeout=20_000)
    read.append(page.evaluate(_CAPTIONS))
    shots.tab(page, "Cards")
    shots.pick(page, "Card", CARD)
    _card_titled(page, "Archetypes").wait_for(timeout=20_000)
    read.append(page.evaluate(_CAPTIONS))
    shots.chip(page, "View", "Co-occurrence")
    _card_titled(page, "Played with").wait_for(timeout=20_000)
    read.append(page.evaluate(_CAPTIONS))

    for view in read:
        assert len(view["captions"]) >= 2
        assert view["older"] == []
        for caption in view["captions"]:
            assert caption["colour"] == _rgb(TOKENS["text"]), caption
            assert caption["figures"] <= 1, caption
            assert "·" not in caption["said"] and "node" not in caption["said"], caption
