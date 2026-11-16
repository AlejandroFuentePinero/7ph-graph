"""What a visitor actually downloads to use the explorer.

Gradio ships a Brotli middleware but gates it on a file extension in the request
path (``BrotliMiddleware._is_compressible_file_type`` returns ``False`` when the
path has no dot), so it only ever compresses static assets. Gradio's own
endpoints carry no extension and are served uncompressed. The config is the
heavy one: it carries the whole widget tree, including the ~5000-card catalogue,
so a visitor pays 912 KB of it before the first paint on every load, and the
deployed Space is fronted by a proxy that adds no compression of its own
(issue #93). Compressed it is 198 KB.

Brotli specifically, not gzip: gzip manages 3.2x on this payload and Brotli 4.6x,
which is 90 KB a visit. The gap used to be far wider. The catalogue once shipped
three times over, the copies sat far apart, and only Brotli's larger window
spanned the distance to spot them as repeats (3x against 17x). Shipping it once
(#220) took that redundancy away and most of the ratio with it, but Brotli still
wins by enough to keep.

The page shell is in the rule too. Every launch renders in the browser
(``app.launch_kwargs``, ADR 0029), so the root embeds that same widget tree and is
1,072 KB, or 223 KB compressed.

The page shell is also where a shared link's preview card is read from, by a
fetcher that runs no script. Gradio's template writes its own card there and
delivers the app's ``head`` through the script, so the card is swapped in the
page as it goes out (``_WriteOwnShell``, issue #320). The page's title is written
in the same pass, because the template has none (issue #341).

The event stream deliberately stays out. It is a chunked ``text/event-stream``,
and compressing it risks buffering the incremental updates it exists to deliver,
which is the very failure this was meant to make less likely.

The other heavy download is the vis.js library the graph widget draws with. It
rides that same uncompressible stream, so the only lever is to stop sending it:
it is served here as an ordinary asset instead, once, and cached (issue #97).
"""

import hashlib
import re
from pathlib import Path

import pyvis
from gradio.brotli_middleware import BrotliMiddleware
from starlette.datastructures import MutableHeaders
from starlette.middleware import Middleware
from starlette.responses import FileResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from graph7ph.theme import FONT_DIR, FONTS, ground_style, preview_tags, title_tag

# The page shell and the config. Every other endpoint is either small or already
# covered by Gradio's own middleware.
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
# the one pyvis 0.3.2 inlines (pinned in uv.lock); a bump that renamed
# this directory would fail loudly here at import rather than serve a 404.
_VIS = Path(pyvis.__file__).parent / "lib" / "vis-9.1.2"

# A visitor may hold a cached copy for a year, which is only safe because the URL
# carries a digest of the bytes: a different library is a different URL, so there
# is no stale copy to invalidate.
_CACHE_FOREVER = {"Cache-Control": "public, max-age=31536000, immutable"}

# Under ``/static`` since #97, when the Space rendered on the server and the routing
# layer in front of the app passed on only the paths opening with one of Gradio's
# own routes. Nothing renders on the server now (ADR 0029), so the prefix is no
# longer needed. It stays because moving it would buy nothing: the path below it is
# ours alone, so nothing of Gradio's own ``/static`` tree is shadowed.
_ASSET_ROOT = "/static/graph7ph"

_ASSETS: dict[str, Path] = {}
"""The files we serve ourselves, by the URL each is served at."""


def _serve_asset(path: Path) -> str:
    """Add a file to what we serve, answering with the URL it is served at: a
    digest of its bytes, so a different file cannot reuse a cached copy of this
    one."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    url = f"{_ASSET_ROOT}/{digest}/{path.name}"
    _ASSETS[url] = path
    return url


VIS_JS_URL = _serve_asset(_VIS / "vis-network.min.js")
VIS_CSS_URL = _serve_asset(_VIS / "vis-network.css")

# The face a graph's labels are drawn in (issue #330). The graph is a document of
# its own, which the face embedded in the page's stylesheet does not reach, and
# embedding it again in every graph drawn would put 47 KB on the stream that cannot
# be compressed or cached. So the graph asks for it here, once.
LABEL_FACE_URL = _serve_asset(FONT_DIR / FONTS["body"]["file"])


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


# A preview tag as Gradio's page template writes them: a ``<meta>`` whose property
# or name opens with ``og:`` or ``twitter:``. Nothing else in the page can match,
# because the one part of it the app's data reaches is the config, and Gradio writes
# that with every ``<`` escaped.
_GRADIO_PREVIEW_TAG = re.compile(
    rb"""<meta\b[^>]*\b(?:property|name)\s*=\s*["'](?:og|twitter):[^>]*>\s*"""
)


def _with_own_head(page: bytes) -> bytes:
    """The page with Gradio's preview tags gone, and the app's title, ground and
    card where the first one stood.

    There rather than at the end of the head, because the head also carries the
    whole config: a megabyte a fetcher would have to read through to reach them.
    """
    first = _GRADIO_PREVIEW_TAG.search(page)
    if first is None:
        # A template with no preview tags at all is one this was not written for:
        # the page goes out as Gradio made it, and the suite says so (test_serve).
        return page
    kept, rest = page[: first.start()], page[first.start() :]
    own = f"{title_tag()}\n{ground_style()}\n{preview_tags()}\n".encode()
    return kept + own + _GRADIO_PREVIEW_TAG.sub(b"", rest)


# Gradio's page element, which takes the theme to draw in as an attribute. The
# first one in the page is the element itself: the config ahead of it is written
# with every ``<`` escaped, and the template's own script names the tag bare.
_PAGE_ELEMENT = b"<gradio-app"


def _asking_for_dark(page: bytes) -> bytes:
    """The page with its element told to draw dark, whatever the browser prefers.

    Gradio settles its theme from this attribute first and from a ``__theme`` marker
    in the address second. The marker was how the app used to ask: the page arrived
    without one, sent itself to its own address with one, and was downloaded a second
    time, with a blank flash between (issue #327). The attribute is read on the first
    download, and the address stays the one the visitor came in on.
    """
    return page.replace(_PAGE_ELEMENT, _PAGE_ELEMENT + b' theme_mode="dark"', 1)


class _WriteOwnShell:
    """Write the app's title, preview card and theme into the page shell, and pass
    everything else through.

    A link pasted into a chat unfurls from the page as served, by a fetcher that
    runs no script. Gradio's template writes two sets of preview tags of its own
    there (title "Gradio", "Click to try out the app!", its image, its address) and
    has no setting for them, while the ``head`` an app passes reaches the page only
    through its script. So the page is held back, corrected and sent on (issue #320).
    It is one response per visit, already built whole in memory by the template.

    The template has no ``<title>`` either: the tab is named by the script. The
    title goes in with the card, so the page has its name as served (issue #341).

    And the page is told to draw dark here, so that it need not reload itself to
    say so (``_asking_for_dark``), and is given its ground, so that it is dark
    before its script has run (``theme.ground_style``, both issue #327).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] != "/":
            await self.app(scope, receive, send)
            return
        start: Message = {}
        held: list[bytes] = []

        async def hold(message: Message) -> None:
            nonlocal start
            if message["type"] == "http.response.start":
                start = message
            elif message["type"] == "http.response.body":
                held.append(message.get("body", b""))
                if message.get("more_body"):
                    return
                page = b"".join(held)
                headers = MutableHeaders(raw=start["headers"])
                if headers.get("content-type", "").startswith("text/html"):
                    page = _asking_for_dark(_with_own_head(page))
                    headers["content-length"] = str(len(page))
                await send(start)
                await send({"type": "http.response.body", "body": page})
            else:
                await send(message)

        await self.app(scope, receive, hold)


# Quality 4 is Gradio's own choice for the same middleware: most of the ratio for
# a fraction of the CPU, which is what a free CPU-only Space has to spend.
#
# The assets are not compressed here: Gradio installs its own Brotli middleware
# outside this one and its rule already covers a ``.js`` or ``.css`` path, so a
# first download arrives compressed without a second pass over the same bytes.
#
# The title and preview card are written inside the compression, so what is
# compressed is the page as corrected.
APP_KWARGS = {
    "middleware": [
        Middleware(_CompressPageAndConfig, quality=4),
        Middleware(_ServeAssets),
        Middleware(_WriteOwnShell),
    ]
}
"""Passed to ``Blocks.launch(app_kwargs=...)``, which forwards them to FastAPI."""
