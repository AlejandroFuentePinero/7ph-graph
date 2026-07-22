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
import plotly.colors as pc
import plotly.graph_objects as pgo

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
from graph7ph.trends import (
    MetaShareOverTime,
    Series,
    pooled_share_cut,
    run_series,
)

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


# The pooled cumulative-share cut, as labelled radio choices (ADR 0013). The cut
# is display legibility only: the tool always returns the full matrix, and this
# picks which of the ~125 archetypes are drawn as lines, default 50%.
_CUTS: dict[str, float] = {"Top 25%": 0.25, "Top 50%": 0.5, "Top 75%": 0.75}
_DEFAULT_CUT = "Top 50%"


# A long qualitative palette so the ~15 lines of the default cut stay distinct
# rather than recycling a 10-colour wheel into look-alike pairs.
_PALETTE = pc.qualitative.Dark24 + pc.qualitative.Light24


def _trend_figure(series: Series, tags: set[str], title: str) -> pgo.Figure:
    """A line chart of the chosen archetypes' meta share over time.

    One trace per archetype, coloured apart, with the data foregrounded: the
    points are the observations, so they are drawn large and hollow with a thick
    rim, while the connecting line is thin and dashed, a reminder that it only
    joins points and asserts no trend between them (ADR 0013). Only cells that
    clear the floor are plotted, ordered by year, so a line skips a gap year
    rather than dropping to a false zero; each point's hover carries its year,
    share, and deck count N, the sample size the reader reasons with.
    """
    by_arch: dict[str, list] = {}
    for cell in sorted(series.cells, key=lambda c: c.year):
        if cell.tag in tags and cell.share is not None:
            by_arch.setdefault(cell.archetype, []).append(cell)

    fig = pgo.Figure()
    for i, (archetype, cells) in enumerate(sorted(by_arch.items())):
        colour = _PALETTE[i % len(_PALETTE)]
        fig.add_trace(pgo.Scatter(
            x=[str(c.year) for c in cells],
            y=[c.share for c in cells],
            customdata=[c.n for c in cells],
            name=archetype,
            mode="lines+markers",
            line=dict(width=1, dash="dash", color=colour),
            marker=dict(size=12, symbol="circle-open", line=dict(width=2.5, color=colour)),
            hovertemplate=f"%{{x}} · {archetype} · %{{y:.1%}} · n=%{{customdata}}<extra></extra>",
        ))
    # Transparent backgrounds so the chart sits on the app's own panel rather than
    # Plotly's white card, with theme-neutral grey text and faint gridlines that
    # read on either the light or dark theme the app inherits from the browser.
    grid = "rgba(128,128,128,0.2)"
    axis = "rgba(128,128,128,0.35)"
    fig.update_layout(
        title=title, hovermode="closest",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#9ca3af"),
        legend=dict(title="Archetype"), margin=dict(t=48, r=8, b=8, l=8),
    )
    fig.update_xaxes(
        title="Year", type="category", categoryorder="category ascending",
        gridcolor=grid, linecolor=axis, zeroline=False,
    )
    fig.update_yaxes(
        title="Share of meta", tickformat=".0%", rangemode="tozero",
        gridcolor=grid, linecolor=axis, zerolinecolor=axis,
    )
    return fig


def build_app(artifact: Path) -> gr.Blocks:
    # The Database is shared and each request opens its own Connection over
    # Gradio's worker threads. That is a simplicity choice, not a safety
    # requirement: a Ladybug Connection *is* thread-safe, so sharing one would
    # also be correct. Read on 0.18.2 rather than assumed, because the opposite
    # belief was inherited from the Kùzu era and carried through the swap
    # unexamined until #65 settled it (8b8537e). A parameterized query holds a
    # `threading.RLock`
    # across prepare, bind and execute (guarding the bound-value map) over a C++
    # `mtx` around `executeWithParams`; a parameterless one skips the Python
    # lock and rests on that same C++ mutex.
    #
    # Read-only lets several readers (and a separate build process) share the
    # file. The artifact is the bundle directory; the database sits inside it
    # (issue #47).
    db = open_database(artifact, read_only=True)
    catalogue = ladybug.Connection(db)
    pilots = _distinguish(pilot_catalogue(catalogue))
    cards = _distinguish(card_catalogue(catalogue))
    # Only the archetypes whose slice can actually answer the gem question; the
    # rest would be an invitation to a result we cannot stand behind (ADR 0012).
    archetypes = _distinguish(gem_archetypes(catalogue))

    # The trend surface reads the full matrix once (a static, read-only graph), so
    # the manual panel can list every archetype and each draw just filters it. The
    # tool never sees the cut; it returns everything (ADR 0013).
    trend_series = run_series(catalogue, MetaShareOverTime())
    trend_archetypes = _distinguish(sorted(
        {(c.archetype, c.tag) for c in trend_series.cells}, key=lambda p: p[0]
    ))

    def draw_cut(cut_label: str) -> pgo.Figure:
        tags = set(pooled_share_cut(trend_series, _CUTS[cut_label]))
        return _trend_figure(trend_series, tags, f"Meta share, {cut_label.lower()} of decks")

    def draw_manual(manual_tags: list[str]):
        # A focused second chart, drawn only once specific archetypes are chosen, so
        # the manual pick reads on its own rather than crowding the cut chart.
        tags = set(manual_tags or [])
        if not tags:
            return gr.update(visible=False)
        return gr.update(
            value=_trend_figure(trend_series, tags, "Selected archetypes"), visible=True
        )

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
        gr.Markdown("# 7 Point Highlander Graph")
        with gr.Tab("Explore"):
            gr.Markdown(
                "Pick what to explore, set filters, and see a filtered subgraph of "
                "the result. Click a node for its details; a deck links out to "
                "Moxfield."
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

        # The trend tab is a separate surface, decoupled from the vis.js graph
        # renderer: it draws the Series as a line chart, never a subgraph (ADR 0013).
        with gr.Tab("Trends"):
            gr.Markdown(
                "Each archetype's share of the meta, per year. The points are the "
                "data; the thin dashed line only joins them and asserts no trend "
                "between years. A year too thin to trust is left as a gap, not a "
                "false zero. Hover a point for its share and deck count."
            )
            cut = gr.Radio(
                list(_CUTS), value=_DEFAULT_CUT,
                label="Archetypes to show (by pooled share of decks)",
            )
            cut_plot = gr.Plot(value=draw_cut(_DEFAULT_CUT))
            manual = gr.Dropdown(
                choices=trend_archetypes, value=[], multiselect=True,
                label="Or focus on specific archetypes",
            )
            # Hidden until a pick is made, so the tab opens on the cut chart alone.
            manual_plot = gr.Plot(visible=False)

            cut.change(draw_cut, inputs=cut, outputs=cut_plot)
            manual.change(draw_manual, inputs=manual, outputs=manual_plot)

    return demo
