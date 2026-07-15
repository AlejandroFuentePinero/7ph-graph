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
import kuzu

from graph7ph.db import rows
from graph7ph.explore import RenderPlan, assess
from graph7ph.models import COLOURS
from graph7ph.query import (
    ArchetypeUniqueCards,
    CardCooccurrence,
    CardUsage,
    HiddenGems,
    PilotAffinity,
    PilotNeighbourhood,
    QuerySpec,
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
    "Card co-occurrence": ["card", "min_shared"],
    "Archetype unique cards": ["archetype", "unique_min_decks"],
    "Hidden gems": [
        "gem_min_decks", "gem_max_decks", "max_norm", "gem_colour", "gem_archetype"
    ],
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
            return CardCooccurrence(values["card"], int(_num(values["min_shared"], 2)))
        case "Archetype unique cards":
            if not values["archetype"]:
                return None
            return ArchetypeUniqueCards(
                values["archetype"], int(_num(values["unique_min_decks"], 3))
            )
        case "Hidden gems":
            return HiddenGems(
                int(_num(values["gem_min_decks"], 2)),
                int(_num(values["gem_max_decks"], 10)),
                float(_num(values["max_norm"], 0.33)),
                values["gem_colour"] or None,
                values["gem_archetype"] or None,
            )
    return None


def _choices(conn: kuzu.Connection, query: str) -> list[tuple[str, str]]:
    """(label, value) pairs for a dropdown, from a two-column query (label, value).

    A label shared by more than one value is suffixed with its value so the
    duplicates stay distinguishable: two pilots the data could not tell apart
    (an under-merge, ADR 0004) would otherwise show as identical rows.
    """
    pairs = [(label, value) for label, value in rows(conn.execute(query))]
    seen = Counter(label for label, _ in pairs)
    return [
        (f"{label} ({value})" if seen[label] > 1 else label, value)
        for label, value in pairs
    ]


def build_app(db_path: Path) -> gr.Blocks:
    # The Database is shared, but a Kùzu Connection is not thread-safe, so each
    # request opens its own over Gradio's worker threads. Read-only lets several
    # readers (and a separate build process) share the file.
    db = kuzu.Database(str(db_path), read_only=True)
    catalogue = kuzu.Connection(db)
    pilots = _choices(catalogue, "MATCH (p:Pilot) RETURN p.displayName, p.pilot ORDER BY p.displayName")
    cards = _choices(catalogue, "MATCH (c:Card) RETURN c.name, c.canon ORDER BY c.name")
    archetypes = _choices(catalogue, "MATCH (a:Archetype) RETURN a.name, a.tag ORDER BY a.name")

    def explore(view: str, *values: object) -> str:
        # Gradio passes the widget values positionally in `keys` order.
        spec = _spec(view, dict(zip(keys, values)))
        if spec is None:
            return _PROMPT
        subgraph = run_query(kuzu.Connection(db), spec)
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
            "archetype": gr.Dropdown(
                choices=archetypes, label="Archetype", value=None, visible=False
            ),
            "min_shared": gr.Number(value=2, precision=0, minimum=1,
                                    label="Min shared decks", visible=False),
            "unique_min_decks": gr.Number(value=3, precision=0, minimum=1,
                                          label="Min decks", visible=False),
            "gem_min_decks": gr.Number(value=2, precision=0, minimum=1,
                                       label="Min decks", visible=False),
            "gem_max_decks": gr.Number(value=10, precision=0, minimum=1,
                                       label="Max decks", visible=False),
            "max_norm": gr.Number(value=0.33, minimum=0, maximum=1,
                                  label="Max mean placement (0 best, 1 worst)", visible=False),
            "gem_colour": gr.Dropdown(choices=list(COLOURS), label="Colour (optional)",
                                      value=None, visible=False),
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
