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
    HeadToHeadTimeline,
    MetaShareOverTime,
    PilotPerformanceOverTime,
    Series,
    latest_year_share_cut,
    pilots_with_history,
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


# The latest-year cumulative-share cut, as labelled radio choices (ADR 0013). The
# cut is display legibility only: the tool always returns the full matrix, and this
# picks which of the ~125 archetypes are drawn as lines, default 50%.
_CUTS: dict[str, float] = {"Top 25%": 0.25, "Top 50%": 0.5, "Top 75%": 0.75}
_DEFAULT_CUT = "Top 50%"

# The trend views, each a window in the Trends tab the picker swaps between, like
# the Explore tab's view dropdown. Named here so the picker and the toggle agree.
_META_TREND = "Meta share over time"
_ADOPTION_TREND = "Card adoption over time"
_PERFORMANCE_TREND = "Pilot performance over time"
_H2H_TREND = "Pilot head-to-head timeline"
_TRENDS = [_META_TREND, _ADOPTION_TREND, _PERFORMANCE_TREND, _H2H_TREND]
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


def _rgba(hex_colour: str, alpha: float) -> str:
    """A hex palette colour as an ``rgba()`` string at the given opacity."""
    r, g, b = pc.hex_to_rgb(hex_colour)
    return f"rgba({r}, {g}, {b}, {alpha})"


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


def _performance_figure(pilot_name: str, series: Series) -> pgo.Figure:
    """One pilot's mean finish (placementNorm) over their qualifying years.

    A single trace of the pilot's year-by-year mean, drawn like the other trend
    charts: the points are the data, large and hollow, the connecting line thin and
    dashed so it only joins them and asserts no direction (ADR 0013). The y-axis is
    the mean finish inverted to a higher-is-better score (1 is a win, 0 is last), so
    a rising line reads as improving; the tool's ``mean_norm`` stays raw placementNorm
    (0 is a win), the codebase convention the agent reads, and only this chart flips
    it for the eye. Fixed to the full 0-to-1 range rather than auto-zoomed so a small
    year-to-year wiggle is not stretched into a dramatic swing. Each point is labelled
    with the number of events it averages, since a two-event mean and a twenty-event
    one sit on the same line and only the count tells them apart. A dotted line at 0.5
    marks a random finisher's expected placement (a normalised rank averages 0.5), so
    a point above it is a season that beat the field, below it one that trailed it.
    A year inside the pilot's span with no qualifying data (a thin middle year) stays
    an empty tick and the line breaks across it rather than bridging a fabricated
    point, since this lone trace has no sibling series to hold the gap year open the
    way the meta and adoption charts do. A pilot short of two qualifying years never
    gets this far (an empty series).
    """
    fig = pgo.Figure()
    cells = sorted(series.cells, key=lambda c: c.year)
    # Span every year from the first qualifying year to the last, pairing each with
    # its cell or None, so a thin middle year is a visible gap (an empty tick, a
    # broken line), not two points collapsed adjacent as if the season never existed.
    # The pairing is built once here rather than re-looked-up per plotted attribute.
    by_year = {c.year: c for c in cells}
    spanned = [(year, by_year.get(year)) for year in range(cells[0].year, cells[-1].year + 1)]
    colour = _PALETTE[0]
    fig.add_trace(pgo.Scatter(
        x=[str(year) for year, _ in spanned],
        y=[1 - c.mean_norm if c else None for _, c in spanned],
        customdata=[c.events if c else None for _, c in spanned],
        name=pilot_name,
        mode="lines+markers+text",
        text=[f"{c.events} ev" if c else "" for _, c in spanned],
        textposition="top center",
        textfont=dict(color="#9ca3af", size=11),
        line=dict(width=1, dash="dash", color=colour),
        marker=dict(size=12, symbol="circle-open", line=dict(width=2.5, color=colour)),
        # Let a marker and its label at the very top (a perfect 1.0 season) draw over
        # the axis edge rather than being clipped out of the plot.
        cliponaxis=False,
        hovertemplate=f"%{{x}} · {pilot_name} · score %{{y:.3f}} · %{{customdata}} events<extra></extra>",
    ))
    _style_trend_chart(fig, f"Pilot performance: {pilot_name}", "Mean finish (1 = 1st, 0 = last)")
    # A bounded 0-1 score, not a share, so a plain decimal axis over the full range,
    # overriding the shared styler's percent format and auto-zoom.
    fig.update_yaxes(tickformat=".2f", range=[0, 1], autorange=False)
    # A plain reference line at 0.5, a random finisher's expected normalised rank
    # (the flip leaves it at 0.5): above it beat the field, below it trailed.
    fig.add_hline(y=0.5, line=dict(color="rgba(128,128,128,0.55)", width=1, dash="dot"))
    return fig


def _between_line_polys(points):
    """Polygons filling the gap between two lines, one per segment, split at crossings.

    ``points`` is a date-ordered list of ``(x, a, b)`` where ``a`` and ``b`` are the
    two lines' y at ``x``, either ``None`` for a value the source never scored. Yields
    ``(xs, ys, a_above)``: the region between the lines over one segment, with
    ``a_above`` True where line ``a`` is the upper edge. A segment with a null end on
    either line is skipped (the lines break there, so the fill does too, ADR 0013,
    never fabricating area over an unscored event); a segment where the lines cross is
    split at the crossing so each half carries the line above it there. A pure geometry
    seam so the crossing/gap cases can be tested without building a figure.
    """
    for (x0, a0, b0), (x1, a1, b1) in zip(points, points[1:]):
        if None in (a0, b0, a1, b1):
            continue
        d0, d1 = a0 - b0, a1 - b1
        if d0 == 0 and d1 == 0:
            continue
        if d0 * d1 < 0:  # the lines cross inside this segment: split at the crossing
            t = d0 / (d0 - d1)
            xc, yc = x0 + (x1 - x0) * t, a0 + (a1 - a0) * t
            yield [x0, xc, x0], [a0, yc, b0], d0 > 0
            yield [xc, x1, x1], [yc, a1, b1], d1 > 0
        else:  # one line stays above across the whole segment: a single trapezoid
            yield [x0, x1, x1, x0], [a0, a1, b1, b0], d0 + d1 > 0


def _head_to_head_figure(name_a: str, name_b: str, series: Series) -> pgo.Figure:
    """Two pilots' rivalry over their shared events, on a registration-date x-axis.

    One line per pilot, coloured apart, the finish on the y-axis (the only quantity
    comparable across events of different field sizes). Unlike every other trend this
    reads a per-deck date, not a Year node (ADR 0013): the x is the event's
    registration date, so two events shared in one year sit apart rather than
    collapsing onto the same year tick. The y-axis is the finish inverted to a
    higher-is-better score (1 a win, 0 last), the same scale as the pilot-performance
    chart so the two read alike, while the tool keeps the raw ``placementNorm`` (0 a
    win) the agent reads. Each point's hover carries the raw finish over the field
    size (``5/143`` is 5th of a 143-entrant field): the position and the tournament
    size the score is normalised against, the two numbers the plotted score is
    computed from. The points are the data; the thin dashed line only joins them and
    asserts no direction. A translucent band fills between the two lines, tinted with
    the colour of whichever pilot is above, so the size and direction of the gap read
    at a glance; it breaks over any event one pilot did not score and splits at a
    crossing. A dotted line at 0.5 marks a random finisher's expected
    score, as on the performance chart. A range slider aligned under the x-axis is the
    time-range filter: its own trace preview is suppressed (it mirrored the lines and
    read as a bug), leaving a plain tinted band, labelled, that drags to slice the
    date range with no server round-trip.
    """
    cells = sorted(series.cells, key=lambda c: c.date)
    fig = pgo.Figure()
    colour_a, colour_b = _PALETTE[0], _PALETTE[1]

    # A translucent band between the two lines, tinted with the colour of whichever
    # pilot sits higher, so the eye reads the size and the direction of the gap at a
    # glance without decoding the two lines apart. The score inverts the finish (1 a
    # win), a null left null so the band breaks over an event a pilot did not score
    # (ADR 0013). Each pilot's polygons collect into one trace, their subpaths joined
    # by a None gap so ``toself`` closes each on its own, keeping this to two fill
    # traces rather than one per segment. Added first so the markers and the dashed
    # joins draw on top.
    def score(norm):
        return None if norm is None else 1 - norm
    points = [(c.date, score(c.norm_a), score(c.norm_b)) for c in cells]
    bands = {True: ([], []), False: ([], [])}  # a_above -> (xs, ys)
    for xs, ys, a_above in _between_line_polys(points):
        bx, by = bands[a_above]
        if bx:  # a None gap separates this polygon from the previous one
            bx.append(None)
            by.append(None)
        bx.extend(xs)
        by.extend(ys)
    for a_above, (bx, by) in bands.items():
        if not bx:
            continue
        fig.add_trace(pgo.Scatter(
            x=bx, y=by, fill="toself",
            fillcolor=_rgba(colour_a if a_above else colour_b, 0.18),
            mode="lines", line=dict(width=0),
            hoverinfo="skip", showlegend=False,
        ))

    pilots = [
        (name_a, colour_a,
         [(c.date, c.placement_a, c.norm_a, c.field_size) for c in cells]),
        (name_b, colour_b,
         [(c.date, c.placement_b, c.norm_b, c.field_size) for c in cells]),
    ]
    for name, colour, points in pilots:
        fig.add_trace(pgo.Scatter(
            x=[date for date, _, _, _ in points],
            # The finish inverted to a score (1 a win), matching the performance
            # chart. A null norm is a finish the source never scored: a gap the line
            # breaks across rather than a fabricated point.
            y=[1 - norm if norm is not None else None for _, _, norm, _ in points],
            customdata=[(placement, field) for _, placement, _, field in points],
            name=name,
            mode="lines+markers",
            line=dict(width=1, dash="dash", color=colour),
            marker=dict(size=12, symbol="circle-open", line=dict(width=2.5, color=colour)),
            cliponaxis=False,
            hovertemplate=(
                f"%{{x|%d %b %Y}} · {name} · score %{{y:.2f}} · "
                "%{customdata[0]}/%{customdata[1]}<extra></extra>"
            ),
        ))
    _style_trend_chart(fig, f"Head-to-head: {name_a} vs {name_b}", "Finish (1 = 1st, 0 = last)")
    # A registration-date x-axis (ADR 0013), not the category-Year axis the shared
    # styler sets, with a range slider as the time-range filter. Its mini-axis is
    # fixed to an off-data band (the score is 0-1, this is 10-11), which parks the
    # trace preview out of view: the slider stays a plain tinted control instead of
    # a second copy of the lines that reads as a bug. A tint distinct from the plot
    # marks it as a control.
    fig.update_xaxes(
        title="Registration date", type="date", categoryorder=None, autorange=True,
        rangeslider=dict(
            visible=True, thickness=0.12,
            bgcolor="rgba(245,158,11,0.12)", bordercolor="rgba(245,158,11,0.55)",
            borderwidth=1, yaxis=dict(rangemode="fixed", range=[10, 11]),
        ),
    )
    # Label the band so it reads as a filter, not a stray strip. Centred both ways
    # over the slider in paper coords (the band sits below the axis, roughly y -0.09
    # to -0.32, so its middle is near -0.20); the bottom margin below seats the
    # slider. Amber, matching the slider tint against the neutral chart. Paper x=0.5
    # is the true centre only because the legend is horizontal above the plot (below):
    # a right-side legend shrinks the plot area by its own width, which changes with
    # the pilot names, drifting this label left as the names lengthen.
    fig.add_annotation(
        x=0.5, y=-0.20, xref="paper", yref="paper", xanchor="center", yanchor="middle",
        showarrow=False, text="◀ Time range filter (drag to slice) ▶",
        font=dict(color="rgba(245,158,11,0.95)", size=11),
    )
    # The 0-1 score (1 a win at the top), fixed to the full range so a small gap is
    # not stretched, overriding the shared styler's percent format and zoom. Matches
    # the performance chart: same scale, same 0.5 reference line.
    fig.update_yaxes(tickformat=".2f", range=[0, 1], autorange=False)
    fig.add_hline(y=0.5, line=dict(color="rgba(128,128,128,0.55)", width=1, dash="dot"))
    # A horizontal legend above the plot, not the shared styler's default right-side
    # one: an external right legend widens with the pilot names and eats into the plot
    # area, which drifts the paper-centred time-range label (above) and leaves it off
    # true centre. A top strip keeps the plot full-width and stable. The title is
    # pinned to the top of a taller margin and the legend seated just above the plot,
    # so a long "A vs B" title and the centred legend sit on their own rows rather than
    # colliding. Room below the axis for the slider band and its label (the shared
    # styler sets a tight b=8 for the label-free charts).
    fig.update_layout(
        title=dict(y=0.97, yanchor="top"),
        legend=dict(
            title="Pilot", orientation="h",
            xanchor="center", x=0.5, yanchor="bottom", y=1.02,
        ),
        margin=dict(t=96, b=90),
    )
    return fig


def build_app(artifact: Path) -> gr.Blocks:
    # The Database is shared and each request opens its own Connection over
    # Gradio's worker threads. That per-request Connection sidesteps the question
    # of whether one Connection may be shared across those threads, which this
    # repo cannot answer and which four earlier passes at this comment answered
    # anyway, each time wrongly (#73).
    #
    # What is actually readable on 0.18.2: the compiled pybind module ships, so
    # `Connection.execute` always takes the pybind path into
    # `_execute_with_pybind` and the C-API branch is dead code in this
    # deployment (every previous wrong citation pointed there). Within
    # `_execute_with_pybind`, a parameterized query holds
    # `_prepared_cache_lock` across prepare and execute; a parameterless query
    # calls through with no Python-level lock. The app runs both. What the C++
    # underneath does is not readable from this repo, since only the compiled
    # module ships, so it was not established.
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

    # The year the cut ranks on, read from the data so it follows the graph forward
    # rather than being pinned; named here only to say so in the chart title and the
    # radio's label, since "top 50%" means nothing without the year it is 50% of.
    latest_year = max(c.year for c in trend_series.cells)

    def draw_cut(cut_label: str) -> pgo.Figure:
        tags = set(latest_year_share_cut(trend_series, _CUTS[cut_label]))
        return _trend_figure(
            trend_series, tags, f"Meta share, {cut_label.lower()} of {latest_year} decks"
        )

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

    # Only pilots the trend can actually draw (at least two qualifying years); the
    # rest would land on "not enough history", so they are withheld the way the
    # meta-share panel offers only drawable archetypes. Names paired with their key.
    performance_pilots = _distinguish(pilots_with_history(catalogue))
    pilot_names = {key: label for label, key in performance_pilots}

    def draw_performance(pilot: str):
        if not pilot:
            return gr.update(visible=False)
        series = run_series(ladybug.Connection(db), PilotPerformanceOverTime(pilot))
        # The dropdown only lists pilots that qualify, so an empty series should not
        # arise; guard anyway so a non-drawable pilot hides the chart rather than
        # crashing `_performance_figure` on `cells[0]` if the two floor queries drift.
        if not series.cells:
            return gr.update(visible=False)
        return gr.update(value=_performance_figure(pilot_names[pilot], series), visible=True)

    # Head-to-head offers every pilot in both slots (like the Explore tab), since the
    # drawable set is pairwise and too large to precompute; a pair that shares too
    # few events is refused with a message rather than drawn as a dot (ADR 0013).
    pilot_labels = {key: label for label, key in pilots}

    def draw_head_to_head(a: str, b: str):
        # Hide both the chart and the note until a valid pair is picked; the note is
        # the "refused, not a dot" surface for a pair the tool comes back empty on.
        if not a or not b:
            return gr.update(visible=False), gr.update(visible=False)
        if a == b:
            return gr.update(visible=False), gr.update(
                value="Pick two different pilots to see their rivalry.", visible=True
            )
        series = run_series(ladybug.Connection(db), HeadToHeadTimeline(a, b))
        if not series.cells:
            return gr.update(visible=False), gr.update(
                value=(
                    f"{pilot_labels[a]} and {pilot_labels[b]} share fewer than two "
                    "events, so there is no rivalry to trace over time."
                ),
                visible=True,
            )
        # The in-chart range slider does the time-range slice client-side, so the
        # callback draws the whole rivalry and never re-filters by date here.
        fig = _head_to_head_figure(pilot_labels[a], pilot_labels[b], series)
        return gr.update(value=fig, visible=True), gr.update(visible=False)

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
                    label=f"Archetypes to show (by share of {latest_year} decks)",
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

            with gr.Group(visible=False) as performance_window:
                gr.Markdown(
                    "A pilot's mean finish per year, over the years they have real "
                    "history, drawn so higher is better (1 is a win, 0 is last). A year "
                    "with only one event to average is left as a gap; a pilot short of "
                    "two such years is not listed. Each point is labelled with the "
                    "number of events it averages, and a dotted line marks the 0.5 "
                    "midpoint. The points are the data; the thin dashed line only joins "
                    "them and asserts no direction."
                )
                performance_pilot = gr.Dropdown(
                    choices=performance_pilots, value=None, label="Pilot",
                )
                # Hidden until a pilot is chosen, matching the other on-demand charts.
                performance_plot = gr.Plot(visible=False)

            with gr.Group(visible=False) as h2h_window:
                gr.Markdown(
                    "Two pilots' rivalry over the events they both entered: each "
                    "point is one shared event, the finish on the y-axis drawn so "
                    "higher is better (1 is a win, 0 is last), the same scale as the "
                    "pilot-performance chart. The x-axis is the registration date, so "
                    "two events shared in one year sit apart. Hover a point for the "
                    "raw finish over the field size (its position and the tournament "
                    "size the score is normalised against). Drag the range slider "
                    "under the chart to slice a time range. A pair sharing fewer than "
                    "two events is a dot, not a timeline, so it is refused rather than "
                    "drawn."
                )
                h2h_pilot_a = gr.Dropdown(choices=pilots, value=None, label="Pilot")
                h2h_pilot_b = gr.Dropdown(choices=pilots, value=None, label="Second pilot")
                # The "refused, not a dot" surface for a pair with too few shared
                # events; hidden until a pick lands on it.
                h2h_note = gr.Markdown(visible=False)
                # Hidden until a valid pair is chosen, matching the other on-demand charts.
                h2h_plot = gr.Plot(visible=False)

            # Each window shows exactly when it is the chosen trend, keyed by name,
            # so a trend added to the picker cannot ride under a "not meta" branch.
            trend_windows = {
                _META_TREND: meta_window,
                _ADOPTION_TREND: adoption_window,
                _PERFORMANCE_TREND: performance_window,
                _H2H_TREND: h2h_window,
            }

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
            performance_pilot.change(
                draw_performance, inputs=performance_pilot, outputs=performance_plot
            )
            for control in (h2h_pilot_a, h2h_pilot_b):
                control.change(
                    draw_head_to_head, inputs=[h2h_pilot_a, h2h_pilot_b],
                    outputs=[h2h_plot, h2h_note],
                )

    return demo
