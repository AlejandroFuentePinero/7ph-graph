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
import pandas  # noqa: F401  (imported for its side effect; see below)
import plotly.colors as pc
import plotly.graph_objects as pgo

# Imported here, unused, to warm it at import time before any request thread runs.
# Nothing in this app imports pandas at startup, but plotly reaches for it lazily
# while building a figure (`is_homogeneous_array` does `isinstance(v, pd.Series)`
# via `sys.modules.get("pandas")`), and Gradio's queue imports it lazily on a
# worker thread for its per-event analytics. On the first import, Python leaves a
# half-initialised `pandas` in `sys.modules`, and a figure-building thread hitting
# `pd.Series` in that window raised `partially initialized module 'pandas' has no
# attribute 'Series'` as a red error box, gone on reload once the process was warm.
# Importing it fully at module load closes that window before launch, so the Trends
# charts cannot lose the race on a cold start.

from graph7ph import numfmt, theme
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
    NotEnoughHistory,
    PilotPerformanceOverTime,
    Series,
    latest_deck_year,
    latest_year_share_cut,
    run_series,
)

_PROMPT = "<p style='padding:1rem'>Pick an entity and filters, then Draw.</p>"

# The app is organised by subject, not by render modality (issue #119, v1 §11).
# Three tabs (Pilots, Cards, Meta) each hold that subject's graph and chart views
# behind a view picker, so a visitor picks the subject once and moves across its
# views. The same nine views the old Explore/Trends split carried are all preserved;
# only the grouping changes, and the graph and chart pipelines stay separate under
# the hood (ADR 0013). Each tab is an ordered map of view id to the label the picker
# shows; the graph views' ids double as the query keys `_spec` dispatches on. Held
# as data so the tests can assert the nine views survive the regrouping intact,
# none added or dropped.
_PILOTS_TAB: dict[str, str] = {
    "pilot_neighbourhood": "Neighbourhood & head-to-head",
    "pilot_affinity": "Archetype affinity",
    "pilot_performance": "Performance over time",
    "pilot_h2h_timeline": "Head-to-head timeline",
}
_CARDS_TAB: dict[str, str] = {
    "card_usage": "Usage",
    "card_cooccurrence": "Co-occurrence",
    "card_adoption": "Adoption over time",
}
_META_TAB: dict[str, str] = {
    "meta_share": "Meta share over time",
    "meta_gems": "Hidden gems",
}

# The picker choices for each tab, as (label, view id) pairs.
def _picker(tab: dict[str, str]) -> list[tuple[str, str]]:
    return [(label, view_id) for view_id, label in tab.items()]


# The reader-language name of every view, keyed by its id: the picker labels do
# double duty as the result title's view name, so the title and the picker cannot
# name the same view two ways.
_VIEW_LABELS: dict[str, str] = {**_PILOTS_TAB, **_CARDS_TAB, **_META_TAB}


def _result_header(view: str, subject: str, filters: list[str], node_count: int) -> str:
    """Frame a query result in page type (issue #110, §3): the view and its subject
    as the title, the filters and how many nodes came back as the caption, so an
    answer is never left as an unlabelled graph. Prepended to the drawn result, the
    empty state, and the refine alert alike, so every post-query state speaks the
    same way. The subject and filters are display labels (free text), so they are
    escaped into the markup."""
    title = f"{_VIEW_LABELS[view]}: {subject}"
    # A drawn result is under the render threshold (250 nodes), so the count needs no
    # thousands separator; the refine alert carries the large counts.
    tail = f"{node_count} node" + ("" if node_count == 1 else "s")
    caption = " · ".join([*filters, tail])
    return (
        f"<div class='t-result-title'>{html.escape(title)}</div>"
        f"<div class='t-caption'>{html.escape(caption)}</div>"
    )


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
        case "pilot_neighbourhood":
            if not values["pilot"]:
                return None
            return PilotNeighbourhood(values["pilot"], values["pilot2"] or None)
        case "pilot_affinity":
            return PilotAffinity(values["pilot"]) if values["pilot"] else None
        case "card_usage":
            if not values["card"]:
                return None
            return CardUsage(values["card"], values["card_board"] or None)
        case "card_cooccurrence":
            if not values["card"]:
                return None
            return CardCooccurrence(
                values["card"],
                values["cooccur_card2"] or None,
                int(_num(values["cooccur_top_n"], 15)),
                bool(values["cooccur_drop_lands"]),
            )
        case "meta_gems":
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
# picks which of the 126 archetypes are drawn as lines, default 50%.
_CUTS: dict[str, float] = {"Top 25%": 0.25, "Top 50%": 0.5, "Top 75%": 0.75}
_DEFAULT_CUT = "Top 50%"

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
        title=y_title, tickformat=numfmt.SHARE_TICKFORMAT, rangemode="tozero",
        gridcolor=grid, linecolor=axis, zerolinecolor=axis,
    )


def _trend_figure(series: Series, tags: set[str], title: str) -> pgo.Figure:
    """A line chart of the chosen archetypes' meta share over time.

    One trace per archetype, with the data foregrounded: the points are the
    observations, so they are drawn large and hollow with a thick rim, while the
    connecting line is thin and dashed, a reminder that it only joins points and
    asserts no trend between them (ADR 0013). Every year draws a point: meta share
    carries no floor, so a thin year states its real share and a year the archetype
    was absent drops to a real zero, with no holes for the eye to read as zeros of
    its own. Each point's hover carries its year, share, and deck count N, the
    sample size the reader reasons with.

    Colour separates the traces up to the palette's 32 entries and recycles past
    that, so a 33rd selected archetype repeats a colour already on the canvas. A
    trace's colour is also its alphabetical position within the current selection,
    not a property of the archetype: all 14 archetypes drawn at the Top 50% cut
    take a different colour at Top 75%, because a wider cut inserts names above
    them. The legend, not the colour, is what identifies a line, and no colour
    carries across two charts.

    Traces are keyed by tag, not by display name, because two tags can share a name
    (as ``SeriesCell`` says) and the rectangular matrix gives each of them a cell in
    every year: keyed by name they would merge into one trace holding two y values
    per year and draw as a sawtooth between two archetypes.
    """
    by_tag: dict[str, list] = {}
    for cell in sorted(series.cells, key=lambda c: c.year):
        if cell.tag in tags:
            by_tag.setdefault(cell.tag, []).append(cell)

    fig = pgo.Figure()
    ordered = sorted(by_tag.values(), key=lambda cells: (cells[0].archetype, cells[0].tag))
    for i, cells in enumerate(ordered):
        archetype = cells[0].archetype
        colour = _PALETTE[i % len(_PALETTE)]
        fig.add_trace(pgo.Scatter(
            x=[str(c.year) for c in cells],
            y=[c.share for c in cells],
            customdata=[[numfmt.share(c.share), numfmt.count_of(c.n, c.year_total, "decks")]
                        for c in cells],
            name=archetype,
            mode="lines+markers",
            line=dict(width=1, dash="dash", color=colour),
            marker=dict(size=12, symbol="circle-open", line=dict(width=2.5, color=colour)),
            hovertemplate=(
                f"%{{x}} · {archetype} · %{{customdata[0]}} · "
                "%{customdata[1]}<extra></extra>"
            ),
        ))
    _style_trend_chart(fig, title, "Share of meta")
    fig.update_layout(legend=dict(title="Archetype"))
    return fig


def _adoption_figure(cards: list[tuple[str, Series]], board_label: str) -> pgo.Figure:
    """One or more cards' adoption (share of that year's decks) over the years.

    A trace per card, so several cards can be compared on one axis. Colour separates
    them up to the palette's 32 entries and recycles past that, and a trace's colour
    is its position in the current selection rather than anything about the card, so
    the same card takes a different colour as the selection changes and the legend,
    not the colour, is what identifies a line. Adoption carries no floor: every year
    is plotted, including the zeros of years a card sat out, so a line shows the
    card entering rather than skipping a gap (ADR 0013). Share, not raw count, is
    the y-value, because the year bases differ (a thin early year against a fat
    recent one) and a count line would read a bigger meta as more adoption; each
    point's hover carries the raw count over the year total so the sample size is
    in hand. As with the meta-share chart the points are drawn large and hollow and
    the connecting line thin and dashed, a reminder it only joins observations and
    asserts no trend between them. The board the count is scoped to rides in the
    title, since it changes what the line means.
    """
    fig = pgo.Figure()
    for i, (card_name, series) in enumerate(cards):
        colour = _PALETTE[i % len(_PALETTE)]
        cells = sorted(series.cells, key=lambda c: c.year)
        fig.add_trace(pgo.Scatter(
            x=[str(c.year) for c in cells],
            y=[c.share for c in cells],
            customdata=[[numfmt.share(c.share), numfmt.count_of(c.count, c.year_total, "decks")]
                        for c in cells],
            name=card_name,
            mode="lines+markers",
            line=dict(width=1, dash="dash", color=colour),
            marker=dict(size=12, symbol="circle-open", line=dict(width=2.5, color=colour)),
            hovertemplate=(
                f"%{{x}} · {card_name} · %{{customdata[0]}} · "
                "%{customdata[1]}<extra></extra>"
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
    A year whose mean was refused as too thin stays an empty tick and the line breaks
    across it rather than bridging a fabricated point, since this lone trace has no
    sibling series to hold the gap year open the way the meta and adoption charts do.
    That now holds at the ends of a career as well as in the middle: the span runs
    from the pilot's first year to their last, not from the first year that cleared
    the floor to the last, because a thin year is overwhelmingly a pilot's first or
    last and spanning only the drawn years erased it from the axis altogether, so the
    chart claimed a later debut or an earlier exit than the pilot had (issue #101).
    A refused year is captioned under its tick with the events that refused it, so it
    reads as a refusal rather than as a year the pilot sat out, which is the same
    empty tick. A pilot short of two qualifying years never gets this far (the tool
    refuses).
    """
    fig = pgo.Figure()
    cells = sorted(series.cells, key=lambda c: c.year)
    # Span every year from the pilot's first year to their last, pairing each with its
    # cell or None, so a thin year is a visible gap (an empty tick, a broken line), not
    # two points collapsed adjacent as if the season never existed. The series covers
    # every year the pilot played, so the only years without a cell here are years they
    # genuinely sat out. The pairing is built once rather than re-looked-up per
    # plotted attribute.
    by_year = {c.year: c for c in cells}
    spanned = [(year, by_year.get(year)) for year in range(cells[0].year, cells[-1].year + 1)]
    # A refused year has a cell but no mean, so it plots as a null exactly like a year
    # with no cell at all: the line breaks and no point is drawn either way.
    drawn = [c if c and c.mean_norm is not None else None for _, c in spanned]
    colour = _PALETTE[0]
    fig.add_trace(pgo.Scatter(
        x=[str(year) for year, _ in spanned],
        y=[1 - c.mean_norm if c else None for c in drawn],
        customdata=[[numfmt.score(1 - c.mean_norm), c.events] if c else [None, None]
                    for c in drawn],
        name=pilot_name,
        mode="lines+markers+text",
        text=[f"{c.events} ev" if c else "" for c in drawn],
        textposition="top center",
        textfont=dict(color="#9ca3af", size=11),
        line=dict(width=1, dash="dash", color=colour),
        marker=dict(size=12, symbol="circle-open", line=dict(width=2.5, color=colour)),
        # Let a marker and its label at the very top (a perfect 1.0 season) draw over
        # the axis edge rather than being clipped out of the plot.
        cliponaxis=False,
        hovertemplate=f"%{{x}} · {pilot_name} · %{{customdata[0]}} · %{{customdata[1]}} events<extra></extra>",
    ))
    # The score's sense rides the readout (score() -> "0.62 (1 = 1st)"), stated once,
    # so the axis title names the quantity without restating which end is a win.
    _style_trend_chart(fig, f"Pilot performance: {pilot_name}", "Mean finish")
    # A bounded 0-1 score, not a share, so a plain decimal axis over the full range,
    # overriding the shared styler's percent format and auto-zoom.
    fig.update_yaxes(tickformat=numfmt.SCORE_TICKFORMAT, range=[0, 1], autorange=False)
    # A refused year and a year the pilot sat out both leave an empty tick, so the
    # refused ones are captioned with what was refused and why. Without it the chart
    # re-creates at the display layer the very conflation the tool was changed to
    # end: a bare gap says only "nothing plotted", where "1 ev" says the pilot turned
    # up and one event is not a season. Captioned under the axis rather than in the
    # plot, so it can never be read as a position on the score.
    for year, cell in spanned:
        if cell and cell.mean_norm is None:
            fig.add_annotation(
                x=str(year), y=0, xref="x", yref="paper", yshift=-32,
                text="played, unscored" if not cell.events else f"{cell.events} ev, too thin",
                showarrow=False, font=dict(color="#9ca3af", size=10),
            )
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
    size (``5/143`` is a 5th read against a field of 143): the placement and the
    tournament size the score is normalised against, the two numbers the plotted
    score is computed from. The placement is not always the pilot's alone: at the 4
    teams events every one is shared between the 1 to 11 pilots on a team. That
    denominator is the field the finish was ranked against, which is not an entrant
    count: it counts teams at 4 events and ranking slots at 5 more, and sits below
    the number of pilots who entered at 10 of 108 events. The points are the data; the thin dashed line only joins them and
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
    def flip(norm):
        return None if norm is None else 1 - norm
    points = [(c.date, flip(c.norm_a), flip(c.norm_b)) for c in cells]
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
            customdata=[[numfmt.score(1 - norm), numfmt.count_of(placement, field)]
                        if norm is not None else [None, None]
                        for _, placement, norm, field in points],
            name=name,
            mode="lines+markers",
            line=dict(width=1, dash="dash", color=colour),
            marker=dict(size=12, symbol="circle-open", line=dict(width=2.5, color=colour)),
            cliponaxis=False,
            hovertemplate=(
                f"%{{x|%d %b %Y}} · {name} · %{{customdata[0]}} · "
                "%{customdata[1]}<extra></extra>"
            ),
        ))
    # The finish's sense rides the readout (score() -> "0.62 (1 = 1st)"), stated once,
    # so the axis title names the quantity without restating which end is a win.
    _style_trend_chart(fig, f"Head-to-head: {name_a} vs {name_b}", "Finish")
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
    fig.update_yaxes(tickformat=numfmt.SCORE_TICKFORMAT, range=[0, 1], autorange=False)
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

    # Key -> display label, for the callbacks that name an entity in a chart title
    # or a note. Both keyed off the full catalogue: since #119 one shared subject
    # dropdown per tab feeds every view (the full pilot/card list), so a label
    # lookup must cover every entity the dropdown offers, not a per-view subset.
    pilot_labels = {key: label for label, key in pilots}
    card_names = {canon: label for label, canon in cards}
    archetype_labels = {key: label for label, key in archetypes}

    # The trend surface reads the full matrix once (a static, read-only graph), so
    # the manual panel can list the archetypes and each draw just filters it. The
    # tool never sees the cut; it returns everything (ADR 0013).
    trend_series = run_series(catalogue, MetaShareOverTime())
    # Every archetype is offered: meta share carries no floor, so each one draws a
    # line of real shares and real zeros with nothing withheld (ADR 0013). Unlike
    # the gem view and the pilot trend, which offer only slices their floor can
    # answer for, there is nothing here a pick can land on that cannot be drawn.
    # Sorted on the whole pair, not the name alone: two archetypes can share a
    # display name, and a name-only key would leave their order to the set's
    # iteration order, so the dropdown would list them differently per process.
    trend_archetypes = _distinguish(sorted(
        {(c.archetype, c.tag) for c in trend_series.cells}
    ))

    # The year the cut ranks on, read from the data so it follows the graph forward
    # rather than being pinned; named here only to say so in the chart title and the
    # radio's label, since "top 50%" means nothing without the year it is 50% of. The
    # same helper the cut ranks with, so the title cannot name a different year.
    latest_year = latest_deck_year(trend_series)

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
    # `run_graph`) rather than precomputed like the whole meta matrix. The tab's
    # shared card is the primary line; more cards can be overlaid to compare, one
    # tool call each, so the compare control is a multi-select (issue #119).
    def draw_adoption(primary: str, extra: list[str], board: str):
        # The subject leads: with no subject card nothing draws, even if compare
        # cards are picked, so the chart never shows a lead line the visitor did not
        # choose as the subject (issue #119).
        if not primary:
            return gr.update(visible=False)
        # Then the compare picks, deduped so a card chosen in both is one line, order
        # preserved so the subject stays the first trace.
        seen: set[str] = set()
        canons = [
            c for c in [primary, *(extra or [])]
            if c and not (c in seen or seen.add(c))
        ]
        conn = ladybug.Connection(db)
        series = [
            (card_names[canon], run_series(conn, CardAdoptionOverTime(canon, board or None)))
            for canon in canons
        ]
        return gr.update(
            value=_adoption_figure(series, _BOARD_LABELS[board]), visible=True
        )

    def draw_performance(pilot: str):
        # Return the chart and a refusal note. The shared pilot dropdown offers the
        # full catalogue (#119), so a pilot short of two averageable years reaches
        # here; rather than a silent blank it gets the same "refused, not a dot" note
        # head-to-head uses. Phrased from the qualifying-year count itself, so raising
        # the floor cannot leave it asserting a pilot has none when they have some
        # (issue #101).
        if not pilot:
            return gr.update(visible=False), gr.update(visible=False)
        try:
            series = run_series(ladybug.Connection(db), PilotPerformanceOverTime(pilot))
        except NotEnoughHistory as e:
            had = "no year" if not e.found else f"only {e.found} year" + ("" if e.found == 1 else "s")
            return gr.update(visible=False), gr.update(
                value=(
                    f"{pilot_labels[pilot]} has {had} with enough events to average, "
                    "so there is no performance trend to trace over time."
                ),
                visible=True,
            )
        # The refusal above is the only way the tool declines, so an empty series
        # should not arise; guard anyway so a drift between the two floor queries
        # hides the chart rather than crashing `_performance_figure` on `cells[0]`.
        if not series.cells:
            return gr.update(visible=False), gr.update(visible=False)
        return (
            gr.update(value=_performance_figure(pilot_labels[pilot], series), visible=True),
            gr.update(visible=False),
        )

    # Head-to-head offers every pilot in both slots, since the drawable set is
    # pairwise and too large to precompute; a pair that shares too few events is
    # refused with a message rather than drawn as a dot (ADR 0013).
    def draw_head_to_head(a: str, b: str):
        # Hide both the chart and the note until a valid pair is picked; the note is
        # the "refused, not a dot" surface for a pair the tool comes back empty on.
        if not a or not b:
            return gr.update(visible=False), gr.update(visible=False)
        if a == b:
            return gr.update(visible=False), gr.update(
                value="Pick two different pilots to see their rivalry.", visible=True
            )
        try:
            series = run_series(ladybug.Connection(db), HeadToHeadTimeline(a, b))
        except NotEnoughHistory as e:
            # The refusal carries the shared events it found, so the note says how
            # many rather than lumping every refused pair together: a single meeting
            # is a fact, and "fewer than two" hid it (issue #101). Phrased from the
            # number itself, so raising MIN_SHARED_EVENTS cannot leave this asserting
            # that a pair who did meet never did.
            met = (
                "have never met" if not e.found
                else f"share only {e.found} event" + ("s" if e.found > 1 else "")
            )
            return gr.update(visible=False), gr.update(
                value=(
                    f"{pilot_labels[a]} and {pilot_labels[b]} {met}, so there is no "
                    "rivalry to trace over time."
                ),
                visible=True,
            )
        # The in-chart range slider does the time-range slice client-side, so the
        # callback draws the whole rivalry and never re-filters by date here.
        fig = _head_to_head_figure(pilot_labels[a], pilot_labels[b], series)
        return gr.update(value=fig, visible=True), gr.update(visible=False)

    def _graph_meta(view: str, values: dict) -> tuple[str, list[str]]:
        # The subject a graph result is about and the reader-language filters under
        # which it ran, for the result header (#110). Written from the display labels
        # the dropdowns carry, never the raw keys, so the caption reads like the
        # controls above it. Reached only after `_spec` confirmed the subject is set.
        match view:
            case "pilot_neighbourhood":
                second = [f"vs {pilot_labels[values['pilot2']]}"] if values["pilot2"] else []
                return pilot_labels[values["pilot"]], second
            case "pilot_affinity":
                return pilot_labels[values["pilot"]], []
            case "card_usage":
                # Named through _BOARD_LABELS ("main" / "side"), the same casing the
                # adoption chart renders the board in, and no `_Avoid_` word.
                board = [_BOARD_LABELS[values["card_board"]]] if values["card_board"] else []
                return card_names[values["card"]], board
            case "card_cooccurrence":
                filters = []
                if values["cooccur_card2"]:
                    filters.append(f"with {card_names[values['cooccur_card2']]}")
                filters.append(f"top {int(_num(values['cooccur_top_n'], 15))}")
                if values["cooccur_drop_lands"]:
                    filters.append("lands filtered out")
                return card_names[values["card"]], filters
            case "meta_gems":
                # The archetype is optional; with none picked the gems span the format.
                archetype = values["gem_archetype"]
                return (archetype_labels[archetype] if archetype else "the format"), []
        return "", []

    def run_graph(view: str, values: dict) -> str:
        # A graph view's button hands its view id and the values it surfaces; _spec
        # turns them into a query, or None (returns the prompt) until the subject is
        # picked. The graph and chart pipelines stay separate: this renders a
        # subgraph, never a Series (ADR 0013).
        spec = _spec(view, values)
        if spec is None:
            return _PROMPT
        try:
            subgraph = run_query(ladybug.Connection(db), spec)
        except SliceTooSmall as e:
            return _note(f"{e}, so no gem claim is made here.")
        # A result too big to draw refuses with its own node count and narrowing
        # hints, so it carries no page-type header: a second "N nodes" caption above
        # it would read as if N had been drawn (#110).
        plan = assess(subgraph)
        if not plan.render:
            return _refine_alert(plan)
        # A drawn or empty result is framed in page type before it is shown (#110): a
        # title and caption naming the view, its subject, the filters, and how much
        # came back, so no result is left as an unlabelled graph. Empty reads 0 nodes.
        subject, filters = _graph_meta(view, values)
        header = _result_header(view, subject, filters, plan.node_count)
        if not subgraph.nodes:
            return header + _note("Nothing matched. The query ran and came back empty.")
        return header + _embed(render_subgraph(subgraph))

    def _toggle(groups: dict, chosen: str) -> list:
        """Show the chosen view's group, hide the rest: the per-tab view picker."""
        return [gr.update(visible=view_id == chosen) for view_id in groups]

    with gr.Blocks(
        title="7 Point Highlander Graph",
        theme=theme.dark_theme(),
        css=theme.build_css(),
        js=theme.FORCE_DARK_JS,
    ) as demo:
        gr.Markdown("# 7 Point Highlander Graph")

        # The app is organised by subject (issue #119): each tab picks its subject
        # once at the top, then a view picker swaps between that subject's graph and
        # chart views. Every view is its own group, shown only when the picker names
        # it; the shared subject sits above the groups so it carries across the swap.
        with gr.Tab("Pilots"):
            gr.Markdown(
                "Explore a pilot across their graph and their trends. Pick a pilot "
                "once, then switch views; head-to-head takes a second pilot of its "
                "own.",
                elem_classes="t-lede",
            )
            # The picker opens on the first view; each group's initial visibility is
            # tied to that same default, so reordering the tab map cannot leave the
            # picker naming one view while another's controls show (code review #4).
            pilots_default = next(iter(_PILOTS_TAB))
            # Subject, then view: the two shared controls sit together above the
            # per-view filters (§ controls order, #110). Held in one group so they
            # read as a unit and stay put as the view picker swaps the filters below.
            with gr.Group():
                pilot = gr.Dropdown(
                    choices=pilots, label="Pilot", value=None,
                    elem_classes="primary-control",
                )
                pilots_view = gr.Dropdown(
                    choices=_picker(_PILOTS_TAB), value=pilots_default, label="View",
                )

            with gr.Group(visible=pilots_default == "pilot_neighbourhood") as g_pilot_neighbourhood:
                gr.Markdown(
                    "One pilot's neighbourhood: the decks they piloted and the "
                    "archetypes those decks carried. Add a second pilot to compare "
                    "the two neighbourhoods. Click a node for its details; a deck "
                    "links out to Moxfield."
                )
                nb_pilot2 = gr.Dropdown(
                    choices=pilots, value=None,
                    label="Second pilot (optional, for head-to-head)",
                )
                nb_go = gr.Button("Draw", variant="primary")
                nb_out = gr.HTML(_PROMPT, elem_classes="result-region")

            with gr.Group(visible=pilots_default == "pilot_affinity") as g_pilot_affinity:
                gr.Markdown(
                    "How strongly a pilot leans on each archetype, across the decks "
                    "they piloted. Click a node for its details; a deck links out to "
                    "Moxfield."
                )
                af_go = gr.Button("Draw", variant="primary")
                af_out = gr.HTML(_PROMPT, elem_classes="result-region")

            with gr.Group(visible=pilots_default == "pilot_performance") as g_pilot_performance:
                gr.Markdown(
                    "A pilot's mean finish per year, over every year they played, "
                    "a year being the UTC year the lists were registered in rather "
                    "than a confirmed event date, drawn so higher is better (1 is "
                    "a win, 0 is last). A year with "
                    "only one event to average is left as a gap, an empty tick the "
                    "line breaks across, captioned with what it holds; a pilot short "
                    "of two averageable years draws nothing. Each point is labelled "
                    "with the "
                    "number of events it averages, and a dotted line marks the 0.5 "
                    "midpoint. The points are the data; the thin dashed line only joins "
                    "them and asserts no direction."
                )
                # The "refused, not a dot" surface for a pilot short of averageable
                # years, mirroring head-to-head; hidden until a pick lands on it.
                performance_note = gr.Markdown(visible=False, elem_classes="result-region")
                performance_plot = gr.Plot(visible=False, elem_classes="result-region")

            with gr.Group(visible=pilots_default == "pilot_h2h_timeline") as g_pilot_h2h:
                gr.Markdown(
                    "Two pilots' rivalry over the events they both entered: each "
                    "point is one shared event, the finish on the y-axis drawn so "
                    "higher is better (1 is a win, 0 is last), the same scale as the "
                    "pilot-performance chart. The x-axis is the registration date, so "
                    "two events shared in one year sit apart. Hover a point for the "
                    "raw finish over the field size (the placement, which other "
                    "pilots at the same event can share, and the tournament size "
                    "the score is normalised against). Drag the range slider "
                    "under the chart to slice a time range. A pair sharing fewer than "
                    "two events is a dot, not a timeline, so it is refused rather than "
                    "drawn."
                )
                h2h_pilot_b = gr.Dropdown(choices=pilots, value=None, label="Second pilot")
                # The "refused, not a dot" surface for a pair with too few shared
                # events; hidden until a pick lands on it.
                h2h_note = gr.Markdown(visible=False, elem_classes="result-region")
                h2h_plot = gr.Plot(visible=False, elem_classes="result-region")

            pilots_groups = {
                "pilot_neighbourhood": g_pilot_neighbourhood,
                "pilot_affinity": g_pilot_affinity,
                "pilot_performance": g_pilot_performance,
                "pilot_h2h_timeline": g_pilot_h2h,
            }
            pilot_chart_outs = [performance_plot, performance_note, h2h_plot, h2h_note]

            def draw_pilot_chart(view: str, p: str, pb: str):
                # Draw only the chart the picker currently shows, so selecting the
                # shared pilot never runs the other view's query into a hidden plot
                # (code review #1). Each chart returns its plot and its note.
                if view == "pilot_performance":
                    return (*draw_performance(p), gr.update(), gr.update())
                if view == "pilot_h2h_timeline":
                    return (gr.update(), gr.update(), *draw_head_to_head(p, pb))
                return (gr.update(),) * 4

            def switch_pilot(view: str, p: str, pb: str):
                # Show the chosen view and draw it fresh for the current pilot, so a
                # view switched to with a pilot already picked opens drawn.
                return [*_toggle(pilots_groups, view), *draw_pilot_chart(view, p, pb)]

            def pick_pilot(view: str, p: str, pb: str):
                # A new pilot leaves the button-drawn graphs showing the old one, so
                # they drop back to the prompt (re-Draw for the new pilot) while the
                # active chart redraws (code review #3).
                return [_PROMPT, _PROMPT, *draw_pilot_chart(view, p, pb)]

            pilots_view.change(
                switch_pilot, inputs=[pilots_view, pilot, h2h_pilot_b],
                outputs=[*pilots_groups.values(), *pilot_chart_outs],
            )
            pilot.change(
                pick_pilot, inputs=[pilots_view, pilot, h2h_pilot_b],
                outputs=[nb_out, af_out, *pilot_chart_outs],
            )
            # The second pilot only feeds head-to-head, which is the shown view when
            # its control is reachable, so it just redraws that chart.
            h2h_pilot_b.change(
                draw_head_to_head, inputs=[pilot, h2h_pilot_b], outputs=[h2h_plot, h2h_note],
            )
            nb_go.click(
                lambda p, p2: run_graph("pilot_neighbourhood", {"pilot": p, "pilot2": p2}),
                inputs=[pilot, nb_pilot2], outputs=nb_out,
            )
            af_go.click(
                lambda p: run_graph("pilot_affinity", {"pilot": p}),
                inputs=pilot, outputs=af_out,
            )

        with gr.Tab("Cards"):
            gr.Markdown(
                "Explore a card across its graph and its trends. Pick a card once, "
                "then switch views.",
                elem_classes="t-lede",
            )
            cards_default = next(iter(_CARDS_TAB))
            with gr.Group():
                card = gr.Dropdown(
                    choices=cards, label="Card", value=None,
                    elem_classes="primary-control",
                )
                cards_view = gr.Dropdown(
                    choices=_picker(_CARDS_TAB), value=cards_default, label="View",
                )

            with gr.Group(visible=cards_default == "card_usage") as g_card_usage:
                gr.Markdown(
                    "Where a card shows up: the decks running it and the archetypes "
                    "those decks carry, scoped to a board. Click a node for its "
                    "details; a deck links out to Moxfield."
                )
                usage_board = gr.Dropdown(choices=_BOARD_CHOICES, label="Board", value="")
                usage_go = gr.Button("Draw", variant="primary")
                usage_out = gr.HTML(_PROMPT, elem_classes="result-region")

            with gr.Group(visible=cards_default == "card_cooccurrence") as g_card_cooccurrence:
                gr.Markdown(
                    "The cards that share decks with the chosen card: its most common "
                    "companions. Add a second card to see a shared package, filter "
                    "out lands, or widen the cut. Click a node for its details."
                )
                co_card2 = gr.Dropdown(
                    choices=cards, value=None,
                    label="Second card (optional, for shared packages)",
                )
                co_top_n = gr.Dropdown(
                    choices=[5, 15, 25], value=15, label="Top cards by co-occurrence rate",
                )
                co_drop_lands = gr.Checkbox(value=False, label="Filter out lands")
                co_go = gr.Button("Draw", variant="primary")
                co_out = gr.HTML(_PROMPT, elem_classes="result-region")

            with gr.Group(visible=cards_default == "card_adoption") as g_card_adoption:
                gr.Markdown(
                    "How a card's adoption moves across the years: the decks running "
                    "it as a share of that year's decks, a year being the UTC "
                    "year the lists were registered in rather than a confirmed "
                    "event date. A low count is the signal "
                    "of a card entering the format, not noise, so nothing is "
                    "withheld; a year a card sat out reads as a real zero. Add more "
                    "cards to compare. Hover a point for its raw count over the "
                    "year's total decks."
                )
                adoption_extra = gr.Dropdown(
                    choices=cards, value=[], multiselect=True,
                    label="Compare with other cards (optional)",
                )
                adoption_board = gr.Dropdown(choices=_BOARD_CHOICES, value="", label="Board")
                adoption_plot = gr.Plot(visible=False, elem_classes="result-region")

            cards_groups = {
                "card_usage": g_card_usage,
                "card_cooccurrence": g_card_cooccurrence,
                "card_adoption": g_card_adoption,
            }
            def draw_card_chart(view: str, c: str, extra: list[str], board: str):
                # Adoption is the tab's only chart, drawn only when it is the shown
                # view, so selecting the shared card does not run it while a graph
                # view is up (code review #1).
                if view == "card_adoption":
                    return draw_adoption(c, extra, board)
                return gr.update()

            def switch_card(view: str, c: str, extra: list[str], board: str):
                return [*_toggle(cards_groups, view), draw_card_chart(view, c, extra, board)]

            def pick_card(view: str, c: str, extra: list[str], board: str):
                # A new card leaves the button-drawn graphs on the old one, so they
                # drop back to the prompt while adoption (if shown) redraws (#3).
                return [_PROMPT, _PROMPT, draw_card_chart(view, c, extra, board)]

            cards_view.change(
                switch_card, inputs=[cards_view, card, adoption_extra, adoption_board],
                outputs=[*cards_groups.values(), adoption_plot],
            )
            card.change(
                pick_card, inputs=[cards_view, card, adoption_extra, adoption_board],
                outputs=[usage_out, co_out, adoption_plot],
            )
            # Adoption's own controls only ever change while adoption is the shown
            # view, so they just redraw it.
            for control in (adoption_extra, adoption_board):
                control.change(
                    draw_adoption, inputs=[card, adoption_extra, adoption_board],
                    outputs=adoption_plot,
                )
            usage_go.click(
                lambda c, b: run_graph("card_usage", {"card": c, "card_board": b}),
                inputs=[card, usage_board], outputs=usage_out,
            )
            co_go.click(
                lambda c, c2, n, dl: run_graph(
                    "card_cooccurrence",
                    {"card": c, "cooccur_card2": c2, "cooccur_top_n": n,
                     "cooccur_drop_lands": dl},
                ),
                inputs=[card, co_card2, co_top_n, co_drop_lands], outputs=co_out,
            )

        # Meta has no single subject entity, so it is a view picker only: the meta
        # share chart and the archetype-entered hidden-gems graph (v1 §11 places
        # gems here, beside meta share, not under Cards).
        with gr.Tab("Meta"):
            gr.Markdown(
                "The metagame over time, and the hidden gems within it.",
                elem_classes="t-lede",
            )
            meta_default = next(iter(_META_TAB))
            # Meta carries no subject entity, so the group holds the view picker
            # alone; it still sits apart from the per-view filters below it.
            with gr.Group():
                meta_view = gr.Dropdown(
                    choices=_picker(_META_TAB), value=meta_default, label="View",
                )

            with gr.Group(visible=meta_default == "meta_share") as g_meta_share:
                gr.Markdown(
                    "Each archetype's share of the meta, per year, a year being the "
                    "UTC year the lists were registered in rather than a confirmed "
                    "event date. The points are the data; the thin dashed line only "
                    "joins them and asserts no trend between years. Every year is "
                    "stated, including the thin ones an archetype enters or leaves "
                    "the format on, and a year it was absent is a real zero. That "
                    "zero is a smaller claim than it looks: the share is by primary "
                    "archetype, so it says no deck led with the archetype that "
                    "year, not that none carried it. Decks are grouped by the "
                    "source's classification as of the latest fetch, applied to "
                    "every year alike, so a rerun after a refresh can restate a "
                    "past year: over the two fetches held here, 723 of 4553 decks "
                    "were rewritten in 5 days and 16 changed primary archetype, "
                    "moving 17 of 504 cells (0 of 56 at the default cut). Hover a "
                    "point for its share and deck count, the sample the share came "
                    "from."
                )
                cut = gr.Radio(
                    list(_CUTS), value=_DEFAULT_CUT,
                    label=f"Archetypes to show (by share of {latest_year} decks)",
                )
                cut_plot = gr.Plot(value=draw_cut(_DEFAULT_CUT), elem_classes="result-region")
                manual = gr.Dropdown(
                    choices=trend_archetypes, value=[], multiselect=True,
                    label="Or focus on specific archetypes",
                )
                # Hidden until a pick is made, so the view opens on the cut chart.
                manual_plot = gr.Plot(visible=False, elem_classes="result-region")

            with gr.Group(visible=meta_default == "meta_gems") as g_meta_gems:
                gr.Markdown(
                    "Under-the-radar cards for an archetype: cards that over-index in "
                    "the archetype's decks against the wider format. Click a node for "
                    "its details; a deck links out to Moxfield."
                )
                gem_archetype = gr.Dropdown(
                    choices=archetypes, label="Archetype (optional)", value=None,
                )
                gem_go = gr.Button("Draw", variant="primary")
                gem_out = gr.HTML(_PROMPT, elem_classes="result-region")

            meta_groups = {"meta_share": g_meta_share, "meta_gems": g_meta_gems}
            meta_view.change(
                lambda v: _toggle(meta_groups, v),
                inputs=meta_view, outputs=list(meta_groups.values()),
            )
            cut.change(draw_cut, inputs=cut, outputs=cut_plot)
            manual.change(draw_manual, inputs=manual, outputs=manual_plot)
            gem_go.click(
                lambda a: run_graph("meta_gems", {"gem_archetype": a}),
                inputs=gem_archetype, outputs=gem_out,
            )

    return demo
