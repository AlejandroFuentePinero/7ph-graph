"""Compression of the explorer's own endpoints (issue #93).

Gradio compresses static assets but not its own extensionless endpoints, which
is where the widget tree (three copies of the card catalogue) is served from.
These build a stand-in app with the same shape, big dropdowns and all, rather
than the real graph: what is under test is the middleware wiring, not the data.
"""

import re

import gradio as gr
import pytest
from gradio.routes import INTERNAL_ROUTES, App
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
    return TestClient(App.create_app(demo, app_kwargs=APP_KWARGS))


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
def test_the_library_is_reachable_where_the_space_serves_it_from(url):
    """The Space runs Gradio in SSR mode, and its routing middleware sits outside
    this one: it proxies anything whose path does not open with one of Gradio's
    internal routes to a Node server that knows nothing of these files. An asset
    hosted outside that set 404s on the deployed Space and draws a graph with no
    library, while every other test here, SSR off, stays green (issue #97)."""
    assert any(url.startswith(f"/{route}") for route in INTERNAL_ROUTES)


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
