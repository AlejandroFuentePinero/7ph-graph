"""Compression of the explorer's own endpoints (issue #93).

Gradio compresses static assets but not its own extensionless endpoints, which
is where the widget tree (three copies of the card catalogue) is served from.
These build a stand-in app with the same shape, big dropdowns and all, rather
than the real graph: what is under test is the middleware wiring, not the data.
"""

import html
import re

import gradio as gr
import pytest
from starlette.testclient import TestClient

from graph7ph.query import Node, Subgraph
from graph7ph.render import render_subgraph
from graph7ph.serve import APP_KWARGS, VIS_CSS_URL, VIS_JS_URL, _CompressPageAndConfig

# Big enough that a failure to compress is unambiguous, and repetitive in the way
# a card catalogue is: the same list twice, far enough apart that only Brotli's
# window spans the gap.
_CHOICES = [f"Card Number {i} of the Ancient Guildpact" for i in range(4000)]


@pytest.fixture(scope="module")
def client():
    with gr.Blocks() as demo:
        gr.Dropdown(choices=_CHOICES, label="Card")
        gr.Dropdown(choices=_CHOICES, label="Second card")
    # Launched, as the app is, rather than assembled with `App.create_app`: since
    # Gradio 6 the theme is set at launch, and the page template reads it, so an app
    # that was never launched answers `/config` and fails on `/`. The server it
    # binds is not used; the requests below go to the same ASGI app in process.
    demo.launch(prevent_thread_lock=True, app_kwargs=APP_KWARGS)
    yield TestClient(demo.app)
    demo.close()


def _preview_tags(page: str) -> dict[str, list[str]]:
    """Every preview tag in a served page: what each says, by name, in page order.

    Read more loosely than the app writes them (either quote, either attribute, any
    order), so a tag of Gradio's that the app failed to remove is still found here.
    """
    found: dict[str, list[str]] = {}
    for tag in re.findall(r"<meta\b[^>]*>", page):
        name = re.search(r"""(?:property|name)\s*=\s*["']((?:og|twitter):[^"']+)""", tag)
        if name:
            content = re.search(r"""content\s*=\s*["']([^"']*)""", tag)
            found.setdefault(name.group(1), []).append(html.unescape(content.group(1)))
    return found


def test_the_page_as_served_carries_the_apps_own_preview_card_and_no_other(client):
    """A link pasted into a chat unfurls from tags in the page as the server sends it:
    a preview fetcher runs no script. Gradio's template writes two sets of its own
    there ("Gradio", "Click to try out the app!", its image, its address), and hands
    the app's ``head`` to the page's script, which a fetcher never runs. So the card
    #115 designed reached nobody, on gradio 5 as on 6 (issue #320).

    Read with a plain GET, which is all a fetcher does. Each tag once, so there is no
    second title for a platform to prefer, and that also fails the day Gradio's
    template writes a tag in a shape the app no longer removes.
    """
    page = client.get("/").text

    card = _preview_tags(page)
    description = card.pop("og:description")
    assert card.pop("twitter:description") == description
    assert len(description) == 1
    assert "knowledge graph of the Australian 7 Point Highlander metagame" in description[0]
    assert card == {
        "og:type": ["website"],
        "og:title": ["7 Point Highlander Graph"],
        "og:url": ["https://www.7phgraph.com"],
        "twitter:card": ["summary"],
        "twitter:title": ["7 Point Highlander Graph"],
    }
    # Near the top, ahead of the megabyte of config the head also carries: a fetcher
    # that reads only the start of a page still finds the card.
    assert page.index("og:title") < page.index("window.gradio_config")


def test_the_page_is_otherwise_what_gradio_wrote(client):
    """The card is swapped in the page as it goes out, so the swap must cost the page
    nothing else: the config the browser boots from arrives whole."""
    page = client.get("/").text

    assert page.rstrip().endswith("</html>")
    config = client.get("/config").json()
    assert f'"app_id":{config["app_id"]}' in page.replace(" ", "")


@pytest.mark.parametrize("path", ["/", "/config"])
def test_the_page_and_its_config_are_compressed(client, path):
    response = client.get(path, headers={"Accept-Encoding": "br"})

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "br"


@pytest.mark.parametrize("path", ["/", "/config"])
def test_compression_is_what_makes_the_payload_small(client, path):
    """The header alone would pass if the body were compressed to no effect."""
    response = client.get(path, headers={"Accept-Encoding": "br"})

    # httpx decodes the body, so the decoded length is what a visitor would have
    # paid uncompressed and content-length is what they actually pay.
    on_the_wire = int(response.headers["content-length"])
    assert on_the_wire < len(response.content) / 10


def test_a_client_that_cannot_take_brotli_still_gets_the_page(client):
    response = client.get("/config", headers={"Accept-Encoding": "identity"})

    assert response.status_code == 200
    assert "content-encoding" not in response.headers


@pytest.mark.parametrize(
    ("url", "media_type", "marker"),
    [(VIS_JS_URL, "javascript", b"vis-network"), (VIS_CSS_URL, "css", b".vis-network")],
)
def test_the_graph_library_is_served_by_the_app(client, url, media_type, marker):
    """The widget's library comes from us, so nothing has to be inlined into a
    result and no third party has to be reachable for the graph to draw."""
    response = client.get(url)

    assert response.status_code == 200
    assert media_type in response.headers["content-type"]
    assert marker in response.content


@pytest.mark.parametrize("url", [VIS_JS_URL, VIS_CSS_URL])
def test_the_library_is_cached_instead_of_refetched_on_every_explore(client, url):
    """The whole point of serving it separately: the second Explore click reuses
    the browser's copy. Safe to promise for a year because the URL carries a
    digest of the bytes, so a different library is a different URL."""
    cache = client.get(url).headers["cache-control"]

    assert "immutable" in cache
    assert "max-age=31536000" in cache


def test_the_library_a_rendered_graph_asks_for_is_one_the_app_serves(client):
    """The two halves are wired through the same constants, so this reads the URLs
    back out of a real widget: a tag rewritten into a path the app does not answer
    would leave the graph blank, which no test on either half alone would catch."""
    doc = render_subgraph(Subgraph(nodes=[Node("deck:d1", "Grixis", "Deck")], edges=[]))
    # Every URL the widget asks this app for; pyvis's own leftovers are either
    # document-relative or absolute, so these are the two library tags.
    asked_for = re.findall(r'(?:src|href)="(/[^"]+)"', doc)

    assert len(asked_for) == 2  # the library and its stylesheet
    for url in asked_for:
        assert client.get(url).status_code == 200


@pytest.mark.parametrize("url", [VIS_JS_URL, VIS_CSS_URL])
def test_the_first_download_of_the_library_is_compressed(client, url):
    """The one download a visitor does pay for is compressed, by Gradio's own
    middleware outside this one: its rule already covers a ``.js`` or ``.css``
    path, so these are never compressed twice."""
    response = client.get(url, headers={"Accept-Encoding": "br"})

    assert response.headers["content-encoding"] == "br"


def test_the_event_stream_is_never_compressed():
    """Compressing a chunked ``text/event-stream`` buffers the updates it exists
    to deliver incrementally, which is the failure this was meant to reduce, so
    the stream has to stay outside the rule however the rule grows."""
    middleware = _CompressPageAndConfig(app=None)

    assert not middleware._is_compressible_file_type(
        {"type": "http", "path": "/gradio_api/queue/data"}
    )
