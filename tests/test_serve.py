"""Compression of the explorer's own endpoints (issue #93).

Gradio compresses static assets but not its own extensionless endpoints, which
is where the widget tree (three copies of the card catalogue) is served from.
These build a stand-in app with the same shape, big dropdowns and all, rather
than the real graph: what is under test is the middleware wiring, not the data.
"""

import gradio as gr
import pytest
from gradio.routes import App
from starlette.testclient import TestClient

from graph7ph.serve import APP_KWARGS, _CompressPageAndConfig

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


def test_the_event_stream_is_never_compressed():
    """Compressing a chunked ``text/event-stream`` buffers the updates it exists
    to deliver incrementally, which is the failure this was meant to reduce, so
    the stream has to stay outside the rule however the rule grows."""
    middleware = _CompressPageAndConfig(app=None)

    assert not middleware._is_compressible_file_type(
        {"type": "http", "path": "/gradio_api/queue/data"}
    )
