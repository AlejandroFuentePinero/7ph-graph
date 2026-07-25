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

from graph7ph import numfmt, palette, theme
from graph7ph.db import open_database
from graph7ph.explore import RenderPlan, assess, dominant_kind
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

def _state_message(text: str) -> str:
    """The one on-theme treatment every state with nothing (or not yet) to draw shares
    (#114): the message set in the app's own body type role (§3) on the tokens, in place
    of the retired hand-styled inline divs (``style='padding:1rem;font-family:sans-serif'``)
    that overrode the theme font with a system sans. The nothing-picked prompt, the empty
    result, the ``SliceTooSmall`` refusal, and the too-large refine alert all speak through
    this, so the app has one voice when it has nothing to draw, reading the same as the
    Markdown refusal notes (the same ``.t-body`` type role as ``.prose p``). One short line
    in the interface's voice; the message is free text, so it is escaped into the markup."""
    return f"<div class='t-state t-body'>{html.escape(text)}</div>"


# The run_graph fallback for a spec that cannot be built (a subject not yet chosen).
# With the results stack hidden until a Draw with a subject, this is unreachable in
# normal flow; kept as a defensive value so a stray call renders something, not None.
_PROMPT = _state_message("Pick an entity and filters, then Draw.")

# The guidance that used to sit as a duplicated empty-prompt card in every plot region
# now rides the subject dropdown's help text (§14, user feedback): one place, at the
# control you drive from, rather than a row of identical "nothing yet" boxes. Shown
# under the dropdown until a Draw fills the view's results stack.
_PICK_PILOT = "Pick a pilot, then Draw."
_PICK_PILOT2 = "Required: pick a second pilot to compare."
_PICK_CARD = "Pick a card, then Draw to see its plots."
_PICK_ARCHETYPE = "Pick an archetype, then Draw to see its hidden gems."

# The app is organised by subject, not by render modality (issue #119, v1 §11).
# Since #126 each of Pilots and Cards collapses to two views, and one Draw per view
# fans out to all of that view's plots (a subgraph query and a series query stay two
# seams under the hood, ADR 0013; only the presentation combines). Each multi-view
# tab is an ordered map of view id to the label the picker shows, held as data so
# the tests can assert the two-view shape. Meta and Hidden gems are single-view
# (since #125 promoted gems to its own top-level tab), so they carry no picker and
# are built inline; the gems query id `meta_gems` is kept (a navigation move, not a
# query change).
_PILOTS_TAB: dict[str, str] = {
    "pilot_overview": "Pilot overview",
    "pilot_head_to_head": "Head-to-head",
}
_CARDS_TAB: dict[str, str] = {
    "card_overview": "Card overview",
    "card_cooccurrence": "Co-occurrence",
}

# The picker choices for each tab, as (label, view id) pairs.
def _picker(tab: dict[str, str]) -> list[tuple[str, str]]:
    return [(label, view_id) for view_id, label in tab.items()]


# The reader-language name of each graph plot, keyed by its query id. A view now
# holds several plots (#126), so a drawn subgraph is titled by the plot it draws
# (neighbourhood, affinity, usage, co-occurrence, gems), not by the view it sits in.
# The trend plots title themselves through `_chart_heading`. These ids double as the
# query keys `_spec` and `_graph_filters` dispatch on, so every query the two-view shape
# reaches keeps a heading here.
_PLOT_LABELS: dict[str, str] = {
    "pilot_neighbourhood": "Neighbourhood",
    "pilot_affinity": "Archetype affinity",
    "card_usage": "Usage",
    "card_cooccurrence": "Co-occurrence",
    "meta_gems": "Hidden gems",
}


def _result_header(plot: str, filters: list[str], node_count: int) -> str:
    """Frame a query result as an insight-card head (§12/§14): the plot type alone as
    the title (the subject is stated once above the cards, §14, not echoed here), the
    filters and how many nodes came back as the caption, so an answer is never left as
    an unlabelled graph. Prepended to the drawn result, the empty state, and the refine
    alert alike, so every post-query state speaks the same way. The filters are display
    labels (free text), so they are escaped into the markup."""
    # A drawn result is under the render threshold (250 nodes), so the count needs no
    # thousands separator; the refine alert carries the large counts.
    tail = f"{node_count} node" + ("" if node_count == 1 else "s")
    caption = " · ".join([*filters, tail])
    return (
        f"<div class='t-result-title'>{html.escape(_PLOT_LABELS[plot])}</div>"
        f"<div class='t-caption'>{html.escape(caption)}</div>"
    )


def _chart_heading(title: str, caption: str | None = None, caption_html: str | None = None) -> str:
    """A chart's title as an insight-card head in the insight-title type role (§3/§12).

    The trend charts' titles leave the Plotly figure (where they were font baked into
    an image) and become a heading the app draws above the plot, so a chart reads as a
    titled answer on the page. The title names the plot type only (§14) and takes an
    optional caption for a filter qualifier (a board, a cut). Title and ``caption`` are
    free-text display labels, so they are escaped into the markup, exactly as
    :func:`_result_header` frames a graph result. ``caption_html`` is the escape hatch for
    a caption the app builds itself (the performance chart's field-standing line, with the
    share emphasised): app-generated numerics, no user free-text, so it is inserted as
    trusted markup and takes precedence over the plain ``caption``."""
    head = f"<div class='t-result-title'>{html.escape(title)}</div>"
    if caption_html:
        head += caption_html
    elif caption:
        head += f"<div class='t-caption'>{html.escape(caption)}</div>"
    return head


def _subject_line(prefix: str, *names: str) -> str:
    """The subject stated once for a whole result set (§14), above the insight cards.

    A single line naming what the answer is about (the pilot, the card pair, the
    archetype), so the individual insight titles never repeat it. The prefix is the
    subject's kind in reader language ("Pilot", "Card", "Head-to-head"); the names are
    free-text display labels, escaped, joined by "vs" for the two-pilot case."""
    inner = " vs ".join(
        f"<span class='subject-name'>{html.escape(n)}</span>" for n in names
    )
    return f"<div class='subject-line'>{html.escape(prefix)} {inner}</div>"


def _subject_update(prefix: str, name: str | None):
    """A ``gr.update`` that shows the subject line for a single-subject view, or hides
    it when no subject is chosen (§14). The pilot, card, and gems views share this;
    head-to-head builds its own two-name line inline, since it names a pair."""
    if not name:
        return gr.update(visible=False)
    return gr.update(value=_subject_line(prefix, name), visible=True)


# Every graph plot shares one frame height (§12). The pilot neighbourhood renders well
# at this size, and a uniform frame reads as one coherent canvas across the tabs rather
# than each plot jumping to its own node-count-scaled height. Tall enough that a dense
# graph lays out legibly, not so tall that a small one floats in emptiness.
GRAPH_HEIGHT = 760


def _embed(doc: str) -> str:
    """Wrap a standalone pyvis document in an iframe so its scripts run, at the shared
    graph frame height (:data:`GRAPH_HEIGHT`).

    gr.HTML does not execute injected <script> tags, so the widget is isolated in
    an iframe via srcdoc (which the browser renders as its own document)."""
    srcdoc = html.escape(doc, quote=True)
    style = f"width:100%;height:{GRAPH_HEIGHT}px;border:none"
    return f'<iframe srcdoc="{srcdoc}" style="{style}"></iframe>'


def _refine_alert(plan: RenderPlan) -> str:
    """The too-large-to-draw state, as one line in the app's voice (#114).

    A result over the render threshold is never drawn or truncated; rather than the old
    multi-paragraph inline-styled div with a ``<ul>`` of hints, it refuses in one short
    line through the shared on-theme treatment (:func:`_state_message`): the count, the
    kind flooding the view (the most numerous, so the reader narrows the axis that is
    actually oversized), the draw limit, and what to do. The count carries a thousands
    separator, since a refused result is well over the 250-node line."""
    dominant = dominant_kind(plan.by_kind)
    return _state_message(
        f"{plan.node_count:,} nodes (mostly {dominant}s) is over the "
        f"{plan.threshold}-node draw limit; narrow the query and Draw again."
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
            if not values["gem_archetype"]:
                return None
            return HiddenGems(values["gem_archetype"])
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


def _adoption_caption(board: str | None) -> str | None:
    """The adoption card's caption: the board the share is scoped to (§12/§14), or
    ``None`` when board-agnostic (#126).

    Card overview has a board control, so its caption names the board the share is
    scoped to (``""`` reads "main or side", the same either-board reading the query
    takes as no filter). Co-occurrence is board-agnostic: it has no board control, so
    ``board is None`` drops the qualifier entirely. The string "main or side" must
    never reach the co-occurrence plot, since there is no control to disambiguate it.
    The plot title itself is always "Adoption over time" (the plot type, §14); the
    board is a filter and rides the caption, never the title.
    """
    if board is None:
        return None
    return f"{_BOARD_LABELS[board].capitalize()} board"


def _adoption_cards(subject: str, second: str | None) -> list[str]:
    """The cards an adoption trend plots: the subject, then an optional second card.

    The subject leads (its trace draws first and keeps its colour, §5), and with no
    subject nothing draws even if a second is chosen. A second card equal to the
    subject collapses to one line. This is the only multi-card compare (#126): the
    arbitrary overlay is gone, so the list holds at most two cards.
    """
    if not subject:
        return []
    if second and second != subject:
        return [subject, second]
    return [subject]


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


# The chart chrome, drawn once from the design tokens (§2/§6) so a hardcoded grey
# can never assume a background the app no longer inherits: the gridline is the
# hairline border token, the axis, ticks, and font the muted token. Concrete hexes
# rather than `var(--token)` because Plotly draws the chart as SVG the CSS custom
# properties never reach.
_GRID = theme.TOKENS["border"]
_AXIS = theme.TOKENS["text-mute"]
_SURFACE = theme.TOKENS["surface"]


def _observation_marker(colour: str) -> dict:
    """A hollow observation marker (ADR 0013) on a 2px surface ring (§6).

    The points are the observations, so they read as hollow rings in the series
    colour. The ring is a filled marker whose fill is the chart surface: on the
    surface it reads hollow, but where two markers overlap the top one's surface
    fill occludes the ring beneath it rather than letting the two rings cross into
    mud. The 2px outline is the series colour; the thin dashed join stays the
    caller's line, which only joins the points and asserts no trend between them.
    """
    return dict(size=12, symbol="circle", color=_SURFACE, line=dict(width=2, color=colour))


def _confidence_size(events: int) -> float:
    """Marker diameter growing with the events a year's mean is taken over.

    A two-event mean and a twenty-event one sit on the same line and read alike;
    sizing the ring by its event count puts the sample size the reader must weight
    into the marker itself, not only its printed label, so a thin year draws as a
    small dot the eye discounts on sight. Area (not diameter) tracks the count, the
    honest mapping, so the diameter goes as the square root; clamped so the thinnest
    year stays visible and the fattest does not swamp the plot.
    """
    return min(28.0, max(11.0, 8.0 + 3.2 * events ** 0.5))


def _style_trend_chart(fig: pgo.Figure, y_title: str) -> None:
    """The dark-theme styling both trend charts share (the meta and one card).

    Transparent backgrounds so the chart sits on the app's own surface rather than
    Plotly's white card, with the axis, ticks, and gridlines on the design tokens
    (§6). The title no longer rides the figure: it is a page heading the caller
    draws above the plot (§6), so the figure carries no Plotly-font title. Only the
    y-axis label differs between the two charts (a share of the meta, or a card's
    adoption), so it is passed in; the rest is held in one place so the two cannot
    drift apart. The caller adds its own legend, the one thing they do not share.
    """
    fig.update_layout(
        hovermode="closest",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=_AXIS), margin=dict(t=8, r=8, b=8, l=8),
    )
    # Caution: this is a category x-axis. An annotation anchored with xref="x" and a
    # numeric-looking value (a year) does NOT snap to the matching category slot; Plotly
    # places it at the linear coordinate (2024), off the categories, and autorange chases
    # it, crushing the real points into one edge. The performance chart hit exactly this
    # with its refusal captions and moved to a linear year axis (see `_performance_figure`);
    # if the meta/adoption charts ever gain an x-anchored annotation, do the same rather
    # than debugging the blow-up from scratch.
    fig.update_xaxes(
        title="Year", type="category", categoryorder="category ascending",
        gridcolor=_GRID, linecolor=_AXIS, zeroline=False,
    )
    # A trimmed two-decimal percent, not a rounded whole one: fringe shares sit
    # below 1% (a card in a handful of a 2000-deck year), and rounding to integer
    # percents would floor them to "0%" and collide adjacent ticks on one label.
    fig.update_yaxes(
        title=y_title, tickformat=numfmt.SHARE_TICKFORMAT, rangemode="tozero",
        gridcolor=_GRID, linecolor=_AXIS, zerolinecolor=_AXIS,
    )


def _trend_figure(series: Series, tags: list[str]) -> pgo.Figure:
    """A line chart of the chosen archetypes' meta share over time.

    One trace per archetype, with the data foregrounded: the points are the
    observations, so they are drawn large and hollow with a thick rim, while the
    connecting line is thin and dashed, a reminder that it only joins points and
    asserts no trend between them (ADR 0013). Every year draws a point: meta share
    carries no floor, so a thin year states its real share and a year the archetype
    was absent drops to a real zero, with no holes for the eye to read as zeros of
    its own. Each point's hover carries its year, share, and deck count N, the
    sample size the reader reasons with.

    ``tags`` is drawn in the order given, which is the caller's meaningful order: the
    cut passes them strongest-first, the manual panel in pick order. At eight or fewer
    lines each archetype takes a direct hue from the shared eight-hue set by entity
    (§5), assigned in that order, so a narrower cut (a prefix of a wider one) never
    repaints the survivors it shares (the reversal of ADR-0013's colour-by-position).
    Past eight the shared set is exhausted (the emphasis threshold, §6, a separate
    slice), so the ninth-plus fall back to the long palette by position rather than a
    None; that branch keeps the old rainbow until emphasis lands.

    Traces are keyed by tag, not by display name, because two tags can share a name
    (as ``SeriesCell`` says) and the rectangular matrix gives each of them a cell in
    every year: keyed by name they would merge into one trace holding two y values
    per year and draw as a sawtooth between two archetypes.
    """
    wanted = set(tags)
    by_tag: dict[str, list] = {}
    for cell in sorted(series.cells, key=lambda c: c.year):
        if cell.tag in wanted:
            by_tag.setdefault(cell.tag, []).append(cell)

    fig = pgo.Figure()
    # Drawn in the caller's order, keeping only tags that have cells. The shared
    # palette assigns the first eight by entity; past eight `assign` returns None (the
    # emphasis threshold), so the ninth-plus fall back to the long palette by position,
    # the same fallback the adoption chart uses.
    drawn = [t for t in tags if t in by_tag]
    slots = palette.assign(drawn)
    for i, tag in enumerate(drawn):
        cells = by_tag[tag]
        archetype = cells[0].archetype
        colour = slots.get(tag) or _PALETTE[i % len(_PALETTE)]
        fig.add_trace(pgo.Scatter(
            x=[str(c.year) for c in cells],
            y=[c.share for c in cells],
            customdata=[[numfmt.share(c.share), numfmt.count_of(c.n, c.year_total, "decks")]
                        for c in cells],
            name=archetype,
            mode="lines+markers",
            line=dict(width=1, dash="dash", color=colour),
            marker=_observation_marker(colour),
            hovertemplate=(
                f"%{{x}} · {archetype} · %{{customdata[0]}} · "
                "%{customdata[1]}<extra></extra>"
            ),
        ))
    _style_trend_chart(fig, "Share of meta")
    fig.update_layout(legend=dict(title="Archetype"))
    return fig


def _adoption_figure(cards: list[tuple[str, Series]]) -> pgo.Figure:
    """One or more cards' adoption (share of that year's decks) over the years.

    A trace per card, so several cards can be compared on one axis. At eight or fewer
    cards each takes a direct hue from the shared eight-hue set by entity (§5), the
    subject first, so a card keeps its colour as the compare set changes rather than
    repainting on its position; past eight the set is exhausted and the ninth-plus
    fall back to the long palette. Adoption carries no floor: every year
    is plotted, including the zeros of years a card sat out, so a line shows the
    card entering rather than skipping a gap (ADR 0013). Share, not raw count, is
    the y-value, because the year bases differ (a thin early year against a fat
    recent one) and a count line would read a bigger meta as more adoption; each
    point's hover carries the raw count over the year total so the sample size is
    in hand. As with the meta-share chart the points are drawn large and hollow and
    the connecting line thin and dashed, a reminder it only joins observations and
    asserts no trend between them. The board the count is scoped to rides in the
    page heading the caller draws above the chart, since it changes what the line means.
    """
    fig = pgo.Figure()
    # Each card takes a direct hue from the shared eight-hue set by entity (§5), the
    # subject first, so a card keeps its colour as the compare set changes. Past eight
    # cards `assign` returns None (the emphasis threshold, a separate slice), so the
    # ninth-plus fall back to the long palette rather than a None the figure chokes on.
    colours = palette.assign([name for name, _ in cards])
    for i, (card_name, series) in enumerate(cards):
        colour = colours.get(card_name) or _PALETTE[i % len(_PALETTE)]
        cells = sorted(series.cells, key=lambda c: c.year)
        fig.add_trace(pgo.Scatter(
            x=[str(c.year) for c in cells],
            y=[c.share for c in cells],
            customdata=[[numfmt.share(c.share), numfmt.count_of(c.count, c.year_total, "decks")]
                        for c in cells],
            name=card_name,
            mode="lines+markers",
            line=dict(width=1, dash="dash", color=colour),
            marker=_observation_marker(colour),
            hovertemplate=(
                f"%{{x}} · {card_name} · %{{customdata[0]}} · "
                "%{customdata[1]}<extra></extra>"
            ),
        ))
    _style_trend_chart(fig, "Adoption (share of decks)")
    fig.update_layout(legend=dict(title="Card"))
    return fig


def _performance_caption(series: Series) -> str:
    """The field-standing line drawn above the plot as its insight (§14), as HTML.

    The flat 0-to-1 axis keeps a pilot's real spread honest but reads as eventless, so
    the standing the plot is silent about is stated once here: the mean finish over
    every scored year, weighted by that year's events (a twenty-event season counts ten
    times a two-event one), phrased as the share of the field it beat, since the flipped
    score is exactly that (a 0.74 mean beat ~74% of the field). The share is rounded to a
    whole percent (a mean over a handful of events does not carry two decimals) and set in
    the accent so the eye lands on it first; the event total and scored-year count trail
    quiet as the sample it rests on. Returned as trusted markup (all app-built numerics,
    no user text) for :func:`_chart_heading`'s ``caption_html``; empty when no year
    cleared the floor, which the caller's refusal note has already covered.
    """
    scored = [c for c in series.cells if c.mean_norm is not None]
    if not scored:
        return ""
    total = sum(c.events for c in scored)
    mean_score = sum((1 - c.mean_norm) * c.events for c in scored) / total
    years = "year" if len(scored) == 1 else "years"
    return (
        "<div class='t-fieldstat'>Finishes ahead of "
        f"<span class='pct'>{round(mean_score * 100)}%</span> of the field on average"
        f"<span class='sample'> · {total} events over {len(scored)} scored {years}</span>"
        "</div>"
    )


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
    # A single-series chart: the one entity takes the palette's first slot (§5), a
    # direct colour by entity, not a position in a rank.
    colour = palette.CATEGORICAL[0]
    fig.add_trace(pgo.Scatter(
        # Numeric years on a linear axis, not category strings: the refusal captions below
        # are anchored to the x-axis, and Plotly places an annotation's x by value, so a
        # numeric-string year on a category axis lands at the linear coordinate 2024 (far
        # off the three category slots), dragging autorange out to it and crushing the real
        # markers into the opposite edge. Numeric years put caption and marker on one scale.
        x=[year for year, _ in spanned],
        y=[1 - c.mean_norm if c else None for c in drawn],
        customdata=[[numfmt.score(1 - c.mean_norm), c.events] if c else [None, None]
                    for c in drawn],
        name=pilot_name,
        mode="lines+markers+text",
        text=[f"{c.events} ev" if c else "" for c in drawn],
        textposition="top center",
        textfont=dict(color=_AXIS, size=11),
        line=dict(width=1, dash="dash", color=colour),
        # The hollow ring's size carries each year's event count, so the sample size
        # is read from the marker and not only its label; a null year takes the base
        # size but draws no marker.
        marker={**_observation_marker(colour),
                "size": [_confidence_size(c.events) if c else 12 for c in drawn]},
        # Let a marker and its label at the very top (a perfect 1.0 season) draw over
        # the axis edge rather than being clipped out of the plot.
        cliponaxis=False,
        hovertemplate=f"%{{x}} · {pilot_name} · %{{customdata[0]}} · %{{customdata[1]}} events<extra></extra>",
    ))
    # The y-axis names the quantity and its sense together, so a reader who never
    # hovers a point still knows which end is good (user feedback): the score inverts
    # the raw finish, so 1 is a win (first) and 0 is last.
    _style_trend_chart(fig, "Mean finish (1 = first, 0 = last)")
    # A bounded 0-1 score, not a share, so a plain decimal axis over the full range,
    # overriding the shared styler's percent format and auto-zoom.
    fig.update_yaxes(tickformat=numfmt.SCORE_TICKFORMAT, range=[0, 1], autorange=False)
    # A linear year axis overriding the shared styler's category axis (see the trace's x):
    # one tick per spanned year with a plain integer label (no thousands comma), and a
    # range pinned half a year past each end. The fixed range is the belt to the numeric
    # x's braces: even a stray annotation coordinate cannot stretch the axis, so the
    # markers keep their real spacing and a refusal caption cannot fling the plot open.
    years = [year for year, _ in spanned]
    fig.update_xaxes(
        type="linear", tickmode="array", tickvals=years, ticktext=[str(y) for y in years],
        range=[years[0] - 0.5, years[-1] + 0.5], autorange=False,
        # The hover reads %{x}, now a raw number on the linear axis; pin the year format
        # so the tooltip stays "2024" and never picks up a thousands separator ("2,024").
        hoverformat="d",
    )
    # A refused year and a year the pilot sat out both leave an empty tick, so the
    # refused ones are captioned with what was refused and why. Without it the chart
    # re-creates at the display layer the very conflation the tool was changed to
    # end: a bare gap says only "nothing plotted", where "1 ev" says the pilot turned
    # up and one event is not a season. Captioned under the axis rather than in the
    # plot, so it can never be read as a position on the score.
    for year, cell in spanned:
        if cell and cell.mean_norm is None:
            fig.add_annotation(
                x=year, y=0, xref="x", yref="paper", yshift=-32,
                text="played, unscored" if not cell.events else f"{cell.events} ev, too thin",
                showarrow=False, font=dict(color=_AXIS, size=10),
            )
    # A reference line at 0.5, a random finisher's expected normalised rank (the flip
    # leaves it at 0.5): above it beat the field, below it trailed. The field-standing
    # caption above the plot already names where the pilot sits, so the line stays a
    # quiet unlabelled reference rather than repeating it.
    fig.add_hline(y=0.5, line=dict(color=_rgba(_AXIS, 0.55), width=1, dash="dot"))
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
    # Two fixed lines, so each pilot takes a direct hue from the shared eight-hue set by
    # position-as-entity (§5): the pilot named first is slot 1, the second slot 2, never
    # a recycled wheel. Taken by slot rather than through `assign` keyed on the display
    # label, so two distinct pilot ids that happen to share a label still draw apart
    # (assign would dedup the shared label to one slot and collapse the two lines).
    colour_a, colour_b = palette.CATEGORICAL[0], palette.CATEGORICAL[1]

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
            marker=_observation_marker(colour),
            cliponaxis=False,
            hovertemplate=(
                f"%{{x|%d %b %Y}} · {name} · %{{customdata[0]}} · "
                "%{customdata[1]}<extra></extra>"
            ),
        ))
    # The finish's sense rides the readout (score() -> "0.62 (1 = 1st)"), stated once,
    # so the axis title names the quantity without restating which end is a win.
    _style_trend_chart(fig, "Finish")
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
    fig.add_hline(y=0.5, line=dict(color=_rgba(_AXIS, 0.55), width=1, dash="dot"))
    # A horizontal legend above the plot, not the shared styler's default right-side
    # one: an external right legend widens with the pilot names and eats into the plot
    # area, which drifts the paper-centred time-range label (above) and leaves it off
    # true centre. A top strip keeps the plot full-width and stable. The title is now a
    # page heading above the chart (§6), so the top margin only has to seat the centred
    # legend, not a title above it. Room below the axis for the slider band and its
    # label (the shared styler sets a tight b=8 for the label-free charts).
    fig.update_layout(
        legend=dict(
            title="Pilot", orientation="h",
            xanchor="center", x=0.5, yanchor="bottom", y=1.02,
        ),
        margin=dict(t=48, b=90),
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

    def draw_cut(cut_label: str):
        # The title is a page heading above the plot now (§6), returned beside the
        # figure. The cut's tags stay in rank order (strongest first) so a narrower
        # cut is a prefix of a wider one and the survivors keep their colour (§5).
        tags = latest_year_share_cut(trend_series, _CUTS[cut_label])
        # Title is the plot type only (§14); the cut is a filter and rides the caption.
        return (
            _chart_heading("Meta share over time", f"{cut_label} of {latest_year} decks"),
            _trend_figure(trend_series, tags),
        )

    def draw_manual(manual_tags: list[str]):
        # A focused second chart, drawn only once specific archetypes are chosen, so
        # the manual pick reads on its own rather than crowding the cut chart. Its whole
        # insight card toggles with it, so no empty bordered card sits under the cut when
        # nothing is focused (§12).
        tags = list(dict.fromkeys(manual_tags or []))
        if not tags:
            return gr.update(visible=False), gr.update(visible=False), gr.update(visible=False)
        return (
            gr.update(visible=True),
            gr.update(value=_chart_heading("Selected archetypes"), visible=True),
            gr.update(value=_trend_figure(trend_series, tags), visible=True),
        )

    # Adoption is per-card, so it is run on demand (a fresh Connection like
    # `run_graph`) rather than precomputed like the whole meta matrix. It is the same
    # trend in both card views (#126): Card overview draws the subject alone scoped to
    # a board, Co-occurrence draws the subject plus the co-occurrence pair's second
    # card, board-agnostic (board is None, no board qualifier reaches the plot).
    def draw_adoption(subject: str, second: str | None, board: str | None):
        # Returns the heading, the plot, and a refusal note. With no card picked the
        # whole results stack is hidden by the view callback (the guidance lives in the
        # dropdown help text now), so this simply hides its three parts; a drawn result
        # fills the heading and plot, a refusal fills the note.
        canons = _adoption_cards(subject, second)
        if not canons:
            hide = gr.update(visible=False)
            return hide, hide, hide
        conn = ladybug.Connection(db)
        # `board or None` collapses the either-board reading ("") and the board-
        # agnostic sentinel (None) to the same unfiltered count over both boards.
        series = [
            (card_names[canon], run_series(conn, CardAdoptionOverTime(canon, board or None)))
            for canon in canons
        ]
        return (
            gr.update(
                value=_chart_heading("Adoption over time", _adoption_caption(board)),
                visible=True,
            ),
            gr.update(value=_adoption_figure(series), visible=True),
            gr.update(visible=False),
        )

    def draw_performance(pilot: str):
        # Return the heading, the chart, and a refusal note. The shared pilot dropdown offers the
        # full catalogue (#119), so a pilot short of two averageable years reaches
        # here; rather than a silent blank it gets the same "refused, not a dot" note
        # head-to-head uses. Phrased from the qualifying-year count itself, so raising
        # the floor cannot leave it asserting a pilot has none when they have some
        # (issue #101).
        if not pilot:
            hide = gr.update(visible=False)
            return hide, hide, hide
        try:
            series = run_series(ladybug.Connection(db), PilotPerformanceOverTime(pilot))
        except NotEnoughHistory as e:
            had = "no year" if not e.found else f"only {e.found} year" + ("" if e.found == 1 else "s")
            # One short line in the app's voice (#114, §14): what happened, from the
            # qualifying-year count itself, with no methodology-restating tail. No "pick
            # another" direction, since the sibling neighbourhood and affinity plots may
            # have drawn for this same pilot and should not be waved off.
            return gr.update(visible=False), gr.update(visible=False), gr.update(
                value=f"{pilot_labels[pilot]} has {had} with enough events to average.",
                visible=True,
            )
        # The refusal above is the only way the tool declines, so an empty series
        # should not arise; guard anyway so a drift between the two floor queries drops
        # back to the empty state rather than crashing `_performance_figure` on
        # `cells[0]`, keeping the "never blank" invariant (#113) on this branch too.
        if not series.cells:
            hide = gr.update(visible=False)
            return hide, hide, hide
        return (
            gr.update(value=_chart_heading("Performance over time", caption_html=_performance_caption(series)), visible=True),
            gr.update(value=_performance_figure(pilot_labels[pilot], series), visible=True),
            gr.update(visible=False),
        )

    # Head-to-head offers every pilot in both slots, since the drawable set is
    # pairwise and too large to precompute; a pair that shares too few events is
    # refused with a message rather than drawn as a dot (ADR 0013).
    def draw_head_to_head(a: str, b: str):
        # Returns the heading, the plot, and a refusal note. A missing or same-pilot
        # pair hides all three (the view callback carries the "pick both" / "pick two
        # different" guidance on the neighbourhood card and in the dropdown help text);
        # the note is the "refused, not a dot" surface for a pair the tool comes back
        # empty on.
        if not a or not b or a == b:
            hide = gr.update(visible=False)
            return hide, hide, hide
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
            # One short line in the app's voice (#114, §14): what happened, from the
            # shared-event count itself, with no methodology-restating tail.
            return gr.update(visible=False), gr.update(visible=False), gr.update(
                value=f"{pilot_labels[a]} and {pilot_labels[b]} {met}.",
                visible=True,
            )
        # The in-chart range slider does the time-range slice client-side, so the
        # callback draws the whole rivalry and never re-filters by date here. The pair
        # (a vs b) is named in the subject line above the cards (§14), so the card title
        # is the plot type alone.
        fig = _head_to_head_figure(pilot_labels[a], pilot_labels[b], series)
        return (
            gr.update(value=_chart_heading("Head-to-head timeline"), visible=True),
            gr.update(value=fig, visible=True),
            gr.update(visible=False),
        )

    def _graph_filters(view: str, values: dict) -> list[str]:
        # The reader-language filters a graph result ran under, for the insight-card
        # caption (#110/#132). Written from the display labels the dropdowns carry,
        # never the raw keys, so the caption reads like the controls above it. The
        # subject is stated once above the cards (§14), sourced from the view callback,
        # not here. Reached only after `_spec` confirmed the subject is set.
        match view:
            case "pilot_neighbourhood":
                return [f"vs {pilot_labels[values['pilot2']]}"] if values["pilot2"] else []
            case "pilot_affinity":
                return []
            case "card_usage":
                # Named through _BOARD_LABELS ("main" / "side"), the same casing the
                # adoption chart renders the board in, and no `_Avoid_` word.
                return [_BOARD_LABELS[values["card_board"]]] if values["card_board"] else []
            case "card_cooccurrence":
                filters = []
                if values["cooccur_card2"]:
                    filters.append(f"with {card_names[values['cooccur_card2']]}")
                filters.append(f"top {int(_num(values['cooccur_top_n'], 15))}")
                if values["cooccur_drop_lands"]:
                    filters.append("lands filtered out")
                return filters
            case "meta_gems":
                return []
        return []

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
            return _state_message(f"{e}.")
        # A result too big to draw refuses with its own node count and narrowing
        # hints, so it carries no page-type header: a second "N nodes" caption above
        # it would read as if N had been drawn (#110).
        plan = assess(subgraph)
        if not plan.render:
            return _refine_alert(plan)
        # A drawn or empty result is framed as an insight-card head before it is shown
        # (#110/#132): the plot type as the title and the filters plus how much came
        # back as the caption, so no result is left as an unlabelled graph. The subject
        # is stated once above the cards (§14), sourced from the view callback. Empty
        # reads 0 nodes.
        filters = _graph_filters(view, values)
        header = _result_header(view, filters, plan.node_count)
        if not subgraph.nodes:
            return header + _state_message("No matches for these filters.")
        return header + _embed(render_subgraph(subgraph))

    with gr.Blocks(
        title="7 Point Highlander Graph",
        theme=theme.dark_theme(),
        css=theme.build_css(),
        js=theme.FORCE_DARK_JS,
    ) as demo:
        gr.Markdown("# 7 Point Highlander Graph")

        # The app is organised by subject (issue #119), and since #126 each subject
        # tab collapses to two views, one Draw per view rendering all of that view's
        # plots stacked (graph(s) first, then trend). All controls sit in one raised
        # panel above the results (§13); each view's own controls and Draw toggle with
        # the picker, so none is ever stranded below a plot. Each plot renders in its
        # own bounded insight card (§12). The graph and trend pipelines stay separate
        # under the hood (ADR 0013); only the presentation is combined.

        # Meta is single-view since #125 promoted hidden gems to its own tab (v1
        # §11): it holds meta share over time alone, so there is no subject entity
        # and no view picker, just the chart and its controls.
        with gr.Tab("Meta"):
            gr.Markdown("## Meta")
            gr.Markdown(
                "The metagame over time.",
                elem_classes="t-lede",
            )
            # §13: both plot-affecting controls sit in the panel above the charts. The
            # archetype-focus multiselect used to sit *below* the cut chart (#132); it
            # moves up here with the cut, so no control is stranded below its plot.
            with gr.Group(elem_classes="control-panel"):
                cut = gr.Radio(
                    list(_CUTS), value=_DEFAULT_CUT,
                    label=f"Archetypes to show (by share of {latest_year} decks)",
                )
                manual = gr.Dropdown(
                    choices=trend_archetypes, value=[], multiselect=True,
                    label="Or focus on specific archetypes",
                )
            # The cut chart in its own insight card, open on the default cut.
            _cut_heading, _cut_fig = draw_cut(_DEFAULT_CUT)
            with gr.Group(elem_classes="insight-card"):
                cut_heading = gr.HTML(value=_cut_heading, padding=False)
                cut_plot = gr.Plot(value=_cut_fig)
            # The focused-archetypes chart in its own card, the whole card hidden until
            # a pick is made so the view opens on the cut chart alone (no empty card).
            with gr.Group(elem_classes="insight-card", visible=False) as manual_card:
                manual_heading = gr.HTML(visible=False, padding=False)
                manual_plot = gr.Plot(visible=False)

            cut.change(draw_cut, inputs=cut, outputs=[cut_heading, cut_plot])
            manual.change(draw_manual, inputs=manual, outputs=[manual_card, manual_heading, manual_plot])

        with gr.Tab("Cards"):
            gr.Markdown("## Cards")
            gr.Markdown(
                "Explore any card's usage, companions, and adoption over time.",
                elem_classes="t-lede",
            )
            cards_default = next(iter(_CARDS_TAB))
            cov_default = cards_default == "card_overview"
            cooc_default = cards_default == "card_cooccurrence"

            # §13 control panel: subject, view, and every plot-affecting control in one
            # raised surface. Each view's own filters (the board; the co-occurrence
            # second card, top-N, and land toggle) and its Draw toggle with the picker
            # (`toggle_cards_view`), so none is stranded below a plot.
            with gr.Group(elem_classes="control-panel"):
                card = gr.Dropdown(
                    choices=cards, label="Card", value=None, info=_PICK_CARD,
                    elem_classes="primary-control",
                )
                cards_view = gr.Dropdown(
                    choices=_picker(_CARDS_TAB), value=cards_default, label="View",
                )
                cov_board = gr.Dropdown(
                    choices=_BOARD_CHOICES, label="Board", value="", visible=cov_default,
                )
                cooc_card2 = gr.Dropdown(
                    choices=cards, value=None,
                    label="Second card (optional, for shared packages)",
                    visible=cooc_default,
                )
                cooc_top_n = gr.Dropdown(
                    choices=[5, 15, 25], value=15,
                    label="Top cards by co-occurrence rate", visible=cooc_default,
                )
                cooc_drop_lands = gr.Checkbox(
                    value=False, label="Filter out lands", visible=cooc_default,
                )
                cov_go = gr.Button("Draw", variant="primary", visible=cov_default)
                cooc_go = gr.Button("Draw", variant="primary", visible=cooc_default)

            # Card overview: one card + board, two plots (usage graph and the adoption
            # trend, both scoped to the board). Results stack hidden until a Draw (§14).
            with gr.Group(visible=cov_default) as g_card_overview:
                cov_subject = gr.HTML(visible=False)
                with gr.Group(visible=False, elem_classes="results-stack") as cov_results:
                    with gr.Group(elem_classes="insight-card"):
                        cov_usage_out = gr.HTML()
                    with gr.Group(elem_classes="insight-card"):
                        cov_adopt_heading = gr.HTML(visible=False, padding=False)
                        cov_adopt_note = gr.Markdown(visible=False)
                        cov_adopt_plot = gr.Plot(visible=False)

            # Co-occurrence: card + second card (optional) + top-N + drop-lands, two
            # plots. Board-agnostic: no board control, so the adoption trend counts
            # across both boards and carries no board qualifier text.
            with gr.Group(visible=cooc_default) as g_card_cooccurrence:
                cooc_subject = gr.HTML(visible=False)
                with gr.Group(visible=False, elem_classes="results-stack") as cooc_results:
                    with gr.Group(elem_classes="insight-card"):
                        cooc_graph_out = gr.HTML()
                    with gr.Group(elem_classes="insight-card"):
                        cooc_adopt_heading = gr.HTML(visible=False, padding=False)
                        cooc_adopt_note = gr.Markdown(visible=False)
                        cooc_adopt_plot = gr.Plot(visible=False)

            def draw_card_overview(c: str, board: str):
                # One Draw: the usage subgraph and the adoption series, the trend scoped
                # to the same board the graph is (#126). No compare card here, so the
                # adoption plots the subject alone. The results stack shows only when a
                # card is picked (§14). draw_adoption returns (heading, plot, note).
                subject = _subject_update("Card", card_names[c] if c else None)
                if not c:
                    return (subject, gr.update(visible=False),
                            gr.update(), *draw_adoption(None, None, board))
                return (
                    subject,
                    gr.update(visible=True),
                    gr.update(value=run_graph("card_usage", {"card": c, "card_board": board})),
                    *draw_adoption(c, None, board),
                )

            def draw_cooccurrence(c: str, c2: str, n: int, dl: bool):
                # One Draw: the co-occurrence subgraph and the adoption series. The trend
                # is board-agnostic (board=None, no board qualifier reaches the plot) and
                # plots the pair: the subject alone, or both cards when a second is
                # chosen (#126). The subject line names the chosen card.
                subject = _subject_update("Card", card_names[c] if c else None)
                if not c:
                    return (subject, gr.update(visible=False),
                            gr.update(), *draw_adoption(None, c2, None))
                graph = run_graph("card_cooccurrence", {
                    "card": c, "cooccur_card2": c2,
                    "cooccur_top_n": n, "cooccur_drop_lands": dl,
                })
                return (subject, gr.update(visible=True),
                        gr.update(value=graph), *draw_adoption(c, c2, None))

            def reset_card():
                # A changed card or filter hides the drawn stacks so a stale board- or
                # filter-scoped answer never sits under changed controls.
                hide = gr.update(visible=False)
                return [hide, hide, hide, hide]

            def toggle_cards_view(v):
                # The picker swaps the view: its own filters, its Draw, and its cards
                # show; the other view's hide.
                is_ov = v == "card_overview"
                is_co = v == "card_cooccurrence"
                return [
                    gr.update(visible=is_ov),   # cov_board
                    gr.update(visible=is_co),   # cooc_card2
                    gr.update(visible=is_co),   # cooc_top_n
                    gr.update(visible=is_co),   # cooc_drop_lands
                    gr.update(visible=is_ov),   # cov_go
                    gr.update(visible=is_co),   # cooc_go
                    gr.update(visible=is_ov),   # g_card_overview
                    gr.update(visible=is_co),   # g_card_cooccurrence
                ]

            reset_card_outs = [cov_subject, cov_results, cooc_subject, cooc_results]
            cards_view.change(
                toggle_cards_view, inputs=cards_view,
                outputs=[cov_board, cooc_card2, cooc_top_n, cooc_drop_lands,
                         cov_go, cooc_go, g_card_overview, g_card_cooccurrence],
            )
            # As on the Pilots tab, every control that determines a result hides the drawn
            # stacks: the shared card, and each view's own filters (the board, and the
            # co-occurrence second card, top-N, and land toggle).
            card.change(reset_card, outputs=reset_card_outs)
            for _control in (cov_board, cooc_card2, cooc_top_n, cooc_drop_lands):
                _control.change(reset_card, outputs=reset_card_outs)
            cov_go.click(
                draw_card_overview, inputs=[card, cov_board],
                outputs=[cov_subject, cov_results, cov_usage_out,
                         cov_adopt_heading, cov_adopt_plot, cov_adopt_note],
            )
            cooc_go.click(
                draw_cooccurrence, inputs=[card, cooc_card2, cooc_top_n, cooc_drop_lands],
                outputs=[cooc_subject, cooc_results, cooc_graph_out,
                         cooc_adopt_heading, cooc_adopt_plot, cooc_adopt_note],
            )

        # Hidden gems is its own top-level tab since #125, promoted out of Meta (v1
        # §11): entered by archetype, it outputs the cards that over-index in the
        # archetype's decks against the wider format, the SliceTooSmall refusal
        # intact (ADR 0012). A navigation move, not a query change: the query id
        # stays `meta_gems`, so _spec, _graph_filters, and the plot heading are untouched.
        with gr.Tab("Hidden gems"):
            gr.Markdown("## Hidden gems")
            gr.Markdown(
                "Under-the-radar cards for an archetype.",
                elem_classes="t-lede",
            )
            with gr.Group(elem_classes="control-panel"):
                gem_archetype = gr.Dropdown(
                    choices=archetypes, label="Archetype", value=None, info=_PICK_ARCHETYPE,
                )
                gem_go = gr.Button("Draw", variant="primary")
            gem_subject = gr.HTML(visible=False)
            # One card, hidden until a Draw (§14); the dropdown help text guides on open.
            gem_out = gr.HTML(visible=False, elem_classes="insight-card")

            def draw_gems(a: str):
                # The archetype is named once above the card (§14); the card title is
                # the plot type alone ("Hidden gems"). With no archetype the card stays
                # hidden and the dropdown help text guides.
                subject = _subject_update("Archetype", archetype_labels[a] if a else None)
                if not a:
                    return subject, gr.update(visible=False)
                return subject, gr.update(value=run_graph("meta_gems", {"gem_archetype": a}), visible=True)

            gem_go.click(
                draw_gems, inputs=gem_archetype, outputs=[gem_subject, gem_out],
            )

        with gr.Tab("Pilots"):
            gr.Markdown("## Pilots")
            gr.Markdown(
                "Explore any pilot's decks, rivalries, and results over time.",
                elem_classes="t-lede",
            )
            pilots_default = next(iter(_PILOTS_TAB))
            po_default = pilots_default == "pilot_overview"
            h2h_default = pilots_default == "pilot_head_to_head"

            # §13 control panel: subject, view, and every plot-affecting control in one
            # raised surface above the results. The subject dropdown's help text carries
            # the "pick and Draw" guidance (§14), so the results below open empty rather
            # than as duplicated prompt cards. The second pilot and each view's Draw
            # toggle with the picker (`toggle_pilots_view`).
            with gr.Group(elem_classes="control-panel"):
                pilot = gr.Dropdown(
                    choices=pilots, label="Pilot", value=None, info=_PICK_PILOT,
                    elem_classes="primary-control",
                )
                pilots_view = gr.Dropdown(
                    choices=_picker(_PILOTS_TAB), value=pilots_default, label="View",
                )
                h2h_pilot_b = gr.Dropdown(
                    choices=pilots, value=None, label="Second pilot (required)",
                    info=_PICK_PILOT2, visible=h2h_default,
                )
                po_go = gr.Button("Draw", variant="primary", visible=po_default)
                h2h_go = gr.Button("Draw", variant="primary", visible=h2h_default)

            # Pilot overview: one pilot, three plots, each in its own insight card. The
            # results stack is hidden until a Draw fills it (§14), so the view opens as
            # controls over empty ground, the dropdown help text guiding, not a row of
            # duplicated empty-prompt cards. The subject line sits above the stack.
            with gr.Group(visible=po_default) as g_pilot_overview:
                po_subject = gr.HTML(visible=False)
                with gr.Group(visible=False, elem_classes="results-stack") as po_results:
                    with gr.Group(elem_classes="insight-card"):
                        po_nb_out = gr.HTML()
                    with gr.Group(elem_classes="insight-card"):
                        po_af_out = gr.HTML()
                    with gr.Group(elem_classes="insight-card"):
                        po_perf_heading = gr.HTML(visible=False, padding=False)
                        po_perf_note = gr.Markdown(visible=False)
                        po_perf_plot = gr.Plot(visible=False)

            # Head-to-head: two pilots, the second required, two plots (the pair's
            # neighbourhood and their shared-event timeline).
            with gr.Group(visible=h2h_default) as g_pilot_head_to_head:
                h2h_subject = gr.HTML(visible=False)
                with gr.Group(visible=False, elem_classes="results-stack") as h2h_results:
                    with gr.Group(elem_classes="insight-card"):
                        h2h_nb_out = gr.HTML()
                    with gr.Group(elem_classes="insight-card", visible=False) as h2h_plot_card:
                        h2h_heading = gr.HTML(visible=False, padding=False)
                        h2h_note = gr.Markdown(visible=False)
                        h2h_plot = gr.Plot(visible=False)

            def draw_pilot_overview(p: str):
                # One Draw fans out to all three plots: two subgraph queries and a
                # series, each independent so a graph that refines composes beside a
                # trend that refuses (#126). The results stack shows only when a pilot is
                # picked (§14); with none, the dropdown help text guides and the stack
                # stays hidden. draw_performance returns (heading, plot, note).
                subject = _subject_update("Pilot", pilot_labels[p] if p else None)
                if not p:
                    return (subject, gr.update(visible=False),
                            gr.update(), gr.update(), *draw_performance(None))
                return (
                    subject,
                    gr.update(visible=True),
                    gr.update(value=run_graph("pilot_neighbourhood", {"pilot": p, "pilot2": None})),
                    gr.update(value=run_graph("pilot_affinity", {"pilot": p})),
                    *draw_performance(p),
                )

            def draw_head_to_head_view(a: str, b: str):
                # One Draw fans out to the pair's neighbourhood and their timeline. A
                # missing pair keeps the stack hidden (the dropdown help text guides); a
                # same-pilot pair shows the stack with a single "pick two different" note
                # on the neighbourhood card, the empty timeline card hidden (self-vs-self is
                # refused the same way the timeline refuses it, so the two plots never
                # disagree); a valid pair draws both and names the pair once above them. The
                # timeline card is shown only for a valid pair, so a self/empty result never
                # leaves an empty bordered card below the note (§12).
                hide_card = gr.update(visible=False)
                if not a or not b:
                    return (gr.update(visible=False), gr.update(visible=False),
                            gr.update(), *draw_head_to_head(a, b), hide_card)
                if a == b:
                    return (gr.update(visible=False), gr.update(visible=True),
                            gr.update(value=_state_message("Pick two different pilots to compare.")),
                            *draw_head_to_head(a, b), hide_card)
                subject = gr.update(
                    value=_subject_line("Head-to-head", pilot_labels[a], pilot_labels[b]),
                    visible=True,
                )
                nb = gr.update(value=run_graph("pilot_neighbourhood", {"pilot": a, "pilot2": b}))
                return (subject, gr.update(visible=True), nb,
                        *draw_head_to_head(a, b), gr.update(visible=True))

            def reset_pilot():
                # A changed subject drops the drawn results: hide the subject lines and
                # both results stacks, so a stale answer never sits under changed controls.
                hide = gr.update(visible=False)
                return [hide, hide, hide, hide]

            def toggle_pilots_view(v):
                # The picker swaps the view: its own controls (head-to-head's second
                # pilot), its Draw, and its cards show; the other view's hide.
                is_ov = v == "pilot_overview"
                is_h2h = v == "pilot_head_to_head"
                return [
                    gr.update(visible=is_h2h),  # h2h_pilot_b
                    gr.update(visible=is_ov),   # po_go
                    gr.update(visible=is_h2h),  # h2h_go
                    gr.update(visible=is_ov),   # g_pilot_overview
                    gr.update(visible=is_h2h),  # g_pilot_head_to_head
                ]

            reset_pilot_outs = [po_subject, po_results, h2h_subject, h2h_results]
            pilots_view.change(
                toggle_pilots_view, inputs=pilots_view,
                outputs=[h2h_pilot_b, po_go, h2h_go, g_pilot_overview, g_pilot_head_to_head],
            )
            # Any control that determines a result hides the drawn stacks so a stale
            # answer never sits under changed controls: the shared pilot, the second pilot.
            pilot.change(reset_pilot, outputs=reset_pilot_outs)
            h2h_pilot_b.change(reset_pilot, outputs=reset_pilot_outs)
            po_go.click(
                draw_pilot_overview, inputs=pilot,
                outputs=[po_subject, po_results, po_nb_out, po_af_out,
                         po_perf_heading, po_perf_plot, po_perf_note],
            )
            h2h_go.click(
                draw_head_to_head_view, inputs=[pilot, h2h_pilot_b],
                outputs=[h2h_subject, h2h_results, h2h_nb_out,
                         h2h_heading, h2h_plot, h2h_note, h2h_plot_card],
            )

    return demo
