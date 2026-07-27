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
from datetime import datetime
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
    Coverage,
    HiddenGems,
    PilotAffinity,
    PilotNeighbourhood,
    QuerySpec,
    SliceTooSmall,
    card_catalogue,
    coverage,
    gem_archetypes,
    pilot_catalogue,
    run_query,
)
from graph7ph.provenance import built_at
from graph7ph.render import render_subgraph
from graph7ph.trends import (
    ArchetypeLandscape,
    ArchetypeTimeline,
    BestPlayerRace,
    CardAdoptionOverTime,
    HeadToHeadTimeline,
    LandscapeCell,
    MetaShareOverTime,
    NotEnoughHistory,
    MAJOR_FIELD_SIZE,
    PilotPerformanceOverTime,
    RACE_INTERVAL,
    RaceCell,
    Series,
    archetypes_with_history,
    comparable_points,
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
_PICK_TIMELINE_ARCHETYPE = "Pick an archetype to trace its finishes; it draws straight away."

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


# The button's label is the word, not the glyph it wears. A Gradio button's text *is*
# its accessible name and the component takes no `aria-label`, so a bare "×" reaches a
# screen reader as "multiplication sign, button", which names neither the action nor the
# field it acts on. The word is the name; the stylesheet collapses it and draws the ×
# (`theme`, `.clear-btn::before`), so the control reads as "Clear" and looks like ×.
CLEAR_LABEL = "Clear"


def _clearable(*, visible: bool = True, **kwargs) -> tuple[gr.Dropdown, gr.Row]:
    """A subject or filter dropdown with a clear (×) glyph on its right edge.

    Gradio's dropdown has no way back to "nothing picked": once a card, archetype,
    or pilot is chosen, the reader can swap it but not unset it, so a comparison
    (a second archetype, a head-to-head opponent) is a one-way door. The glyph
    puts the empty state back within reach. Only the controls that name *data* get
    one; the option controls (year, view, board, top-N) always hold a value, so
    clearing them would mean nothing.

    ``equal_height=False`` matters: a Gradio row stretches its children to the
    tallest, which turns a small button into a full-height slab beside the field.
    The glyph is then sized and quieted in CSS (``theme``, ``.clearable``), and the
    button carries no ``scale``/``min_width`` because those render as inline styles
    that stylesheet rules cannot reach.

    Clearing writes the value through the backend, so it fires the same ``change``
    handlers a manual pick does, and the view redraws or resets exactly as if the
    reader had emptied the field themselves.

    Returns the dropdown and the row holding the pair. ``visible`` applies to the
    row, and a caller that shows or hides the control (the view pickers do) must
    toggle that row: hiding the dropdown alone would strand its glyph.
    """
    empty = [] if kwargs.get("multiselect") else None
    with gr.Row(elem_classes="clearable", equal_height=False, visible=visible) as row:
        dropdown = gr.Dropdown(**kwargs)
        clear = gr.Button(CLEAR_LABEL, elem_classes="clear-btn")
    clear.click(lambda: gr.update(value=empty), outputs=dropdown)
    return dropdown, row


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


# Who to reach about the graph, under the coverage line: the data behind it is
# curated by hand, so a reader who spots a wrong archetype or a missing pilot needs
# a person to tell, not an issue tracker.
_MAINTAINER = "Alejandro de la Fuente"
_CONTACT_EMAIL = "alejandrofuentepinero@gmail.com"
_CONTACT_DISCORD = "alejandrofp92"


def _last_updated(built_iso: str | None) -> str:
    """The artifact's build date as a last-updated line, or that it is unknown.

    Day granularity: the stamp records a UTC instant, but the finest thing it
    honestly identifies is which day's data the artifact was built from, so a
    reader gets the date and not a spurious wall-clock second. An unreadable or
    absent stamp (``None``) reads as unknown rather than as ``None``.
    """
    if not built_iso:
        return "Last updated unknown"
    try:
        day = datetime.fromisoformat(built_iso).date().isoformat()
    except ValueError:
        day = built_iso
    return f"Last updated {day}"


def _provenance_html(cov: Coverage, built_iso: str | None) -> str:
    """The coverage and contact surface (issue #115): how much of the metagame the
    graph holds, when it was last updated, and who to reach about it.

    The coverage row uses the one numeric convention (§4): each count
    thousands-comma'd and set in tabular figures so the digits align, the years as a
    span (a single year where the graph is one year deep). Every value is an
    app-generated count or a fixed contact string, so nothing here is user free text
    to escape.
    """
    years = (
        str(cov.first_year) if cov.first_year == cov.last_year
        else f"{cov.first_year}–{cov.last_year}"
    )
    row = " · ".join([
        f"<b>{cov.events:,}</b> events",
        f"<b>{cov.pilots:,}</b> pilots",
        f"<b>{cov.decks:,}</b> decks",
        f"<b>{cov.cards:,}</b> distinct cards",
        years,
    ])
    contact = " · ".join([
        _last_updated(built_iso),
        _MAINTAINER,
        f"<a href='mailto:{_CONTACT_EMAIL}'>{_CONTACT_EMAIL}</a>",
        f"Discord {_CONTACT_DISCORD}",
    ])
    return (
        "<div class='provenance'>"
        f"<div class='coverage tabular'>{row}</div>"
        f"<div class='t-caption'>{contact}</div>"
        "</div>"
    )


def _subject_update(prefix: str, name: str | None):
    """A ``gr.update`` that shows the subject line for a single-subject view, or hides
    it when no subject is chosen (§14). The pilot, card, and gems views share this;
    head-to-head builds its own two-name line inline, since it names a pair."""
    if not name:
        return gr.update(visible=False)
    return gr.update(value=_subject_line(prefix, name), visible=True)


# Every graph plot shares one frame (§12), so a dense graph and a sparse one land in
# the same canvas across the tabs rather than each jumping to its own node-count-scaled
# height. The frame is a share of the viewport between a floor and a ceiling, not a
# fixed slab: at 760px flat it was most of a phone's screen and a letterbox on a tall
# monitor, which is the fixed-height letterbox #85 set out to retire.
#
# The ceiling began as the size the pilot neighbourhood was tuned to (760px), and all
# three values were then raised 25 percent together: same width, a taller plot, traded
# knowingly against the room `72vh` used to leave around the card. The floor keeps a
# dense graph legible where the proportion would otherwise squeeze it.
GRAPH_HEIGHT = 950
GRAPH_MIN_HEIGHT = 525
GRAPH_VIEWPORT_SHARE = "90vh"


def _embed(doc: str) -> str:
    """Wrap a standalone pyvis document in an iframe so its scripts run, in the shared
    responsive graph frame (:data:`GRAPH_HEIGHT` and friends).

    The pyvis document inside is already a full-height flex column (see
    ``render._DOC_STYLE``), so it takes whatever height the iframe gives it and the
    colour key and details panel stay pinned to its top and bottom at any size. This
    frame is the only thing that was ever fixed.

    gr.HTML does not execute injected <script> tags, so the widget is isolated in
    an iframe via srcdoc (which the browser renders as its own document)."""
    srcdoc = html.escape(doc, quote=True)
    height = f"clamp({GRAPH_MIN_HEIGHT}px, {GRAPH_VIEWPORT_SHARE}, {GRAPH_HEIGHT}px)"
    style = f"width:100%;height:{height};border:none"
    return f'<iframe srcdoc="{srcdoc}" style="{style}"></iframe>'


# The Draw button's two states, and the running state's words.
#
# §8 asks for progress on *every* query-running action and for running to share one look
# with the other four states. Those four all speak as a line in the results region
# (:func:`_state_message`), and running says the same word wherever it appears, so the
# app reads as saying one thing while it works rather than two:
#
# - A view driven by a Draw button carries it on the button, which is the component
#   certain to be on screen at the moment of the click (:func:`_draw_with_progress`).
# - A view driven by a dropdown has no button to carry it, so it speaks in the results
#   region through the note line the other four states already use (the Archetypes tab's
#   landscape and timeline, wired below).
DRAW_LABEL = "Draw"
DRAWING_LABEL = "Drawing…"


def _draw_busy():
    """The Draw button while its query runs."""
    return gr.update(value=DRAWING_LABEL, interactive=False)


def _draw_idle():
    """The Draw button at rest."""
    return gr.update(value=DRAW_LABEL, interactive=True)


def _draw_with_progress(button, fn, *, inputs, outputs) -> None:
    """Wire a Draw button to its query so that the click is visibly acknowledged.

    Gradio does give every event a progress indicator, but it paints it *over the
    output components*, and since #132/#138 a view's results stack is hidden until its
    callback fills it (and changing the subject hides it again). So on a Draw there is
    nothing on screen for that indicator to sit on, and the panel simply holds still
    for the whole round trip. Against a local artifact that is invisible; on a phone
    over a slow link it is a click that appears to do nothing, which is the case user
    story 17 exists for.

    The button itself carries the state instead, because it is the one component
    certain to be on screen at the moment of the click and the one the reader is
    already looking at. It reads "Drawing…" and stops taking clicks while the query
    runs (which also rules out a second Draw queued behind the first), then returns.

    The return is chained with ``.then`` rather than ``.success`` deliberately:
    ``.then`` runs whether the query returned or raised, so a query that fails cannot
    strand the button disabled with no way back.
    """
    (
        button.click(_draw_busy, outputs=button)
        .then(fn, inputs=inputs, outputs=outputs)
        .then(_draw_idle, outputs=button)
    )


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

# The FAQ tab (#133): the how-it-is-calculated notes #132 strips off the plots, homed
# one click away so the plot surfaces stay scannable. Each entry is (elem_id, category,
# question, answer); the id anchors the box for any deep link. Kept succinct on the main
# metrics for now and expanded as users ask (the issue's steer). The headline plots
# (seven since the archetype timeline, #151) reduce to a shared primitive (a normalised
# finish) plus one entry each, so the finish is explained first and the rest lean on it.
# The category is the subject the answer is about, from `theme.FAQ_TAGS`, and the entries
# are held in category order: the tab renders them down the page in this order, so the
# boxes read as grouped by subject rather than as an arbitrary sequence.
_FAQ_ENTRIES: list[tuple[str, str, str, str]] = [
    (
        "faq-finish",
        "Metric",
        'What does a "finish" mean, and why is it shown as a percentage?',
        "Every event places its decks, but 5th out of 200 is not 5th out of 12. Each "
        "finish is normalised to where it landed in its field, on a 0-to-1 scale (1 is "
        "a win, 0 is last). That single number is what lets finishes from "
        "different-sized events be compared and averaged. A finish only counts where the "
        "event actually recorded a placement.",
    ),
    (
        "faq-meta",
        "Archetypes",
        'How is "Meta share over time" calculated?',
        "Each deck is counted once, under its primary archetype. An archetype's share in "
        "a year is its decks that year divided by every deck that year. Low counts are "
        "kept, not hidden: a small share is a real signal of an archetype entering or "
        "leaving the format, and a year it is absent is a real zero, not a gap. The "
        '"Top 25 / 50 / 75%" control only changes how many lines are drawn, not the '
        "data: archetypes are ordered by their share of the most recent year, and the "
        "strongest are kept until they add up to that percentage.",
    ),
    (
        "faq-landscape",
        "Archetypes",
        'How is the "Metagame landscape" built?',
        "Each deck is counted once, under its primary archetype, exactly as meta share "
        "is. For the year selected, an archetype sits horizontally at its share of "
        "that year's decks and vertically at the average of its finishes; "
        "the dot's size is how many separate events those finishes came from. Only the "
        "25 most-played archetypes of that year are drawn, recomputed for each year, "
        "and the chart says how many archetypes the year held in all. More of them sit "
        "above the halfway line than below it, because the source records top finishers "
        "more completely than the rest of the field.",
    ),
    (
        "faq-archetype-timeline",
        "Archetypes",
        'How is an archetype\'s "Finishes over time" built?',
        "Each point is one event, placed on the date the event's first deck was "
        "registered, and its height is the average of that archetype's finishes there. "
        "That is an average, not a single placement: an archetype usually brings a handful "
        "of decks to an event, and typically one to three of them were given a "
        "placement, which is what the size of each point shows. Picking a second "
        "archetype narrows the chart to the events both attended, so every point has "
        "something to compare against, and the headline counts how many of those events "
        "each of them finished ahead at. Two archetypes given a placement at fewer than "
        "two events in common are refused rather than drawn.",
    ),
    (
        "faq-performance",
        "Pilots",
        'How is a pilot\'s "Performance over time" calculated?',
        "For each year, the pilot's finishes are averaged into one normalised score "
        "(drawn flipped so higher is better; 0.5 is the coin-flip line a random finisher "
        "would average). A year needs at least two events, otherwise it is left as a gap "
        "rather than a misleading single-event dot, and a pilot needs at least two such "
        "years before the chart is drawn at all, since a line through one point is not a "
        "trajectory. The headline \"finishes ahead of X% "
        "of the field\" is the average across all scored years, weighted by how many "
        "events each year held, so a busy year counts for more than a quiet one.",
    ),
    (
        "faq-head-to-head",
        "Pilots",
        'How is the "Head-to-head" timeline built?',
        "It plots the two pilots' finishes at every event they both entered, placed on "
        "the date the event's first deck was registered. Each point is one real placement, not an average. A pair needs "
        "at least two shared events, otherwise there is no trajectory to draw and the "
        "tool says so.",
    ),
    (
        "faq-race",
        "Pilots",
        'How is the "Best player race" scored?',
        "Only the biggest events count: a field of more than 64, which is about the top "
        "fifth of them, so that every pilot in the race is measured on the same kind of "
        "event. This is the only plot in the app that leaves events out. Everywhere "
        "else, including a pilot's own performance chart and the hidden gems, every "
        "event with a recorded finish counts, so a pilot's standing here and their "
        "record elsewhere are answering different questions and will not always agree. "
        "A pilot's score is the average of their finishes there, pulled toward "
        "the average of the whole field by how little evidence stands behind it: a "
        "pilot with five such events is scored about half on their own record and half "
        "on the field's, while one with three times that record is scored mostly on "
        "their own. "
        "That is what stops a good weekend outranking a long strong record, and how "
        "hard it pulls is measured from the data rather than chosen, by comparing how "
        "much one pilot's results bounce around against how much pilots genuinely "
        "differ. To be in the race at all a pilot needs five of these events behind "
        "them and two of them in the last year, so the field is who is playing now. "
        "Each point on a line is that same score over every one of these events the "
        "pilot had played by that date, so a line climbing is their record filling in "
        "rather than the pilot improving, and the last point of a line is the score "
        "the standings rank them on. It is drawn this way because the obvious "
        "alternative, a rolling window of recent form, was measured and found to be "
        "noise: shuffling a pilot's results into a different order moved those lines "
        "just as much as their real career did.",
    ),
    (
        "faq-race-certainty",
        "Pilots",
        'How settled is the "Best player race" order?',
        "Less than three decimal places make it look. These are the biggest events, so "
        "there are only a couple of dozen of them and a typical contender has played "
        "eight, which is not enough to separate pilots whose scores differ in the "
        "thousandths. The standings put a number on that: each pilot's results are "
        "redrawn at random from their own record a thousand times, the whole field is "
        "rescored, and the last column reports the range their rank landed in 90% of "
        "the time. Those ranges are wide, and they overlap heavily near the top. Read "
        "the leading group as a group, not as a 1-2-3.",
    ),
    (
        "faq-adoption",
        "Cards",
        'How is "Adoption over time" calculated?',
        "For each year, it is the share of decks that ran the card: decks with the card "
        "that year divided by all decks that year. As with meta share, low counts are "
        "shown as signal and a year with none is a real zero.",
    ),
    (
        "faq-gems",
        "Cards",
        'What makes a card a "Hidden gem"?',
        "A gem is a card that is rare in the slice (in at least five decks but no more "
        "than 10% of them) yet finishes in the slice's top third on average. Only decks "
        "with a recorded finish count toward the rarity and the average. If a slice has "
        'too few placed decks to tell "rare" from "absent", the tool refuses rather than '
        "guessing.",
    ),
]

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


# How many archetypes the landscape draws, the display cut the tool never sees (the
# same division of labour as `_CUTS`: `archetype_landscape` returns the year's whole
# field and this picks what is drawn). Twenty-five, because that is 78 / 73 / 69 / 68
# percent of the field for 2023 / 2024 / 2025 / 2026, landing near the Meta tab's own
# "top 75%" cut vocabulary, and because every one of the 25 carries its name on the
# chart: labelling a subset of a larger set would read as arbitrary (issue #145).
_LANDSCAPE_TOP_N = 25


def _landscape_top(series: Series, top_n: int) -> list[LandscapeCell]:
    """The ``top_n`` archetypes of a landscape by share, strongest first.

    The cut is recomputed for whichever year is drawn, so the chart follows the meta
    rather than being pinned to one year's ranking (the opposite of
    ``latest_year_share_cut``, which deliberately fixes one set so its lines span the
    whole x axis, and so cannot be reused here). Ties are broken on the tag, so a year
    always draws the same set rather than leaving it to the row order.

    The cut runs over every archetype the year held and an archetype the year never
    scored is dropped **after** it, so it leaves a gap rather than pulling a deeper
    archetype up into the drawn set. A dot needs both axes, and such an archetype has a
    share but no finish to place it at; promoting rank 26 in its place would quietly
    redraw the claim the caption makes, which is a cut of the year's most-played
    archetypes. It stays in the series either way, and so in the count of archetypes the
    year held that the surface states. No corpus year exercises this: 2023 is the only
    year with unscored archetypes (3 of 56) and none of them fall inside a top 25.
    """
    ranked = sorted(series.cells, key=lambda c: (-c.n, c.tag))[:top_n]
    return [cell for cell in ranked if cell.mean_norm is not None]


# How many pilots the race draws as lines, and how many the leaderboard beside it
# lists. Both are display cuts the tool never sees, the same division of labour as
# `_LANDSCAPE_TOP_N`: `best_player_race` returns every contender's whole trajectory and
# these pick what is drawn and what is tabled.
#
# Eight lines because eight is where the shared palette's *named* hues stop
# (`palette.MAX_SLOTS`): past the eighth, the palette's own contract says a hue traces a
# line but does not name it, which would leave this chart's legend decorative when the
# legend is the only thing tying a line to a pilot. It is not a natural break in the
# data and does not pretend to be: on the current record the gap from rank 8 to rank 9
# is 0.0007, a seventh of the gap from 3 to 4 and half the gap between the top two. The
# leaderboard is what makes that visible, which is most of why it is there.
_RACE_LINES = palette.MAX_SLOTS
_LEADERBOARD_ROWS = 50
# The decimals the standings are written to, in the table and in the chart's hover
# alike. Three, not the charts' usual two, because the board is separated by
# thousandths: at two, ranks 4, 5 and 6 all print 0.74 on the current record and 7 and
# 8 both print 0.73. Named once and shared, because the running score makes the chart's
# right edge the leaderboard exactly (ADR 0017), so the two read one quantity and a
# drift between them would print it two ways.
LEADERBOARD_SCORE_PLACES = 3


def _race_trajectories(series: Series) -> list[list[RaceCell]]:
    """Every contender's trajectory, best first, each one oldest window first.

    A trajectory is one pilot's cells in window order, which is the shape a line trace
    wants. The tool already returns the field in standing order with each career in
    window order, so this groups rather than re-ranks: re-sorting here would let the
    chart's order and the leaderboard's drift apart on a tie. The caller slices it into
    the leading few it draws in hue and the rest it draws as context behind them.
    """
    trajectories: dict[str, list[RaceCell]] = {}
    for cell in series.cells:
        trajectories.setdefault(cell.pilot, []).append(cell)
    return list(trajectories.values())


def _rgba(hex_colour: str, alpha: float) -> str:
    """A hex palette colour as an ``rgba()`` string at the given opacity."""
    r, g, b = pc.hex_to_rgb(hex_colour)
    return f"rgba({r}, {g}, {b}, {alpha})"


# The chart chrome, drawn once from the design tokens (§2/§6) so a hardcoded grey
# can never assume a background the app no longer inherits: the gridline is the
# hairline border token, the axis, ticks, and font the muted token. Concrete hexes
# rather than `var(--token)` because Plotly draws the chart as SVG the CSS custom
# properties never reach.
_GRID = theme.TOKENS["border"]
_AXIS = theme.TOKENS["text-mute"]
_SURFACE = theme.TOKENS["surface"]
# The colour a control drawn inside a figure takes, so the range slider and its label
# read as a control against the neutral chart rather than as more plot. §2 commits the
# app to one accent, and this used to be a Tailwind amber left over from the
# light-theme era, which made the one orange thing inside a chart a different orange
# from every orange outside it.
_CONTROL_ACCENT = theme.TOKENS["accent-bright"]

# The colour and opacity of the race's context layer, the contenders the cut left out.
#
# The app's own accent (§2) rather than the neutral axis token it first drew in (the
# maintainer's call): the layer is the field, which is a thing the chart is about, and
# the neutral read as chrome. The bright accent rather than the base one, since that is
# the token §2 already assigns to a chart mark, and a layer at this opacity needs the
# lighter of the two to register on the surface at all. It stays clear of the entity
# palette's own orange (slot 2, `#d95926`), which one of the eight drawn lines may be
# carrying, though the two are the same family: what separates them is strength, the
# drawn line at full and this at a sixth of it.
_RACE_CONTEXT_COLOUR = theme.TOKENS["accent-bright"]

# Still well below `_CONTEXT_ALPHA` beside it, because the layer is four times as dense:
# the meta chart fades at most 31 lines and this one draws every contender the cut left
# out, 131 on the current record. Overlap is what sets the reading, not the single
# stroke: ten lines crossing at one point composite to 83% at this value, so the crowded
# middle of the field reads as a solid band while a lone contender out on their own
# stays the faint thread they are.
_RACE_CONTEXT_ALPHA = 0.16

# The opacity the emphasis model (§6) fades its context lines to: far enough back that
# a raised line reads as raised even against the widest cut, while each faded line keeps
# enough of its hue to be traced across the years. Settled by eye on the real cuts.
_CONTEXT_ALPHA = 0.20

# How many lines a cut opens already raised. The cut hands its tags over strongest
# first, so these are the year's leading archetypes: enough that a cold start is a
# chart with a reading in it rather than a field of context the reader has to click
# before it says anything, and few enough that the raise still reads as a raise.
_OPEN_RAISED = 3


def _legend_title(hint: str) -> str:
    """The emphasis legend's title: its name, then how to work it.

    The hint sits on its own line and in the palette's blue, so it reads as an
    invitation rather than as part of the label. Plotly draws the legend as SVG, so the
    colour is one of its own inline spans (a class the stylesheet reaches would not
    survive) and the break is Plotly's ``<br>``, not markup the app's CSS lays out.
    """
    return (
        f'Archetype<br><span style="color:{palette.CATEGORICAL[0]}">{hint}</span>'
    )


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


# A figure carrying its legend below the plot is taller than Plotly's 450px default by
# the room that legend needs. Sized against the worst case, the meta chart's fourteen
# archetypes at phone width, where they stack one per row; the same legend packs into
# two rows at desktop width, so the plot simply gets the difference back. One height has
# to serve both, because a figure has one layout and the row count is a function of the
# width it is drawn at.
#
# It is not optional. A figure merely *told* to put its legend below, without the height
# for it, lays that legend out past its own bottom edge and Plotly clips the overflow --
# measured as the last two of fourteen archetypes missing at phone width. On the meta
# chart the legend is the control (click to raise), so a clipped entry is an archetype
# the reader cannot reach at all.
_LEGEND_BELOW_HEIGHT = 640

# The legend's title sits a step above its entries, which take the figure's 12px: it
# names what the whole box is (and on the meta chart says how to work it), so at the
# entries' own size it read as one more entry rather than as their heading.
_LEGEND_TITLE_SIZE = 14

# Every finish axis says which end is winning. The score is flipped from the raw
# placement (a 1st place is 1, not 1st), so without it the reader's guess about which way
# the plot runs is a coin toss. Named once because three charts state it.
_SCORE_DIRECTION = "1 = first, 0 = last"


def _legend_below_plot(fig: pgo.Figure, title: str, **extra) -> None:
    """Lay a figure's legend horizontally under the plot, with the height to fit it.

    Plotly's default puts the legend to the right *inside the figure's width*, so it
    takes its space from the plot: at a phone's 390px the meta chart's fourteen
    archetype names claimed about half of it and crushed the lines into a strip. The
    figure is one image with one layout, so there is no width-conditional fix here
    (no media query reaches into an SVG Plotly lays out itself); the legend has to sit
    somewhere that costs no width at any size, which means above or below.

    Below, not above: on these two charts the legend is long enough to wrap, and a
    wrapping legend above the plot pushes the plot itself down the page, away from
    the heading that names it. The rivalry charts anchor theirs above instead
    (:func:`_style_rivalry_chart`) because they carry two entries and one row, and
    because their range slider already owns the space underneath.

    The title sits on ``top`` rather than Plotly's ``left`` default for a horizontal
    legend: the meta chart's title is two lines (its name, then how to work it), and
    two stacked lines to the left of a wrapping row of entries reads as a stray label
    beside the strip rather than as its heading. ``y`` clears the x-axis title, which
    the legend otherwise overlaps at the default offset.
    """
    fig.update_layout(legend=dict(
        title=dict(text=title, side="top", font=dict(size=_LEGEND_TITLE_SIZE)),
        orientation="h", yanchor="top", y=-0.22, xanchor="left", x=0,
        # No fill and no outline: a hairline box round the strip was tried and dropped
        # at the maintainer's call (2026-07-26), so do not add one back. The legend
        # sits directly on the chart ground, which is already the card's own surface.
        bgcolor="rgba(0,0,0,0)",
        **extra,
    ))
    # Only reserve the room when a legend will actually be drawn. Plotly draws one for
    # two or more legend-carrying traces and suppresses it for one, so a single-card
    # adoption chart would otherwise hold 190px open under the plot for a legend that
    # never arrives. Counted over the traces that carry an entry, not every trace: the
    # meta chart's faded context lines are all `showlegend=False`.
    if sum(1 for t in fig.data if t.showlegend is not False) > 1:
        fig.update_layout(height=_LEGEND_BELOW_HEIGHT)


def _style_trend_chart(fig: pgo.Figure, y_title: str) -> None:
    """The dark-theme styling both trend charts share (the meta and one card).

    Transparent backgrounds so the chart sits on the app's own surface rather than
    Plotly's white card, with the axis, ticks, and gridlines on the design tokens
    (§6). The title no longer rides the figure: it is a page heading the caller
    draws above the plot (§6), so the figure carries no Plotly-font title. Only the
    y-axis label differs between the two charts (a share of the meta, or a card's
    adoption), so it is passed in; the rest is held in one place so the two cannot
    drift apart. The caller adds its own legend, the one thing they do not share.

    The chart chrome is set in the app's own body face, not Plotly's default
    ``"Open Sans", verdana, arial``: a figure left unstated renders its axis titles,
    ticks, legend and hovers in a stack nothing else on the page uses, and the whole
    point of §1 is that every surface follows one direction. ``gr.Plot`` draws into
    the page's own DOM rather than an iframe, so the ``@font-face`` the theme injects
    already covers the SVG, and naming the stack here is all it takes.
    """
    fig.update_layout(
        hovermode="closest",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=_AXIS, family=theme.FONT_STACK),
        margin=dict(t=8, r=8, b=8, l=8),
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


def _trend_figure(
    series: Series, tags: list[str], *, start_raised: int = _OPEN_RAISED,
) -> pgo.Figure:
    """A line chart of the chosen archetypes' meta share over time.

    One trace per archetype, with the data foregrounded: the points are the
    observations, so they are drawn large and hollow with a thick rim, while the
    connecting line is thin and dashed, a reminder that it only joins points and
    asserts no trend between them (ADR 0013). Every year draws a point: meta share
    carries no floor, so a thin year states its real share and a year the archetype
    was absent drops to a real zero, with no holes for the eye to read as zeros of
    its own. Each point's hover carries its year, share, and deck count N, the
    sample size the reader reasons with.

    The chart draws on **emphasis** (§6, ADR-0013's #116 amendment as revised in issue
    #117), at every width. Every archetype is drawn twice, in one hue at two strengths:
    a faded line, and a full-strength twin that carries the legend entry and starts at
    ``legendonly``. So the chart opens as the whole field at low contrast, and the
    reader raises one line out of it from the legend. Emphasis is not a mode that
    switches on past some line count: moving the cut changes how many lines are drawn
    and nothing else, where a threshold would have made the same archetype read two
    different ways either side of it.

    The hue is per archetype, from the extended scale, because a field faded to a
    single grey is untraceable: at fourteen lines the eye cannot follow one line across
    the years, which is most of what a reader wants the meta chart for. Hue traces
    here, it does not name: the legend and the hover carry identity, and the extended
    scale claims none of the distinguishability the signed eight do. That
    scale opens on the signed eight in slot order, so an archetype holds its colour
    across every cut: a narrower cut is a prefix of a wider one, so it never repaints
    the survivors it shares (the reversal of ADR-0013's colour-by-position).

    ``tags`` is drawn in the order given, which is the caller's meaningful order: the
    cut passes them strongest-first, the manual panel in pick order.

    ``start_raised`` is how many of the leading lines open raised. A cut opens on its
    strongest few (:data:`_OPEN_RAISED`), so a cold start is a chart with a reading in
    it and the rest of the field behind them. A hand-picked set opens with every line
    raised, since each was named by the reader and a leading-few rule would make them
    choose the same archetypes twice. Either way the click goes both directions, on the
    same two layers, and a faded line stays on the canvas rather than leaving it.

    A second click raises a second line rather than swapping, since Plotly's own
    isolate handler cannot be used here (see the legend comment below). Two raised
    lines still hold their own hues, so the reader who wants a pair gets one.

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
    # Drawn in the caller's order, keeping only tags that have cells.
    drawn = [t for t in tags if t in by_tag]

    def trace(tag: str, colour: str, *, markers: bool = True, **overrides) -> pgo.Scatter:
        cells = by_tag[tag]
        archetype = cells[0].archetype
        return pgo.Scatter(
            x=[str(c.year) for c in cells],
            y=[c.share for c in cells],
            customdata=[[numfmt.share(c.share), numfmt.count_of(c.n, c.year_total, "decks")]
                        for c in cells],
            name=archetype,
            mode="lines+markers" if markers else "lines",
            line=dict(width=1, dash="dash", color=colour),
            marker=_observation_marker(colour),
            hovertemplate=(
                f"%{{x}} · {archetype} · %{{customdata[0]}} · "
                "%{customdata[1]}<extra></extra>"
            ),
            **overrides,
        )

    # Emphasis (§6): every archetype is drawn twice in one hue at two strengths. The
    # faded line is on screen from the first paint and carries no legend entry of its
    # own; its full-strength twin holds the legend entry and starts hidden, so a click
    # raises exactly one line out of the field. The two layers are added in passes, not
    # per archetype, so every raised line draws above every faded one rather than only
    # above the ones that happen to follow it.
    hues = palette.extended(drawn)
    for tag in drawn:
        # Line only. The observation marker's fill is the *opaque* surface, so a wide
        # cut would tile thirty-one archetypes' worth of discs over each other and chop
        # every faded line into segments, destroying the tracing the fade is for. The
        # marker's own rationale (an opaque fill so two rings do not cross into mud)
        # was written for a handful of full-strength lines, where the ring is visible;
        # at this opacity the ring is not, so all it leaves is the occlusion.
        fig.add_trace(
            trace(tag, _rgba(hues[tag], _CONTEXT_ALPHA), markers=False, showlegend=False)
        )
    for i, tag in enumerate(drawn):
        fig.add_trace(trace(tag, hues[tag],
                            visible=True if i < start_raised else "legendonly"))
    _style_trend_chart(fig, "Share of meta")
    # Under emphasis the legend is the control, so it says so, and both its click
    # handlers are pinned. `toggle` raises the clicked archetype alone; Plotly's
    # `toggleothers`, the one that sounds like the isolate this model asks for, is
    # unusable from a hidden start: its handler switches on the *clicked* trace's
    # visibility, and a `legendonly` one takes the branch that turns every trace in
    # the legend on (`case"legendonly": q(He,!0)` in plotly.min.js). Since every
    # raise starts hidden, that is the first click, and it would draw all fifteen
    # accent lines at once, the rainbow emphasis exists to retire. Double click is
    # switched off for the same reason: it defaults to `toggleothers`.
    _legend_below_plot(
        fig, _legend_title("click to raise or fade"),
        itemclick="toggle", itemdoubleclick=False,
    )
    return fig


def _adoption_figure(cards: list[tuple[str, Series]]) -> pgo.Figure:
    """One or more cards' adoption (share of that year's decks) over the years.

    A trace per card, so the subject and its co-occurrence pair can be compared on one
    axis. Each takes a direct hue from the shared eight-hue set by entity (§5), the
    subject first, so a card keeps its colour as the compare set changes rather than
    repainting on its position. Adoption carries no floor: every year
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
    # subject first, so a card keeps its colour as the compare set changes. The list is
    # the subject plus at most one co-occurrence card (#126), so the set never runs out
    # and no card reaches `assign`'s past-eight None.
    colours = palette.assign([name for name, _ in cards])
    for card_name, series in cards:
        colour = colours[card_name]
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
    _legend_below_plot(fig, "Card")
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
    _style_trend_chart(fig, f"Mean finish ({_SCORE_DIRECTION})")
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


def _race_hues(lines: list[list[RaceCell]]) -> dict[str, str]:
    """The drawn contenders' hues, keyed by pilot (§5).

    One assignment feeding both surfaces: the chart draws its lines in these and the
    leaderboard marks its rows with them, so a pilot's hue is the same thing in both
    places. Keyed by pilot rather than by rank, the ADR-0013 reversal, so a contender
    keeps their colour rather than inheriting the previous occupant's place.
    """
    return palette.assign([line[0].pilot for line in lines])


def _leaderboard_html(
    series: Series, names: dict[str, str], hues: dict[str, str], rows: int
) -> str:
    """The standings as a table: rank, pilot, score, the majors it rests on, and its width.

    The chart's companion, and the reason the eight-line cut can be honest about being
    a palette constraint: the table carries the standings past the cut, so a reader sees
    how close rank 9 was to rank 8 rather than taking the cut as a break in the field.
    Each drawn pilot is marked in their line's hue, which is what ties a row to a line;
    the rest are listed unmarked, since colour names eight and no more.

    Majors sits beside the score because it *is* the score's sample, so the two are on
    one span and a reader can see a high score resting on the fewest events allowed. The
    score is written to three decimals rather than the charts' two: the top of the board
    is separated by thousandths, and at two decimals a dozen contenders would print as
    ties they are not.

    The last column is what keeps the first one honest (ADR 0017). Three decimals of
    score over a median eight majors invites a reader to take the order as settled, and
    it is not: on the current record only about five of the top eight survive a resample
    of their own results. So every row states the interval its rank really sits in,
    beside the rank rather than in a footnote, because a rank of 4 that could be 17 is a
    different claim from a rank of 4 and a reader has to see the two together. It is the
    one column that can say the table is less certain than it looks.

    Display names are free text from the source, so they are escaped.
    """
    def row(cell: RaceCell) -> str:
        hue = hues.get(cell.pilot)
        swatch = (f"<span class='swatch swatch-hue' style='background:{hue}'></span>"
                  if hue else "<span class='swatch'></span>")
        return (
            "<tr>"
            f"<td class='rank'>{cell.rank}</td>"
            f"<td>{swatch}{html.escape(names[cell.pilot])}</td>"
            f"<td class='score'>{cell.score:.{LEADERBOARD_SCORE_PLACES}f}</td>"
            f"<td class='score'>{cell.majors}</td>"
            f"<td class='score spread'>{cell.rank_low}&ndash;{cell.rank_high}</td>"
            "</tr>"
        )

    standings = list({cell.pilot: cell for cell in series.cells}.values())[:rows]
    body = "".join(row(cell) for cell in standings)
    return (
        "<table class='leaderboard'><thead><tr>"
        "<th class='rank'>#</th><th>Pilot</th><th class='score'>Score</th>"
        "<th class='score'>Major events</th>"
        "<th class='score spread'>Rank could be</th>"
        f"</tr></thead><tbody>{body}</tbody></table>"
    )


def _race_caption(series: Series, drawn: int) -> str:
    """The race's caption: how much of the field is drawn, on what, and who is in it.

    Three things the lines cannot say for themselves, in the field-standing form
    :func:`_performance_caption` and :func:`_landscape_caption` share. The cut leads,
    because eight lines out of a field of a hundred and thirty-odd read as the whole
    field unless the caption says otherwise, and because where the cut falls is a
    palette constraint rather than a break in the data. Then what a score is measured
    on, since scoring the biggest events only is the chart's strongest assumption and
    is invisible in the picture. Then the faded layer, which is the one mark on the
    chart a reader cannot name from the picture (its predecessor, a p25-p75 band, was
    asked about on sight at first review). Then what a point *is*, which under the
    running score is the reading most at risk of being taken for its opposite: a rising
    line is a record filling in, not a pilot improving, and nothing in the picture says
    so (ADR 0017).

    The eligibility gates are deliberately not here. They were, and the caption grew to
    five clauses; the FAQ entry carries them, and none of the three readings above has
    another home. All app-built numerics off named constants, no user free text, so it is
    returned as trusted markup for :func:`_chart_heading`'s ``caption_html``.
    """
    contenders = len({cell.pilot for cell in series.cells})
    return (
        f"<div class='t-fieldstat'><span class='pct'>{drawn} of {contenders:,}</span> "
        "contenders drawn, best first"
        f"<span class='sample'> · scored on {series.cells[0].major_events:,} major "
        f"events, the ones with a field over {MAJOR_FIELD_SIZE}, and this is the only "
        "chart here that leaves the smaller events out · every other contender "
        "is traced faintly behind them · each point counts every major a pilot had "
        "played by then, so a line rises as their record fills in rather than as they "
        "improve</span></div>"
    )


def _standings_caption(series: Series, rows: int) -> str:
    """The standings table's caption: how much of the field it holds, and in what order.

    Stated as the rule the table applied rather than as the number of lines it drew, the
    same two readings :func:`_landscape_caption` keeps apart: a field larger than the cap
    is a top-N of it, and a field smaller than the cap was never cut at all and must not
    claim a ranking it did not apply.

    Then what the last column's numbers are, since "Rank could be" says the direction of
    the reading but not its strength, and a range with no confidence attached is not one.
    """
    contenders = len({cell.pilot for cell in series.cells})
    held = (f"top {rows:,} of {contenders:,} contenders" if contenders > rows
            else f"all {contenders:,} contenders")
    return (f"{held}, best first · the last column is where a pilot's rank landed "
            f"{RACE_INTERVAL:.0%} of the time when the record was resampled")


def _race_label(at: datetime) -> str:
    """A sample date's tick label: the month it falls in, ``"Jul 2026"``."""
    return f"{at:%b %Y}"


def _as_of_label(cell: RaceCell) -> str:
    """A sample date as the reading it is, for the hover: ``"by Jul 2026"``.

    "By", not a span: the point counts the pilot's whole record up to that date, and the
    predecessor's ``"Jan 2025 to Jul 2026"`` said the opposite of what the number now
    means (ADR 0017).
    """
    return f"by {_race_label(cell.as_of)}"


def _race_figure(
    lines: list[list[RaceCell]],
    context: list[list[RaceCell]],
    names: dict[str, str],
) -> pgo.Figure:
    """The best-player race: the leading contenders traced by what their record said.

    One line per contender in the trend-chart grammar the rest of the app draws in: a
    thin dashed join that only connects observations and asserts nothing between them,
    hollow rings whose area is the majors each point rests on, and a direct hue per
    pilot from the shared eight (§5). A point the tool refused for want of evidence is a
    gap rather than a bridged one, the same break a refused year leaves on the
    performance chart.

    The x axis is five discrete samples, not a continuous timeline, so it is a category
    axis labelled by the month each sample falls in; a point is every major the pilot
    had played by then and the hover says so (ADR 0017). The y axis is the score, left
    to fit the data rather than pinned to the full 0-to-1 range: contenders live in a
    narrow band near the top of the scale, and the field drawn behind them is what gives
    the height its meaning, where on the single-pilot chart the full range does that job.

    What the shape means, and it is not what a rolling chart would have meant: a line
    climbing is a record thickening under a pilot who was always this good, not a pilot
    improving, because a thin record is held near the field average until there is
    enough of it to say otherwise. The right edge is the leaderboard exactly, since a
    pilot's record by the newest event is their whole record.
    """
    fig = pgo.Figure()
    labels = [_race_label(cell.as_of) for cell in lines[0]]
    # The context first, so every drawn line sits over it. It is one trace holding every
    # other contender end to end, not one per pilot: 131 traces would fill the legend
    # with names hue cannot tell apart, and the point of the layer is the shape of the
    # crowd rather than any line in it. Each pilot's run is closed with a null y, or the
    # last point of one would join the first of the next into a sawtooth.
    #
    # It replaces a p25-p75 band (user call). The band summarised the field; this is the
    # field, so a reader sees the spread, the outliers, and where a drawn line really
    # sits inside the crowd rather than against a box standing in for it. Under the
    # running score it also carries a reading of its own: the crowd starts wide and
    # closes on the drawn lines as the record fills in, and at the right edge none of it
    # is above them, which is the chart saying how long it took to be sure (ADR 0017).
    xs: list[str] = []
    ys: list[float | None] = []
    for cells in context:
        xs.extend(labels + labels[-1:])
        ys.extend([cell.as_of_score for cell in cells] + [None])
    # A field small enough to draw whole has no "other" contender, so the layer is
    # dropped rather than added empty: an empty trace still claims its legend entry, and
    # the chart would advertise a set with nothing in it.
    if context:
        fig.add_trace(pgo.Scatter(
            x=xs, y=ys, mode="lines",
            # Lines only, no observation markers: their fill is the opaque surface, so
            # a hundred of them per sample would tile over each other and over the drawn
            # lines beneath, the same reason the meta chart's faded layer carries none.
            # The accent, not a ninth hue from the entity palette: it names nobody.
            line=dict(width=1, dash="dash", color=_rgba(_RACE_CONTEXT_COLOUR, _RACE_CONTEXT_ALPHA)),
            # Named "contenders" and not "the field", which on this tab already means an
            # event's field size ("a field over 64" in the caption above it).
            name="All other contenders", hoverinfo="skip",
            # Sorted past the pilots, so the legend reads as the standings with the
            # ground they are drawn on named at the end.
            legendrank=_RACE_LINES + 1,
        ))
    hues = _race_hues(lines)
    for rank, cells in enumerate(lines, start=1):
        pilot = names[cells[0].pilot]
        colour = hues[cells[0].pilot]
        fig.add_trace(pgo.Scatter(
            x=labels,
            y=[c.as_of_score for c in cells],
            customdata=[
                [_as_of_label(c),
                 # Three decimals, the leaderboard's precision rather than the charts':
                 # the right edge of this chart is the leaderboard by construction, so
                 # the two state one quantity and cannot state it two ways.
                 numfmt.score(c.as_of_score, LEADERBOARD_SCORE_PLACES)
                 if c.as_of_score is not None else "",
                 f"{c.as_of_rank} of {c.as_of_contenders}", c.as_of_majors]
                for c in cells
            ],
            name=pilot,
            mode="lines+markers",
            line=dict(width=1, dash="dash", color=colour),
            marker={**_observation_marker(colour),
                    "size": [_confidence_size(c.as_of_majors) for c in cells]},
            legendrank=rank,
            hovertemplate=(
                f"{pilot} · %{{customdata[0]}} · %{{customdata[1]}} · "
                # The rank's denominator is the contenders who had a record *by then*,
                # which is not the whole field the caption counts, so it is stated
                # rather than left to be assumed.
                "#%{customdata[2]} then · %{customdata[3]} major events so far"
                "<extra></extra>"
            ),
        ))
    _style_trend_chart(fig, f"Score ({_SCORE_DIRECTION})")
    # A bounded 0-1 score, so a plain decimal axis, overriding the shared styler's
    # percent format; and left to fit the data, overriding its zero-anchored range,
    # because the whole field lives in a narrow band near the top of the scale and
    # anchoring at zero would flatten the race into a strip. What keeps a height
    # readable is the field drawn behind it rather than the axis reaching the floor.
    fig.update_yaxes(tickformat=numfmt.SCORE_TICKFORMAT, rangemode="normal")
    # The samples in time order. The shared styler sorts categories alphabetically,
    # which on month labels puts "Jan 2025" before "Jul 2024".
    fig.update_xaxes(
        title="Every major played up to",
        categoryorder="array", categoryarray=labels,
    )
    # The legend is a key, not a control: this is a race, and a reader must not be able
    # to tick a pilot out of the field. Both handlers are pinned off, so a click on an
    # entry does nothing at all.
    _legend_below_plot(fig, "Standings", itemclick=False, itemdoubleclick=False)
    return fig


def _landscape_caption(
    series: Series, drawn: list[LandscapeCell], top_n: int, in_progress: bool
) -> str:
    """The landscape's caption: the 0.5 line, the cut, and the season it rests on.

    Three things the dots cannot say for themselves, in the field-standing form
    :func:`_performance_caption` already uses: the reading first with its number in the
    accent, the sample quiet behind it. The line leads, because "above 0.5" is only a
    reading if the reader is told what it means (above it, an archetype beat the middle
    of the field) and because more dots sit above it than below, which is a property of
    the source rather than of the archetypes, so the count is stated rather than left to
    be noticed. Counted on the drawn dots, not asserted: it runs 15 to 19 of 25 across
    the four corpus years, which "slightly more than half" would misdescribe. Then the
    cut, because a bounded chart that does not say what it is bounded to reads as the
    whole field. The cut is stated as the rule it is (``top_n`` of the field), not as
    the number of dots that survived it, so it stays true of a year where an unscored
    archetype leaves a gap, and a year small enough that nothing was cut says so rather
    than claiming a ranking it never applied. Then the season, because a year is the
    reader's only sample size here, and the latest one is partial (the corpus ends
    mid-year), so an in-progress year says so rather than being silently compared
    against a full one. All app-built numerics and no user free text, so it is returned
    as trusted markup for :func:`_chart_heading`'s ``caption_html``.
    """
    year = series.cells[0]
    # The flip is the chart's, so above the line reads as a raw norm below 0.5.
    above = sum(1 for cell in drawn if cell.mean_norm < 0.5)
    field = len(series.cells)
    cut = (f"top {top_n} of {field} archetypes by share" if field > top_n
           else f"all {field} archetypes the year held")
    season = f"{year.year_events:,} events, {year.year_total:,} decks"
    season = (
        f"{year.year} still in progress: {season} so far" if in_progress
        else f"{year.year}: {season}"
    )
    return (
        f"<div class='t-fieldstat'><span class='pct'>{above} of {len(drawn)}</span> "
        "above the 0.5 line, the middle of the field"
        f"<span class='sample'> · {cut} · {season}</span></div>"
    )


def _landscape_figure(cells: list[LandscapeCell]) -> pgo.Figure:
    """One year's metagame as a scatter: meta share against mean finish.

    One dot per archetype, and the quadrants are the point: popular and winning, niche
    and winning, popular and losing. A ranked table cannot show that trade-off, which
    is why this is a scatter and not a leaderboard. The x axis is the archetype's share
    of the year's decks, linear (at 25 dots the share range is only 6 to 11x and steps
    down smoothly, so a log axis crowds the left in two of the four corpus years and
    helps in one, while linear keeps the axis truthful and needs no caveat). The y axis
    is the finish inverted to a higher-is-better score, the same scale and the same 0.5
    reference the pilot charts use, so the three read alike while the tool keeps the raw
    ``placementNorm`` (0 a win) the agent reads.

    Every dot carries its archetype name: the set is bounded to
    :data:`_LANDSCAPE_TOP_N`, so labelling a subset of it would read as arbitrary, and
    an all-hover chart says nothing in a screenshot (§14, #132/#133). Plotly has no
    collision avoidance, so if the labels overprint in the browser the fallback is the
    top 8 by share plus the top 5 and worst 3 by finish, about 14 labels. The ring's
    size carries the archetype's distinct events, not its decks: within a year share is
    monotone in decks, so sizing by decks would say the same thing the x axis already
    does, while the events are the independent trials the finish rests on. One colour
    throughout, the palette's first slot: the dots are one series (archetypes), so hue
    would encode nothing (§5 tops out at eight hues, and this draws 25).
    """
    scores = [1 - cell.mean_norm for cell in cells]
    colour = palette.CATEGORICAL[0]
    fig = pgo.Figure()
    fig.add_trace(pgo.Scatter(
        x=[cell.share for cell in cells],
        y=scores,
        mode="markers+text",
        text=[cell.archetype for cell in cells],
        textposition="top center",
        textfont=dict(color=_AXIS, size=11),
        marker={**_observation_marker(colour),
                "size": [_confidence_size(cell.events) for cell in cells]},
        customdata=[
            [cell.archetype, numfmt.share(cell.share),
             numfmt.count_of(cell.n, cell.year_total, "decks"), numfmt.score(score),
             cell.events]
            for cell, score in zip(cells, scores)
        ],
        # Let a dot and its label at the edge of the field draw over the axis rather
        # than being clipped out of the plot.
        cliponaxis=False,
        hovertemplate=(
            "%{customdata[0]} · %{customdata[1]} · %{customdata[2]} · "
            "%{customdata[3]} · %{customdata[4]} events<extra></extra>"
        ),
        showlegend=False,
    ))
    _style_trend_chart(fig, f"Mean finish ({_SCORE_DIRECTION})")
    # A linear share axis overriding the shared styler's category-Year axis, from zero
    # (a share reads against its whole, so the origin is not optional) and in the one
    # share format the meta chart's axis carries.
    fig.update_xaxes(
        title="Share of the year's decks", type="linear", categoryorder=None,
        tickformat=numfmt.SHARE_TICKFORMAT, rangemode="tozero",
    )
    # The 0-1 score, but ranged to the dots rather than pinned to the full scale the
    # single-pilot charts use: a year's finishes span as little as 0.43 to 0.62, which
    # a 0-to-1 axis would squash into a fifth of the height with 25 labels on top of
    # each other. The range always covers 0.5, so the reference line the caption
    # explains cannot autorange out of frame, and the headroom above is double, since
    # each dot's name is drawn over it.
    # Clamped to the ends of the score, since it is bounded: 2023's best archetype
    # finishes at 0.93, and unclamped headroom carried the axis to 1.09, which is axis
    # no dot could ever occupy. A label at the very top draws over the edge instead
    # (`cliponaxis` above), rather than the axis growing to hold it.
    low, high = min([*scores, 0.5]), max([*scores, 0.5])
    pad = 0.02 + (high - low) * 0.1
    fig.update_yaxes(
        tickformat=numfmt.SCORE_TICKFORMAT, autorange=False,
        range=[max(0.0, low - pad), min(1.0, high + pad * 2)],
    )
    # The reference line at 0.5: above it an archetype beat the middle of the field.
    # Quiet and unlabelled, as on the pilot charts; the caption above the plot says
    # what crossing it means.
    fig.add_hline(y=0.5, line=dict(color=_rgba(_AXIS, 0.55), width=1, dash="dot"))
    return fig


def _archetype_timeline_caption(name_a: str, name_b: str | None, series: Series) -> str:
    """The timeline's headline and the two things its points cannot say for themselves.

    The countable claim first, in the field-standing form the performance and landscape
    captions already use: the individual points are thin, so what a reader can quote is
    the count across the whole run. With two archetypes that is the win count over the
    events both were placed at ("finished better at 30 of 44"), the tally the
    shape of the band only suggests; with one it is the same count against the middle of
    the field, the 0.5 line the rest of the app already reads finishes against.

    Then the two caveats, quiet behind it. First, that a point is a **mean** of that
    archetype's ranked decks at that event and typically rests on one to three of
    them (measured over the whole graph: 88% of ``(archetype, event)`` points hold one
    to three ranked decks and the median is one). The pilot head-to-head's "each point
    is one real result, not an average" is exactly false here and must never be
    borrowed. Second, the shared-event restriction: stated as a definition when it
    is in force, and as what a second archetype would do when it is not, since adding
    one drops points from the first line and visibly reshapes it (Grixis attended 85
    events and Jund 73, but they shared 62).

    All app-built numerics, with the two display names escaped, so it is returned as
    trusted markup for :func:`_chart_heading`'s ``caption_html``.
    """
    a, b = html.escape(name_a), html.escape(name_b) if name_b else None
    # The same definition the tool floors on, so the denominator here is the count the
    # refusal would have named.
    comparable = comparable_points(series.cells, paired=b is not None)
    # The year selector above governs the scatter alone, which its own help text says
    # from that side; here the span is stated in three words rather than restating the
    # contrast, so the caption keeps to a headline and a short tail.
    span = "every year in the data"
    if b is None:
        # A lower norm is a better finish, so beating the middle of the field is a
        # mean under 0.5, the same reading the 0.5 line carries on every other chart.
        led = sum(1 for c in comparable if c.mean_norm_a < 0.5)
        headline = (f"{a} finished above the middle of the field at "
                    f"<span class='pct'>{led} of {len(comparable)}</span> events")
        restriction = "a second archetype narrows this to the events both attended"
        decks = f"each point averages {a}'s placed decks there, typically 1 to 3"
    else:
        wins_a = sum(1 for c in comparable if c.mean_norm_a < c.mean_norm_b)
        wins_b = sum(1 for c in comparable if c.mean_norm_b < c.mean_norm_a)
        leader, led = (a, wins_a) if wins_a >= wins_b else (b, wins_b)
        # A tie is a real answer, and naming one side the leader of a draw would not
        # be, so the two counts are stated instead.
        headline = (
            f"{a} and {b} finished better at <span class='pct'>{wins_a} each</span> "
            f"of {len(comparable)} shared events" if wins_a == wins_b
            else f"{leader} finished better at "
                 f"<span class='pct'>{led} of {len(comparable)}</span> shared events"
        )
        restriction = ("drawn over the events both attended, counted over the ones "
                       "both were placed at")
        decks = "each point averages that side's placed decks there, typically 1 to 3"
    return (
        f"<div class='t-fieldstat'>{headline}"
        f"<span class='sample'> · {restriction} · {decks} · {span}</span></div>"
    )


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


def _band_traces(points, colour_a: str, colour_b: str) -> list[pgo.Scatter]:
    """The translucent band between two lines, as one trace per side that leads.

    ``points`` is :func:`_between_line_polys`'s input, and the geometry is entirely
    its; this turns the polygons it yields into traces. The band is tinted with the
    colour of whichever line sits higher, so the eye reads the size and the direction
    of the gap at a glance without decoding the two lines apart. Each side's polygons
    collect into one trace, their subpaths joined by a ``None`` gap so ``toself``
    closes each on its own: two fill traces at most, not one per segment. Shared by
    the two rivalry charts (pilots and archetypes), which must not drift apart in how
    the band reads; the caller adds them before its lines, so the markers and the
    dashed joins draw on top.
    """
    bands = {True: ([], []), False: ([], [])}  # a_above -> (xs, ys)
    for xs, ys, a_above in _between_line_polys(points):
        bx, by = bands[a_above]
        if bx:  # a None gap separates this polygon from the previous one
            bx.append(None)
            by.append(None)
        bx.extend(xs)
        by.extend(ys)
    return [
        pgo.Scatter(
            x=bx, y=by, fill="toself",
            fillcolor=_rgba(colour_a if a_above else colour_b, 0.18),
            mode="lines", line=dict(width=0),
            hoverinfo="skip", showlegend=False,
        )
        for a_above, (bx, by) in bands.items() if bx
    ]


def _style_rivalry_chart(fig: pgo.Figure, legend_title: str) -> None:
    """The chrome the two date-axis rivalry charts share (pilots, and archetypes).

    Both plot a finish against the event's registration date, so both need the same
    four things, and holding them in one place is what keeps the archetype timeline
    "matching the pilot head-to-head's styling exactly" (issue #151) as either is
    edited. Only the legend's title differs, so it is passed in.

    A registration-date x-axis (ADR 0013), not the category-Year axis the shared
    styler sets, with a range slider as the time-range filter. Its mini-axis is fixed
    to an off-data band (the score is 0-1, this is 10-11), which parks the trace
    preview out of view: the slider stays a plain tinted control instead of a second
    copy of the lines that reads as a bug. A tint distinct from the plot marks it as a
    control, and a label centred under it says so, since an unlabelled strip reads as
    a stray band rather than as a filter. Then the 0-1 score (1 a win at the top),
    fixed to the full range so a small gap is not stretched, with the dotted 0.5 the
    performance chart also carries. The legend is a horizontal strip above the plot,
    not the shared styler's right-side default: an external right legend widens with
    the names in it and eats into the plot area, which drifts the paper-centred slider
    label off true centre.
    """
    # The axis states its own direction, as the performance and landscape charts do: a
    # bare "Finish" leaves a reader to guess whether the top of the plot is winning, and
    # this score is flipped from the raw placement so the guess is a coin toss.
    _style_trend_chart(fig, f"Finish ({_SCORE_DIRECTION})")
    fig.update_xaxes(
        title="Registration date", type="date", categoryorder=None, autorange=True,
        rangeslider=dict(
            visible=True, thickness=0.12,
            bgcolor=_rgba(_CONTROL_ACCENT, 0.12),
            bordercolor=_rgba(_CONTROL_ACCENT, 0.55),
            borderwidth=1, yaxis=dict(rangemode="fixed", range=[10, 11]),
        ),
    )
    # Centred both ways over the slider in paper coords (the band sits below the axis,
    # roughly y -0.09 to -0.32, so its middle is near -0.20); the bottom margin below
    # seats the slider. The app's accent, matching the slider tint it labels, against
    # the neutral chart.
    fig.add_annotation(
        x=0.5, y=-0.20, xref="paper", yref="paper", xanchor="center", yanchor="middle",
        showarrow=False, text="◀ Time range filter (drag to slice) ▶",
        font=dict(color=_rgba(_CONTROL_ACCENT, 0.95), size=11),
    )
    fig.update_yaxes(tickformat=numfmt.SCORE_TICKFORMAT, range=[0, 1], autorange=False)
    fig.add_hline(y=0.5, line=dict(color=_rgba(_AXIS, 0.55), width=1, dash="dot"))
    # Room below the axis for the slider band and its label (the shared styler sets a
    # tight b=8 for the label-free charts), and above for the centred legend.
    fig.update_layout(
        legend=dict(
            title=legend_title, orientation="h",
            xanchor="center", x=0.5, yanchor="bottom", y=1.02,
        ),
        margin=dict(t=48, b=90),
    )


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
    the number of pilots who entered at 10 of 107 events. The points are the data; the thin dashed line only joins them and
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

    # The band between the two lines, added first so the markers and the dashed joins
    # draw on top. The score inverts the finish (1 a win), a null left null so the band
    # breaks over an event a pilot did not score (ADR 0013).
    def flip(norm):
        return None if norm is None else 1 - norm
    fig.add_traces(_band_traces(
        [(c.date, flip(c.norm_a), flip(c.norm_b)) for c in cells], colour_a, colour_b,
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
    # The date axis, its range slider and label, the 0-1 score and its 0.5 reference,
    # and the legend strip: the chrome this shares with the archetype timeline. The
    # finish's sense rides the readout (score() -> "0.62 (1 = 1st)"), stated once, so
    # the axis title names the quantity without restating which end is a win.
    _style_rivalry_chart(fig, "Pilot")
    return fig


def _archetype_timeline_figure(
    name_a: str, name_b: str | None, series: Series
) -> pgo.Figure:
    """One archetype's finish over time, or two archetypes' over their shared events.

    The head-to-head's form at archetype scale, on the same registration-date x and the
    same flipped 0-to-1 finish (1 a win), so the two read alike: the points are the
    data, the thin dashed line only joins them, a dotted 0.5 marks the middle of the
    field, and a range slider under the axis slices the dates client-side. With two
    archetypes the band between the lines is tinted toward whoever is ahead, built by
    the same :func:`_between_line_polys` geometry and breaking over any event one
    side was not scored at. With one it is a single line filled to the axis, which is
    the same read against the axis rather than against a rival.

    What differs from the pilot chart is what a point rests on. A pilot brings one deck
    to an event, so its point is one real result; an archetype brings several, so this
    point is their mean, and usually a mean of very few (typically one to three ranked
    decks). The deck count rides the hover and the caption, not the marker: sizing the
    rings by it (as the year charts do, #151) cost more than it bought here, because this
    plot draws one point per *event* rather than per year, so a busy pair puts hundreds of
    rings of a dozen sizes on one axis and the lines stop being followable. The year
    charts keep :func:`_confidence_size`: a handful of points a year apart carry a size
    channel; sixty overlapping ones cannot.
    """
    cells = sorted(series.cells, key=lambda c: c.date)
    colour_a, colour_b = palette.CATEGORICAL[0], palette.CATEGORICAL[1]
    fig = pgo.Figure()

    def flip(norm):
        return None if norm is None else 1 - norm

    # The band first, so the markers and the dashed joins draw over it. Solo has no
    # second line to fill against, and fills to the axis on its own trace below.
    if name_b is not None:
        fig.add_traces(_band_traces(
            [(c.date, flip(c.mean_norm_a), flip(c.mean_norm_b)) for c in cells],
            colour_a, colour_b,
        ))

    sides = [(name_a, colour_a, [(c.mean_norm_a, c.decks_a) for c in cells])]
    if name_b is not None:
        sides.append((name_b, colour_b, [(c.mean_norm_b, c.decks_b) for c in cells]))
    for name, colour, values in sides:
        fig.add_trace(pgo.Scatter(
            x=[c.date for c in cells],
            y=[flip(mean) for mean, _ in values],
            # Filled to the axis only when the archetype is alone: with two lines the
            # filled region is the gap between them, and a second fill under each
            # would bury it.
            fill="tozeroy" if name_b is None else None,
            fillcolor=_rgba(colour, 0.18),
            customdata=[[numfmt.score(1 - mean), decks] if mean is not None
                        else [None, decks]
                        for mean, decks in values],
            name=name,
            mode="lines+markers",
            line=dict(width=1, dash="dash", color=colour),
            # Smaller than the shared observation ring: two common archetypes share most
            # of the corpus (Grixis and Lands, 59 of its 107 events), and at that spacing
            # the default 12px rings overlap into a band that buries the lines they sit
            # on. The pilot head-to-head is the same form through the same styler and
            # keeps the shared ring: it is drawn over the events one *pair of pilots*
            # both attended, and has not crowded. If it ever does, this size belongs in
            # one constant both rivalry charts read, not in two places.
            marker={**_observation_marker(colour), "size": 9},
            cliponaxis=False,
            hovertemplate=(
                f"%{{x|%d %b %Y}} · {name} · %{{customdata[0]}} · "
                "%{customdata[1]} decks<extra></extra>"
            ),
        ))
    # The same date axis, range slider, 0-1 score, 0.5 reference and legend strip the
    # head-to-head carries, from the one place both read it, which is what holds the
    # two to matching styling as either is edited (issue #151).
    _style_rivalry_chart(fig, "Archetype")
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

    # Every year the graph holds, newest first: the Archetypes year selector's choices
    # and its default. Read off the meta-share matrix already in hand, which is
    # rectangular over every year with decks, so the selector costs no query of its own.
    # Empty only for a graph with no archetype at all, which still has to build (a
    # deckless artifact and one whose decks carry no engine tag both build today), so
    # the default year is optional the way `latest_deck_year` is for the Meta cut.
    corpus_years = sorted({c.year for c in trend_series.cells}, reverse=True)
    latest_landscape_year = corpus_years[0] if corpus_years else None

    def _landscape_view(year: int):
        # The landscape's heading, figure, and refusal note for one year, as plain
        # values rather than `gr.update`s, so the tab can draw itself at build time
        # from the same code path the year selector re-draws through (the Meta
        # precedent: a Plotly aggregate needs no Draw button). Exactly one of the
        # figure and the note is ever set.
        try:
            series = run_series(ladybug.Connection(db), ArchetypeLandscape(year))
        except NotEnoughHistory as e:
            # One short line in the app's voice (#114, §14), phrased from the count
            # the refusal carries, so a year with a couple of archetypes reads
            # differently from a year with none.
            had = ("no archetype" if not e.found
                   else f"only {e.found} archetype" + ("" if e.found == 1 else "s"))
            return None, None, f"{year} has {had} with a finish to place on a landscape."
        drawn = _landscape_top(series, _LANDSCAPE_TOP_N)
        return (
            _chart_heading(
                "Metagame landscape",
                # The corpus's latest year is the year still filling: a snapshot cannot
                # hold decks from after it was taken, so its newest year is partial
                # unless the snapshot happened to land on a 31 December. Read off the
                # data rather than a calendar, so it follows the graph forward.
                caption_html=_landscape_caption(
                    series, drawn, _LANDSCAPE_TOP_N,
                    in_progress=year == latest_landscape_year,
                ),
            ),
            _landscape_figure(drawn),
            None,
        )

    def landscape_drawing():
        """The landscape card as the running line while its query runs (§8).

        The year dropdown has no Draw button to carry the running state, so it speaks
        here, in the note line the tab's refusals already use. The old figure is taken
        down rather than left sitting under a spinner: a stale plot beside a new year in
        the control reads as the answer to the new year, which is the very thing #114's
        running state exists to prevent.
        """
        return (
            gr.update(visible=False),                               # heading
            gr.update(visible=False),                               # plot
            gr.update(value=DRAWING_LABEL, visible=True),           # note
        )

    def draw_landscape(year):
        heading, fig, note = _landscape_view(int(year))
        return (
            gr.update(value=heading, visible=heading is not None),
            gr.update(value=fig, visible=fig is not None),
            gr.update(value=note, visible=note is not None),
        )

    # The timeline's own catalogue, offering every archetype that can draw a line (121
    # of 126 today) with its count in the label: this plot is the escape hatch for
    # everything the landscape's top 25 hides, so the offer is filtered by drawability
    # alone. `_distinguish` still runs over the finished labels, since two archetypes
    # can share a display name and would then share a label even with the count on it.
    drawable_archetypes = archetypes_with_history(catalogue)
    timeline_archetypes = _distinguish([
        (f"{name} ({events} events)", tag)
        for name, tag, events in drawable_archetypes
    ])
    # The names the headline and the legend use: the plain display name, suffixed
    # with the tag only where two archetypes share one, exactly as the pilot labels
    # are built. Not the dropdown label, which carries the event count as well and
    # would read as "Grixis (12 events) finished better at ..." in a sentence.
    timeline_labels = dict(reversed(pair) for pair in _distinguish(
        [(name, tag) for name, tag, _ in drawable_archetypes]
    ))

    def timeline_drawing(a: str | None, _b: str | None):
        """The timeline card as the running line while its query runs (§8).

        Same reasoning as :func:`landscape_drawing`, and the reason AC 9 was not closed
        by the Draw buttons alone: this card is built hidden, so on the first pick there
        was nothing on screen for Gradio's own indicator to paint over and the click
        looked like a no-op. With nothing picked the card stays down, since the real
        handler is about to hide it anyway and a card that flashes "Drawing…" before
        disappearing is worse than one that never moved.

        Takes the same two selectors as the handler it precedes, though only the first
        decides anything, so both steps of the chain read one input list rather than the
        wiring carrying two.
        """
        if not a:
            hide = gr.update(visible=False)
            return hide, hide, hide, hide
        return (
            gr.update(visible=True),                                # card
            gr.update(visible=False),                               # heading
            gr.update(visible=False),                               # plot
            gr.update(value=DRAWING_LABEL, visible=True),           # note
        )

    def draw_archetype_timeline(a: str | None, b: str | None):
        # Returns the card, the heading, the plot, and a refusal note. The card holds
        # the whole thing, so with nothing picked it hides rather than sitting empty
        # under the scatter (§12, the Meta manual panel's precedent). A second
        # archetype equal to the first collapses to the solo line, as the adoption
        # chart's second card does, rather than drawing one line twice.
        if not a:
            hide = gr.update(visible=False)
            return hide, hide, hide, hide
        second = b if b and b != a else None
        try:
            series = run_series(ladybug.Connection(db), ArchetypeTimeline(a, second))
        except NotEnoughHistory as e:
            # One short line in the app's voice (#114, §14), phrased from the count the
            # refusal carries. Refusal is a common path for a pair, not an edge case
            # (the median pair of the 105 best-covered archetypes shares 4 events and a
            # fifth share one or none), so it has to read as an answer about the two
            # archetypes rather than as a failure.
            if second is None:
                had = ("no event" if not e.found
                       else f"only {e.found} event" + ("s" if e.found > 1 else ""))
                note = f"{timeline_labels[a]} has {had} with a finish to place on a timeline."
            else:
                met = ("were never both placed at the same event" if not e.found
                       else f"were both placed at only {e.found} event"
                            + ("s" if e.found > 1 else ""))
                note = (f"{timeline_labels[a]} and {timeline_labels[second]} {met}, "
                        "so there is no run to compare.")
            return (
                gr.update(visible=True), gr.update(visible=False),
                gr.update(visible=False), gr.update(value=note, visible=True),
            )
        name_a = timeline_labels[a]
        name_b = timeline_labels[second] if second else None
        return (
            gr.update(visible=True),
            gr.update(
                value=_chart_heading(
                    "Finishes over time",
                    caption_html=_archetype_timeline_caption(name_a, name_b, series),
                ),
                visible=True,
            ),
            gr.update(value=_archetype_timeline_figure(name_a, name_b, series), visible=True),
            gr.update(visible=False),
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
            # Hand-picked, so every pick opens raised rather than the cut's leading
            # few: the reader has already said which lines they want, and opening any
            # of them faded would ask them to choose the same archetypes twice.
            gr.update(value=_trend_figure(trend_series, tags, start_raised=len(tags)),
                      visible=True),
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

    # The race is field-wide and carries no control at all, so it is computed once here
    # rather than behind a callback: one query at startup, and the tab below is the four
    # values it produced. Exactly one of the figure and the note is ever set.
    try:
        race = run_series(catalogue, BestPlayerRace())
    except NotEnoughHistory as e:
        race_heading = race_fig = race_table = race_standings = None
        # One short line in the app's voice (#114, §14), phrased from the count the
        # refusal carries, so a graph one contender short reads differently from one
        # holding no major at all.
        race_note = (
            "No pilot has" if not e.found
            else f"Only {e.found} pilot has" if e.found == 1
            else f"Only {e.found} pilots have"
        ) + " enough major events to race here yet."
    else:
        trajectories = _race_trajectories(race)
        drawn = trajectories[:_RACE_LINES]
        race_heading = _chart_heading(
            "The race", caption_html=_race_caption(race, drawn=len(drawn)),
        )
        race_fig = _race_figure(drawn, trajectories[_RACE_LINES:], pilot_labels)
        race_table = _leaderboard_html(
            race, pilot_labels, _race_hues(drawn), _LEADERBOARD_ROWS,
        )
        race_standings = _standings_caption(race, _LEADERBOARD_ROWS)
        race_note = None

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
        head=theme.build_head(),  # real favicon + social preview (#115)
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
            # Meta and Archetypes are both about archetypes, so each lede states the
            # reader question that is its own (#145): who is played, against who wins.
            gr.Markdown(
                "Who is being played, and how that has shifted year to year.",
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
                manual, _ = _clearable(
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

        # Archetypes sits directly after Meta and splits the two archetype questions
        # between them (#145): Meta keeps "who is played, over time", this owns "who
        # wins". Single-view, so no picker, and Plotly aggregates only, so no Draw
        # button either: the scatter is drawn at build time on the latest year and
        # re-draws on the year selector, the same way the Meta cut chart does.
        with gr.Tab("Archetypes") as archetypes_tab:
            gr.Markdown("## Archetypes")
            gr.Markdown(
                "Which archetypes actually win, and how many people are on them.",
                elem_classes="t-lede",
            )
            with gr.Group(elem_classes="control-panel"):
                landscape_year = gr.Dropdown(
                    choices=[(str(y), y) for y in corpus_years],
                    value=latest_landscape_year, label="Year",
                    # The two plots below read different spans, and they sit under one
                    # control panel (§13), so the year says which of them it governs.
                    # The timeline's own caption says the same from the other side.
                    info="The landscape only; the timeline below spans every year.",
                )
                timeline_a, _ = _clearable(
                    choices=timeline_archetypes, value=None, label="Archetype",
                    info=_PICK_TIMELINE_ARCHETYPE, elem_classes="primary-control",
                )
                timeline_b, _ = _clearable(
                    choices=timeline_archetypes, value=None,
                    label="Second archetype (optional, to compare)",
                )
            # A graph with no archetype at all has no year to open on, so the tab shows
            # the same shape of refusal a thin year does rather than failing to build.
            _ls_heading, _ls_fig, _ls_note = (
                _landscape_view(latest_landscape_year) if latest_landscape_year
                else (None, None, "This graph holds no archetype to place on a landscape.")
            )
            with gr.Group(elem_classes="insight-card"):
                landscape_heading = gr.HTML(
                    value=_ls_heading, visible=_ls_heading is not None, padding=False,
                )
                landscape_note = gr.Markdown(
                    value=_ls_note, visible=_ls_note is not None,
                )
                landscape_plot = gr.Plot(value=_ls_fig, visible=_ls_fig is not None)

            # The timeline under the scatter, its whole card hidden until an archetype
            # is picked, so the tab opens on the landscape alone with no empty card
            # below it (§12, the Meta focus panel's precedent).
            with gr.Group(elem_classes="insight-card", visible=False) as timeline_card:
                timeline_heading = gr.HTML(visible=False, padding=False)
                timeline_note = gr.Markdown(visible=False)
                timeline_plot = gr.Plot(visible=False)

            # Each of these two runs a real query, so each shows the running state first
            # and then fills (§8). Chained with `.then`, which unlike `.success` runs
            # whether the query returned or raised, so a refusal cannot leave the card
            # stuck reading "Drawing…".
            landscape_outputs = [landscape_heading, landscape_plot, landscape_note]
            landscape_year.change(
                landscape_drawing, outputs=landscape_outputs,
            ).then(
                draw_landscape, inputs=landscape_year, outputs=landscape_outputs,
            )
            # Either selector redraws, and the year is not among the inputs: the
            # timeline spans the whole corpus whatever the landscape is showing.
            timeline_outputs = [
                timeline_card, timeline_heading, timeline_plot, timeline_note,
            ]
            for control in (timeline_a, timeline_b):
                control.change(
                    timeline_drawing, inputs=[timeline_a, timeline_b],
                    outputs=timeline_outputs,
                ).then(
                    draw_archetype_timeline, inputs=[timeline_a, timeline_b],
                    outputs=timeline_outputs,
                )
            # The landscape is the one plot drawn before its tab is ever shown, so it is
            # the one that comes up at Plotly's fallback width; opening the tab measures
            # it against the card it actually sits in (theme.RESIZE_PLOTS_JS).
            archetypes_tab.select(fn=None, js=theme.RESIZE_PLOTS_JS)

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
                card, _ = _clearable(
                    choices=cards, label="Card", value=None, info=_PICK_CARD,
                    elem_classes="primary-control",
                )
                cards_view = gr.Dropdown(
                    choices=_picker(_CARDS_TAB), value=cards_default, label="View",
                )
                cov_board = gr.Dropdown(
                    choices=_BOARD_CHOICES, label="Board", value="", visible=cov_default,
                )
                cooc_card2, cooc_card2_row = _clearable(
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
                cov_go = gr.Button(DRAW_LABEL, variant="primary", visible=cov_default)
                cooc_go = gr.Button(DRAW_LABEL, variant="primary", visible=cooc_default)

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
                    gr.update(visible=is_co),   # cooc_card2_row
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
                outputs=[cov_board, cooc_card2_row, cooc_top_n, cooc_drop_lands,
                         cov_go, cooc_go, g_card_overview, g_card_cooccurrence],
            )
            # As on the Pilots tab, every control that determines a result hides the drawn
            # stacks: the shared card, and each view's own filters (the board, and the
            # co-occurrence second card, top-N, and land toggle).
            card.change(reset_card, outputs=reset_card_outs)
            for _control in (cov_board, cooc_card2, cooc_top_n, cooc_drop_lands):
                _control.change(reset_card, outputs=reset_card_outs)
            _draw_with_progress(
                cov_go, draw_card_overview, inputs=[card, cov_board],
                outputs=[cov_subject, cov_results, cov_usage_out,
                         cov_adopt_heading, cov_adopt_plot, cov_adopt_note],
            )
            _draw_with_progress(
                cooc_go, draw_cooccurrence,
                inputs=[card, cooc_card2, cooc_top_n, cooc_drop_lands],
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
                gem_archetype, _ = _clearable(
                    choices=archetypes, label="Archetype", value=None, info=_PICK_ARCHETYPE,
                )
                gem_go = gr.Button(DRAW_LABEL, variant="primary")
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

            _draw_with_progress(
                gem_go, draw_gems, inputs=gem_archetype,
                outputs=[gem_subject, gem_out],
            )

        with gr.Tab("Pilots"):
            gr.Markdown("## Pilots")
            gr.Markdown(
                "Explore any pilot's decks, rivalries, and placements over time.",
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
                pilot, _ = _clearable(
                    choices=pilots, label="Pilot", value=None, info=_PICK_PILOT,
                    elem_classes="primary-control",
                )
                pilots_view = gr.Dropdown(
                    choices=_picker(_PILOTS_TAB), value=pilots_default, label="View",
                )
                h2h_pilot_b, h2h_pilot_b_row = _clearable(
                    choices=pilots, value=None, label="Second pilot (required)",
                    info=_PICK_PILOT2, visible=h2h_default,
                )
                po_go = gr.Button(DRAW_LABEL, variant="primary", visible=po_default)
                h2h_go = gr.Button(DRAW_LABEL, variant="primary", visible=h2h_default)

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
                    gr.update(visible=is_h2h),  # h2h_pilot_b_row
                    gr.update(visible=is_ov),   # po_go
                    gr.update(visible=is_h2h),  # h2h_go
                    gr.update(visible=is_ov),   # g_pilot_overview
                    gr.update(visible=is_h2h),  # g_pilot_head_to_head
                ]

            reset_pilot_outs = [po_subject, po_results, h2h_subject, h2h_results]
            pilots_view.change(
                toggle_pilots_view, inputs=pilots_view,
                outputs=[h2h_pilot_b_row, po_go, h2h_go, g_pilot_overview, g_pilot_head_to_head],
            )
            # Any control that determines a result hides the drawn stacks so a stale
            # answer never sits under changed controls: the shared pilot, the second pilot.
            pilot.change(reset_pilot, outputs=reset_pilot_outs)
            h2h_pilot_b.change(reset_pilot, outputs=reset_pilot_outs)
            _draw_with_progress(
                po_go, draw_pilot_overview, inputs=pilot,
                outputs=[po_subject, po_results, po_nb_out, po_af_out,
                         po_perf_heading, po_perf_plot, po_perf_note],
            )
            _draw_with_progress(
                h2h_go, draw_head_to_head_view, inputs=[pilot, h2h_pilot_b],
                outputs=[h2h_subject, h2h_results, h2h_nb_out,
                         h2h_heading, h2h_plot, h2h_note, h2h_plot_card],
            )

        # The race sits after Pilots and asks the question that tab cannot: not how one
        # pilot did, but who the best of them were and when (#135). It is field-wide, so
        # it takes the Archetypes tab's shape rather than the Pilots one: no subject
        # picker, no Draw, and nothing to click on the chart. Everything on it is fixed
        # at build time, so unlike the landscape it needs no callbacks at all.
        with gr.Tab("Best player race") as race_tab:
            gr.Markdown("## Best player race")
            # Held inside the 62ch reading measure (`theme.MEASURE_CH`) so it sits on
            # one row: the longer first draft wrapped, and the measure is repo-wide.
            # It used to promise "how they rose and fell", which the chart could not
            # deliver: that movement measured as noise (ADR 0017).
            gr.Markdown(
                "Who the best pilots are, and when the record could tell.",
                elem_classes="t-lede",
            )
            with gr.Group(elem_classes="insight-card"):
                gr.HTML(value=race_heading, visible=race_heading is not None,
                        padding=False)
                gr.Markdown(value=race_note, visible=race_note is not None)
                gr.Plot(value=race_fig, visible=race_fig is not None)
            # The standings in their own card below the chart, and only when there is a
            # race to stand in: a refused race leaves no empty bordered table behind it
            # (§12, the timeline card's precedent).
            with gr.Group(elem_classes="insight-card", visible=race_table is not None):
                gr.HTML(value=_chart_heading("Standings", race_standings),
                        padding=False)
                gr.HTML(value=race_table, padding=False)
            # Drawn before its tab is ever shown, so like the landscape it comes up at
            # Plotly's fallback width until the tab is opened and it is measured against
            # the card it actually sits in.
            race_tab.select(fn=None, js=theme.RESIZE_PLOTS_JS)

        # FAQ is the last tab (#133): static content, no controls. Each question is its
        # own box in the same insight-card frame the plots use (§12), so the methodology
        # reads apart from the plot surfaces and is one click away rather than crowding
        # them. One box per row, each the full width of the page, and the answer runs the
        # width of its box: an FAQ answer is four or five sentences, so held to the
        # reading measure inside a wide box it broke into a narrow ragged column against
        # empty space, and in two columns it did the same in half the width. The category
        # tag on each box replaces the table of contents the tab used to lead with: eight
        # tagged boxes are a scannable list already, so a contents list linking to what
        # the reader can see was a page of indirection. The boxes keep their ``faq-``
        # ids, the anchors a deep link uses.
        with gr.Tab("FAQ"):
            gr.Markdown("## FAQ")
            gr.Markdown(
                "How the headline numbers are calculated.",
                elem_classes="t-lede",
            )
            for _eid, _cat, _q, _a in _FAQ_ENTRIES:
                with gr.Group(elem_classes="insight-card faq-card", elem_id=_eid):
                    gr.HTML(
                        f"<span class='faq-tag faq-tag-{_cat.lower()}'>{_cat}</span>",
                        padding=False,
                    )
                    gr.Markdown(f"### {_q}\n\n{_a}", elem_classes="faq")

        # The coverage and contact surface (issue #115), below the tabs so it sits
        # under every one as a page footer: coverage read from this graph's own
        # counts, the last-updated date from the artifact's stamp, and who to reach.
        # It replaces the retired Gradio footer (theme.py) with the app's own line.
        gr.HTML(_provenance_html(coverage(catalogue), built_at(artifact)), padding=False)

    return demo
