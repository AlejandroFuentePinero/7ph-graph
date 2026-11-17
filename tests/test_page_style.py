"""What the page's own stylesheet actually paints, measured on a real browser.

Issue #307: on the move to Gradio 6 the suite stayed green while the page lost its
design. Gradio's own component rules began to outrank the app's stylesheet, a group
began to carry its classes on two nested elements, and an HTML block began to pad
itself whatever it was told, so the title shrank, every card was drawn twice, and
each plot gained a frame. None of it is visible to a test that reads the stylesheet
as text, or to one that asks the server for the page: which rule wins is decided in
the browser. These tests ask the running page instead.

A stand-in page with the same parts (the app's own header, the seven tabs, a card holding HTML
blocks and a control, the app's table in a card) rather than the real explorer: what is under test is that the stylesheet
lands on Gradio's markup, not the data. It is launched with ``launch_kwargs``, the
settings every real launch passes, so the stylesheet and theme are the shipped ones.

Skipped rather than failed where playwright or its browser is missing, the same call
``tests/test_graph_desktop.py`` makes, and for the same reason (issue #197).
"""

import gradio as gr
import pytest

from graph7ph.app import Pages, _masthead, _share_list, launch_kwargs
from graph7ph.parts import Column, Group, table
from graph7ph.query import Coverage
from graph7ph.theme import TOKENS
from graph7ph.trends import Series, SeriesCell

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

# The explorer's own tab names: what is under test at phone width is that these seven,
# at the width they are actually set in, stay on the strip.
TABS = ["Metagame", "Archetypes", "Cards", "Hidden gems", "Pilots", "Player leaderboard", "FAQ"]
PHONE = {"width": 390, "height": 844}

# The app's one table, as `parts.table` builds it: a header row, a line heading a
# group, and two rows under it. The suite's small snapshot draws neither the standings
# nor the gems, so the table is measured here.
TABLE = table(
    [Column("Pilot"), Column("Score", numeric=True)],
    [Group("Majors"), ["one", "0.512"], ["two", "0.498"]],
)


# Metagame's list of the most played (#334), over a record worked up to be as wide as
# a row gets: two years, so the change column is drawn, names long enough to wrap,
# every name a link, and shares and changes at their full five and six characters.
# The suite's small snapshot lists two short names over one year.
_SHARES = {
    "Gluten Free Storm": ((2025, 238), (2026, 351)),
    "Selesnya Lands": ((2025, 16), (2026, 372)),
    "Tabernacle Stax Prison": ((2025, 262), (2026, 61)),
    "Esper Midrange": ((2025, 0), (2026, 40)),
}
_DECKS = {2025: 2096, 2026: 3368}
SHARE_LIST = _share_list(
    Series(cells=[
        SeriesCell(tag=name, archetype=name, year=year, n=n, share=n / _DECKS[year],
                   year_total=_DECKS[year])
        for name, years in _SHARES.items() for year, n in years
    ]),
    Pages(archetypes={name: name for name in _SHARES}),
)


def _rgb(hex_: str) -> str:
    """A ``#rrggbb`` token as the browser reports a computed colour."""
    value = hex_.lstrip("#")
    return "rgb({}, {}, {})".format(*(int(value[i:i + 2], 16) for i in (0, 2, 4)))


@pytest.fixture(scope="module")
def page():
    """The stand-in page, loaded in Chromium, or a skip naming how to install one.

    Rendered in the browser, as ``launch_kwargs`` has every launch render it (ADR 0029).
    """
    with gr.Blocks() as demo:
        _masthead(
            Coverage(events=162, pilots=1083, decks=4591, cards=4995,
                     first_year=2023, last_year=2026),
            "2026-11-06T23:05:46+00:00",
        )
        with gr.Tab(TABS[0]):
            with gr.Group(elem_classes="insight-card"):
                gr.HTML("<div class='flush'>set flush</div>", padding=False)
                gr.HTML(
                    "<div class='inset'>set in <a href='mailto:someone@example.com'>"
                    "someone@example.com</a></div>",
                    padding=True,
                )
                gr.Radio(["one", "two"], value="one", label="Pick", info="A help line.")
            # Set as the standings are (flush under a heading) and as the gems are
            # (inset, the table being the whole of the result).
            with gr.Group(elem_classes="insight-card"):
                gr.HTML(TABLE, padding=False)
            with gr.Group(elem_classes="insight-card"):
                gr.HTML(TABLE, padding=True)
            with gr.Group(elem_classes="insight-card"):
                gr.HTML(SHARE_LIST, padding=False)
        for name in TABS[1:]:
            with gr.Tab(name):
                gr.Markdown(name)
    _, url, _ = demo.launch(prevent_thread_lock=True, **launch_kwargs())
    try:
        with sync_playwright() as play:
            try:
                engine = play.chromium.launch()
            except Exception as missing:  # noqa: BLE001 - any launch failure is the same skip
                pytest.skip(f"no Chromium to measure the page on: {missing}")
            tab = engine.new_page(viewport={"width": 1440, "height": 900})
            tab.goto(url, wait_until="networkidle")
            tab.wait_for_selector(".insight-card .flush")
            yield tab
            engine.close()
    finally:
        demo.close()


def _style(page, selector: str, *properties: str) -> list[str]:
    """Computed style of the first element matching ``selector``."""
    return page.evaluate(
        "([selector, properties]) => { const s = getComputedStyle(document.querySelector(selector));"
        " return properties.map(p => s.getPropertyValue(p)); }",
        [selector, list(properties)],
    )


def test_the_header_name_is_set_in_the_display_face_at_the_rows_scale(page):
    # The first thing the reader sees, and the first thing lost: it is the page's
    # top-level heading, so without the stylesheet's lift Gradio's own heading rule
    # wins and it is drawn at 26px in the body face, margins and all.
    family, size, margin = _style(page, ".brand-name", "font-family", "font-size", "margin")

    assert (size, margin) == ("20px", "0px")
    assert family.startswith("Fraunces")


def test_a_card_is_drawn_once_as_the_surface_the_design_names(page):
    # Gradio 6 puts a group's classes on two nested elements. The outer one is the
    # card; the inner one must add no surface, edge or inset of its own.
    radius, background, edge = _style(
        page, ".insight-card", "border-radius", "background-color", "border-top-width"
    )
    assert (radius, background, edge) == ("12px", _rgb(TOKENS["surface"]), "1px")

    inner = page.evaluate(
        "() => [...document.querySelectorAll('.insight-card .insight-card')].map(el => {"
        " const s = getComputedStyle(el);"
        " return [s.padding, s.borderTopWidth, s.backgroundColor, s.marginBottom]; })"
    )
    assert all(found == ["0px", "0px", "rgba(0, 0, 0, 0)", "0px"] for found in inner)


def test_nothing_inside_a_card_draws_a_frame_of_its_own(page):
    # Gradio 6 writes `border-style: solid` on a block's own style attribute, so a
    # rule that only removes the style leaves a 3px frame in the text colour round
    # every plot and control in a card. The width is what has to be zero.
    widths = page.evaluate(
        "() => [...document.querySelectorAll('.insight-card .block')]"
        ".map(el => getComputedStyle(el).borderTopWidth)"
    )

    assert widths and set(widths) == {"0px"}


def test_an_html_block_is_inset_only_when_it_asks_to_be(page):
    # Every heading the app sets flush is a `gr.HTML(padding=False)`. Gradio 6 pads the
    # block's container whatever that argument says.
    flush = page.evaluate(
        "() => getComputedStyle(document.querySelector('.flush').closest('.html-container')).padding"
    )
    inset = page.evaluate(
        "() => getComputedStyle(document.querySelector('.inset').closest('.html-container')).padding"
    )

    assert flush == "0px"
    assert inset != "0px"


def test_a_table_draws_only_the_hairline_between_its_rows(page):
    # Gradio 6 frames every table, row and cell in an HTML block with a 1px line in
    # the text colour. The table sits in a card and is ruled by the card's own
    # hairline between rows, so that frame showed as a light rule either side of
    # every cell and a box round the whole table (issue #324).
    tables = page.evaluate(
        """() => [...document.querySelectorAll('table.data-table')].map(table => {
             const edge = (el, side) => { const s = getComputedStyle(el);
               return s['border' + side + 'Width'] + ' ' + s['border' + side + 'Color']; };
             const widths = (els, sides) => [...new Set(els.flatMap(el => sides.map(
               side => getComputedStyle(el)['border' + side + 'Width'])))];
             const cells = [...table.querySelectorAll('th, td')];
             const rows = [...table.querySelectorAll('tr')];
             return {
               frame: widths([table, ...rows], ['Top', 'Right', 'Bottom', 'Left']),
               sides: widths(cells, ['Top', 'Right', 'Left']),
               under: [edge(table.querySelector('th'), 'Bottom'),
                       edge(table.querySelector('tbody td'), 'Bottom'),
                       getComputedStyle(table.querySelector('tbody tr:last-child td'))
                         .borderBottomWidth],
             }; })"""
    )

    hairline = f"1px {_rgb(TOKENS['border'])}"
    as_designed = {
        "frame": ["0px"],
        "sides": ["0px"],
        # Under the header and between rows, and not under the last row.
        "under": [hairline, hairline, "0px"],
    }
    # The two stand-in tables, and the list of the most played, which is one too.
    assert tables == [as_designed] * 3


def test_a_link_in_an_html_block_is_text_in_its_line(page):
    # Gradio 6 pads a link in HTML as if it were a button, which opened a gap either
    # side of the contact address in the footer.
    assert _style(page, ".inset a", "padding") == ["0px"]


def test_a_controls_help_line_is_set_as_body_text(page):
    # Drawn as Markdown by Gradio 5, where it took the body role, and as a plain
    # element of its own by Gradio 6, where it would fall to a small muted caption.
    size, colour = _style(page, ".info-text", "font-size", "color")

    assert (size, colour) == ("15px", _rgb(TOKENS["text"]))


def test_an_unselected_mark_has_no_outline(page):
    # The theme gives the radio and checkbox marks the input's border width, which is
    # none. Gradio 6 resets both to 1px in a rule that outranks the component's own.
    assert _style(page, "input[type=radio]:not(:checked)", "border-top-width") == ["0px"]


def test_every_tab_stays_on_the_strip_at_phone_width(page):
    # AC (#172): at 390px the seven tabs need about 607px against 326px of container,
    # and Gradio's answer is to slice the ones that do not fit into a "..." menu. The
    # design keeps all seven on one strip that scrolls sideways instead. Gradio decides
    # the slice by measuring a hidden copy of the strip, and how it measures changed
    # from 5 to 6 (where a button's right edge fell, then the sum of the buttons'
    # widths), so a rule written against the first passed every stylesheet check while
    # five tabs went behind the menu. Only the running page shows which tabs are drawn.
    phone = page.context.browser.new_page(viewport=PHONE)
    try:
        phone.goto(page.url, wait_until="networkidle")
        phone.wait_for_selector("[role=tablist] [role=tab]")
        # Gradio's own pass runs a frame after the strip is laid out.
        phone.wait_for_timeout(500)
        strip = phone.evaluate(
            "() => { const strip = document.querySelector('[role=tablist]');"
            " const tabs = [...strip.querySelectorAll('[role=tab]')];"
            " return {names: tabs.map(tab => tab.textContent.trim()),"
            " rows: new Set(tabs.map(tab => Math.round(tab.getBoundingClientRect().top))).size,"
            " scrolls: strip.scrollWidth > strip.clientWidth,"
            " menu: getComputedStyle(document.querySelector('.overflow-menu')).display}; }"
        )
        assert strip == {"names": TABS, "rows": 1, "scrolls": True, "menu": "none"}

        # The last tab is off the right edge, and still one tap away.
        last = phone.get_by_role("tab", name=TABS[-1], exact=True)
        last.click()
        assert last.get_attribute("aria-selected") == "true"
    finally:
        phone.close()


def test_the_list_of_the_most_played_fits_a_phone(page):
    # Issue #334: at 390px nothing in the list is cut off. The chart it replaced ran
    # its legend out of its figure there (#314). A row at its widest: a name that
    # wraps, a share and a change at full length, the share the change started from
    # under it, and the two years the header names, which ran into each other at
    # four digits and so are written without their century on a narrow screen.
    phone = page.context.browser.new_page(viewport=PHONE)
    try:
        phone.goto(page.url, wait_until="networkidle")
        phone.wait_for_selector(".share-list tbody tr")
        read = phone.evaluate(
            """() => { const list = document.querySelector('.share-list');
                 const card = list.closest('.insight-card');
                 const style = getComputedStyle(card), frame = card.getBoundingClientRect();
                 const inside = {left: frame.left + parseFloat(style.paddingLeft),
                                 right: frame.right - parseFloat(style.paddingRight)};
                 const box = el => el.getBoundingClientRect();
                 // What is drawn: every element of the list that takes room, and the
                 // text of every cell, which can run past the cell that holds it.
                 const boxes = [...list.querySelectorAll('*')].map(box);
                 for (const cell of list.querySelectorAll('th, td')) {
                   const text = document.createRange(); text.selectNodeContents(cell);
                   boxes.push(text.getBoundingClientRect());
                 }
                 // A column left out on a narrow screen takes no room and has no place.
                 const drawn = boxes.filter(b => b.width > 0 && b.height > 0);
                 const years = [...list.querySelector('thead .share-years').children];
                 return {
                   past: Math.max(...drawn.map(b => Math.max(b.right - inside.right,
                                                             inside.left - b.left))),
                   sideways: document.documentElement.scrollWidth
                     - document.documentElement.clientWidth,
                   headers: [...list.querySelectorAll('thead th')].map(th => th.innerText),
                   years: years.map(year => year.innerText),
                   apart: box(years[1]).left - box(years[0]).right,
                   from: [...list.querySelectorAll('.share-from')].map(el => el.innerText),
                   name_lines: [...list.querySelectorAll('tbody td:nth-child(2) a')].map(
                     name => name.getClientRects().length),
                 }; }"""
        )
    finally:
        phone.close()

    assert read["past"] <= 0.5 and read["sideways"] <= 0
    assert "CHANGE FROM 2025" in [header.upper() for header in read["headers"]]
    assert read["years"] == ["25", "26"] and read["apart"] > 0
    # Most played in 2026 first (372, 351, 61 and 40 decks), each beside its share of
    # 2025's 2,096: 16, 238, 262 and none.
    assert read["from"] == ["from 0.76%", "from 11.35%", "from 12.5%", "from 0%"]
    # The fixture does what it is for: at least one name is on two lines.
    assert max(read["name_lines"]) >= 2
