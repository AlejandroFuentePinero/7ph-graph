"""What a visitor actually downloads to use the explorer.

Gradio ships a Brotli middleware but gates it on a file extension in the request
path (``BrotliMiddleware._is_compressible_file_type`` returns ``False`` when the
path has no dot), so it only ever compresses static assets. Gradio's own
endpoints carry no extension and are served uncompressed. The config is the
heavy one: it carries the whole widget tree, which holds three copies of the
~5000-card catalogue, so a visitor pays 1.99 MB of it before the first paint on
every load, and the deployed Space is fronted by a proxy that adds no
compression of its own (issue #93). Compressed it is 116 KB.

Brotli specifically, not gzip: the three catalogue copies sit far apart in the
payload, and only the larger window spans the distance to spot them as repeats
(gzip manages about 3x on this, Brotli 17x).

The page shell is in the rule too, but it earns its keep only off the Space. The
Space runs Gradio in SSR mode, which serves a ~23 KB root through a route that
never reaches this middleware; measure there and the entry looks dead. It is not:
with SSR off, as ``graph7ph app`` runs locally, the root embeds that same widget
tree and is 4.03 MB.

The event stream deliberately stays out. It is a chunked ``text/event-stream``,
and compressing it risks buffering the incremental updates it exists to deliver,
which is the very failure this was meant to make less likely.

The other heavy download is the vis.js library the graph widget draws with. It
rides that same uncompressible stream, so the only lever is to stop sending it:
it is served here as an ordinary asset instead, once, and cached (issue #97).
"""

import hashlib
from pathlib import Path

import pyvis
from gradio.brotli_middleware import BrotliMiddleware
from starlette.middleware import Middleware
from starlette.responses import FileResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# The config on any deployment, the page shell only where SSR is off. Every other
# endpoint is either small or already covered by Gradio's own middleware.
_COMPRESSIBLE_PATHS = frozenset({"/", "/config"})


class _CompressPageAndConfig(BrotliMiddleware):
    """Gradio's Brotli middleware, pointed at the endpoints it skips."""

    def _is_compressible_file_type(self, scope: Scope) -> bool:
        # A replacement for the base rule rather than an addition to it, so that
        # this middleware and Gradio's own never both claim a response: this one
        # takes the two endpoints, Gradio's keeps the static assets it handles.
        return scope.get("path", "") in _COMPRESSIBLE_PATHS


# pyvis ships the library it would otherwise inline, so hosting it ourselves adds
# no vendored copy and no third party to be reachable at runtime. The version is
# the one pyvis 0.3.2 inlines (pinned in requirements.txt); a bump that renamed
# this directory would fail loudly here at import rather than serve a 404.
_VIS = Path(pyvis.__file__).parent / "lib" / "vis-9.1.2"

# A visitor may hold a cached copy for a year, which is only safe because the URL
# carries a digest of the bytes: a different library is a different URL, so there
# is no stale copy to invalidate.
_CACHE_FOREVER = {"Cache-Control": "public, max-age=31536000, immutable"}

# Under ``/static`` because the Space runs Gradio in SSR mode, where a routing
# middleware sits outside this one and proxies every request to a Node server
# unless its path opens with one of Gradio's own internal routes (``static`` is
# one of them). A URL outside that set never reaches this middleware there: it
# would 404 from Node and the graph would draw with no library, while every local
# run stayed green. The path below it is ours alone, so nothing of Gradio's own
# ``/static`` tree is shadowed.
_ASSET_ROOT = "/static/graph7ph"

_ASSETS: dict[str, Path] = {}
"""The files we serve ourselves, by the URL each is served at."""


def _serve_asset(name: str) -> str:
    """Add one of pyvis's own library files to what we serve, answering with the
    URL it is served at: a digest of its bytes, so a different library cannot
    reuse a cached copy of this one."""
    path = _VIS / name
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    url = f"{_ASSET_ROOT}/{digest}/{name}"
    _ASSETS[url] = path
    return url


VIS_JS_URL = _serve_asset("vis-network.min.js")
VIS_CSS_URL = _serve_asset("vis-network.css")


class _ServeAssets:
    """Answer the asset URLs from disk, and pass everything else to Gradio."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = _ASSETS.get(scope["path"]) if scope["type"] == "http" else None
        if path is None:
            await self.app(scope, receive, send)
            return
        await FileResponse(path, headers=_CACHE_FOREVER)(scope, receive, send)


# Quality 4 is Gradio's own choice for the same middleware: most of the ratio for
# a fraction of the CPU, which is what a free CPU-only Space has to spend.
#
# The assets are not compressed here: Gradio installs its own Brotli middleware
# outside this one and its rule already covers a ``.js`` or ``.css`` path, so a
# first download arrives compressed without a second pass over the same bytes.
APP_KWARGS = {
    "middleware": [
        Middleware(_CompressPageAndConfig, quality=4),
        Middleware(_ServeAssets),
    ]
}
"""Passed to ``Blocks.launch(app_kwargs=...)``, which forwards them to FastAPI."""
