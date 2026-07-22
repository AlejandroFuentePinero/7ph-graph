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
    CardAdoptionOverTime,
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

# The trend views, each a window in the Trends tab the picker swaps between, like
# the Explore tab's view dropdown. Named here so the picker and the toggle agree.
_META_TREND = "Meta share over time"
_ADOPTION_TREND = "Card adoption over time"
_TRENDS = [_META_TREND, _ADOPTION_TREND]
_DEFAULT_TREND = _META_TREND

# The board filter, shared by the card views: the label the dropdown shows, the
# empty string standing for "either board" the query reads as no filter. Kept in
# one place so the adoption chart's title label and the dropdown never disagree.
_BOARD_CHOICES = [("Main or side", ""), ("Main", "Main"), ("Side", "Side")]
_BOARD_LABELS = {value: label.lower() for label, value in _BOARD_CHOICES}


def _luminance(hex_colour: str) -> float:
    """A hex colour's sRGB relative luminance, 0 (black) to 1 (white)."""
    def _linear(channel: float) -> float:
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
    r, g, b = (_linear(int(hex_colour[i:i + 2], 16) / 255) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


# A long qualitative palette so the ~15 lines of the default cut stay distinct
# rather than recycling a 10-colour wheel into look-alike pairs. Filtered to a
# mid-luminance band because the chart background is transparent and inherits the
# browser's light or dark theme: a near-black colour (Dark24's #222A2A) is invisible
# on a dark theme, a pale one (parts of Light24) on a light theme, so a trace could
# sit in the legend yet never show on the canvas (the "Initiative line is missing"
# case). The band drops both extremes, keeping ~32 colours legible on either theme.
_PALETTE = [
    c for c in pc.qualitative.Dark24 + pc.qualitative.Light24
    if 0.12 <= _luminance(c) <= 0.70
]


def _style_trend_chart(fig: pgo.Figure, title: str, y_title: str) -> None:
    """The neutral-theme styling both trend charts share (the meta and one card).

    Transparent backgrounds so the chart sits on the app's own panel rather than
    Plotly's white card, with theme-neutral grey text and faint gridlines that
    read on either the light or dark theme the app inherits from the browser. Only
    the titles differ between the two charts (the y-axis is a share of the meta, or
    a card's adoption), so they are passed in; the rest is held in one place so the
    two cannot drift apart. The caller adds its own legend, the one thing they do
    not share.
    """
    grid = "rgba(128,128,128,0.2)"
    axis = "rgba(128,128,128,0.35)"
    fig.update_layout(
        title=title, hovermode="closest",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#9ca3af"), margin=dict(t=48, r=8, b=8, l=8),
    )
    fig.update_xaxes(
        title="Year", type="category", categoryorder="category ascending",
        gridcolor=grid, linecolor=axis, zeroline=False,
    )
    # A trimmed two-decimal percent, not a rounded whole one: fringe shares sit
    # below 1% (a card in a handful of a 2000-deck year), and rounding to integer
    # percents would floor them to "0%" and collide adjacent ticks on one label.
    fig.update_yaxes(
        title=y_title, tickformat=".2~%", rangemode="tozero",
        gridcolor=grid, linecolor=axis, zerolinecolor=axis,
    )


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
            hovertemplate=f"%{{x}} · {archetype} · %{{y:.2~%}} · n=%{{customdata}}<extra></extra>",
        ))
    _style_trend_chart(fig, title, "Share of meta")
    fig.update_layout(legend=dict(title="Archetype"))
    return fig


def _adoption_figure(cards: list[tuple[str, Series]], board_label: str) -> pgo.Figure:
    """One or more cards' adoption (share of that year's decks) over the years.

    A trace per card, coloured apart, so several cards can be compared on one axis.
    Adoption carries no floor: every year is plotted, including the zeros of years
    a card sat out, so a line shows the card entering rather than skipping a gap
    (ADR 0013). Share, not raw count, is the y-value, because the year bases differ
    (a thin early year against a fat recent one) and a count line would read a
    bigger meta as more adoption; each point's hover carries the raw count over the
    year total so the sample size is in hand. As with the meta-share chart the
    points are drawn large and hollow and the connecting line thin and dashed, a
    reminder it only joins observations and asserts no trend between them. The board
    the count is scoped to rides in the title, since it changes what the line means.
    """
    fig = pgo.Figure()
    for i, (card_name, series) in enumerate(cards):
        colour = _PALETTE[i % len(_PALETTE)]
        cells = sorted(series.cells, key=lambda c: c.year)
        fig.add_trace(pgo.Scatter(
            x=[str(c.year) for c in cells],
            y=[c.share for c in cells],
            customdata=[(c.count, c.year_total) for c in cells],
            name=card_name,
            mode="lines+markers",
            line=dict(width=1, dash="dash", color=colour),
            marker=dict(size=12, symbol="circle-open", line=dict(width=2.5, color=colour)),
            hovertemplate=(
                f"%{{x}} · {card_name} · %{{y:.2~%}} · "
                "%{customdata[0]}/%{customdata[1]} decks<extra></extra>"
            ),
        ))
    _style_trend_chart(fig, f"Card adoption over time ({board_label})", "Adoption (share of decks)")
    fig.update_layout(legend=dict(title="Card"))
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
    # the manual panel can list the archetypes and each draw just filters it. The
    # tool never sees the cut; it returns everything (ADR 0013).
    trend_series = run_series(catalogue, MetaShareOverTime())
    # Only archetypes with at least one year above the cell floor are offered:
    # `_trend_figure` plots a cell only where it clears the floor (a thinner cell is
    # a gap), so an archetype thin in every year would draw an empty line, a control
    # that answers with nothing. Offer only what can draw, as `gem_archetypes` does.
    drawable = {c.tag for c in trend_series.cells if c.share is not None}
    trend_archetypes = _distinguish(sorted(
        {(c.archetype, c.tag) for c in trend_series.cells if c.tag in drawable},
        key=lambda p: p[0],
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

    # Adoption is per-card, so it is run on demand (a fresh Connection like
    # `explore`) rather than precomputed like the whole meta matrix. Several cards
    # are overlaid, one tool call each, so the picker is a multi-select.
    card_names = {canon: label for label, canon in cards}

    def draw_adoption(canons: list[str], board: str):
        chosen = canons or []
        if not chosen:
            return gr.update(visible=False)
        conn = ladybug.Connection(db)
        series = [
            (card_names[canon], run_series(conn, CardAdoptionOverTime(canon, board or None)))
            for canon in chosen
        ]
        return gr.update(
            value=_adoption_figure(series, _BOARD_LABELS[board]), visible=True
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
                    choices=_BOARD_CHOICES, label="Board", value="", visible=False,
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
        # It mirrors the Explore tab: a view picker swaps between the trends, each
        # its own window of controls and chart, so only one shows at a time.
        with gr.Tab("Trends"):
            trend = gr.Dropdown(
                choices=list(_TRENDS), value=_DEFAULT_TREND, label="Trend",
            )
            with gr.Group() as meta_window:
                gr.Markdown(
                    "Each archetype's share of the meta, per year. The points are "
                    "the data; the thin dashed line only joins them and asserts no "
                    "trend between years. A year too thin to trust is left as a gap, "
                    "not a false zero. Hover a point for its share and deck count."
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
                # Hidden until a pick is made, so the window opens on the cut chart.
                manual_plot = gr.Plot(visible=False)

            with gr.Group(visible=False) as adoption_window:
                gr.Markdown(
                    "How cards' adoption moves across the years: the decks running "
                    "each as a share of that year's decks. A low count is the signal "
                    "of a card entering the format, not noise, so nothing is "
                    "withheld; a year a card sat out reads as a real zero. Pick "
                    "several to compare. Hover a point for its raw count over the "
                    "year's total decks."
                )
                adoption_cards = gr.Dropdown(
                    choices=cards, value=[], multiselect=True, label="Cards",
                )
                adoption_board = gr.Dropdown(
                    choices=_BOARD_CHOICES, value="", label="Board",
                )
                # Hidden until a card is chosen, matching the manual archetype chart.
                adoption_plot = gr.Plot(visible=False)

            # Each window shows exactly when it is the chosen trend, keyed by name,
            # so a trend added to the picker cannot ride under a "not meta" branch.
            trend_windows = {_META_TREND: meta_window, _ADOPTION_TREND: adoption_window}

            def _show_trend(chosen: str):
                return [gr.update(visible=name == chosen) for name in trend_windows]

            trend.change(_show_trend, inputs=trend, outputs=list(trend_windows.values()))
            cut.change(draw_cut, inputs=cut, outputs=cut_plot)
            manual.change(draw_manual, inputs=manual, outputs=manual_plot)
            adoption_cards.change(
                draw_adoption, inputs=[adoption_cards, adoption_board], outputs=adoption_plot
            )
            adoption_board.change(
                draw_adoption, inputs=[adoption_cards, adoption_board], outputs=adoption_plot
            )

    return demo
