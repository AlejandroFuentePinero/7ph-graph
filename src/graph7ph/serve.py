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
"""

from gradio.brotli_middleware import BrotliMiddleware
from starlette.middleware import Middleware
from starlette.types import Scope

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


# Quality 4 is Gradio's own choice for the same middleware: most of the ratio for
# a fraction of the CPU, which is what a free CPU-only Space has to spend.
APP_KWARGS = {"middleware": [Middleware(_CompressPageAndConfig, quality=4)]}
"""Passed to ``Blocks.launch(app_kwargs=...)``, which forwards them to FastAPI."""
