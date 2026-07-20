"""Gradio explorer: controls emit a query spec, the spine returns a subgraph, and
the result is either drawn or refined.

Thin glue over the tested query, decision, and render seams. The controls pick an
entity and filters and build a ``QuerySpec`` (:mod:`graph7ph.query`); the spec
drives the shared spine via ``run_query``; the returned subgraph passes through
``assess`` (:mod:`graph7ph.explore`), which either clears it to render or, when it
would flood the view, refines instead of truncating. The app itself is not unit
tested (Gradio wiring and pyvis HTML are verified by running it).
"""

import html
from collections import Counter
from pathlib import Path

import gradio as gr
import ladybug

from graph7ph.db import open_database
from graph7ph.explore import RenderPlan, assess
from graph7ph.query import (
    CardCooccurrence,
    CardUsage,
    HiddenGems,
    PilotAffinity,
    PilotNeighbourhood,
    QuerySpec,
    SliceTooSmall,
    card_catalogue,
    gem_archetypes,
    pilot_catalogue,
    run_query,
)
from graph7ph.render import render_subgraph

_PROMPT = "<p style='padding:1rem'>Pick an entity and filters, then Explore.</p>"

# Each view names the query it drives and the input widgets it needs. The widget
# keys index into the fixed widget set below; a view shows exactly its keys.
_VIEWS: dict[str, list[str]] = {
    "Pilot head-to-head": ["pilot", "pilot2"],
    "Pilot archetype affinity": ["pilot"],
    "Card usage": ["card", "card_board"],
    "Card co-occurrence": ["card", "cooccur_card2", "cooccur_top_n", "cooccur_drop_lands"],
    "Hidden gems": ["gem_archetype"],
}


def _embed(doc: str) -> str:
    """Wrap a standalone pyvis document in an iframe so its scripts run.

    gr.HTML does not execute injected <script> tags, so the widget is isolated in
    an iframe via srcdoc (which the browser renders as its own document)."""
    srcdoc = html.escape(doc, quote=True)
    return f'<iframe srcdoc="{srcdoc}" style="width:100%;height:760px;border:none"></iframe>'


def _refine_alert(plan: RenderPlan) -> str:
    """The alert-and-refine message for a result too big to draw."""
    breakdown = ", ".join(f"{n} {kind}" for kind, n in sorted(plan.by_kind.items()))
    tips = "".join(f"<li>{html.escape(s)}</li>" for s in plan.suggestions)
    return (
        "<div style='padding:1rem;font-family:sans-serif'>"
        f"<p><strong>{plan.node_count} nodes</strong> is more than the "
        f"{plan.threshold}-node limit, so nothing is drawn (no result is dropped, "
        "and none is silently truncated).</p>"
        f"<p>The result breaks down as: {breakdown}.</p>"
        f"<p>Narrow it and try again:</p><ul>{tips}</ul></div>"
    )


def _note(message: str) -> str:
    """A plain message where a graph would go, for results with nothing to draw.

    An empty subgraph is under the render threshold, so it would otherwise draw
    as a blank canvas that reads as a broken app rather than as an answer.
    """
    return (
        "<div style='padding:1rem;font-family:sans-serif'>"
        f"<p>{html.escape(message)}</p></div>"
    )


def _num(value: object, default: float) -> float:
    """A cleared ``gr.Number`` arrives as ``None``; fall back to its default."""
    return default if value is None else value  # type: ignore[return-value]


def _spec(view: str, values: dict) -> QuerySpec | None:
    """Build the query spec a view's control values describe, or ``None`` when a
    required entity has not been chosen yet."""
    match view:
        case "Pilot head-to-head":
            if not values["pilot"]:
                return None
            return PilotNeighbourhood(values["pilot"], values["pilot2"] or None)
        case "Pilot archetype affinity":
            return PilotAffinity(values["pilot"]) if values["pilot"] else None
        case "Card usage":
            if not values["card"]:
                return None
            return CardUsage(values["card"], values["card_board"] or None)
        case "Card co-occurrence":
            if not values["card"]:
                return None
            return CardCooccurrence(
                values["card"],
                values["cooccur_card2"] or None,
                int(_num(values["cooccur_top_n"], 15)),
                bool(values["cooccur_drop_lands"]),
            )
        case "Hidden gems":
            return HiddenGems(values["gem_archetype"] or None)
    return None


def _distinguish(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Make (label, value) pairs safe for a dropdown.

    A label shared by more than one value is suffixed with its value so the
    duplicates stay distinguishable: two pilots the data could not tell apart
    (an under-merge, ADR 0004) would otherwise show as identical rows.
    """
    seen = Counter(label for label, _ in pairs)
    return [
        (f"{label} ({value})" if seen[label] > 1 else label, value)
        for label, value in pairs
    ]


def build_app(artifact: Path) -> gr.Blocks:
    # The Database is shared, but a Ladybug Connection is not thread-safe, so each
    # request opens its own over Gradio's worker threads. Read-only lets several
    # readers (and a separate build process) share the file. The artifact is the
    # bundle directory; the database sits inside it (issue #47).
    db = open_database(artifact, read_only=True)
    catalogue = ladybug.Connection(db)
    pilots = _distinguish(pilot_catalogue(catalogue))
    cards = _distinguish(card_catalogue(catalogue))
    # Only the archetypes whose slice can actually answer the gem question; the
    # rest would be an invitation to a result we cannot stand behind (ADR 0012).
    archetypes = _distinguish(gem_archetypes(catalogue))

    def explore(view: str, *values: object) -> str:
        # Gradio passes the widget values positionally in `keys` order.
        spec = _spec(view, dict(zip(keys, values)))
        if spec is None:
            return _PROMPT
        try:
            subgraph = run_query(ladybug.Connection(db), spec)
        except SliceTooSmall as e:
            return _note(f"{e}, so no gem claim is made here.")
        if not subgraph.nodes:
            return _note("Nothing matched. The query ran and came back empty.")
        plan = assess(subgraph)
        if not plan.render:
            return _refine_alert(plan)
        return _embed(render_subgraph(subgraph))

    with gr.Blocks(title="7 Point Highlander Graph") as demo:
        gr.Markdown(
            "# 7 Point Highlander Graph\n"
            "Pick what to explore, set filters, and see a filtered subgraph of the "
            "result. Click a node for its details; a deck links out to Moxfield."
        )
        view = gr.Dropdown(
            choices=list(_VIEWS), label="Explore", value="Pilot head-to-head"
        )
        # The fixed widget set; each view shows only the widgets it names.
        w = {
            "pilot": gr.Dropdown(choices=pilots, label="Pilot", value=None),
            "pilot2": gr.Dropdown(
                choices=pilots, label="Second pilot (optional, for head-to-head)",
                value=None,
            ),
            "card": gr.Dropdown(choices=cards, label="Card", value=None, visible=False),
            "card_board": gr.Dropdown(
                choices=[("Main or side", ""), ("Main", "Main"), ("Side", "Side")],
                label="Board", value="", visible=False,
            ),
            "cooccur_card2": gr.Dropdown(
                choices=cards, label="Second card (optional, for shared packages)",
                value=None, visible=False,
            ),
            "cooccur_top_n": gr.Dropdown(
                choices=[5, 15, 25], value=15,
                label="Top cards by co-occurrence rate", visible=False,
            ),
            "cooccur_drop_lands": gr.Checkbox(
                value=False, label="Filter out lands", visible=False,
            ),
            "gem_archetype": gr.Dropdown(choices=archetypes, label="Archetype (optional)",
                                         value=None, visible=False),
        }
        go = gr.Button("Explore", variant="primary")
        out = gr.HTML(_PROMPT)

        keys = list(w)

        def _show(chosen: str):
            wanted = _VIEWS[chosen]
            return [gr.update(visible=k in wanted) for k in keys]

        view.change(_show, inputs=view, outputs=[w[k] for k in keys])
        go.click(explore, inputs=[view, *[w[k] for k in keys]], outputs=out)

    return demo
