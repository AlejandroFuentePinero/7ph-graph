"""What the explorer draws round its results, measured on a real browser.

Issue #324: Gradio draws a group as a surface of its own, filled with the border
colour so the gaps between its children read as dividers. Inside a card or the
control panel the app answers for that surface. The groups that only hold a view's
results did not, so on Cards and Pilots the border colour showed as a strip behind
the subject line, between the cards and under the last one. Which element carries
the fill is Gradio's markup and not the app's, so no test that reads the stylesheet
or the component tree can see it. These ask the running page.

The explorer itself, over the suite's small snapshot, since the groups under test
are the ones it nests and shows on a Draw. It is driven with the acceptance
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
from graph7ph.theme import FONTS, TOKENS

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
        shots.pick(page, "View", view)
    for label, name in picks:
        shots.pick(page, label, name)

    # Before a Draw the view's group is empty, and should be no more than that.
    assert _filled_with_the_border_colour(page) == []

    shots.draw(page, settle=500)
    # The results are on the page, so the second reading is of a drawn view.
    page.locator(".results-stack .insight-card").locator("visible=true").first.wait_for()
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
                "() => { const table = " + _OPEN_TAB + """.querySelector('table.leaderboard');
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
            shots.pick(page, label, text)
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
    # AC (#327): at 1440 the first result card on Meta starts at or above 370px,
    # from 454px under the three-row header.
    top = page.evaluate(
        "() => " + _OPEN_TAB + ".querySelector('.insight-card').getBoundingClientRect().top"
    )

    assert top <= 370


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
