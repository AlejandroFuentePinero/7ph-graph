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
import json
import re
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import gradio as gr
import pytest

from graph7ph import community, palette
from graph7ph.app import (
    _FAQ_ENTRIES,
    _FIT_EVENTS_JS,
    _LANDSCAPE_NAMES_JS,
    _archetype_timeline_figure,
    _landscape_figure,
    build_app,
    launch_kwargs,
)
from graph7ph.build import build_graph
from graph7ph.db import artifact_path, database_path, open_for_reading, rows
from graph7ph.figures import figures_path
from graph7ph.models import load_snapshot
from graph7ph.provenance import staleness
from graph7ph.query import card_deck_counts
from graph7ph.theme import FONTS, HEADER_ONE_ROW_PX, TOKENS, contrast_ratio
from graph7ph.trends import ArchetypeTimelinePoint, LandscapeCell, Series

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


def _loaded(chromium, url: str, width: str = "desktop", on: str | None = "Metagame"):
    """The explorer freshly loaded at one of the acceptance script's three widths,
    with the tab ``on`` opened.

    A visit opens on Community (issue #346). Almost every test here is about a tab
    behind it and was written when a visit opened on Metagame, so that is the tab
    opened unless a test asks for another, or for the page as a visit finds it
    (``on=None``).
    """
    tab = chromium.new_page(**shots.VIEWPORTS[width])
    tab.goto(url, wait_until="networkidle")
    tab.get_by_role("tab", name="Metagame", exact=True).wait_for()
    if on:
        shots.tab(tab, on)
    return tab


@pytest.fixture(scope="module")
def suite_bundle(tmp_path_factory, snapshot_dir) -> Path:
    """The suite's snapshot, built: the bundle the explorer below is over."""
    artifact = tmp_path_factory.mktemp("explorer") / "graph"
    build_graph(load_snapshot(snapshot_dir), artifact)
    return artifact


@pytest.fixture(scope="module")
def explorer(suite_bundle):
    """The explorer over the suite's snapshot."""
    with _explorer_over(suite_bundle) as url:
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
    """A fresh load for each test, so none inherits a Draw, with Metagame open."""
    tab = _loaded(chromium, explorer)
    yield tab
    tab.close()


@pytest.fixture
def landing(chromium, explorer):
    """A fresh load left as a visit finds it: on the Community page, drawn."""
    tab = _loaded(chromium, explorer, on=None)
    tab.locator("circle.cm-event").first.wait_for()
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


# The standings on the open tab: where the table and the chart under it start, the
# two ends the header names over the tracks, and for each row its rank, the track its
# rank range is drawn on, the bar and the tick drawn on it, and the range written
# beside it.
_STANDINGS = "() => { const open = " + _OPEN_TAB + """;
    const box = el => { const r = el.getBoundingClientRect();
                        return {left: r.left, right: r.right, top: r.top, bottom: r.bottom,
                                width: r.width}; };
    const shown = el => el.getBoundingClientRect().height > 0;
    const table = open.querySelector('table.data-table');
    return {
      first: [...open.querySelectorAll('.insight-card')].filter(shown)[0].contains(table),
      table: box(table),
      chart: box(open.querySelector('.js-plotly-plot')),
      ends: [...table.querySelectorAll('thead .rank-ends span')].map(box),
      named: table.querySelector('thead .rank-ends').textContent,
      rows: [...table.querySelectorAll('tbody tr')].map(row => ({
        rank: row.querySelector('td').innerText,
        track: box(row.querySelector('.rank-track')),
        bar: box(row.querySelector('.rank-bar')),
        mark: box(row.querySelector('.rank-mark')),
        said: row.querySelector('.rank-numbers').innerText,
        ink: getComputedStyle(row.querySelector('.rank-numbers')).color,
      })),
    }; }"""


def test_the_standings_are_the_first_result_on_the_leaderboard_on_the_real_record(
    chromium, record
):
    # Issue #336: the tab opened on its chart and the standings started a screen
    # down (985px at 1440 by 900). They are the first result now, on the first
    # screen, and the chart follows them. The suite's snapshot has no race, so this
    # is the real record's.
    page = _loaded(chromium, record)
    try:
        assert page.viewport_size == {"width": 1440, "height": 900}
        shots.tab(page, "Player leaderboard")
        read = page.evaluate(_STANDINGS)
    finally:
        page.close()

    assert read["first"]
    assert _in_view(page, read["table"]["top"])
    assert read["chart"]["top"] > read["table"]["bottom"]


@pytest.mark.parametrize("width", list(shots.VIEWPORTS))
def test_each_standings_row_draws_the_rank_range_it_states_on_the_real_record(
    chromium, record, width
):
    # Issue #336: a row's rank range is drawn as a bar beside the numbers that state
    # it. Where the bar lands is the stylesheet's arithmetic on what the markup
    # carries, so only the drawn page can say the two agree: each bar is measured
    # against its track, read back as ranks, and held to the numbers beside it. A
    # track is every place in the field, so its scale is the count of contenders the
    # card's own caption states.
    page = _loaded(chromium, record, width)
    try:
        shots.tab(page, "Player leaderboard")
        caption = _card_titled(page, "Standings").inner_text()
        read = page.evaluate(_STANDINGS)
    finally:
        page.close()

    places = int(re.search(r"of (\d+) contenders", caption).group(1))
    rows = read["rows"]
    assert len(rows) == 50
    # One scale: every row's track starts at the same point and is the same length.
    assert len({(round(row["track"]["left"], 1), round(row["track"]["width"], 1))
                for row in rows}) == 1
    for row in rows:
        place = row["track"]["width"] / places
        assert place > 0
        drawn = (round((row["bar"]["left"] - row["track"]["left"]) / place) + 1,
                 round((row["bar"]["right"] - row["track"]["left"]) / place))
        stated = tuple(int(rank) for rank in re.findall(r"\d+", row["said"]))
        assert drawn == stated, row
        # The tick is the pilot's own rank: it stands at the middle of that place.
        middle = (row["mark"]["left"] + row["mark"]["right"]) / 2 - row["track"]["left"]
        assert round(middle / place + 0.5) == int(row["rank"]), row
    # The header names the track's two ends, first place and last, and each name sits
    # over the end it names: the first starts where a track starts, the last ends
    # where a track ends, and the two do not meet.
    assert re.findall(r"\d+", read["named"]) == ["1", str(places)]
    first, last = read["ends"]
    assert first["left"] == pytest.approx(rows[0]["track"]["left"], abs=0.5)
    assert last["right"] == pytest.approx(rows[0]["track"]["right"], abs=0.5)
    assert first["right"] < last["left"]
    # The numbers are still set back, as the column was before it drew anything:
    # Gradio colours every element inside an HTML block, so a figure wrapped in an
    # element of its own takes the main ink unless the stylesheet hands it its cell's.
    assert {row["ink"] for row in rows} == {_rgb(TOKENS["text-mute"])}


# Metagame's list of the most played, read off the open tab: the two years the
# header names over the history, and for each row the share it states, its bar on its
# track, and each year's column in the frame it stands in.
_SHARE_LIST = "() => { const open = " + _OPEN_TAB + """;
    const box = el => { const r = el.getBoundingClientRect();
                        return {left: r.left, right: r.right, top: r.top, bottom: r.bottom,
                                width: r.width, height: r.height}; };
    const table = open.querySelector('.share-list table.data-table');
    const ends = [...table.querySelector('thead .share-years').children];
    return {
      ends: ends.map(box), named: ends.map(el => el.innerText),
      rows: [...table.querySelectorAll('tbody tr')].map(row => ({
        said: row.querySelector('.share-now').innerText,
        track: box(row.querySelector('.share-track')),
        bar: box(row.querySelector('.share-bar')),
        frame: box(row.querySelector('.share-history')),
        years: [...row.querySelectorAll('.share-year')].map(column => ({
          said: column.title, box: box(column),
          colour: getComputedStyle(column).backgroundColor,
        })),
      })),
    }; }"""


def _percent(said: str) -> float:
    return float(re.search(r"([\d.]+)%", said).group(1))


def _assert_the_list_draws_the_shares_it_states(page, width: str) -> None:
    """Metagame's list, as drawn: every bar and every year's column is as long as the
    share written beside it says, on one scale down the list, and the table is inside
    its card."""
    _card_titled(page, "Meta share").wait_for(timeout=30_000)
    read = page.evaluate(_SHARE_LIST)
    _assert_tables_fit_and_are_plain(page, f"Metagame at {width}")

    rows = read["rows"]
    assert rows
    shares = [_percent(row["said"]) for row in rows]
    assert shares == sorted(shares, reverse=True)
    # One scale for the bars: every track starts at one point and is one length, and
    # the leader's bar fills it. A bar is read back as a share, to within a pixel.
    assert len({(round(row["track"]["left"], 1), round(row["track"]["width"], 1))
                for row in rows}) == 1
    for row, share in zip(rows, shares):
        a_pixel = shares[0] / row["track"]["width"]
        assert abs(row["bar"]["left"] - row["track"]["left"]) <= 0.5, row
        assert abs(row["bar"]["width"] * a_pixel - share) <= a_pixel, row
    # And one for the years: the tallest column on the list fills its frame, each
    # stands on the frame's foot, and each is read back as the share its year states.
    tallest = max(_percent(year["said"]) for row in rows for year in row["years"])
    assert len({round(row["frame"]["height"], 1) for row in rows}) == 1
    for row in rows:
        a_pixel = tallest / row["frame"]["height"]
        assert [year["said"][:4] for year in row["years"]] == sorted(
            year["said"][:4] for year in row["years"]), row
        for year in row["years"]:
            assert abs(year["box"]["bottom"] - row["frame"]["bottom"]) <= 0.5, row
            assert abs(year["box"]["height"] * a_pixel - _percent(year["said"])) <= a_pixel, row
        # The latest year is in the charts' blue and the years before it are set back.
        *before, now = (year["colour"] for year in row["years"])
        assert now == _rgb(palette.CATEGORICAL[0]), row
        assert set(before) <= {_rgb(TOKENS["text-mute"])}, row
        # The row's share is its latest year's.
        assert _percent(row["years"][-1]["said"]) == _percent(row["said"]), row
    # The header names the first and the last year over the history, each over its own
    # end, and the two do not meet: at 390px they ran into each other at four digits,
    # so a narrow screen leaves the century out.
    first_year, last_year = rows[0]["years"][0]["said"][:4], rows[0]["years"][-1]["said"][:4]
    digits = slice(2, 4) if width == "phone" else slice(0, 4)
    assert read["named"] == [first_year[digits], last_year[digits]]
    first, last = read["ends"]
    frame = rows[0]["frame"]
    assert first["left"] >= frame["left"] - 0.5 and last["right"] <= frame["right"] + 0.5
    if first_year != last_year:
        assert first["right"] <= last["left"]


@pytest.mark.parametrize("width", list(shots.VIEWPORTS))
def test_metagame_lists_the_most_played_with_every_share_drawn_as_stated(
    chromium, explorer, width
):
    # Issue #334: who is played is a list, where it was a chart of dashed lines. How
    # long a bar or a column is drawn is the stylesheet's arithmetic on what the markup
    # carries, so only the drawn page can say the picture agrees with its numbers.
    page = _loaded(chromium, explorer, width)
    try:
        _assert_the_list_draws_the_shares_it_states(page, width)
    finally:
        page.close()


@pytest.mark.parametrize("width", list(shots.VIEWPORTS))
def test_the_list_draws_every_share_as_stated_on_the_real_record(chromium, record, width):
    # The real record's list: some thirty rows over four years, with names long enough
    # to wrap on a phone. Nothing in it is cut off at 390px, which the chart's legend
    # was (#314: 20 of its 35 entries fell outside the figure at the widest cut).
    page = _loaded(chromium, record, width)
    try:
        _assert_the_list_draws_the_shares_it_states(page, width)
        assert page.locator(".share-list tbody tr").count() > 20
    finally:
        page.close()


@pytest.mark.parametrize("width", ["phone", "desktop"])
def test_an_archetype_in_the_list_opens_its_page_on_the_real_record(chromium, record, width):
    # Issues #333 and #334: an archetype's own share over the years was the focus
    # picker's to draw, and is its page's now. A name in the list is the way there.
    # The suite's snapshot has no archetype with a page, so this is the real record's,
    # and whichever archetype stands tenth in it.
    page = _loaded(chromium, record, width)
    try:
        _card_titled(page, "Meta share").wait_for(timeout=30_000)
        address = _marked(page)
        name = _names_on_show(page, "archetype", ".share-list").nth(9)
        archetype = name.inner_text()
        name.scroll_into_view_if_needed()

        (name.tap if width == "phone" else name.click)()

        _assert_opened(page, "Archetypes", archetype, address)
        _card_titled(page, "Share over time").wait_for(timeout=30_000)
    finally:
        page.close()


# Every tab, the picks that put its charts on screen, and how many charts that is.
# Cards is walked twice, since its two views each draw a chart of their own. The
# suite's snapshot draws a card's adoption in both views; it has no year with a
# landscape, no archetype with a timeline, no pilot with a year to average or a
# rivalry to chart, and no race. Metagame's share card is a list since #334.
SUITE_WALK = [
    ("Metagame", [], 0),
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
# cannot: the landscape, an archetype's share and its timeline, a pilot's
# performance, a rivalry, and the race. Pilots is walked twice as Cards is, so the
# walk covers all eight of the app's plots. The picks are the acceptance script's own.
RECORD_WALK = [
    # The landscape, under the list of the most played.
    ("Metagame", [], 1),
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
    # contrasts with it. Read off the box as the browser drew it, over a real point
    # of a card's adoption chart: the chart Meta opened on, which this was first read
    # over, is a list since #334.
    shots.tab(page, "Cards")
    shots.pick(page, "Card", CARD)
    mark = page.locator(".js-plotly-plot .scatterlayer path.point").locator(
        "visible=true").first
    # The chart is under the card's graph: brought on screen, and left to settle
    # there, since the pick is still bringing its own result into view.
    mark.wait_for(timeout=30_000)
    shots.wait(page, 1500)
    mark.scroll_into_view_if_needed()
    shots.wait(page, 500)
    point = mark.bounding_box()
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

    assert CARD in drawn.pop("says")
    assert drawn == {
        "face": FONTS["body"]["family"],
        "text": _rgb(TOKENS["text"]),
        "surface": _rgb(TOKENS["surface"]),
        "border": _rgb(TOKENS["border"]),
    }


def _picked_by_typing(page, tab: str, label: str, text: str) -> str:
    """What a picker holds once ``text`` is typed into it and Enter is pressed.

    Keystrokes, as ``shots.pick`` types them, and then Enter where the script clicks
    an option. The picker shows its pick as the field's text.
    """
    shots.tab(page, tab)
    box = shots.field(page, label)
    box.click()
    box.press("ControlOrMeta+a")
    box.press_sequentially(text)
    shots.wait(page, 500)
    box.press("Enter")
    shots.wait(page, 400)
    return box.input_value()


def test_typing_a_full_name_and_pressing_enter_picks_that_name(page):
    # Issue #326: a picker keeps every option that holds the typed text anywhere and
    # Enter takes the first one kept, so "Swamp" then Enter picked "Snow-Covered
    # Swamp", which the suite's snapshot lists ahead of it by name and by decks alike.
    # Which option Enter lands on is Gradio's, so only a real page can say.
    assert _picked_by_typing(page, "Cards", "Card", "Swamp") == "Swamp"


@pytest.mark.parametrize("name", ["Grixis", "Oracle"])
def test_typing_an_archetype_and_pressing_enter_picks_it_on_the_real_record(
    chromium, record, name
):
    # The two the issue measured: "Grixis" picked "Bus Stop Grixis" and "Oracle"
    # picked "Noracle". The Meta focus list they were also measured in went with #334.
    page = _loaded(chromium, record)
    try:
        timeline = _picked_by_typing(page, "Archetypes", "Archetype", name)
    finally:
        page.close()

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
        page.get_by_role("tab", name="Metagame", exact=True).wait_for()
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
    # Every tab is on the strip, with none scrolled out of it at this width: eight
    # since #346 put Community first.
    strip = page.locator("[role=tablist]")
    assert strip.get_by_role("tab").count() == 8
    assert strip.evaluate("strip => strip.scrollWidth <= strip.clientWidth")


def test_the_narrowest_one_row_header_holds_every_tab(chromium, record):
    # Issue #332: "Metagame" is 33px wider than "Meta", and the header went to one
    # row at a window 5px too narrow for seven tabs: from 1100 to 1105px the strip
    # was clipped by up to 5px beside the freshness line, with nothing saying it
    # scrolled. At the narrowest window whose header is one row, every tab is on the
    # strip. On the real record, whose freshness line is the width the row is
    # measured against. An eighth tab (#346) moved that window from 1140 to 1240px:
    # at 1140 the eight were clipped by 69px.
    page = chromium.new_page(viewport={"width": HEADER_ONE_ROW_PX, "height": 900})
    try:
        page.goto(record, wait_until="networkidle")
        page.get_by_role("tab", name="Metagame", exact=True).wait_for()
        parts = _header_parts(page)
        strip = page.locator("[role=tablist]").evaluate(
            "strip => [strip.scrollWidth, strip.clientWidth]")
    finally:
        page.close()

    order = [parts[part] for part in ("mark", "name", "tabs", "freshness")]
    assert max(box["top"] for box in order) < min(box["bottom"] for box in order)
    assert strip[0] <= strip[1]


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


# The FAQ's list of questions and its boxes as the browser laid them out (issue
# #338): the list's own box and the text of each link in it, each answer box in
# turn, the tab's own edges, how many columns the list is set in, how far the page
# has scrolled, and the button back to the list where it is on show.
_FAQ_LAYOUT = "() => { const open = " + _OPEN_TAB + """;
    const rect = el => { const box = el.getBoundingClientRect();
      return {left: box.left, right: box.right, top: box.top, bottom: box.bottom}; };
    const list = open.querySelector('.faq-index nav');
    const back = open.querySelector('.faq-index a.faq-up');
    return {list: rect(list), tab: rect(open), scrolled: window.scrollY,
            links: [...list.querySelectorAll('a')].map(a => a.innerText),
            columns: new Set([...list.querySelectorAll('a')]
              .map(a => Math.round(a.getBoundingClientRect().left))).size,
            boxes: [...open.querySelectorAll('.faq-card')]
              .filter(box => box.parentElement.closest('.faq-card') === null)
              .map(rect),
            back: back.getBoundingClientRect().height > 0 ? rect(back) : null}; }"""


@pytest.mark.parametrize("width, columns", [("phone", 1), ("desktop", 3), ("wide", 3)])
def test_the_faq_opens_on_a_list_of_its_questions_over_full_width_boxes(
    chromium, explorer, width, columns
):
    # Issue #338: 21 boxes down one column, several screens of them, and no way to
    # find a question. The tab opens on the questions as a list, over the boxes,
    # which are laid out as they were: one to a row, each the width of the tab.
    # A list beside narrow boxes was tried first and turned down on 2026-11-18,
    # for what it did to the reading.
    page = _loaded(chromium, explorer, width, on="FAQ")
    try:
        laid = page.evaluate(_FAQ_LAYOUT)
    finally:
        page.close()

    assert laid["links"] == [q for _, _, q, _ in _FAQ_ENTRIES]
    # Side by side where there is room, so the list is a short block over the first
    # answer and not a screen of its own.
    assert laid["columns"] == columns
    boxes = laid["boxes"]
    assert len(boxes) == len(_FAQ_ENTRIES)
    assert 0 < laid["list"]["top"] < laid["list"]["bottom"] <= boxes[0]["top"]
    for box in boxes:
        assert (box["left"], box["right"]) == (laid["tab"]["left"], laid["tab"]["right"])
    assert all(above["bottom"] <= below["top"] for above, below in zip(boxes, boxes[1:]))
    # With the list on screen there is nothing to go back to.
    assert laid["back"] is None


# The first question is the one whose box sits right under the list, so a jump to it
# leaves the list only just out of view: at 1100px wide its foot came to rest within
# half a pixel of the window's edge, still counted as on screen, and no button showed.
@pytest.mark.parametrize("window, entry", [
    (shots.VIEWPORTS["phone"], "faq-race"),
    (shots.VIEWPORTS["desktop"], "faq-race"),
    ({"viewport": {"width": 1100, "height": 800}}, "faq-finish"),
], ids=["phone", "desktop", "the first question"])
def test_a_question_opens_its_answer_and_a_button_goes_back_to_the_list(
    chromium, explorer, window, entry
):
    # Issue #338: the list is the way to a question, so a click on one puts its box
    # on screen. From there the list is screens away, so a button stays in the
    # corner of the window for as long as the list is out of view, and brings the
    # tab back to its top.
    page = chromium.new_page(**window)
    window = page.viewport_size
    try:
        page.goto(explorer, wait_until="networkidle")
        shots.tab(page, "FAQ")
        address = _marked(page)
        page.locator(f".faq-index a[href='#{entry}']").click()
        # The page moves on the frame after the click.
        page.locator(".faq-index a.faq-up").wait_for()
        _assert_in_view_on_the_faq(page, entry, address)
        away = page.evaluate(_FAQ_LAYOUT)

        page.locator(".faq-index a.faq-up").click()
        page.locator(".faq-index a.faq-up").wait_for(state="hidden")
        back = page.evaluate(_FAQ_LAYOUT)
        _assert_same_document(page, address)
    finally:
        page.close()

    # At the foot of the window, whole, its edge on the edge of the boxes.
    button = away["back"]
    assert button["right"] == pytest.approx(away["boxes"][0]["right"], abs=1)
    assert window["height"] / 2 < button["top"] < button["bottom"] <= window["height"]
    # The tab as it opened, with the button gone.
    assert back["scrolled"] == 0
    assert back["back"] is None


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
        # A card's adoption: Metagame, the tab this opens on, draws no chart on the
        # suite's snapshot since its share chart became a list (#334). This chart's
        # plot area is 259px here, where the share chart's was 265px; without the
        # 48px the tighter margins gave, it would be 211px.
        shots.tab(page, "Cards")
        shots.pick(page, "Card", CARD)
        page.locator(".js-plotly-plot .nsewdrag").locator("visible=true").first.wait_for(
            timeout=30_000)
        plot_area = page.evaluate(
            "() => " + _OPEN_TAB + ".querySelector('.js-plotly-plot .nsewdrag')"
            ".getBoundingClientRect().width"
        )
    finally:
        page.close()

    assert len(tabs) == 8 and min(tabs) >= 44
    # No room for one row here: the name and the freshness share a line, clear of
    # each other, and the strip has the full width of the line below.
    assert parts["name"]["right"] < parts["freshness"]["left"]
    assert parts["tabs"]["top"] >= max(parts["name"]["bottom"], parts["freshness"]["bottom"])
    # The strip scrolls sideways (#172), and what says so is a tab cut at its edge.
    # The tighter margin moved that edge into the gap between two tabs, where nothing
    # showed, so the tabs sit a little closer here: a label is cut, with enough of it
    # on screen to read as one.
    assert len(cut_label_showing) == 1 and cut_label_showing[0] >= 20
    assert plot_area >= 255


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
    # its own, two clicks to change one of three values. Each is a row of chips, and
    # only a search is left as a dropdown. Metagame carries one control, the year
    # beside the landscape it governs (#332): the cut and the focus search over the
    # share chart went with the chart (#334).
    ((year, years),) = _chips(page).items()
    assert year == "Year" and years and all(y.isdigit() for y in years)
    assert _dropdown_labels(page) == []

    shots.tab(page, "Archetypes")
    assert _chips(page) == {}
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


# Every state of every tab's control panel: the tab, and the chip that turns it to
# its other view. Metagame's one panel is the year's own, over the landscape
# (#332): it holds one control and is no row to grade.
PANELS = [
    ("Archetypes", None),
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
    # Issue #328, at 1440 by 900: a pick drew its answer below the window (a focus
    # archetype on Meta, until #334 took that picker away, drew its chart 965px down
    # a 900px one), so the pick looked like it had done nothing. The page brings
    # what a pick drew into view.
    assert page.viewport_size == {"width": 1440, "height": 900}

    subject = page.locator(".view-body", has=page.locator(".page-head")).locator(
        "visible=true").first
    shots.tab(page, "Cards")
    shots.pick(page, "Card", CARD)
    assert _in_view(page, _settled_top(page, subject))

    shots.tab(page, "Pilots")
    shots.pick(page, "Pilot", PILOT)
    assert _in_view(page, _settled_top(page, subject))


def test_a_pick_brings_its_result_into_view_on_the_real_record(chromium, record):
    # The picks the suite's snapshot cannot draw: an archetype's page, whose
    # timeline landed 1,330px down a 900px window before #328, and a rivalry. Since
    # #332 the pick draws a whole page, and its head is what comes into view.
    page = _loaded(chromium, record)
    try:
        shots.tab(page, "Archetypes")
        shots.pick(page, "Archetype", shots.ARCHETYPE)
        _card_titled(page, "Finishes over time").wait_for(timeout=30_000)
        archetype = _settled_top(page, page.locator(
            ".view-body", has=page.locator(".page-head")).locator("visible=true").first)

        shots.tab(page, "Pilots")
        shots.chip(page, "View", "Head-to-head")
        shots.pick(page, "Pilot", shots.PILOT_RIVAL)
        shots.pick(page, "Second pilot (required)", shots.PILOT_B)
        page.get_by_role("button", name="Draw", exact=True).locator("visible=true").click()
        rivalry = _settled_top(page, page.locator(
            ".view-body", has=page.locator(".subject-line")).locator("visible=true").first)
        in_view = [_in_view(page, archetype), _in_view(page, rivalry)]
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


@pytest.mark.parametrize("tab", ["Archetypes", "Cards", "Pilots"])
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
    # The pilot's two results beside the neighbourhood, the one archetype they were
    # played on beside the affinity graph (#330), and the two again as the record.
    assert read["rows"] == [2, 1, 2]


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


def test_a_drawn_archetype_page_runs_head_then_share_then_finishes(chromium, record):
    # Issue #332: everything about one archetype was split across two tabs, two picks
    # and no numbers. One pick draws its page: the name in the display face, a
    # descriptor and four tiles, then its share over time and its finishes over
    # time, with the page's top brought into view. A second archetype is compared
    # on both charts and named in the descriptor. The suite's snapshot has no
    # archetype with a timeline, so this is the real record's.
    page = _loaded(chromium, record)
    try:
        shots.tab(page, "Archetypes")
        opened_on = page.evaluate(
            "() => [..." + _OPEN_TAB + """.querySelectorAll('.page-head, .insight-card')]
                 .filter(part => part.getBoundingClientRect().height > 0).length"""
        )
        shots.pick(page, "Archetype", shots.ARCHETYPE)
        _card_titled(page, "Finishes over time").wait_for(timeout=30_000)
        read = page.evaluate(_PAGE_FROM_THE_TOP)
        name = page.locator(".page-name").locator("visible=true").inner_text()
        alone = _lines_named(page)

        shots.pick(page, "Second archetype (optional)", shots.ARCHETYPE_B)
        page.locator(".page-descriptor").locator("visible=true").filter(
            has_text=f"compared with {shots.ARCHETYPE_B}").wait_for(timeout=30_000)
        _card_titled(page, "Finishes over time").wait_for(timeout=30_000)
        compared = _lines_named(page)
    finally:
        page.close()

    # It opens on its picker and nothing else: no head and no card.
    assert opened_on == 0
    assert read["order"] == [*_HEAD, "Share over time", "Finishes over time"]
    assert read["tiles"] == 4 and _display_face(read) and name == shots.ARCHETYPE
    assert alone == [[shots.ARCHETYPE]] * 2
    assert compared == [[shots.ARCHETYPE, shots.ARCHETYPE_B]] * 2


def _lines_named(page) -> list[list[str]]:
    """For each chart the open tab shows, the lines it draws by name, in order. Read
    off the chart's own traces, since the chart library draws no legend for a line
    on its own."""
    return page.evaluate(
        "() => [..." + _OPEN_TAB + """.querySelectorAll('.js-plotly-plot')]
             .filter(chart => chart.getBoundingClientRect().height > 0)
             .map(chart => chart.data
                .filter(trace => trace.name && trace.showlegend !== false)
                .map(trace => trace.name))"""
    )


def test_metagame_draws_the_landscape_under_the_share_list_with_its_year_beside_it(
    chromium, record
):
    # Issue #332: Metagame is the whole field, so the landscape is drawn on it, under
    # the list of the most played (a share chart until #334), when the app opens. The
    # year governs the landscape alone and sits between the two cards, directly over
    # the one it redraws. The landscape
    # is drawn at the width of its card: on Archetypes it was drawn before its tab
    # was ever shown and came up at the chart library's fallback width until the
    # tab was opened. Read off the running page, at each of the three widths.
    found = {}
    for width in shots.VIEWPORTS:
        page = _loaded(chromium, record, width)
        try:
            _card_titled(page, "Metagame landscape").wait_for(timeout=30_000)
            page.wait_for_timeout(1500)
            found[width] = page.evaluate(
                "() => { const open = " + _OPEN_TAB + """;
                    const box = el => el.getBoundingClientRect();
                    // Gradio sets a group as two boxes of one class, one in the other.
                    const cards = [...open.querySelectorAll('.insight-card')]
                      .filter(card => box(card).height > 0
                                      && !card.parentElement.closest('.insight-card'));
                    const year = [...open.querySelectorAll('fieldset')]
                      .find(group => group.querySelector('[data-testid=block-info]')
                                          .textContent.trim() === 'Year');
                    const landscape = cards[cards.length - 1];
                    const plot = landscape.querySelector('.js-plotly-plot .main-svg');
                    return {
                      titles: cards.map(card =>
                        card.querySelector('.t-result-title').innerText),
                      year_under_share: box(year).top >= box(cards[0]).bottom,
                      year_over_landscape: box(year).bottom <= box(landscape).top,
                      fills: box(plot).width / landscape.clientWidth,
                    }; }"""
            )
        finally:
            page.close()

    for width, read in found.items():
        assert read["titles"] == ["Meta share", "Metagame landscape"], width
        # Under the share list and over the landscape, with no card between: the
        # two titles above are every card on show.
        assert read["year_under_share"] and read["year_over_landscape"], width
        assert read["fills"] >= 0.9, width


def test_a_drawn_card_page_opens_with_its_head_and_carries_a_list_on_each_view(page):
    # Issue #329: the usage graph's archetypes as a table grouped by broad class, and
    # on the co-occurrence view the cards drawn beside the subject, ranked. Since
    # #330 that ranking is the panel beside the co-occurrence graph, all of it, and
    # the card that repeated it under the plots is gone.
    shots.tab(page, "Cards")
    shots.pick(page, "Card", CARD)
    _card_titled(page, "Archetypes").wait_for(timeout=20_000)
    overview = page.evaluate(_PAGE_FROM_THE_TOP)

    shots.chip(page, "View", "Co-occurrence")
    _card_titled(page, "Co-occurrence").wait_for(timeout=20_000)
    cooccurrence = page.evaluate(_PAGE_FROM_THE_TOP)

    assert overview["order"] == [*_HEAD, "Usage", "Adoption over time", "Archetypes"]
    assert overview["tiles"] == 4 and _display_face(overview)
    # Brainstorm is in all three decks: its two archetypes beside the usage graph,
    # then two broad classes, each heading one archetype.
    assert overview["rows"] == [2, 4]
    assert cooccurrence["order"] == [*_HEAD, "Co-occurrence", "Adoption over time"]
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
        _card_titled(page, "Co-occurrence").wait_for(timeout=20_000)
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
        _card_titled(page, "Co-occurrence").wait_for(timeout=30_000)
        _assert_tables_fit_and_are_plain(page, "Cards, co-occurrence")
    finally:
        page.close()


def _two_cards_drawn(page, first: str, second: str):
    """The Co-occurrence view drawn for two cards, down to its last card."""
    shots.tab(page, "Cards")
    shots.chip(page, "View", "Co-occurrence")
    shots.pick(page, "Card", first)
    shots.pick(page, "Second card (optional)", second)
    _card_titled(page, "Co-occurrence by year").wait_for(timeout=30_000)


@pytest.mark.parametrize("width", list(shots.VIEWPORTS))
def test_two_cards_year_by_year_table_is_under_the_chart_and_fits_its_card(
    chromium, explorer, width
):
    # Issue #351: each card's share of its own decks that also run the other, a row
    # per year, as the last card of the two-card page. A cell is a share with its two
    # counts, which a number's cell never breaks, in three columns.
    page = _loaded(chromium, explorer, width)
    try:
        _two_cards_drawn(page, CARD, "Daze")
        read = page.evaluate(_PAGE_FROM_THE_TOP)
        assert read["order"] == [*_HEAD, "Co-occurrence", "Adoption over time",
                                 "Co-occurrence by year"]
        # The played-with list beside the graph, then the one year on record.
        assert read["rows"][-1] == 1
        _assert_tables_fit_and_are_plain(page, f"two cards, {width}")
    finally:
        page.close()


@pytest.mark.parametrize("width", list(shots.VIEWPORTS))
def test_two_cards_year_by_year_table_fits_its_card_on_the_real_record(
    chromium, record, width
):
    # The same table where it is widest: the two cards in the most decks, whose
    # counts run to four digits on both sides of the slash in the record's fuller
    # years. Measured at 390px on the record of November 2026, the table needs 305px
    # of the 332 its card has.
    conn = open_for_reading(artifact_path())
    names = dict(rows(conn.execute("MATCH (c:Card) RETURN c.canon, c.name")))
    first, second = (names[canon] for canon, _ in sorted(
        card_deck_counts(conn).items(), key=lambda kv: (-kv[1], kv[0]))[:2])
    page = _loaded(chromium, record, width)
    try:
        _two_cards_drawn(page, first, second)
        _assert_tables_fit_and_are_plain(page, f"two cards of the record, {width}")
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
    _card_titled(page, "Co-occurrence").wait_for(timeout=20_000)
    read.append(page.evaluate(_CAPTIONS))

    for view in read:
        assert len(view["captions"]) >= 2
        assert view["older"] == []
        for caption in view["captions"]:
            assert caption["colour"] == _rgb(TOKENS["text"]), caption
            assert caption["figures"] <= 1, caption
            assert "·" not in caption["said"] and "node" not in caption["said"], caption


# How far the first card of a drawn page starts below its tiles, and how a table's
# group heading is set beside the rows under it.
_HEAD_GAP = "() => { const open = " + _OPEN_TAB + """;
    const shown = el => el.getBoundingClientRect().height > 0;
    const tiles = [...open.querySelectorAll('.tiles')].find(shown);
    const card = [...open.querySelectorAll('.results-stack .insight-card')].find(shown);
    return card.getBoundingClientRect().top - tiles.getBoundingClientRect().bottom; }"""


@pytest.mark.parametrize("width", ["phone", "desktop"])
def test_the_tiles_stand_clear_of_the_first_card(chromium, explorer, width):
    # After the maintainer's look (#329): the tiles sat 2px over the first graph's
    # card, as one block. They stand apart from it by at least the space that parts
    # one card from the next.
    page = _loaded(chromium, explorer, width)
    try:
        gaps = {}
        shots.tab(page, "Pilots")
        shots.pick(page, "Pilot", PILOT)
        _card_titled(page, "Record").wait_for(timeout=20_000)
        gaps["Pilots"] = page.evaluate(_HEAD_GAP)
        shots.tab(page, "Cards")
        shots.pick(page, "Card", CARD)
        _card_titled(page, "Archetypes").wait_for(timeout=20_000)
        gaps["Cards"] = page.evaluate(_HEAD_GAP)
    finally:
        page.close()

    assert all(gap >= 20 for gap in gaps.values()), gaps


def test_a_tables_group_heading_reads_as_a_heading_not_as_a_row(page):
    # After the maintainer's look (#329): a broad class over its archetypes, or an
    # archetype over its gems, was set like the rows under it and read as one more
    # row. It takes the display face the app's titles are in, a size up from a row.
    shots.tab(page, "Cards")
    shots.pick(page, "Card", CARD)
    _card_titled(page, "Archetypes").wait_for(timeout=20_000)

    set_as = page.evaluate(
        "() => { const open = " + _OPEN_TAB + """;
             const of = sel => { const s = getComputedStyle(open.querySelector(sel));
               return [s.fontFamily.split(',')[0].replace(/['"]/g, ''), parseFloat(s.fontSize)]; };
             return { heading: of('table.data-table .group th'),
                      row: of('table.data-table tr:not(.group) td') }; }"""
    )

    assert set_as["heading"][0] == FONTS["display"]["family"]
    assert set_as["row"][0] == FONTS["body"]["family"]
    assert set_as["heading"][1] > set_as["row"][1]


# --- A graph beside the panel that answers it (issue #330) ---


# Every graph on show on the open tab: its card's title, and where its frame and the
# three parts of its panel sit. The panel's own box is read too, which is empty on a
# narrow screen, where the parts take their places round the graph.
_GRAPHS = "() => { const open = " + _OPEN_TAB + """;
    const box = el => { const r = el.getBoundingClientRect();
      return {left: r.left, right: r.right, top: r.top, bottom: r.bottom,
              width: r.width, height: r.height}; };
    return [...open.querySelectorAll('.graph-split')]
      .filter(split => split.getBoundingClientRect().height > 0).map(split => ({
        title: split.closest('.insight-card').querySelector('.t-result-title').innerText,
        frame: box(split.querySelector('iframe')),
        panel: box(split.querySelector('.graph-panel')),
        key: box(split.querySelector('.panel-key')),
        answer: box(split.querySelector('.panel-answer')),
        list: box(split.querySelector('.panel-list')),
        listed: split.querySelectorAll('.panel-list tr[data-node]').length,
        window: [innerWidth, innerHeight],
      })); }"""


def _assert_each_graph_sits_beside_its_panel(page, titles: list[str]) -> None:
    graphs = page.evaluate(_GRAPHS)
    assert [graph["title"] for graph in graphs] == titles
    for graph in graphs:
        frame, panel = graph["frame"], graph["panel"]
        # Side by side at one height: the graph on the left, about two thirds.
        assert frame["right"] <= panel["left"] + 0.5, graph
        assert frame["top"] == pytest.approx(panel["top"], abs=0.5), graph
        assert frame["height"] == pytest.approx(panel["height"], abs=0.5), graph
        assert 1.8 * panel["width"] <= frame["width"] <= 2.6 * panel["width"], graph
        assert panel["width"] >= 300, graph
        # A frame fitted to the window: all of it fits on screen under the header.
        assert frame["height"] <= 0.8 * graph["window"][1] + 0.5, graph
        # And no shorter than the canvas the graph had with the card to itself, which
        # on a 1440 by 900 window was 697px under its key (measured on main at
        # c25481e): a graph is fitted by its height, so the drawing is no smaller.
        if graph["window"] == [1440, 900]:
            assert frame["height"] >= 697, graph
        assert graph["listed"] > 0, graph
    assert _sideways_scroll(page) <= 0


@pytest.mark.parametrize("width", ["desktop", "wide"])
def test_every_graph_sits_beside_its_panel_on_a_desktop(chromium, explorer, width):
    # The graph sat alone in a frame 90% of the window tall, with its key over it and
    # the answer to a click at its far edge. Each graph now has a panel beside it at
    # its own height, holding the key, the answer and the page's list.
    page = _loaded(chromium, explorer, width)
    try:
        shots.tab(page, "Pilots")
        shots.pick(page, "Pilot", PILOT)
        _card_titled(page, "Record").wait_for(timeout=20_000)
        _assert_each_graph_sits_beside_its_panel(page, ["Neighbourhood", "Archetype affinity"])

        shots.chip(page, "View", "Head-to-head")
        shots.pick(page, "Second pilot (required)", PILOT_B)
        shots.draw(page, settle=500)
        _assert_each_graph_sits_beside_its_panel(page, ["Neighbourhood"])

        shots.tab(page, "Cards")
        shots.pick(page, "Card", CARD)
        _card_titled(page, "Archetypes").wait_for(timeout=20_000)
        _assert_each_graph_sits_beside_its_panel(page, ["Usage"])
        shots.chip(page, "View", "Co-occurrence")
        _card_titled(page, "Co-occurrence").wait_for(timeout=20_000)
        _assert_each_graph_sits_beside_its_panel(page, ["Co-occurrence"])
    finally:
        page.close()


@pytest.mark.parametrize("width", ["desktop", "wide"])
def test_the_gems_table_is_the_panel_beside_the_gems_graph_on_the_real_record(
    chromium, record, width
):
    # The suite's snapshot has no gem. On the real record the table sat over the
    # graph, the full width of the card; it is the graph's panel now, every gem a row
    # that names its node. A phone's table is held to its card with every other
    # table's, in `test_no_table_runs_past_its_card_on_any_tab_of_the_real_record`.
    page = _loaded(chromium, record, width)
    try:
        shots.tab(page, "Hidden gems")
        shots.wait(page, 500)
        _assert_each_graph_sits_beside_its_panel(page, ["Hidden gems"])
        tables = page.evaluate(
            "() => [...(" + _OPEN_TAB + ").querySelectorAll('table.data-table')]"
            ".map(table => [!!table.closest('.panel-list'),"
            " [...table.querySelectorAll('thead th')].map(th => th.innerText.toLowerCase())])")
    finally:
        page.close()

    assert tables == [[True, [
        "card", "decks running it", "in top 20%", "pilots", "odds it is luck"]]]


# What the panel beside the first graph on show says, and where: the facts of the
# node it is answering for, the row it marks, and what the graph itself has selected.
_ANSWERED = "() => { const open = " + _OPEN_TAB + """;
    const split = [...open.querySelectorAll('.graph-split')]
      .find(el => el.getBoundingClientRect().height > 0);
    const shown = [...split.querySelectorAll('.node-facts')].filter(el => !el.hidden);
    const inside = split.querySelector('iframe').contentWindow;
    const box = shown.length ? shown[0].getBoundingClientRect() : null;
    return {
      answering: shown.map(el => el.dataset.node),
      said: shown.length ? shown[0].innerText : '',
      top: box && box.top, bottom: box && box.bottom, window: innerHeight,
      prompt: getComputedStyle(split.querySelector('.panel-prompt')).display !== 'none',
      marked: [...split.querySelectorAll('tr.picked')].map(row => row.dataset.node),
      selected: inside.network.getSelectedNodes(),
      faces: [...inside.document.fonts].filter(face => face.status === 'loaded')
        .map(face => face.family.replace(/['"]/g, '')),
      labelled: inside.network.nodesHandler.options.font.face,
    }; }"""


def _assert_a_clicked_deck_is_answered_in_the_window(page) -> dict:
    before = page.evaluate(_ANSWERED)
    assert before["answering"] == [] and before["prompt"]

    shots.click_a_node(page)

    read = page.evaluate(_ANSWERED)
    # One node answered for, the one the graph has selected, and the prompt gone.
    assert read["answering"] == read["selected"] and len(read["selected"]) == 1
    assert read["selected"][0].startswith("deck:") and not read["prompt"]
    # What a reader asks of a deck next, with its list one click away.
    for fact in ("EVENT", "FINISH", "REGISTERED", "Open the decklist on Moxfield"):
        assert fact in read["said"], read["said"]
    # All of it inside the window as the click left it, to within half a pixel (a
    # page scrolls to a fraction of one): the reader has not scrolled.
    assert -0.5 <= read["top"] and read["bottom"] <= read["window"] + 0.5, read
    # And the graph's labels are in the app's face, which its own document loaded.
    assert read["faces"] == [FONTS["body"]["family"]]
    assert read["labelled"].strip("'\"").startswith(FONTS["body"]["family"])
    return read


def test_a_click_on_a_node_is_answered_in_the_panel_inside_the_window(page):
    # The answer was a line at the foot of a frame taller than most windows, in view
    # only with the page scrolled exactly into place, and it read "Kind: Deck, Name:
    # Grixis". It is in the panel beside the graph, at the top, the moment of the click.
    shots.tab(page, "Pilots")
    shots.pick(page, "Pilot", PILOT)
    shots.drawn(page, settle=3000)

    read = _assert_a_clicked_deck_is_answered_in_the_window(page)

    # A click on a deck is a click on a result the list holds, so its row is marked.
    assert read["marked"] == read["selected"]


def test_a_click_on_a_node_is_answered_inside_the_window_on_the_real_record(
    chromium, record
):
    # A hundred nodes, a record of dozens, and a panel that scrolls: the answer is
    # still at the top of it.
    page = _loaded(chromium, record)
    try:
        shots.tab(page, "Pilots")
        shots.pick(page, "Pilot", shots.PILOT)
        shots.drawn(page, settle=6000)
        _assert_a_clicked_deck_is_answered_in_the_window(page)
    finally:
        page.close()


def test_an_answer_is_brought_into_the_window_when_the_panels_top_is_off_screen(page):
    # The frame is most of a window tall, so a reader part way down the graph has
    # the top of the panel, where the answer is, above the top of the window. A
    # click on a node they can see brings the answer to them.
    shots.tab(page, "Pilots")
    shots.pick(page, "Pilot", PILOT)
    shots.drawn(page, settle=3000)
    frame = page.locator("iframe").locator("visible=true").first.element_handle()
    page.evaluate("frame => window.scrollBy(0, frame.getBoundingClientRect().top + 200)", frame)
    # The lowest node drawn, well inside what is left of the frame on screen.
    point = frame.content_frame().evaluate(
        """() => { const at = network.getPositions();
             const lowest = Object.keys(at).reduce((a, b) => (at[b].y > at[a].y ? b : a));
             return network.canvasToDOM(at[lowest]); }""")
    assert point["y"] > 260
    assert page.evaluate(_GRAPHS)[0]["answer"]["top"] < 0  # the answer's place is off screen

    page.frame_locator("iframe >> visible=true").first.locator(
        "#mynetwork canvas").click(position=point)
    shots.wait(page, 400)

    read = page.evaluate(_ANSWERED)
    assert len(read["answering"]) == 1
    assert -0.5 <= read["top"] and read["bottom"] <= read["window"] + 0.5, read


def test_a_row_of_the_list_and_a_named_neighbour_each_select_their_node(page):
    # The panel works the other way too: a row of the list is the node it is about,
    # and so is a neighbour named under a node's facts.
    shots.tab(page, "Pilots")
    shots.pick(page, "Pilot", PILOT)
    shots.drawn(page, settle=3000)
    panel = page.locator(".graph-split").locator("visible=true").first

    row = panel.locator(".panel-list tr[data-node]").first
    named = row.get_attribute("data-node")
    row.click()
    shots.wait(page, 300)
    by_row = page.evaluate(_ANSWERED)

    assert by_row["selected"] == by_row["answering"] == by_row["marked"] == [named]

    # From the pilot at the hub, whose facts name the events round them, to one of them.
    hub = page.evaluate(
        "() => { const split = [...(" + _OPEN_TAB + ").querySelectorAll('.graph-split')]"
        ".find(el => el.getBoundingClientRect().height > 0);"
        " const inside = split.querySelector('iframe').contentWindow;"
        " const id = inside.network.body.data.nodes.getIds().find(id => id.startsWith('pilot:'));"
        " inside.network.selectNodes([id]); inside.network.body.emitter.emit('selectNode',"
        " {nodes: [id]}); return id; }")
    neighbour = panel.locator(".node-facts:not([hidden]) li[data-node]").first
    event = neighbour.get_attribute("data-node")
    assert event.startswith("event:") and page.evaluate(_ANSWERED)["answering"] == [hub]
    neighbour.click()
    shots.wait(page, 300)
    by_neighbour = page.evaluate(_ANSWERED)

    assert by_neighbour["selected"] == by_neighbour["answering"] == [event]
    assert by_neighbour["marked"] == []  # an event is no row of a pilot's results


# The fill the widget would paint each of these nodes with right now.
_FILLS = "(ids) => ids.map((id) => network.body.nodes[id].getFormattingValues().color)"

# The event a deck was played at: the one event node its own node touches.
_ITS_EVENT = ("(deck) => network.getConnectedNodes(deck)"
              ".find((id) => id.startsWith('event:'))")


def test_a_row_takes_the_focus_from_a_node_the_pointer_has_left(page):
    # Issue #331: the node under the pointer is the graph's focus. A pointer that
    # leaves the graph for the panel is over no node, and vis.js is never told it
    # left, so the last node it crossed kept the focus: a row clicked next selected
    # its node and lit the other one's neighbourhood.
    shots.tab(page, "Pilots")
    shots.pick(page, "Pilot", PILOT)
    shots.drawn(page, settle=3000)
    split = page.locator(".graph-split").locator("visible=true").first
    frame = split.locator("iframe").element_handle()
    row = split.locator(".panel-list tr[data-node]").first
    named = row.get_attribute("data-node")
    frame.scroll_into_view_if_needed()
    shots.wait(page, 500)
    # An event the row's deck was not played at.
    crossed = frame.content_frame().evaluate(
        """(deck) => { const id = network.body.nodeIndices.find((id) => id.startsWith('event:')
             && !network.getConnectedNodes(id).includes(deck));
             const at = network.canvasToDOM(network.getPositions([id])[id]);
             return {id, x: at.x, y: at.y}; }""", named)
    box = frame.bounding_box()
    inside = frame.content_frame()
    reached = inside.evaluate(_ITS_EVENT, named)
    at_rest = inside.evaluate(_FILLS, [crossed["id"], reached])

    page.mouse.move(box["x"] + crossed["x"], box["y"] + crossed["y"])
    shots.wait(page, 300)
    under_the_pointer = inside.evaluate(_FILLS, [crossed["id"], reached])
    assert under_the_pointer[0] == at_rest[0] and under_the_pointer[1] != at_rest[1], (
        "the pointer never took the focus, so nothing is being held here")
    row.click()
    shots.wait(page, 300)

    assert inside.evaluate("() => network.getSelectedNodes()") == [named]
    row_clicked = inside.evaluate(_FILLS, [crossed["id"], reached])
    assert row_clicked[1] == at_rest[1], "the event of the row's deck is not lit"
    assert row_clicked[0] != at_rest[0], "the node the pointer left is still lit"


def test_on_a_phone_a_row_takes_the_focus_from_a_node_tapped_before_it(chromium, explorer):
    # A phone has no pointer to hover with, so there the focus is the selection and
    # nothing else. A tap is followed by the mouse events a browser makes up for it,
    # and a node tapped on the graph must not be left holding the focus as a hovered
    # one when a row is tapped next.
    page = _loaded(chromium, explorer, "phone")
    try:
        shots.tab(page, "Pilots")
        shots.pick(page, "Pilot", PILOT)
        shots.drawn(page, settle=3000)
        split = page.locator(".graph-split").locator("visible=true").first
        frame = split.locator("iframe").element_handle()
        row = split.locator(".panel-list tr[data-node]").first
        named = row.get_attribute("data-node")
        frame.scroll_into_view_if_needed()
        shots.wait(page, 500)
        tapped = frame.content_frame().evaluate(
            """(deck) => { const id = network.body.nodeIndices.find((id) => id.startsWith('event:')
                 && !network.getConnectedNodes(id).includes(deck));
                 const at = network.canvasToDOM(network.getPositions([id])[id]);
                 return {id, x: at.x, y: at.y}; }""", named)
        box = frame.bounding_box()
        inside = frame.content_frame()
        reached = inside.evaluate(_ITS_EVENT, named)
        at_rest = inside.evaluate(_FILLS, [tapped["id"], reached])

        page.touchscreen.tap(box["x"] + tapped["x"], box["y"] + tapped["y"])
        shots.wait(page, 400)
        assert inside.evaluate("() => network.getSelectedNodes()") == [tapped["id"]]
        assert inside.evaluate(_FILLS, [tapped["id"], reached])[1] != at_rest[1], (
            "the tap took no focus")
        # Those made-up mouse events land where the finger was, and by then the
        # tapped node's row has scrolled the page under it: 65px down the graph here,
        # on another node 1 load in 5. So one over the other event takes nothing.
        other = inside.evaluate(
            "(id) => network.canvasToDOM(network.getPositions([id])[id])", reached)
        box = frame.bounding_box()
        page.mouse.move(box["x"] + other["x"], box["y"] + other["y"])
        shots.wait(page, 300)
        assert inside.evaluate(_FILLS, [tapped["id"], reached])[1] != at_rest[1], (
            "a mouse event took the focus from the tapped node")
        row.scroll_into_view_if_needed()
        row.tap()
        shots.wait(page, 400)

        assert inside.evaluate("() => network.getSelectedNodes()") == [named]
        row_tapped = inside.evaluate(_FILLS, [tapped["id"], reached])
        assert row_tapped[1] == at_rest[1], "the event of the row's deck is not lit"
        assert row_tapped[0] != at_rest[0], "the node tapped before the row is still lit"
    finally:
        page.close()


def test_a_selected_or_hovered_row_keeps_every_word_legible(page):
    # A row answers the pointer and the selection with a tint, which lightens the
    # ground under it. The quiet figure in a row is 4.57:1 on the card and would be
    # 3.76:1 on the selected tint, so in a tinted row it steps up a tone.
    shots.tab(page, "Cards")
    shots.pick(page, "Card", CARD)
    shots.drawn(page, settle=3000)
    rows = page.locator(".graph-split").locator("visible=true").first.locator(
        ".panel-list tr[data-node]")
    read = ("row => [...row.querySelectorAll('td, td .sub')].map(el => ["
            "getComputedStyle(el).color, getComputedStyle(el.closest('td')).backgroundColor])")

    rows.nth(0).click()
    shots.wait(page, 300)
    selected = rows.nth(0).evaluate(read)
    rows.nth(1).hover()
    shots.wait(page, 300)
    hovered = rows.nth(1).evaluate(read)

    surface = _rgb(TOKENS["surface"])
    for state, cells in (("selected", selected), ("hovered", hovered)):
        assert len(cells) == 3, state
        for ink, tint in cells:
            assert _channels(tint)[3:] not in ([], [0.0]), (state, "the row is not tinted")
            ground = _over(tint, surface)
            assert contrast_ratio(_over(ink, _rgb(ground)), ground) >= 4.5, (
                state, ink, ground)


def test_on_a_phone_the_list_comes_before_the_graph(chromium, explorer):
    # A graph is not the readable thing on a phone (ADR 0018), so the list leads.
    # The key stays over the graph and a clicked node's facts under it, as near to
    # the graph as they were when they lived inside its frame.
    page = _loaded(chromium, explorer, "phone")
    try:
        shots.tab(page, "Pilots")
        shots.pick(page, "Pilot", PILOT)
        _card_titled(page, "Record").wait_for(timeout=20_000)
        graphs = page.evaluate(_GRAPHS)
        sideways = _sideways_scroll(page)
    finally:
        page.close()

    assert [graph["title"] for graph in graphs] == ["Neighbourhood", "Archetype affinity"]
    for graph in graphs:
        assert graph["listed"] > 0
        assert (graph["list"]["bottom"] <= graph["key"]["top"] + 0.5
                and graph["key"]["bottom"] <= graph["frame"]["top"] + 0.5
                and graph["frame"]["bottom"] <= graph["answer"]["top"] + 0.5), graph
        # Each part the width of the card, one under another.
        assert graph["list"]["width"] == pytest.approx(graph["frame"]["width"], abs=0.5)
    assert sideways <= 0


# --- A name opens its page (issue #333) ---

# The page head on show on the open tab: the name it opens with.
_PAGE_NAME = ("() => { const head = [...(" + _OPEN_TAB + ").querySelectorAll('.page-name')]"
              ".find(el => el.getBoundingClientRect().height > 0);"
              " return head ? head.textContent.trim() : null; }")


def _marked(page) -> str:
    """Mark the document, and hand back its address: what a jump must leave as it is.

    A mark on the window does not survive a reload, so finding it after a click says
    the click loaded nothing.
    """
    return page.evaluate("() => { window.__same_document = true; return location.href; }")


def _assert_same_document(page, address: str) -> None:
    """The mark :func:`_marked` left is still on the window, and the address is the
    one it handed back."""
    assert page.evaluate("() => [window.__same_document === true, location.href]") == [
        True, address]


def _assert_opened(page, tab: str, name: str, address: str) -> None:
    """``name``'s page is drawn on ``tab``, in the document and at the address the
    click was made in."""
    page.wait_for_function(
        "(name) => (" + _PAGE_NAME + ")() === name", arg=name, timeout=30_000)
    _results_on_show(page).first.wait_for(timeout=30_000)
    assert page.get_by_role("tab", name=tab, exact=True).get_attribute(
        "aria-selected") == "true"
    _assert_same_document(page, address)


def _names_on_show(page, kind: str, within: str = ""):
    """The names on the open tab that open a ``kind``'s page, inside ``within``."""
    return page.locator(f"{within} a[data-opens={kind}]").locator("visible=true")


def test_a_card_named_beside_a_card_opens_that_cards_page(page):
    # Issue #333: a name was plain text everywhere, so going from a card to one it is
    # played with meant typing the second card's name into the picker. The name in the
    # list is the way there: its own page, the overview, drawn by the click.
    shots.tab(page, "Cards")
    shots.chip(page, "View", "Co-occurrence")
    shots.pick(page, "Card", CARD)
    _card_titled(page, "Co-occurrence").wait_for(timeout=20_000)
    address = _marked(page)

    name = page.locator(".panel-list a[data-opens=card]").locator("visible=true").first
    beside = name.inner_text()
    assert beside != CARD
    # The name is the link, and only the name: the rest of its row still selects the
    # card's node in the graph beside it (#330).
    row = page.locator(".panel-list tr[data-node]").locator("visible=true").first
    row.locator("td.num").last.click()
    shots.wait(page, 300)
    assert page.evaluate(_ANSWERED)["selected"] == [row.get_attribute("data-node")]
    assert page.evaluate(_PAGE_NAME) == CARD  # and it opened nothing
    name.click()

    _assert_opened(page, "Cards", beside, address)
    # The card's own page is its overview, and the picker holds the card now.
    _card_titled(page, "Archetypes").wait_for(timeout=20_000)
    assert shots.field(page, "Card").input_value() == beside


def _assert_in_view_on_the_faq(page, entry: str, address: str) -> None:
    """``entry``'s box is on screen on the FAQ tab, in the same document and at the
    same address."""
    # Gradio draws a group as two nested elements and gives both its id; the outer
    # one is the box.
    box = page.locator(f"#{entry}").first
    box.wait_for()
    assert page.get_by_role("tab", name="FAQ", exact=True).get_attribute(
        "aria-selected") == "true"
    assert 0 <= box.bounding_box()["y"] < page.viewport_size["height"] / 2
    _assert_same_document(page, address)


# Each chart on show on the open tab: its card's title, and the FAQ entry the
# "How this is measured" link in that card's head names, or null where it has none.
_MEASURED = "() => { const open = " + _OPEN_TAB + """;
    return [...open.querySelectorAll('.js-plotly-plot, .share-list')]
      .filter(chart => chart.getBoundingClientRect().height > 0).map(chart => {
        const card = chart.closest('.insight-card');
        const link = card.querySelector('a.measured');
        return [card.querySelector('.t-result-title').innerText,
                link && link.getBoundingClientRect().height > 0
                  ? link.getAttribute('href') : null];
      }); }"""


def _assert_each_chart_opens_its_entry(page, walk, entries: dict) -> None:
    """Walk every chart of ``walk`` and follow its link to the FAQ and back."""
    address = _marked(page)
    seen = {}
    for name, picks, charts in walk:
        # Metagame's list of the most played is headed as the chart it replaced was
        # (#334), so its card carries the link too, beside the charts of the walk.
        linked = charts + (name == "Metagame")
        if not linked:
            continue
        _walked(page, [(name, picks, charts)])
        on_show = page.evaluate(_MEASURED)
        assert len(on_show) == linked, (name, on_show)
        for title, href in on_show:
            assert href == "#" + entries[title], (name, title, href)
            seen[title] = href
            _card_titled(page, title).locator("a.measured").click()
            _assert_in_view_on_the_faq(page, href[1:], address)
            shots.tab(page, name)
    assert set(seen) == set(entries)


def test_each_chart_carries_a_link_that_opens_how_it_is_measured(page):
    # Issue #333: how a chart is worked out is the FAQ's to say, a tab away. Each
    # chart's head has a small link that opens the FAQ on that chart's own entry.
    # The suite's snapshot draws one kind of chart, and Metagame's list.
    _assert_each_chart_opens_its_entry(page, SUITE_WALK, {
        "Meta share": "faq-meta", "Adoption over time": "faq-adoption",
    })


def test_each_chart_opens_how_it_is_measured_on_the_real_record(chromium, record):
    # All eight charts, which only the real record draws, and Metagame's list.
    page = _loaded(chromium, record)
    try:
        _assert_each_chart_opens_its_entry(page, RECORD_WALK, {
            "Meta share": "faq-meta",
            "Metagame landscape": "faq-landscape", "Share over time": "faq-meta",
            "Finishes over time": "faq-archetype-timeline",
            "Adoption over time": "faq-adoption",
            "Performance over time": "faq-performance",
            "Head-to-head timeline": "faq-head-to-head", "Score over time": "faq-race",
        })
    finally:
        page.close()


def test_a_pilot_named_in_a_head_to_head_opens_their_own_page(page):
    # The one pilot's name the suite's snapshot can click: a head-to-head draws two
    # pilots, and neither's own page is the one on show. The name of a clicked node
    # is the way to it, and the click turns the view back to the overview, which a
    # pick alone would not draw on the view it was made from.
    shots.tab(page, "Pilots")
    shots.chip(page, "View", "Head-to-head")
    shots.pick(page, "Pilot", PILOT)
    shots.pick(page, "Second pilot (required)", PILOT_B)
    shots.draw(page, settle=3000)
    address = _marked(page)
    # The second pilot's node, selected as a click on it selects it.
    page.evaluate(
        "() => { const split = [...(" + _OPEN_TAB + ").querySelectorAll('.graph-split')]"
        ".find(el => el.getBoundingClientRect().height > 0);"
        " const inside = split.querySelector('iframe').contentWindow;"
        " const id = inside.network.body.data.nodes.getIds()"
        ".filter(id => id.startsWith('pilot:'))[1];"
        " inside.network.selectNodes([id]);"
        " inside.network.body.emitter.emit('selectNode', {nodes: [id]}); }")

    name = _names_on_show(page, "pilot", ".node-facts:not([hidden]) .node-name").first
    pilot = name.inner_text()
    assert pilot in (PILOT, PILOT_B)
    name.click()

    _assert_opened(page, "Pilots", pilot, address)
    _card_titled(page, "Record").wait_for(timeout=20_000)
    assert shots.field(page, "Pilot").input_value() == pilot


def test_a_name_is_reached_by_keyboard_with_a_ring_and_opens_on_enter(page):
    # Issue #333: a name is a link, so the Tab key reaches it like any other, it
    # shows the app's focus ring while it has the focus, and Enter follows it.
    shots.tab(page, "Cards")
    shots.chip(page, "View", "Co-occurrence")
    shots.pick(page, "Card", CARD)
    _card_titled(page, "Co-occurrence").wait_for(timeout=20_000)
    address = _marked(page)

    # From the card's own picker, by the Tab key alone, to the first name after it.
    shots.field(page, "Card").focus()
    for _ in range(40):
        page.keyboard.press("Tab")
        if page.evaluate("() => document.activeElement.matches('a[data-opens]')"):
            break
    name, ring = page.evaluate(
        "() => { const el = document.activeElement, style = getComputedStyle(el);"
        " return [el.matches('a[data-opens]:focus-visible') ? el.textContent : null,"
        " [style.outlineStyle, parseFloat(style.outlineWidth) >= 2, style.outlineColor]]; }")
    assert name and name != CARD
    assert ring == ["solid", True, _rgb(TOKENS["accent-bright"])]

    page.keyboard.press("Enter")

    _assert_opened(page, "Cards", name, address)


# What is in the accent on the open tab, where it should not be: text inside a
# table or a caption (the caption rule's, and the two older roles a chart's head
# still uses) that is no link, and a name that opens a page that is not in it.
_ACCENTED = "(accent) => { const open = " + _OPEN_TAB + """;
    const shown = el => el.getBoundingClientRect().height > 0;
    const inked = el => [...el.childNodes].some(
      node => node.nodeType === Node.TEXT_NODE && node.textContent.trim());
    const said = [...open.querySelectorAll(
      'table.data-table, .caption, .t-caption, .t-fieldstat')].filter(shown);
    const names = [...open.querySelectorAll('a[data-opens]')].filter(shown);
    return {
      read: said.length,
      names: names.length,
      strays: said.flatMap(box => [box, ...box.querySelectorAll('*')]
        .filter(el => inked(el) && accent.includes(getComputedStyle(el).color)
                      && !el.closest('a'))
        .map(el => el.innerText.slice(0, 60))),
      plain: names.filter(el => !accent.includes(getComputedStyle(el).color))
        .map(el => el.innerText),
    }; }"""


def _accented_on(page, walk) -> dict:
    """:data:`_ACCENTED` for each stop of ``walk``, by tab and the picks made on it."""
    read = {}
    for name, picks, charts in walk:
        _walked(page, [(name, picks, charts)])
        if picks:
            shots.drawn(page, settle=1500)
        read[(name, *(text for _, text in picks))] = page.evaluate(_ACCENTED, _ACCENTS)
    return read


def _assert_the_accent_is_kept_for_links(read: dict) -> int:
    """Nothing in a table or a caption is in the accent but a link, and every name
    that opens a page is in it. Hands back how many such names were read."""
    for where, found in read.items():
        assert found["strays"] == [], where
        assert found["plain"] == [], where
    assert sum(found["read"] for found in read.values()) > 0
    return sum(found["names"] for found in read.values())


def test_in_tables_and_captions_only_a_link_is_in_the_accent(page):
    # Issue #333: the accent is the colour of what can be clicked, and a name that
    # opens a page is in it. So nothing else in a table or a caption may be, or a
    # reader cannot tell a name from a figure. The chart heads on Metagame set their
    # subtitle in the accent until now.
    read = _accented_on(page, SUITE_WALK)

    assert _assert_the_accent_is_kept_for_links(read) > 0
    assert read[("Metagame",)]["read"] > 0


def test_only_a_link_is_in_the_accent_on_any_tab_of_the_real_record(chromium, record):
    # The tabs the suite's snapshot cannot fill: the landscape and the race state a
    # reading under their titles, the gems a count, and the standings name 50 pilots.
    page = _loaded(chromium, record)
    try:
        read = _accented_on(page, RECORD_WALK)
    finally:
        page.close()

    assert _assert_the_accent_is_kept_for_links(read) > 50
    for tab in ("Hidden gems", "Player leaderboard"):
        assert read[(tab,)]["names"] > 0 and read[(tab,)]["read"] > 1, tab


@pytest.mark.parametrize("width", ["phone", "desktop"])
def test_a_pilot_in_the_standings_opens_their_page_on_the_real_record(
    chromium, record, width
):
    # Issue #333: the 50 standings rows held no clickable name, so from the board to
    # a pilot's page was the Pilots tab and the name typed in. The suite's snapshot
    # has no race, so this is the real record's, and whoever stands 30th there.
    page = _loaded(chromium, record, width)
    try:
        # Left on the head-to-head, where a pick alone draws nothing: a pilot's page
        # is the overview, so the click has to turn the view back as well.
        shots.tab(page, "Pilots")
        shots.chip(page, "View", "Head-to-head")
        shots.tab(page, "Player leaderboard")
        address = _marked(page)
        names = _names_on_show(page, "pilot", ".data-table")
        assert names.count() == 50
        name = names.nth(29)
        pilot = name.inner_text()
        name.scroll_into_view_if_needed()
        assert page.evaluate("() => window.scrollY") > 0

        (name.tap if width == "phone" else name.click)()

        _assert_opened(page, "Pilots", pilot, address)
        _card_titled(page, "Record").wait_for(timeout=30_000)
        assert shots.field(page, "Pilot").input_value() == pilot
        # A page opened from far down another one starts at its top.
        assert page.evaluate("() => window.scrollY") < page.viewport_size["height"] / 2

        # The same name again, with the page it opens already drawn: nothing is
        # redrawn, so nothing brings the window back up but the jump itself.
        shots.tab(page, "Player leaderboard")
        name = _names_on_show(page, "pilot", ".data-table").nth(29)
        name.scroll_into_view_if_needed()
        assert page.evaluate("() => window.scrollY") > page.viewport_size["height"] / 2
        (name.tap if width == "phone" else name.click)()
        _assert_opened(page, "Pilots", pilot, address)
        assert page.evaluate("() => window.scrollY") == 0
    finally:
        page.close()


def test_a_gem_its_archetype_and_who_played_it_each_open_a_page_on_the_real_record(
    chromium, record
):
    # From a gem to its card was the Cards tab and the name typed in. The gems tab
    # names three kinds of subject, and each name is the way to its page: the card
    # in the table, the archetype heading its gems, and, in the details of a deck
    # that ran the gem, the pilot who played it.
    page = _loaded(chromium, record)
    try:
        shots.tab(page, "Hidden gems")
        address = _marked(page)

        gem = _names_on_show(page, "card", ".panel-list").first
        card = gem.inner_text()
        gem.click()
        _assert_opened(page, "Cards", card, address)
        _card_titled(page, "Archetypes").wait_for(timeout=30_000)

        shots.tab(page, "Hidden gems")
        heading = _names_on_show(page, "archetype", ".panel-list .group").first
        archetype = heading.inner_text()
        heading.click()
        _assert_opened(page, "Archetypes", archetype, address)
        _card_titled(page, "Finishes over time").wait_for(timeout=30_000)

        shots.tab(page, "Hidden gems")
        shots.wait(page, 1500)
        # A deck the graph draws, selected as a click on its node selects it.
        page.evaluate(
            "() => { const split = [...(" + _OPEN_TAB + ").querySelectorAll('.graph-split')]"
            ".find(el => el.getBoundingClientRect().height > 0);"
            " const inside = split.querySelector('iframe').contentWindow;"
            " const id = inside.network.body.data.nodes.getIds().find(id => id.startsWith('deck:'));"
            " inside.network.selectNodes([id]);"
            " inside.network.body.emitter.emit('selectNode', {nodes: [id]}); }")
        played_by = _names_on_show(page, "pilot", ".node-facts:not([hidden]) dd").first
        pilot = played_by.inner_text()
        played_by.click()
        _assert_opened(page, "Pilots", pilot, address)
        _card_titled(page, "Record").wait_for(timeout=30_000)
    finally:
        page.close()


def test_an_archetype_in_a_pilots_record_opens_its_page_on_the_real_record(
    chromium, record
):
    # The record names an archetype per deck. No archetype of the suite's snapshot
    # has a page (none has the events for a finishes line), so this is the real
    # record's. The archetype is opened alone, whatever it was last compared with.
    page = _loaded(chromium, record)
    try:
        shots.tab(page, "Archetypes")
        shots.pick(page, "Archetype", shots.ARCHETYPE)
        shots.pick(page, "Second archetype (optional)", shots.ARCHETYPE_B)
        shots.tab(page, "Pilots")
        shots.pick(page, "Pilot", shots.PILOT)
        record_card = _card_titled(page, "Record")
        record_card.wait_for(timeout=30_000)
        address = _marked(page)

        name = record_card.locator("a[data-opens=archetype]").first
        archetype = name.inner_text()
        name.click()

        _assert_opened(page, "Archetypes", archetype, address)
        _card_titled(page, "Finishes over time").wait_for(timeout=30_000)
        assert shots.field(page, "Archetype").input_value().startswith(archetype + " (")
        assert shots.field(page, "Second archetype (optional)").input_value() == ""
        assert "compared with" not in page.locator(
            "#archetype-page .page-descriptor").locator("visible=true").inner_text()
    finally:
        page.close()


# The landscape's names (#337). Plotly prints a name beside every dot and has no way
# to keep two of them apart, so the page decides which are drawn. What a reader gets
# is a matter of pixels (how wide a name is set, where its dot lands at this width),
# so it is read off the rendered chart: each name's box, each dot's, and the chart's
# own frame.
_LANDSCAPE_NAMES = """() => {
  const chart = document.querySelector('#landscape-result .js-plotly-plot');
  const main = selector => [...chart.querySelectorAll(selector)]
    .filter(el => !el.closest('.rangeslider-container'));
  const box = el => { const b = el.getBoundingClientRect();
    return {left: b.left, right: b.right, top: b.top, bottom: b.bottom}; };
  const shown = el => { const style = getComputedStyle(el);
    return style.display !== 'none' && style.visibility !== 'hidden'; };
  const frame = box(chart);
  frame.right = Math.min(frame.right, document.documentElement.clientWidth);
  return {
    frame,
    names: main('.scatterlayer .textpoint text').map(text => (
      {name: text.textContent, drawn: shown(text) && shown(text.parentElement),
       box: box(text)})),
    dots: main('.scatterlayer .points path.point').filter(shown).map(box),
  };
}"""


def _crowd(*rows: tuple) -> list[LandscapeCell]:
    """Landscape cells from ``(name, decks of a thousand, finish[, events])``, with a
    bar a tenth either side. A dot is sized by its events, eight unless a row says."""
    return [
        LandscapeCell(tag=name.lower(), archetype=name, year=2026, n=decks,
                      share=decks / 1000, year_total=1000, year_events=10,
                      mean_norm=1 - finish, events=events[0] if events else 8,
                      scored=decks, mean_low=1 - finish - 0.1,
                      mean_high=1 - finish + 0.1)
        for name, decks, finish, *events in rows
    ]


@contextmanager
def _landscape_of(chromium, cells: list[LandscapeCell], width: str = "desktop"):
    """A page holding the landscape of ``cells`` in the card the app draws it in,
    with the page script the app loads, at one of the three widths."""
    with gr.Blocks() as demo:
        with gr.Group(elem_classes="insight-card", elem_id="landscape-result"):
            gr.Plot(value=_landscape_figure(cells))
        demo.load(fn=None, js=_LANDSCAPE_NAMES_JS)
    _, url, _ = demo.launch(prevent_thread_lock=True, **launch_kwargs())
    tab = chromium.new_page(**shots.VIEWPORTS[width])
    try:
        tab.goto(url, wait_until="networkidle")
        tab.locator("#landscape-result .scatterlayer .textpoint").first.wait_for(
            state="attached", timeout=30_000)
        tab.wait_for_timeout(1000)
        yield tab
    finally:
        tab.close()
        demo.close()


def _overlap(a: dict, b: dict) -> bool:
    return (a["left"] < b["right"] and b["left"] < a["right"]
            and a["top"] < b["bottom"] and b["top"] < a["bottom"])


def _drawn(read: dict) -> list[str]:
    return [name["name"] for name in read["names"] if name["drawn"]]


def _assert_every_drawn_name_is_legible(read: dict, where: str = "") -> None:
    """No drawn name runs past the chart, into another name, or over a dot that is
    not its own. A name sits beside its own dot, so no dot's box reaches it."""
    drawn = [name for name in read["names"] if name["drawn"]]
    frame = read["frame"]
    cut = [name["name"] for name in drawn
           if name["box"]["left"] < frame["left"] or name["box"]["right"] > frame["right"]
           or name["box"]["top"] < frame["top"] or name["box"]["bottom"] > frame["bottom"]]
    assert not cut, f"{where}: cut off at the chart's edge: {cut}"
    stacked = [(a["name"], b["name"]) for i, a in enumerate(drawn) for b in drawn[i + 1:]
               if _overlap(a["box"], b["box"])]
    assert not stacked, f"{where}: printed over each other: {stacked}"
    on_a_dot = [name["name"] for name in drawn
                if any(_overlap(name["box"], dot) for dot in read["dots"])]
    assert not on_a_dot, f"{where}: printed over another archetype's dot: {on_a_dot}"


def test_a_name_that_would_run_off_the_plot_is_set_on_the_other_side_of_its_dot(chromium):
    # Issue #337: at 390px the most played archetype of 2026 sits against the right
    # edge and its name, set to the right of its dot, was cut off there. The dot at
    # the edge is the most played one by construction, so it is the last name a
    # reader should lose: it moves to the left of its dot.
    cells = _crowd(("Grixis Control", 300, 0.55), ("Lands", 100, 0.45), ("Storm", 50, 0.6))
    with _landscape_of(chromium, cells, "phone") as page:
        read = page.evaluate(_LANDSCAPE_NAMES)

    assert _drawn(read) == ["Grixis Control", "Lands", "Storm"]
    _assert_every_drawn_name_is_legible(read)


def test_two_names_wanting_one_spot_leave_it_to_the_more_played_archetype(chromium):
    # Two archetypes a hundredth of a finish and a tenth of a point of share apart,
    # against the left edge of a phone: neither name fits to the left of its dot,
    # and to the right the two land on one spot, 12px apart. One name is drawn
    # there, the more played archetype's, whichever the chart was handed first.
    cells = _crowd(("Rakdos Eclipse", 19, 0.5), ("Grixis", 300, 0.6),
                   ("Selesnya Lands", 20, 0.511))
    with _landscape_of(chromium, cells, "phone") as page:
        read = page.evaluate(_LANDSCAPE_NAMES)

    assert _drawn(read) == ["Grixis", "Selesnya Lands"]
    _assert_every_drawn_name_is_legible(read)


def _hovered(page, index: int, *, tap: bool = False) -> str:
    """What the chart says with the pointer on the middle of its dot number
    ``index``, counted as the chart was handed them, or with a finger put there."""
    point = page.locator(
        "#landscape-result .cartesianlayer .scatterlayer path.point").nth(index)
    point.scroll_into_view_if_needed()
    dot = point.bounding_box()
    x, y = dot["x"] + dot["width"] / 2, dot["y"] + dot["height"] / 2
    if tap:
        page.touchscreen.tap(x, y)
    else:
        # Moved onto the dot rather than placed on it: the library answers movement.
        page.mouse.move(x - 40, y - 40)
        page.mouse.move(x, y, steps=5)
    said = page.locator("#landscape-result .hoverlayer .hovertext")
    said.wait_for()
    # The library answers a few times a second, and on the way in the pointer
    # crosses whatever lies round the dot: the answer read is the one it rests on.
    page.wait_for_timeout(300)
    return said.text_content()


# Three archetypes in a row on one finish, a point of share apart, and a fourth far
# to the right that sets the axis. The middle one's name fits on neither side of
# its dot: each side is another archetype's dot.
_ROW = _crowd(("Grixis", 400, 0.6), ("Oracle", 200, 0.5), ("Blue Moon", 190, 0.5),
              ("Walks", 180, 0.5))


def test_a_name_with_another_dot_on_both_sides_is_left_to_the_hover(chromium):
    # A name printed across a neighbour's ring reads as neither archetype's. It is
    # not drawn, and its dot still says whose it is under the pointer.
    with _landscape_of(chromium, _ROW) as page:
        read = page.evaluate(_LANDSCAPE_NAMES)
        said = _hovered(page, 2)

    assert _drawn(read) == ["Grixis", "Oracle", "Walks"]
    _assert_every_drawn_name_is_legible(read)
    assert said.startswith("Blue Moon · share 19%")


def _drag_the_filter(page, handle: str, to: float) -> None:
    """Drag one handle of the share range filter, ``min`` or ``max``, to a point
    along its band, 0 its left end and 1 its right."""
    spot = page.evaluate(
        """(handle) => { const card = document.querySelector('#landscape-result');
             const grab = card.querySelector(`.rangeslider-grabarea-${handle}`)
               .getBoundingClientRect();
             const band = card.querySelector('.rangeslider-bg').getBoundingClientRect();
             return {x: grab.left + grab.width / 2, y: grab.top + grab.height / 2,
                     left: band.left, width: band.width}; }""", handle)
    page.mouse.move(spot["x"], spot["y"])
    page.mouse.down()
    page.mouse.move(spot["left"] + spot["width"] * to, spot["y"], steps=10)
    page.mouse.up()
    page.wait_for_timeout(800)


def test_opening_the_crowded_end_with_the_range_filter_brings_its_names_back(chromium):
    # The filter is how a reader opens a crowd (ADR 0013). A name left out for want
    # of room is drawn once the drag has made the room.
    cells = [*_ROW, *_crowd(("Jund", 390, 0.6))]
    with _landscape_of(chromium, cells) as page:
        before = _drawn(page.evaluate(_LANDSCAPE_NAMES))
        _drag_the_filter(page, "max", 0.6)
        _drag_the_filter(page, "min", 0.3)
        read = page.evaluate(_LANDSCAPE_NAMES)
        stood_in_for = page.evaluate(
            """() => document.querySelector('#landscape-result .js-plotly-plot')
                 .data[0].customdata.map(row => row[row.length - 1])""")

    assert "Blue Moon" not in before
    # Grixis and Jund are off the right of the filtered range, where the chart
    # draws nothing.
    assert _drawn(read) == ["Oracle", "Blue Moon", "Walks"]
    _assert_every_drawn_name_is_legible(read)
    # Two dots the chart no longer draws are not on one spot, and no dot's hover
    # says they are.
    assert stood_in_for == [""] * 5


def _assert_a_plain_strip(strip: dict, words: list[str]) -> None:
    """A range control as the running page draws it is the one strip every chart
    carries (#335, #366): 24px deep, in the neutral ink a reference line is drawn in
    (the design doc's `text-dim`) at whatever strength, never the accent the slab
    wore, and the chart sets no word about filtering or dragging."""
    assert strip["height"] == pytest.approx(24, abs=1)
    neutral = [int(TOKENS["text-dim"][at:at + 2], 16) for at in (1, 3, 5)]
    for colour in (strip["fill"], strip["edge"]):
        assert [int(part) for part in re.findall(r"\d+", colour)[:3]] == neutral
    assert not [word for word in words
                if re.search(r"filter|drag|zoom|slice", word, re.I)]


def test_the_landscapes_range_control_is_drawn_as_the_strip_the_timelines_carry(chromium):
    # AC (#366): one control, one look. The landscape kept a 44px bar in the app's
    # accent with "◀ Share range filter (drag to zoom) ▶" written across it after
    # #335 gave the timelines a plain strip. On the running page it is that strip, held
    # to the same reading the timelines' is.
    with _landscape_of(chromium, _ROW) as page:
        read = page.evaluate(
            """() => { const chart = document.querySelector(
                   '#landscape-result .js-plotly-plot');
                 const strip = chart.querySelector('.rangeslider-bg');
                 return {strip: {height: strip.getBoundingClientRect().height,
                                 fill: getComputedStyle(strip).fill,
                                 edge: getComputedStyle(strip).stroke},
                         words: [...chart.querySelectorAll('text')]
                           .map(text => text.textContent)}; }""")

    _assert_a_plain_strip(read["strip"], read["words"])


def _landscape_years(page) -> list[str]:
    """The years the landscape's selector offers, as it lists them."""
    return page.evaluate(
        """() => [...[...document.querySelectorAll('fieldset')]
             .find(group => group.querySelector('[data-testid=block-info]')
                                 .textContent.trim() === 'Year')
             .querySelectorAll('label')].map(label => label.textContent.trim())""")


def _landscape_read(page, year: str) -> dict:
    """The landscape's names once it has drawn ``year``, and the most played
    archetype of the ones it draws, off the chart's own data."""
    page.wait_for_function(
        """(year) => { const card = document.querySelector('#landscape-result');
             return card.querySelector('.t-fieldstat')?.textContent.includes(year)
                 && card.querySelector('.scatterlayer .textpoint'); }""",
        arg=year, timeout=30_000)
    page.wait_for_timeout(1500)
    read = page.evaluate(_LANDSCAPE_NAMES)
    read["most_played"] = page.evaluate(
        """() => { const dots = document.querySelector(
               '#landscape-result .js-plotly-plot').data[0];
             return dots.text[dots.x.indexOf(Math.max(...dots.x))]; }""")
    return read


@pytest.mark.parametrize("width", list(shots.VIEWPORTS))
def test_every_name_the_landscape_draws_is_legible_on_the_real_record(
    chromium, record, width
):
    # Issue #337, measured on 2026-11-17 over the real record: the landscape printed
    # 8 to 21 pairs of names over each other at 390px and 2 to 11 at 1440 and 1920,
    # year by year, and at 390 cut the most played archetype's name off at the right
    # edge in three of the four years. Every year the selector offers, each drawn by
    # picking it, which is how a reader reaches all but the first.
    page = _loaded(chromium, record, width)
    try:
        _card_titled(page, "Metagame landscape").wait_for(timeout=30_000)
        years = _landscape_years(page)
        reads = {years[0]: _landscape_read(page, years[0])}
        for year in years[1:]:
            shots.chip(page, "Year", year)
            reads[year] = _landscape_read(page, year)
    finally:
        page.close()

    assert len(reads) >= 4
    for year, read in reads.items():
        _assert_every_drawn_name_is_legible(read, f"{year} at {width}")
        # The most played archetypes are the ones that keep their names, and the
        # first of them stands clear of the rest by a point of share or more.
        assert read["most_played"] in _drawn(read), f"{year} at {width}"
        # Hiding names that fit would pass the lines above. A loose floor, not the
        # measured counts (7 to 15 of 24 or 25 at 390, 18 to 24 at 1440 and 1920):
        # a quarter of the dots keep a name on a phone and half on a desktop.
        floor = len(read["names"]) / (4 if width == "phone" else 2)
        assert len(_drawn(read)) >= floor, f"{year} at {width}"


@pytest.mark.parametrize("width", list(shots.VIEWPORTS))
def test_an_archetype_whose_name_is_not_drawn_gives_it_on_hover_on_the_real_record(
    chromium, record, width
):
    # The names left out are not lost: the pointer on the middle of a dot gets a
    # box with its archetype's name in it, and so does a finger on a phone, where
    # most of the names are left out (59 over the four years, against 18 at 1440).
    # Every year, at the three widths. In 2024 the dots of Grixis and of Boros
    # Moonshine are 1px apart and the box over the middle of Grixis is Boros
    # Moonshine's, so the one box names both.
    page = _loaded(chromium, record, width)
    said = {}
    try:
        _card_titled(page, "Metagame landscape").wait_for(timeout=30_000)
        assert page.evaluate("() => matchMedia('(hover: none)').matches") == (width == "phone")
        years = _landscape_years(page)
        for year in years:
            if year != years[0]:
                shots.chip(page, "Year", year)
            read = _landscape_read(page, year)
            said[year] = {name["name"]: _hovered(page, index, tap=width == "phone")
                          for index, name in enumerate(read["names"]) if not name["drawn"]}
    finally:
        page.close()

    assert sum(len(left_out) for left_out in said.values()) >= 10
    for year, left_out in said.items():
        for name, box in left_out.items():
            assert box.startswith(f"{name} · share ") or re.search(
                rf" · also on this spot: (.+, )?{re.escape(name)}(,|$)", box
            ), f"{name} in {year} at {width}: {box}"


def test_a_dot_that_answers_the_pointer_in_anothers_place_names_both(chromium):
    # Where two dots overlap, the chart library answers for the smaller one. In 2024
    # Grixis and Boros Moonshine have the same share and finishes 0.0007 apart, and
    # Grixis was scored at two more events: its ring is half a pixel wider all
    # round, so the pointer finds Boros Moonshine on all of it but that rim. Here a
    # like pair stands against the left edge of a phone, where one name fits, and the
    # archetype left without one is the one the pointer cannot find. The box that
    # comes up over its dot names it.
    cells = _crowd(("Boros Moonshine", 20, 0.5, 8), ("Grixis Control", 20, 0.5, 9),
                   ("Oracle", 300, 0.6))
    with _landscape_of(chromium, cells, "phone") as page:
        read = page.evaluate(_LANDSCAPE_NAMES)
        over_the_covered_dot = _hovered(page, 1)
        over_a_dot_alone = _hovered(page, 2)

    assert _drawn(read) == ["Boros Moonshine", "Oracle"]
    assert over_the_covered_dot == (
        "Boros Moonshine · share 2% (20 / 1,000 decks) · finish 0.50 "
        "(20 scored at 8 events) · also on this spot: Grixis Control")
    assert over_a_dot_alone == (
        "Oracle · share 30% (300 / 1,000 decks) · finish 0.60 (300 scored at 8 events)")


# The Community page (issue #346): the first tab, and the one the site opens on. Its
# charts are drawn by its own script to the width they are given, so what a reader
# gets is only known on a drawn page.

@contextmanager
def _community_page(chromium, url: str, width: str):
    """The page a visit opens on, at ``width``, once its charts are drawn."""
    page = _loaded(chromium, url, width, on=None)
    try:
        page.locator("circle.cm-event").first.wait_for()
        yield page
    finally:
        page.close()


_COMMUNITY = """() => {
  const page = document.querySelector('#community .community');
  const texts = (selector) => [...page.querySelectorAll(selector)]
    .map(node => node.textContent.replace(/\\s+/g, ' ').trim());
  return {
    open: document.querySelector('[role=tab][aria-selected=true]').textContent.trim(),
    shown: page.getBoundingClientRect().height > 0,
    values: texts('.cm-value'), beneath: texts('.cm-beneath'), key: texts('.cm-key span'),
    dots: page.querySelectorAll('circle.cm-event').length,
    majors: page.querySelectorAll('circle.cm-major').length,
    onDots: texts('[data-chart=events] text.cm-on'),
    ends: texts('[data-chart=pilots] text.cm-ink'),
    months: page.querySelectorAll('path.cm-month').length,
    busiest: texts('[data-chart=months] text.cm-ink'),
    rows: [...page.querySelectorAll('.cm-row')].map(row => ({
      year: row.querySelector('.cm-year').firstChild.textContent,
      parts: [...row.querySelectorAll('.cm-seg')].map(seg => seg.textContent.trim()),
      total: row.querySelector('.cm-total').textContent,
    })),
    note: texts('.cm-note')[0],
  };
}"""


def _community_of(bundle: Path) -> dict:
    return json.loads(figures_path(bundle).read_text())["community"]


def _assert_the_page_shows_the_file(read: dict, held: dict) -> None:
    """``read`` off the drawn page against ``held``, the bundle's own figures."""
    latest = held["years"][-1]
    totals, this_year, largest = held["totals"], held["this_year"], held["largest_field"]
    kinds = ("pilots", "events", "decks", "cards", "archetypes")
    assert read["values"] == [f"{totals[k]:,}" for k in kinds] + [f"{largest['players']:,}"]
    assert read["beneath"][:5] == [f"{this_year[k]:,} in {latest}" for k in kinds]
    assert read["beneath"][5] == (
        f"{largest['major']}, {largest['year']}" if largest["major"] else str(largest["year"]))
    assert read["key"][0].endswith(f"({held['majors']:,})")
    assert read["key"][1] == f"Other events ({held['other_events']:,})"
    # One dot an event, the majors solid, and the busiest month's count on its bar.
    assert read["dots"] == len(held["events"]) == totals["events"]
    assert read["majors"] == held["majors"]
    # A number on a dot is that event's players.
    assert not Counter(read["onDots"]) - Counter(str(e["players"]) for e in held["events"])
    assert read["ends"] == [f"{totals['pilots']:,}"]
    assert read["months"] == len(held["events_by_month"])
    assert read["busiest"] == [str(max(n for _, n in held["events_by_month"]))]
    assert read["rows"] == [
        {"year": str(row["year"]),
         "parts": [f"{n:,}" for n in row["first_played"].values()],
         "total": f"{row['pilots']:,}"}
        for row in held["pilots_by_year"]
    ]
    assert read["note"] == (
        f"{this_year['played_before']:,} of {latest}'s {this_year['pilots']:,} pilots had "
        f"played before. {held['played_every_year']:,} have played every year since "
        f"{held['years'][0]}.")


def test_the_site_opens_on_the_community_page_showing_what_its_figures_file_holds(
    landing, suite_bundle
):
    # AC (#346): Community is the tab a visit opens on, and every number and chart
    # on it is the figures file's, read here off the drawn page.
    read = landing.evaluate(_COMMUNITY)

    assert read["open"] == "Community" and read["shown"]
    _assert_the_page_shows_the_file(read, _community_of(suite_bundle))


@pytest.mark.parametrize("width", list(shots.VIEWPORTS))
def test_the_community_page_shows_what_the_real_records_file_holds(chromium, record, width):
    # The same over the real record, at each width: the charts are drawn again for
    # the room they have, and must draw the same record in it.
    with _community_page(chromium, record, width) as page:
        read = page.evaluate(_COMMUNITY)

    held = _community_of(artifact_path())
    assert read["open"] == "Community" and read["shown"]
    _assert_the_page_shows_the_file(read, held)
    # A dot large enough to hold its number shows it, and on a phone none is.
    assert (str(held["largest_field"]["players"]) in read["onDots"]) == (width != "phone")


@pytest.fixture(params=["explorer", "record"])
def served(request) -> tuple[str, dict]:
    """The explorer over the suite's snapshot and over the real record, each with
    the Community figures of the bundle it is over."""
    url = request.getfixturevalue(request.param)
    bundle = (request.getfixturevalue("suite_bundle") if request.param == "explorer"
              else artifact_path())
    return url, _community_of(bundle)


def test_each_doorway_opens_its_tab_at_the_top(landing):
    # AC (#346): a click on a doorway opens the tab it names, from the top, and
    # there is one for each content tab.
    page = landing
    doors = page.locator("#community a.cm-door")
    tabs = doors.evaluate_all("doors => doors.map(door => door.dataset.tab)")
    assert tabs == [tab for tab, _ in community.DOORS]

    for tab in tabs:
        page.get_by_role("tab", name="Community", exact=True).click()
        page.locator(f"#community a.cm-door[data-tab='{tab}']").click()
        page.wait_for_function(
            "tab => document.querySelector('[role=tab][aria-selected=true]')"
            ".textContent.trim() === tab", arg=tab)
        assert page.evaluate("window.scrollY") == 0, tab
        assert page.evaluate(
            "document.querySelector('#community .community')"
            ".getBoundingClientRect().height") == 0, tab


_YEAR_LINES = """() => ['events', 'pilots', 'months'].map(chart =>
  [...document.querySelectorAll(`#community [data-chart=${chart}] line.cm-yearline`)]
    .map(line => [Number(line.dataset.year),
                  Math.round(line.getBoundingClientRect().left * 10) / 10]))"""


@pytest.mark.parametrize("width", ["phone", "desktop"])
def test_the_three_charts_draw_their_year_lines_at_the_same_positions(
    chromium, served, width
):
    # AC (#346): the dots, the running count and the months are on one time axis,
    # so a year starts at the same place down the page in all three, at 1440 and at
    # 390. One line for each year on record and one where the last year ends.
    url, held = served
    with _community_page(chromium, url, width) as page:
        events, pilots, months = page.evaluate(_YEAR_LINES)

    assert [year for year, _ in events] == [*held["years"], held["years"][-1] + 1]
    assert events == pilots == months
    assert [x for _, x in events] == sorted({x for _, x in events})


_OVERFLOWING = """() => {
  const page = document.querySelector('#community .community');
  const wide = document.documentElement.clientWidth;
  const out = [];
  for (const node of page.querySelectorAll('*')) {
    if (node.closest('.cm-tip')) continue;
    const box = node.getBoundingClientRect();
    if (!box.width || !box.height) continue;
    if (getComputedStyle(node).visibility === 'hidden') continue;
    if (box.left < -0.5 || box.right > wide + 0.5) {
      out.push(`${node.tagName}.${node.getAttribute('class')} ${box.left}..${box.right}`);
    }
  }
  // A number on a year's bar stays inside its own part of the bar.
  for (const part of page.querySelectorAll('.cm-seg:not(.cm-tight)')) {
    const own = part.getBoundingClientRect();
    const number = part.firstElementChild.getBoundingClientRect();
    if (number.right > own.right + 0.5) out.push(`number past its part: ${part.textContent}`);
  }
  return {out, sideways: document.documentElement.scrollWidth - wide};
}"""


def test_on_a_phone_nothing_on_the_community_page_is_cut_off_or_scrolls_sideways(
    chromium, served
):
    # AC (#346): the page is the first thing a phone visitor sees. At 390px every
    # mark and every word of it is inside the window, a dot at the edge of the
    # record included, and the page does not scroll sideways.
    url, _ = served
    with _community_page(chromium, url, "phone") as page:
        read = page.evaluate(_OVERFLOWING)

    assert read["out"] == []
    assert read["sideways"] <= 0


_TIP = """() => {
  const tip = document.querySelector('#community .cm-tip');
  if (getComputedStyle(tip).display === 'none') return null;
  return [tip.querySelector('b').textContent, tip.lastChild.textContent];
}"""

_MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
                "Nov", "Dec"]


def _settled_in_view(page, selector: str):
    """The first ``selector``, scrolled to and left a moment: the page takes a
    mark's box down when it scrolls, and a scroll is reported a frame late."""
    mark = page.locator(selector).first
    mark.scroll_into_view_if_needed()
    page.wait_for_timeout(200)
    return mark


def test_a_dot_a_month_and_a_part_of_a_bar_each_say_their_value_under_the_pointer(
    chromium, served
):
    # AC (#346): hovering each kind of mark shows its value. An event says its
    # players and its year, and its name only if it is a major: never a date. The
    # running count says where the line is, and the month under it how many events
    # it holds. A part of a year's bar says its pilots and what share they are.
    url, held = served
    with _community_page(chromium, url, "desktop") as page:
        # The dots are drawn in the record's order, so the first is the first event.
        page.locator("circle.cm-event").first.hover()
        dot = page.evaluate(_TIP)

        # Past the last date on record the line stays at its last count.
        pair = _settled_in_view(page, "#community .cm-pair [data-chart=months]")
        box = pair.bounding_box()
        page.mouse.move(box["x"] + box["width"] - 2, box["y"] + box["height"] / 2)
        month = page.evaluate(_TIP)

        _settled_in_view(page, "#community .cm-seg").hover()
        part = page.evaluate(_TIP)

        page.mouse.move(2, 2)
        gone = page.evaluate(_TIP)

    first = held["events"][0]
    year = first["date"][:4]
    assert dot == [f"{first['players']:,} players",
                   f"{first['major']} · {year}" if first["major"] else year]
    last_month, events = held["events_by_month"][-1]
    assert last_month == held["through"][:7]
    assert month == [
        f"{held['totals']['pilots']:,} pilots on record",
        f"{events} {'event' if events == 1 else 'events'} in "
        f"{_MONTH_NAMES[int(last_month[5:]) - 1]} {last_month[:4]}"]
    row = held["pilots_by_year"][0]
    first_played, pilots = next(iter(row["first_played"].items()))
    assert part[0] == f"{pilots:,} pilots"
    assert part[1].startswith(f"first played in {first_played} · ")
    assert part[1].endswith(f"% of {row['year']}")
    assert gone is None


def test_a_part_of_a_bar_says_its_value_on_keyboard_focus(landing):
    # AC (#346): what the pointer reaches the keyboard reaches too, where the mark
    # can take focus. The parts of a year's bar can, and are in the tab order.
    page = landing
    part = page.locator("#community .cm-seg").first
    assert part.evaluate("part => part.tabIndex") == 0

    # The bars are below the fold, so focus scrolls the page to the part. A scroll
    # takes a pointer's box down, and is reported a frame late: the box has to be
    # there once the page has settled, or a reader tabbing down never sees it.
    assert part.evaluate("part => part.getBoundingClientRect().top > innerHeight")
    part.focus()
    page.wait_for_timeout(300)
    focused = page.evaluate(_TIP)
    in_view = part.evaluate(
        "part => part.getBoundingClientRect().bottom <= innerHeight")
    part.evaluate("part => part.blur()")
    blurred = page.evaluate(_TIP)

    assert in_view

    assert focused[0] == f"{part.inner_text()} pilots"
    assert focused[1].startswith("first played in ")
    assert blurred is None


def test_on_a_phone_a_tapped_dot_keeps_its_value_up_until_the_next_tap(chromium, explorer):
    # A finger has no hover: a tap on a dot shows its value, the box stays up when
    # the finger lifts, and a tap elsewhere on the page takes it down.
    with _community_page(chromium, explorer, "phone") as page:
        dot = _settled_in_view(page, "circle.cm-event")
        box = dot.bounding_box()
        page.touchscreen.tap(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        tapped = page.evaluate(_TIP)
        title = page.locator("#community .page-name").bounding_box()
        page.touchscreen.tap(title["x"] + 5, title["y"] + 5)
        after = page.evaluate(_TIP)

    assert tapped is not None and tapped[0].endswith(" players")
    assert after is None


@contextmanager
def _timeline_of(chromium, figure, width: str = "desktop"):
    """A page holding one finish timeline in the card the app draws it in, with the
    page script the app loads, at one of the three widths."""
    with gr.Blocks() as demo:
        with gr.Group(elem_classes="insight-card", elem_id="timeline"):
            gr.Plot(value=figure)
        demo.load(fn=None, js=_FIT_EVENTS_JS)
    _, url, _ = demo.launch(prevent_thread_lock=True, **launch_kwargs())
    tab = chromium.new_page(**shots.VIEWPORTS[width])
    try:
        tab.goto(url, wait_until="networkidle")
        tab.locator("#timeline .scatterlayer .point").first.wait_for(
            state="attached", timeout=30_000)
        tab.wait_for_timeout(1000)
        yield tab
    finally:
        tab.close()
        demo.close()


def _timeline(events: int, second: bool = True) -> Series:
    """An archetype's finishes at ``events`` events a day apart, the last of them
    today's, beside a second archetype's unless ``second`` is off."""
    return Series(cells=[
        ArchetypeTimelinePoint(
            event=f"E{at}", date=datetime(2026, 1, 1) + timedelta(days=at),
            mean_norm_a=0.2 + 0.6 * (at % 3) / 2, decks_a=1,
            mean_norm_b=0.5 if second else None, decks_b=int(second),
        )
        for at in range(events)
    ])


# What a timeline draws, read off the running page: the stretch of its x axis on
# show, how wide its plot is, the range control under it, every word it sets, and
# the lines and fills it paints.
_TIMELINE_AS_DRAWN = """() => {
  const chart = document.querySelector('#timeline .js-plotly-plot');
  const strip = chart.querySelector('.rangeslider-bg');
  const painted = selector => [...chart.querySelectorAll('.cartesianlayer ' + selector)]
    .filter(path => (path.getAttribute('d') || '').length > 1).length;
  return {
    range: chart._fullLayout.xaxis.range,
    plot: chart._fullLayout._size.w,
    strip: {height: strip.getBoundingClientRect().height,
            fill: getComputedStyle(strip).fill, edge: getComputedStyle(strip).stroke},
    words: [...chart.querySelectorAll('text')].map(text => text.textContent),
    lines: painted('.js-line'), fills: painted('.js-fill'),
    points: chart.querySelectorAll('.cartesianlayer .point').length,
    sideways: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  };
}"""


@pytest.mark.parametrize("width", ["phone", "desktop", "wide"])
def test_a_long_pair_opens_on_as_many_of_its_latest_events_as_the_plot_has_room_for(
    chromium, width
):
    # AC (#335): evenly spaced, forty shared events still do not fit a phone, where the
    # two lines were unreadable in the prototype with every event on screen. A plot
    # too narrow to give each event 10px opens on the latest that fit, and the strip
    # under it reaches the rest. A desktop has the room and opens on them all.
    figure = _archetype_timeline_figure("Storm", "Lands", _timeline(40))
    with _timeline_of(chromium, figure, width) as page:
        read = page.evaluate(_TIMELINE_AS_DRAWN)

    first, last = read["range"]
    # Whatever it opens on ends at the latest event, three quarters of a slot past
    # its ring: event 39.
    assert last == pytest.approx(39.75)
    if width == "phone":
        # As many as fit: each event on show has its 10px, and one more would not.
        on_show = last - first
        assert on_show < 40
        assert read["plot"] / on_show >= 10 > read["plot"] / (on_show + 1)
    else:
        assert read["plot"] > 400
        assert first == pytest.approx(-0.75)
    assert read["sideways"] == 0


@pytest.mark.parametrize("second", [True, False], ids=["pair", "solo"])
def test_a_timelines_range_control_is_drawn_as_a_plain_strip(chromium, second):
    # AC (#335): the range control carries no instruction text and no accent tint, on
    # the running page.
    figure = _archetype_timeline_figure(
        "Storm", "Lands" if second else None, _timeline(6, second))
    with _timeline_of(chromium, figure) as page:
        read = page.evaluate(_TIMELINE_AS_DRAWN)

    _assert_a_plain_strip(read["strip"], read["words"])


@pytest.mark.parametrize("width", ["phone", "desktop"])
def test_a_solo_timeline_paints_its_events_as_points_and_nothing_else(chromium, width):
    # AC (#335): the single timeline shows points and the 0.5 line, with no fill and no
    # smoothed line. On the running page: a point for every event, and no line and no
    # filled area drawn for the series.
    figure = _archetype_timeline_figure("Storm", None, _timeline(40, second=False))
    with _timeline_of(chromium, figure, width) as page:
        read = page.evaluate(_TIMELINE_AS_DRAWN)

    assert read["points"] == 40
    assert (read["lines"], read["fills"]) == (0, 0)
    assert read["sideways"] == 0


def test_a_long_rivalry_opens_on_its_latest_events_on_a_phone_on_the_real_record(
    chromium, record
):
    # AC (#335): the explorer itself loads the script that fits a pair chart to its
    # plot, which the pages above load by hand. The suite's snapshot has no pair to
    # draw, so this is the real record's most played two, who share more events than
    # a phone has room for: the chart opens on the latest of them, each with its 10px.
    page = _loaded(chromium, record, "phone")
    try:
        shots.tab(page, "Archetypes")
        shots.pick(page, "Archetype", shots.ARCHETYPE)
        shots.pick(page, "Second archetype (optional)", shots.ARCHETYPE_B)
        page.locator(".page-descriptor").locator("visible=true").filter(
            has_text=f"compared with {shots.ARCHETYPE_B}").wait_for(timeout=30_000)
        _card_titled(page, "Finishes over time").wait_for(timeout=30_000)
        page.wait_for_timeout(2000)
        read = page.evaluate(
            "() => { const chart = [..." + _OPEN_TAB + """.querySelectorAll('.js-plotly-plot')]
                 .filter(chart => chart.getBoundingClientRect().height > 0).pop();
               const line = chart.data.find(trace => trace.name && trace.x.length > 1);
               return {range: chart._fullLayout.xaxis.range, events: line.x.length,
                       plot: chart._fullLayout._size.w,
                       sideways: document.documentElement.scrollWidth
                                 - document.documentElement.clientWidth}; }"""
        )
    finally:
        page.close()

    first, last = read["range"]
    on_show = last - first
    assert read["events"] > on_show
    assert last == pytest.approx(read["events"] - 0.25)
    assert read["plot"] / on_show >= 10 > read["plot"] / (on_show + 1)
    assert read["sideways"] == 0


def test_a_fitted_pair_follows_its_plots_width_until_a_reader_slices_it(chromium):
    # AC (#335): what the page opens a long pair on is the page's guess, made from the
    # plot's width, so it is made again when the width changes: a phone turned on its
    # side, or a chart first drawn at a width it does not keep. From the first range a
    # reader sets it is theirs, and no later change of width takes it from them.
    figure = _archetype_timeline_figure("Storm", "Lands", _timeline(40))
    shown = "() => document.querySelector('#timeline .js-plotly-plot')._fullLayout.xaxis.range"
    wide, narrow = ({"width": 1440, "height": 900}, {"width": 390, "height": 844})
    with _timeline_of(chromium, figure, "phone") as page:
        def at(viewport):
            page.set_viewport_size(viewport)
            page.wait_for_timeout(1500)
            return page.evaluate(shown)

        opened = page.evaluate(shown)
        widened = at(wide)
        narrowed = at(narrow)
        page.evaluate(
            "() => Plotly.relayout(document.querySelector('#timeline .js-plotly-plot'),"
            " {'xaxis.range': [5, 15]})")
        sliced = at(wide)

    assert opened[1] - opened[0] < 40
    assert widened == pytest.approx([-0.75, 39.75])
    assert narrowed == pytest.approx(opened)
    assert sliced == pytest.approx([5, 15])
