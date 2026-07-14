"""Gradio page: pick a pilot, see their neighbourhood as an interactive graph.

Thin glue over the tested query and render seams; the app itself is not unit
tested.
"""

import html
from pathlib import Path

import gradio as gr
import kuzu

from graph7ph.db import rows
from graph7ph.query import pilot_subgraph
from graph7ph.render import render_subgraph

_PLACEHOLDER = "<p style='padding:1rem'>Select a pilot to see their decks and cards.</p>"


def _embed(doc: str) -> str:
    """Wrap a standalone HTML document in an iframe so its scripts run.

    gr.HTML does not execute injected <script> tags, so the pyvis widget is
    isolated in an iframe via srcdoc (which the browser renders as its own
    document, scripts and all)."""
    srcdoc = html.escape(doc, quote=True)
    return f'<iframe srcdoc="{srcdoc}" style="width:100%;height:720px;border:none"></iframe>'


def _list_pilots(conn: kuzu.Connection) -> list[str]:
    res = conn.execute("MATCH (p:Pilot) RETURN p.pilot ORDER BY p.pilot")
    return [row[0] for row in rows(res)]


def build_app(db_path: Path) -> gr.Blocks:
    # The Database is shared, but a Kùzu Connection is not thread-safe, so each
    # request opens its own over Gradio's worker threads. Read-only lets several
    # readers (and a separate build process) share the file.
    db = kuzu.Database(str(db_path), read_only=True)
    pilots = _list_pilots(kuzu.Connection(db))

    def show(pilot: str | None) -> str:
        if not pilot:
            return _PLACEHOLDER
        subgraph = pilot_subgraph(kuzu.Connection(db), pilot)
        return _embed(render_subgraph(subgraph))

    with gr.Blocks(title="7 Point Highlander Graph") as demo:
        gr.Markdown("# 7 Point Highlander Graph\nA pilot's decks and the cards they run.")
        pilot = gr.Dropdown(choices=pilots, label="Pilot", value=None)
        graph = gr.HTML(_PLACEHOLDER)
        pilot.change(show, inputs=pilot, outputs=graph)

    return demo
