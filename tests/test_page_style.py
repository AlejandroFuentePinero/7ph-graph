"""What the page's own stylesheet actually paints, measured on a real browser.

Issue #307: on the move to Gradio 6 the suite stayed green while the page lost its
design. Gradio's own component rules began to outrank the app's stylesheet, a group
began to carry its classes on two nested elements, and an HTML block began to pad
itself whatever it was told, so the title shrank, every card was drawn twice, and
each plot gained a frame. None of it is visible to a test that reads the stylesheet
as text, or to one that asks the server for the page: which rule wins is decided in
the browser. These tests ask the running page instead.

A stand-in page with the same parts (a title, a card holding HTML blocks and a
control) rather than the real explorer: what is under test is that the stylesheet
lands on Gradio's markup, not the data. It is launched with ``launch_kwargs``, the
settings every real launch passes, so the stylesheet and theme are the shipped ones.

Skipped rather than failed where playwright or its browser is missing, the same call
``tests/test_graph_desktop.py`` makes, and for the same reason (issue #197).
"""

import gradio as gr
import pytest

from graph7ph.app import launch_kwargs
from graph7ph.theme import TOKENS

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright


def _rgb(hex_: str) -> str:
    """A ``#rrggbb`` token as the browser reports a computed colour."""
    value = hex_.lstrip("#")
    return "rgb({}, {}, {})".format(*(int(value[i:i + 2], 16) for i in (0, 2, 4)))


@pytest.fixture(scope="module", params=[False, True], ids=["client-rendered", "server-rendered"])
def page(request):
    """The stand-in page, loaded in Chromium, or a skip naming how to install one.

    Both ways Gradio can render it, because they do not fail alike: the Space renders
    on the server, where the page's stylesheet meets Gradio's in a different order,
    and the title's size is lost only there. A local ``graph7ph app`` renders in the
    browser.
    """
    with gr.Blocks() as demo:
        gr.Markdown("# The page title")
        with gr.Group(elem_classes="insight-card"):
            gr.HTML("<div class='flush'>set flush</div>", padding=False)
            gr.HTML(
                "<div class='inset'>set in <a href='mailto:someone@example.com'>"
                "someone@example.com</a></div>",
                padding=True,
            )
            gr.Radio(["one", "two"], value="one", label="Pick", info="A help line.")
    _, url, _ = demo.launch(prevent_thread_lock=True, ssr_mode=request.param, **launch_kwargs())
    try:
        # Server rendering needs Node, and without it Gradio falls back to the browser
        # with a warning: skipped by name rather than passed as a second
        # client-rendered run.
        if request.param and not demo.ssr_mode:
            pytest.skip("no Node to render the page on the server, as the Space does")
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


def test_the_title_is_set_in_the_display_role(page):
    # The first thing the reader sees, and the first thing lost: without the
    # stylesheet's lift Gradio's own heading rule wins and the title falls to 26px.
    family, size = _style(page, "h1", "font-family", "font-size")

    assert size == "34px"
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
