"""Gradio explorer: controls emit a query spec, the spine returns a subgraph, and
the result is either drawn or refined.

Thin glue over the tested query, decision, and render seams. The controls pick an
entity and filters and build a ``QuerySpec`` (:mod:`graph7ph.query`); the spec
drives the shared spine via ``run_query``; the returned subgraph passes through
``assess`` (:mod:`graph7ph.explore`), which either clears it to render or, when it
would flood the view, refines instead of truncating. The app's own pure seams are
tested in ``tests/test_app.py`` (the captions, the states, and the tab tree a built
``Blocks`` holds); only the live wiring is verified by running it.
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
    GEM_TOP_CUT,
    MAX_GEM_LUCK,
    MAX_GEM_SHARE,
    MIN_GEM_DECKS,
    MIN_GEM_SLICE,
    CardCooccurrence,
    CardUsage,
    Coverage,
    HiddenGems,
    PilotAffinity,
    PilotNeighbourhood,
    Node,
    QuerySpec,
    Subgraph,
    card_catalogue,
    coverage,
    pilot_catalogue,
    run_query,
)
from graph7ph.provenance import built_at
from graph7ph.render import render_subgraph
from graph7ph.trends import (
    ArchetypeLandscape,
    ArchetypeTimeline,
    CardAdoptionOverTime,
    HeadToHeadTimeline,
    LandscapeCell,
    MetaShareOverTime,
    NotEnoughHistory,
    MAJOR_FIELD_SIZE,
    PerformanceCell,
    PilotPerformanceOverTime,
    PlayerLeaderboard,
    RACE_INTERVAL,
    RaceCell,
    Series,
    archetypes_with_history,
    beats_a_coin,
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
    result, and the too-large refine alert all speak through
    this, so the app has one voice when it has nothing to draw, reading the same as the
    Markdown refusal notes (the same ``.t-body`` type role as ``.prose p``). One short line
    in the interface's voice; the message is free text, so it is escaped into the markup."""
    return f"<div class='t-state t-body'>{html.escape(text)}</div>"


# The run_graph fallback for a spec that cannot be built (a subject not yet chosen).
# With the results stack hidden until a Draw with a subject, this is unreachable in
# normal flow; kept as a defensive value so a stray call renders something, not None.
_PROMPT = _state_message("Nothing picked yet.")

# The subject dropdowns carry no help text at all (§14, issue #156). They used to hold
# a "pick a pilot, then Draw" line each, which said what the label above and the Draw
# button beside them already say; help text that does not change the reader's choice is
# deleted rather than shortened. The one `info` left in the app is the Archetypes year's
# scope, which is a fact about the panel the reader cannot get from the control.
_YEAR_SCOPE = "Landscape only"

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


def _result_header(
    plot: str, filters: list[str], node_count: int, note: str | None = None
) -> str:
    """Frame a query result as an insight-card head (§12/§14): the plot type alone as
    the title (the subject is stated once above the cards, §14, not echoed here), the
    filters and how many nodes came back as the caption, so an answer is never left as
    an unlabelled graph. Prepended to the drawn result, the empty state, and the refine
    alert alike, so every post-query state speaks the same way. The filters are display
    labels (free text), so they are escaped into the markup.

    ``note`` is a second caption line for a legend the drawn picture needs, kept on its
    own row rather than joined into the first: the filters and the node count describe
    what was asked, and a legend describes how to read what came back. Only a drawn
    picture ever carries one, since neither an empty result nor a refused one has a
    mark to explain."""
    # A drawn result is under the render threshold (250 nodes), so the count needs no
    # thousands separator; the refine alert carries the large counts.
    tail = f"{node_count} node" + ("" if node_count == 1 else "s")
    caption = " · ".join([*filters, tail])
    head = (
        f"<div class='t-result-title'>{html.escape(_PLOT_LABELS[plot])}</div>"
        f"<div class='t-caption'>{html.escape(caption)}</div>"
    )
    if note:
        head += f"<div class='t-caption'>{html.escape(note)}</div>"
    return head


def _imputed_placement_note(subgraph: Subgraph) -> str | None:
    """The legend for a drawn placement's imputed mark, or ``None`` where none is drawn.

    The graph views' counterpart to :func:`_head_to_head_caption`, and there for the
    same reason (issue #166): a rank this project decided has to read apart from one
    the source counted, and the mark alone does not say which it is. Issue #199 is that
    the neighbourhood was the one surface printing a placement with neither, so 27 of
    4,591 decks over 26 pilots showed an undisclosed decided rank to anyone clicking
    through nodes. Twenty-seven, not the 51 decks carrying a rule: the other 24 are
    ``none``, a rule that recovered nothing, so they hold no placement and draw no node.

    Judged off the drawn labels rather than off the record, so the line appears exactly
    when a mark is on screen: a legend for an asterisk nobody can see is chrome (§14),
    and reading the labels is what makes the two agree by construction.
    """
    marked = any(
        node.kind == "Placement" and node.label.endswith(numfmt.IMPUTED_MARK)
        for node in subgraph.nodes
    )
    if not marked:
        return None
    return (
        f"{numfmt.IMPUTED_MARK} a placement this project worked out, "
        "not one the source recorded"
    )


# The smallest share the app's numeric convention writes: `numfmt.share` carries two
# decimals of percent, so 0.01% is its last step and everything under half of that reads
# as a flat "0%". The strongest gems land far below it, and "chance alone would never do
# this" is a different claim from "less often than we can write", so the table says
# "under the last step" instead of printing a zero it does not mean.
_SMALLEST_CHANCE = 0.0001


def _gem_luck_label(luck: float) -> str:
    """A gem's odds as the table prints them: a share, or an "under".

    The "under" case is decided by asking the convention rather than by comparing
    against a threshold of its own: the two would have to agree about where `share`
    starts rounding to nothing, and a threshold set a step out would misprint chances
    the convention can in fact write.
    """
    written = numfmt.share(luck)
    if written == numfmt.share(0):
        return f"&lt;{numfmt.share(_SMALLEST_CHANCE)}"
    return written


def _gem_table(subgraph: Subgraph) -> str:
    """The gems as a table: the claim each one makes, and how often chance alone makes it.

    The graph beside it draws which of an archetype's best decks run which card and
    carries no number at all, so the table is where the claim is actually stated. Five
    numbers per gem, in the order the claim is built (#184). **Archetype** is where
    every other number was measured, and it leads because nothing here is a
    format-wide statement: a card is rare, and lands well, *inside one archetype*.
    **Decks running it** is how rare, against that archetype's own ranked decks, and it
    is named for what it counts because "Decks" beside "In top N%" reads as two
    unrelated totals rather than a count and its subset. **In top N%** is
    the whole of the evidence: how much of the card sits in the archetype's best decks,
    over the cut :data:`GEM_TOP_CUT` names and this header prints, so the column and
    the constant cannot drift apart. **Pilots** says whether those decks are as many
    opinions or one pilot's, a
    distinction the deck count cannot make and #175 showed matters more than any
    other. **By chance** is the exact odds of a card that rare landing that much of
    itself in that cut by luck alone.

    Grouped by archetype and then ordered by those odds, longest first. Grouped rather
    than ranked outright, because a gem is a claim about one archetype and a flat
    ranking invites reading two archetypes' cards against each other, which is exactly
    the comparison the rule refuses to make; within an archetype the ranking is real.
    Ties fall back to the card name, so one artifact always draws one table.

    Every other archetype is banded, so where one block stops and the next starts is
    visible without reading the names down the column. Banded whole rather than ruled
    between, because a block is often a single row and a rule around one row reads as
    an emphasis on that row rather than as a boundary.

    Card and archetype names are free text from the source, so they are escaped.
    """
    archetypes = {node.id: node.label for node in subgraph.nodes
                  if node.kind == "Archetype"}
    # Which archetype a gem was measured inside is carried by the edge that draws it,
    # since the same card can be a gem in two archetypes and is then two nodes.
    named = {edge.target: archetypes[edge.source] for edge in subgraph.edges
             if edge.source in archetypes}
    gems = sorted(
        (node for node in subgraph.nodes if node.kind == "Card"),
        key=lambda n: (named[n.id], n.gem_luck, n.label),
    )

    def row(node: Node, band: bool) -> str:
        return (
            ("<tr class='band'>" if band else "<tr>")
            + f"<td>{html.escape(node.label)}</td>"
            + f"<td>{html.escape(named[node.id])}</td>"
            + f"<td class='score'>{node.decks}</td>"
            + f"<td class='score'>{node.top_decks}</td>"
            + f"<td class='score'>{node.pilots}</td>"
            + f"<td class='score spread'>{_gem_luck_label(node.gem_luck)}</td>"
            + "</tr>"
        )

    blocks = {arch: i for i, arch in enumerate(dict.fromkeys(named[n.id] for n in gems))}
    body = "".join(row(node, blocks[named[node.id]] % 2 == 1) for node in gems)
    return (
        "<table class='leaderboard'><thead><tr>"
        "<th>Card</th><th>Archetype</th><th class='score'>Decks running it</th>"
        f"<th class='score'>In top {GEM_TOP_CUT:.0%}</th>"
        "<th class='score'>Pilots</th><th class='score spread' title='How often plain "
        "luck would put this much of the card in the best decks'>By chance</th>"
        f"</tr></thead><tbody>{body}</tbody></table>"
    )


def _gem_caption(subgraph: Subgraph) -> str:
    """The gem table's caption: how many gems, the cut they were found in, and where.

    The last of those is the archetypes the rule could ask at all. Below
    :data:`MIN_GEM_SLICE` ranked decks the band is empty by construction, so those
    archetypes are skipped in the query rather than answered for, and today that is 84
    of the format's 124. Without the clause a reader whose archetype is absent reads
    "no gems here" off a page that means "not enough decks to tell", which is the
    distinction ADR 0012 raised `SliceTooSmall` for and ADR 0020 dropped along with the
    dropdown. Dropping the refusal removed the user to refuse, not the reason.

    The false-positive count is a different matter, and is deliberately not here. A
    list admitted on a probability threshold has
    a false-positive count whether or not it is printed, and this rule has no validation
    route behind it (a temporal holdout was built, run, and set aside: gems are transient
    by nature, so a card still looking like one is a card nobody acted on), so that count
    stays on the page. It is not on *this* line, because it is a property of the list and
    of no row in it: raised beside the table it asks "which ones?" at the one place with
    no room to answer, and the answer is long. It lives in `faq-gems-certainty` instead,
    inside the answer to that question, where the per-row reading (the odds column, and
    what Decks, In top and Pilots each rest on) can be given properly (issue #184).

    All app-built numerics off the drawn nodes, no user free text, so it is returned as
    trusted markup.
    """
    gems = [node for node in subgraph.nodes if node.kind == "Card"]
    return (
        f"<div class='t-fieldstat'><span class='pct'>{len(gems)} "
        f"gem{'' if len(gems) == 1 else 's'}</span>, each in more of its archetype's "
        f"best {GEM_TOP_CUT:.0%} than luck explains<span class='sample'> · only "
        f"archetypes with {MIN_GEM_SLICE} or more scored decks are checked</span></div>"
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
        f"Too much to draw: {plan.node_count:,} nodes, mostly {dominant.lower()}s, "
        f"against a limit of {plan.threshold}. Narrow the filters and Draw again."
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
            return HiddenGems()
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
# one click away so the plot surfaces stay scannable, plus the qualifiers #156 took off
# the captions. Each entry is (elem_id, category, question, answer); the id anchors the
# box for any deep link. The headline plots reduce to two shared primitives (a
# normalised finish and a year) plus one entry each, so both primitives are explained
# first and the rest lean on them. The category is the subject the answer is about,
# from `theme.FAQ_TAGS`, and the entries are held in category order: the tab renders
# them down the page in this order, so the boxes read as grouped by subject rather than
# as an arbitrary sequence.
#
# The review pass #142 asked for grew the tab from twelve entries to twenty, against
# the surfaces as they stand rather than as they stood when #141 wrote the first six.
# Three shapes came out of it. The graph views (usage, co-occurrence, archetype
# affinity) print rates whose denominators appear on no surface, so each got an entry.
# A "how settled is it" question is a sibling entry rather than a last paragraph, which
# is why `faq-landscape-certainty` exists beside `faq-landscape` the way
# `faq-race-certainty` and `faq-gems-certainty` already sat beside theirs. And two
# answers were not merely thin but wrong: the landscape blamed its own display cut on
# the source, and the gem odds described a rule ADR 0020 measured and rejected.
#
# **The answers are the app's one long-form surface, and they carry the app's one
# explanation of each quantity (§14, #156).** Two rules hold them together. Plain
# English: ordinary words, one idea per sentence, paragraphs rather than a wall, and
# only as much detail as the question needs. And one description per quantity: the
# bracket-only-event rule is defined once, in `faq-finish`, and every answer that
# depends on it ("see the finish question above") points there instead of re-deriving
# it, which is how the four charts that drop those events came to describe the same
# rule four different ways. An answer never restates what its own surface already says;
# what it is for is what the surface had no room for.
_FAQ_ENTRIES: list[tuple[str, str, str, str]] = [
    (
        "faq-finish",
        "Metric",
        'What does a "finish" mean, and why is it shown as a percentage?',
        "5th out of 200 is not 5th out of 12, so every placement is rescaled to where "
        "it landed in its own field: 1 is a win, 0 is last, and 0.5 is what a random "
        "finisher would average. Written as a percentage it says the same thing: 0.62 "
        "means the deck finished ahead of 62% of the field. That single number lets "
        "finishes from events of any size be compared and averaged. A deck is "
        "**scored** wherever a finish ended up on record for it; unscored decks still "
        "count as decks played, they just have no finish.\n\n"
        "The field a placement is measured against is the number the event published, "
        "where it published a usable one. Nine of the 107 events on record carry a "
        "field this project settled instead, eight of those at a floor of 24 that "
        "nobody counted. At the four teams events the field counts teams rather than "
        "pilots, so teammates share one placement. Those finishes are averaged in like "
        "any other, and the head-to-head is the only chart that marks the individual "
        "numbers it draws.\n\n"
        "Some events published only their top eight, not full standings. At those "
        "events the only finishes on record are the good ones, so their average is "
        "misleadingly high: 0.97, against 0.51 at an event that published its whole "
        "field. Every chart that averages finishes leaves those events out: the "
        "metagame landscape's vertical axis, the archetype timeline, a pilot's "
        "performance chart, and the player leaderboard. Two charts keep them. The pilot "
        "head-to-head plots single placements rather than averages, so there is nothing "
        "to distort; hidden gems asks its question inside one event at a time, "
        "comparing a card's decks only against the other decks of that same archetype "
        "at that same event. Counting decks is a "
        "different matter, and meta share, the landscape's sideways axis and card "
        "adoption all count every deck the source shipped, brackets included, because "
        "those decks were genuinely played.\n\n"
        "Where an average finish carries an error bar, the bar is the range that "
        "average could reasonably be in, 90% of the time, given how few events stand "
        "behind it. Every interval in the app is a 90% one. The spread it is built "
        "from is the whole field's rather than the two or three finishes under that one "
        "point, so a bar says how thin the evidence is, not how erratic that "
        "particular pilot or archetype was.",
    ),
    (
        "faq-year",
        "Metric",
        "Where does a year come from?",
        "The source records no date for an event at all. What it does record is when "
        "each deck was registered, so an event is dated by the earliest registration "
        "among its decks, and a year is the coarsest, safest slice of that: it is what "
        "the charts that cut up the metagame are grouped by, rather than anything "
        "finer.\n\n"
        "Every year axis, the year selector on the Archetypes tab, the label on the Meta "
        "tab's own control, and the span in the "
        "footer read that one number. The newest year is still filling, so its figures "
        "move as events are added. The two timelines are the exception, and place each "
        "event on its own registration date rather than in a year.",
    ),
    (
        "faq-meta",
        "Archetypes",
        'How is "Meta share over time" calculated?',
        "Each deck counts once, under its main archetype. An archetype's share in a "
        "year is its decks that year divided by every deck that year. Small numbers are "
        "kept rather than hidden: a thin share is a real sign of an archetype arriving "
        "or leaving, and a year with none is a real zero, not a gap.\n\n"
        'The "Top 25 / 50 / 75%" control only changes how many lines are drawn. '
        "Archetypes are ordered by their share of the most recent year, and the biggest "
        "are kept until they add up to that percentage.\n\n"
        "The chart opens with the three biggest archetypes at full strength and the "
        "rest faded. A faded line is the same real data, not an estimate, and clicking "
        "a name in the legend raises that line without hiding the others.",
    ),
    (
        "faq-landscape",
        "Archetypes",
        'How is the "Metagame landscape" built?',
        "For the year you pick, each archetype is placed by two numbers: how far "
        "right it sits is its share of that year's decks, and how high it sits is the "
        "average of its finishes. The dot's size is how many separate events those "
        "finishes came from.\n\n"
        "The two directions count different decks, and the hover shows both. The "
        "share counts every deck played. The height uses only events that published "
        "full standings (see the finish question above); an archetype with none of "
        "those still counts in the year's total but is not drawn.\n\n"
        "Only the 25 most-played archetypes of the year are drawn, and the caption "
        "says how many the year held in all.",
    ),
    (
        "faq-landscape-certainty",
        "Archetypes",
        'How settled is the "Metagame landscape"?',
        "Each dot carries an error bar: the 90% range its average could reasonably sit "
        "in (see the finish question above). Most bars cross the 0.5 line, so most dots "
        "could sit on either side of the middle. That is why "
        "the caption gives two numbers: how many dots sit above the line, and how many "
        "are far enough above that their whole bar clears it. A dot just above the line "
        "could easily be there by luck.\n\n"
        "More dots sit above the line than below, and that is a real pattern rather "
        "than a fault in the data. The 25 most-played archetypes of a year do finish "
        "better than the ones the cut leaves out, in every year on record. Taken across "
        "the whole field, once the top-eight-only events are gone, the average finish "
        "lands within about a hundredth of the middle, so nothing lopsided is left in "
        "the source that could account for the tilt. Read it as what it is: a chart of "
        "the year's most-played archetypes, which are also the year's better-performing "
        "ones.",
    ),
    (
        "faq-archetype-timeline",
        "Archetypes",
        'How is an archetype\'s "Finishes over time" built?',
        "Each point is one event, placed on the date its first deck was registered "
        "(see the year question above). Its height is the average of that archetype's "
        "finishes there, usually over just one to three scored decks, so a single point "
        "is thin evidence. The hover gives the exact count. Events that published only "
        "their top eight get no point (see the finish question above).\n\n"
        "The headline above the chart always shows a count, and it hedges itself "
        "whenever that count is one luck could easily produce. With a single archetype "
        "picked, the count is the events where it finished ahead of the middle of the "
        "field (see the finish question above), out of the events it was scored at, "
        "which is the number beside its name "
        "in the picker. Winning about half the time is what a coin does, and 102 of the "
        "121 archetypes the picker offers are that close, so most of the time the "
        "headline says the count could be luck.\n\n"
        "Picking a second archetype narrows the chart to the events both attended, and "
        "the headline counts how many of those each finished ahead at, hedged the same "
        "way. It counts every meeting the record settles, which is every point you can "
        "see: one event published just 16 of its 28 finishes, so an archetype can have "
        "been there with nothing of its own on record. Its decks finished below every "
        "one that event did publish, so the point is drawn at the best it could have "
        "been and marked with a ▽, meaning the real finish is that or worse, and the "
        "other archetype is counted the winner of that meeting. 72 of the 4,891 pairs "
        "the chart will draw carry one. Where neither archetype was scored there is "
        "nothing to compare, so that meeting is neither drawn nor counted and both "
        "lines break over the date instead.\n\n"
        "The chart spans every year in the data; the year selector above it changes "
        "the landscape only. If two archetypes were scored together at fewer than two "
        "events, the chart says so instead of drawing.",
    ),
    (
        "faq-pilot-identity",
        "Pilots",
        "Is a pilot one person, and where do the names come from?",
        "A pilot is whoever registered a deck, held together by the id the source gives "
        "them. The name is not the source's: it is recovered from the titles of that "
        "pilot's own decks, by taking whichever name appears most often across them.\n\n"
        "Names ending in a number, like \"Dan S 1\" and \"Dan S 2\", are one place a "
        "pilot may not be a single person. The source sometimes files several "
        "entries at one event under one id, which no one person can be, so this "
        "project separates them into numbered careers. 47 of the 1,086 names offered "
        "are one of these, and where one career ends and the next begins is this "
        "project's reading rather than a fact on record. Two numbered names sharing a "
        "stem may or may not be the same person, so a head-to-head between them is not "
        "a rivalry.\n\n"
        "It happens the other way round too: one person can be listed as two names. "
        "Two spellings are joined only when the names recovered from the titles match "
        "outright, so \"Josh V\" and \"Joshua V\" stay apart until a maintainer confirms "
        "they are one person. 123 of the names offered are currently paired with another "
        "that may be the same person, which is a question this project has not settled "
        "rather than an answer it got wrong. If you find yourself in here twice, the "
        "email and Discord at the foot of the page are the way to say so.",
    ),
    (
        "faq-performance",
        "Pilots",
        'How is a pilot\'s "Performance over time" calculated?',
        "For each year, the pilot's finishes are averaged into one score, drawn so "
        "higher is better (see the finish question above). A year needs at least two "
        "events, otherwise it is left as a gap, and a pilot needs at least two such "
        "years before the chart is drawn at all.\n\n"
        'The headline "finishes ahead of X% of the field" is the average across all '
        "scored years, weighted by events, so a busy year counts for more. It counts "
        "every event that published full standings (see the finish question above); "
        "the player leaderboard counts only the biggest ones. The two measure "
        "different things, and typically put a pilot about 10 places apart in the "
        "player leaderboard standings, which rank 137 pilots.\n\n"
        "Each year carries an error bar: the 90% range that year's average could "
        "reasonably sit in (see the finish question above). The bars are wide and they "
        "overlap, because a typical year rests on just three events. "
        "Shuffling a pilot's "
        "finishes into a random order moves the line about as much as their real "
        "career does, so a dip is not a slump and a rise is not improvement. Trust "
        "each year's value with its bar, and the career headline, not the shape of "
        "the line.",
    ),
    (
        "faq-head-to-head",
        "Pilots",
        'How is the "Head-to-head" timeline built?',
        "It plots the two pilots' finishes at the events they both entered, on the date "
        "the event's first deck was registered (see the year question above). Each "
        "point is one real placement, not an average, so the top-eight-only events the "
        "averaging charts drop (see the finish question above) are safe to keep here. "
        "Where only one of the two is on record at a meeting, what happens depends on "
        "how much of that event was published. At the one event that published 16 of "
        "its 28 finishes, the missing pilot finished below all 16, so their point is "
        "drawn at the best it could have been and marked with a ▽. At a top-eight-only "
        "event the same reasoning gives an answer too weak to draw: a cut that published "
        "a handful of finishes puts the missing pilot below only those few, which is most "
        "of the field and says almost nothing, so the line breaks over the meeting "
        "instead. Of the 214 pairs "
        "affected out of the 39,919 the chart will draw, 161 are drawn and 53 keep the "
        "break. A pair needs at least two meetings the record can compare them at, "
        "otherwise there is no trajectory to draw and the tool says so: turning up to "
        "the same event is not enough if the source scored neither of them there.\n\n"
        "A point's hover reads like 3 / 24: the placement, then the field it was ranked "
        "against. That second number is the field, not a headcount, and at a teams event "
        "it counts teams (see the finish question above). Placements can be shared, by a "
        "tie or by teammates, so two pilots sometimes draw the very same point.\n\n"
        "Each of those numbers is marked with a * separately where this project decided "
        "it, because they are decided separately. Sometimes the source recorded no "
        "placement at all and one was recovered from the deck's own title or from the "
        "event's cut: 3* / 24 is such a placement against a field the source counted, "
        "and 3 / 24* is a placement the source recorded against a field this project "
        "settled instead. A decided number is "
        "plotted rather than dropped, since leaving it out would quietly shorten a "
        "career, and the mark is there so it can be weighed for what it is.",
    ),
    (
        "faq-affinity",
        "Pilots",
        'On a pilot\'s "Archetype affinity", what do the dot sizes mean?',
        "It shows which archetypes a pilot plays, grouped under the broad class each "
        "belongs to (aggro, control and the like). Every dot and every link is measured "
        "in separate events rather than "
        "decks, so a day the pilot entered several variants counts once.\n\n"
        "About half the decks on record carry more than one archetype (2,332 of "
        "4,591), each weighted, and a deck counts only under its main one. So a "
        "pilot who played a Grixis deck with a little Storm in it is drawn playing "
        "Grixis, not both. Counting every tag instead read half the specialists in "
        "the record as generalists, which is the question this view exists to "
        "answer. Meta share reads a deck's archetype the same way, so the two "
        "name archetypes identically; they still count different things (decks "
        "there, events here), so their numbers are not meant to match.",
    ),
    (
        "faq-race",
        "Pilots",
        'How is the "Player leaderboard" scored?',
        "Only the biggest events count: fields over 64, about the top fifth of all "
        "events, so every pilot here is measured on the same kind of event. "
        "This is the only chart here that drops events for being small, so a pilot's "
        "standing here and their record elsewhere answer different questions and will "
        "not always agree (see the performance question above). Top-eight-only events "
        "are dropped too (see the finish question above).\n\n"
        "A pilot's score is the average of their finishes at those events, nudged "
        "toward the average score of everyone on the leaderboard when little evidence "
        "stands behind it. That average sits well above the middle of the field, since "
        "everyone here cleared a bar to get on it. A pilot with five such events "
        "is scored about half on their own record and half on everyone's; one with "
        "fifteen is scored mostly on their own. That stops one good weekend from "
        "outranking a long, strong record.\n\n"
        "To be on the leaderboard a pilot needs five of these events, two of them in "
        "the last year, so the leaderboard is who is playing now.\n\n"
        "Each point on a line is that pilot's score over everything they had played "
        "by that date, and a date they reached with fewer than two of these events is "
        "left blank, so a line can begin partway across the chart. A climbing line is "
        "a record filling in, not a pilot improving, and the last point is the score "
        "the standings rank them on. A recent-form version was tried and showed only "
        "noise, so the chart shows the record building up instead.",
    ),
    (
        "faq-race-winning",
        "Pilots",
        "Why is a pilot who has won majors ranked below one who never has?",
        "Because the score reads how far up the field a pilot finished and nothing "
        "else. Places are evenly spaced, so at a major of three hundred decks first "
        "counts barely three thousandths better than second, and a win is worth almost "
        "exactly what a near-miss is worth. A pilot who finishes near the top again "
        "and again will outrank a pilot with a trophy and a thinner record.\n\n"
        "A bonus for winning was built and measured, and it predicted a pilot's later "
        "results no better than the plain score did, so it was left out rather than "
        "added for the look of the thing. Read the standings as a measure of sustained "
        "finishing, not as a trophy count.",
    ),
    (
        "faq-race-certainty",
        "Pilots",
        'How settled is the "Player leaderboard" order?',
        "Less settled than three-decimal scores suggest. These are the biggest "
        "events, so there are only a couple of dozen of them, and a typical contender "
        "has played eight. That is not enough to separate pilots whose scores differ "
        "in the thousandths.\n\n"
        'The standings put a number on that. The "Rank CI" column (CI is short for '
        "confidence interval, which is a range rather than a single number) is built by "
        "re-picking each pilot's own results at random, repeats allowed, rescoring the "
        "whole leaderboard, and doing that a thousand times over. The column is the "
        "band of places a pilot landed in across 900 of those thousand runs. Near the "
        "top the bands are wide and overlap heavily, so read the leading group as a "
        "group, not as a 1-2-3.",
    ),
    (
        "faq-adoption",
        "Cards",
        'How is "Adoption over time" calculated?',
        "For each year, it is the share of decks that ran the card: decks with the card "
        "that year divided by all decks that year. Small numbers are shown rather than "
        "hidden and a year with none is a real zero (see the meta share question "
        "above).\n\n"
        "The board control changes the top of that fraction only. Picking \"Side\" "
        "counts only the decks that ran the card there, still against every "
        "deck played that year, so it narrows what counts as running the card but not "
        "what it is a share of.",
    ),
    (
        "faq-usage",
        "Cards",
        'What do the percentages on the "Usage" graph mean?',
        "Every one of them is a share of decks, but they do not all share a base, and "
        "they are not slices of one another.\n\n"
        "The card itself is labelled with its share of every deck in the graph. A link "
        "from the card to a broad class (aggro, control and the like) is the share of "
        "that class's decks running the card. A link from a class to an archetype is "
        "the share of that archetype's decks running the card, counted across the whole "
        "archetype rather than just the part sitting under that class. So a number never "
        "adds up to the one above it, and reading down a branch the percentages can "
        "climb as easily as fall.\n\n"
        "An archetype is drawn under whichever class its card-running decks mostly "
        "sit in. Its share counts the decks whose main archetype it is, the same decks "
        "meta share counts, so a deck carrying that archetype as a lesser tag beside a "
        "stronger one is not in either term. Counting every tag instead would describe "
        "a pool of other engines wearing this archetype's name: Golgari, for one, is "
        "tagged on 121 decks and is the engine of 8.",
    ),
    (
        "faq-cooccurrence",
        "Cards",
        'What does a percentage on the "Co-occurrence" graph mean?',
        "With one card picked, a partner card's percentage is how often the two turn up "
        "in the same deck *and* in the same board: decks running both in Main, or both "
        "in Side, out of all the decks running the picked card in "
        "either. Because the top of that fraction insists on one board and the bottom "
        "does not, the percentage reads lower than the share of decks that simply run "
        "both cards somewhere, sometimes far lower.\n\n"
        '"Cards to show" keeps the partners with the highest of these percentages. For '
        "a single card that is the same order as the decks they share with it, since "
        "every one of them is measured against the same total.\n\n"
        "With two cards picked, the hub in the middle is the decks running both of "
        "them, and every partner hanging off the hub is a share of that hub. The two "
        "links from the picked cards into the hub are the exception: each is a share of "
        "that card's own decks, so the hub's count is the wrong number to read them "
        "against.",
    ),
    (
        "faq-gems",
        "Cards",
        'What makes a card a "Hidden gem"?',
        "Everything is measured inside one archetype. Put that archetype's scored decks "
        "in order by finish (see the finish question above), and the best "
        f"{GEM_TOP_CUT:.0%} of them are its best decks. A card then has to pass two "
        f"tests. It has to be rare in the archetype: in at least {MIN_GEM_DECKS} of its "
        f"decks, but no more than {MAX_GEM_SHARE:.0%} of them. And it has to turn up in "
        "so many of that archetype's best decks that luck alone would manage it no more "
        "than one time in a hundred.\n\n"
        "That second test is asked inside each event, comparing the card's decks only "
        "against the other decks of the same archetype at that same event, which is why "
        f"the top-eight-only events are safe to keep here. An archetype needs "
        f"{MIN_GEM_SLICE} scored decks before the question can be asked at all, which "
        "today rules most of the format out.\n\n"
        "Nothing is compared across archetypes, so a card is never a gem just because "
        "its archetype wins a lot. The only question is whether the archetype's own "
        "best decks are the ones running it. That is also why the table is grouped by "
        "archetype rather than ordered best to worst down the page: a single ranking "
        "would invite exactly the comparison between archetypes the rule refuses to "
        "make. A card can be a gem in two archetypes at once; that is two separate "
        "findings.\n\n"
        "The graph shows each gem's archetype on one side and, on the other, every one "
        "of the best decks that run it, each openable on Moxfield. The decks drawn "
        f'around a card match its "In top {GEM_TOP_CUT:.0%}" column exactly. Decks of '
        "one archetype "
        "sit close together because many of its best decks run the same one card; a "
        "deck drawn between two cards runs both.",
    ),
    (
        "faq-gems-board",
        "Cards",
        "Does it matter which board a card is in?",
        "Not to the rule. A deck counts once whether the card sat in Main, in Side, or "
        'in both, so "Decks running it" is not a count of Main play alone. Some of the '
        "cards on the list today appear only in Side: they are cards the archetype's "
        "best decks bring alongside, and by this measure that is worth what playing one "
        "in Main is worth.\n\n"
        "It is worth knowing which before acting on a gem, since a card the best decks "
        "bring to a matchup is different advice from one they play every game. The link "
        "between a deck and a card says which board it was in.",
    ),
    (
        "faq-gems-certainty",
        "Cards",
        'How settled is a "Hidden gem"?',
        "Less settled than a printed list suggests. A card only counts as a gem if luck "
        "alone would produce its crowding no more than one time in a hundred (see the "
        "hidden gem question above). But every rare card of every archetype is put to "
        "that test, which is more than a thousand chances for a coincidence, so even a "
        f"bar of {MAX_GEM_LUCK:.0%} lets some cards through on luck alone: likely close "
        "to half the list, and nothing says which ones. The list cannot be checked "
        "against later results either: a gem that works stops being rare, so a card "
        "still looking like a gem a year on is a card nobody acted on.\n\n"
        "The odds are discounted for the pilots behind a card rather than counted per "
        "pilot. The same pilot is often behind several of a card's decks, and one "
        "pilot's decks rise and fall together, so a card in seven decks by three pilots "
        "is charged as about five and a half pieces of evidence rather than seven. The "
        "discount is deliberately partial: counting once per pilot instead was tried, "
        "and it left nothing on the list at all.\n\n"
        "A card's deck count is not all evidence either. Because the question is asked "
        "one event at a time, the card's decks at events where none of that archetype's "
        f"decks reached the best-{GEM_TOP_CUT:.0%} cut could not have landed there, and "
        "so say nothing either way. A card in seventeen decks with four in the best "
        f"{GEM_TOP_CUT:.0%} is usually a "
        "shorter story than that sounds: most of the seventeen never had the chance, and "
        "the odds rest on the few events that could have gone either way.\n\n"
        'The table says what each gem rests on. "Decks running it" is how rare the '
        f'card is in that archetype, "In top {GEM_TOP_CUT:.0%}" is how many of those '
        "landed in the "
        'archetype\'s best decks, and "Pilots" is how many people are behind them, '
        "which matters because a finish follows the pilot more than any card. A card "
        "carried by one pilot is kept and labelled, not hidden. Read a gem as a card "
        "worth trying, not as a card proven good.",
    ),
    (
        "faq-gems-unfiltered",
        "Cards",
        "Why can I not filter the hidden gems?",
        "Because there is nothing left to narrow. The rule is strict enough that the "
        "whole format produces well under a dozen gems across a handful of "
        "archetypes, which fits in one picture, so the tab draws all of them at once. "
        "It recalculates as decks are added, and if it ever finds more gems than the "
        "picture can hold, it draws the ones least likely to be luck and the caption "
        "counts what is shown.",
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

# The landscape draws taller than Plotly's 450px default, because it is the one chart
# whose furniture crowds out its own plot: 25 named dots and their intervals, over an
# axis carrying the share range filter. At 450 the filter's band, the tick labels and
# the axis title took 166 of it and left the dots 276px, 61 percent of the frame, which
# squeezed a year's finishes (0.43 to 0.62 in 2026) into a strip with 25 names over it.
# 640 leaves them 492. The same height the legend-below charts take, so the cards keep
# one rhythm down the page rather than each picking its own number.
_LANDSCAPE_HEIGHT = 640


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
    year with unscored archetypes (4 of 56, every one of them scored at a bracket alone
    and nowhere else, ADR 0022) and none of them fall inside a top 25.
    """
    ranked = sorted(series.cells, key=lambda c: (-c.n, c.tag))[:top_n]
    return [cell for cell in ranked if cell.mean_norm is not None]


# How many pilots the race draws as lines, and how many the leaderboard beside it
# lists. Both are display cuts the tool never sees, the same division of labour as
# `_LANDSCAPE_TOP_N`: `player_leaderboard` returns every contender's whole trajectory and
# these pick what is drawn and what is tabled.
#
# Eight lines because eight is where the shared palette's *named* hues stop
# (`palette.MAX_SLOTS`): past the eighth, the palette's own contract says a hue traces a
# line but does not name it, which would leave this chart's legend decorative when the
# legend is the only thing tying a line to a pilot. It claims no natural break in the
# data, and where it lands is coincidence either way: on the current record the gap from
# rank 8 to rank 9 is 0.00332, which happens to be the widest gap in the drawn set (six
# times the gap from 3 to 4, and wider than the gap between the top two), where on an
# earlier record it was 0.0007 and the cut fell mid-tier. Neither reading is the reason
# for eight. The leaderboard is what makes the surrounding order visible, which is most
# of why it is there.
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
# The reference a reader checks a mark against (:func:`_midpoint_line`), one step up the
# neutral ramp from the axis so it reads over the gridlines without taking a hue. It is
# neutral on purpose and not by omission: §5-6 spends colour on entities, and the
# palette refuses a ninth hue, so chrome that names no entity stays off the wheel.
_REFERENCE = theme.TOKENS["text-dim"]
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


# The glyph a bounded point takes, and the line under the plot that says what it means.
# Not `numfmt.IMPUTED_MARK`: the asterisk means "a number this project worked out in
# place of one the source did not record" (ADR 0016), and a bound is a different claim,
# an inequality rather than a value. Marking both with `*` would blur a distinction that
# ADR spent a provenance column per value establishing, so the bound carries its own
# glyph and its own line (ADR 0024).
#
# The plain `triangle-down`, never the `-open` variant, and that is load-bearing rather
# than a preference. An `-open` symbol draws no fill and strokes itself in
# `marker.color`, ignoring `marker.line` entirely; both rivalry charts set
# `marker.color` fully transparent so their rings read hollow over the band
# (:func:`_observation_marker`, ``over_fill=True``), so an open caret strokes in
# transparent and renders nothing at all. The plain symbol takes its outline from
# `marker.line` exactly as the rings beside it do, which is what makes it hollow here.
_BOUND_SYMBOL = "triangle-down"
_BOUND_LEGEND = (
    "▽ no finish published, so the point is drawn at the best it could have been"
)


def _bound_symbols(values) -> list[str]:
    """One marker symbol per point: a ring for a finish, a caret for a bound."""
    return [_BOUND_SYMBOL if is_bound else "circle" for _, is_bound in values]


def _swatch_pin(name: str, group: str, colour: str, marker: dict) -> pgo.Scatter:
    """A data-less trace that pins a series' legend swatch to the ring (issue #216).

    The legend swatch copies ``marker.symbol[0]`` straight off the trace (the bundled
    plotly.min.js legend code reads the array's first element), so a series whose
    chronologically first point is bounded took the caret as its swatch, presenting
    the whole line as "no finish published" when only one point is. The caller hides
    that trace's own legend entry and adds this one in its place: the same name, line
    and ring, a scalar symbol the swatch cannot misread, and no data to draw.
    ``group`` ties the two traces into one legend item so clicking the swatch still
    toggles the drawn line; it is the side's slot rather than its display label,
    because two distinct entities can share a label, and a label-keyed group would
    toggle both sides at once.
    """
    return pgo.Scatter(
        x=[None], y=[None], name=name, legendgroup=group,
        mode="lines+markers",
        line=dict(width=1, dash="dash", color=colour),
        marker=marker,
        hoverinfo="skip",
    )


def _bounded_readout(norm, is_bound: bool, tail: str = "", *, imputed: bool = False) -> list:
    """One point's hover pair: the score, and whatever the chart states beside it.

    A bounded point reads ``≤ 0.41 (1 = 1st)``, the inequality in the readout itself
    rather than in a footnote a reader has to carry back up to the mark, and its ``tail``
    is replaced: the deck count or the field ratio that sits there describes a finish on
    record, and a bounded point has none. A point the line breaks over gets nulls, which
    render no hover at all.

    ``imputed`` marks the bound's own number, and it is not the ADR 0024 distinction
    coming back. That decision was that ``*`` must not be the glyph for *being* a bound,
    since an inequality and a decided value are different claims; the ▽ and the ≤ carry
    that one. This is the orthogonal question of where the number inside the inequality
    came from, and it is the same question ``*`` answers everywhere else. A bound is
    ``published count / (fieldSize - 1)``, so it is the project's arithmetic exactly
    when the field size is: at ``GGWAD`` the 28 is Rule A's, which is why the scored
    side of that meeting already hovers ``9 / 28*``. Leaving the bound beside it bare
    said the source published it.
    """
    if norm is None:
        return [None, None]
    if is_bound:
        return [f"≤ {numfmt.score(1 - norm, imputed=imputed)}", "no finish published"]
    return [numfmt.score(1 - norm), tail]


def _observation_marker(colour: str, *, over_fill: bool = False) -> dict:
    """A hollow observation marker (ADR 0013) on a 2px surface ring (§6).

    The points are the observations, so they read as hollow rings in the series
    colour. The ring is a filled marker whose fill is the chart surface: on the
    surface it reads hollow, but where two markers overlap the top one's surface
    fill occludes the ring beneath it rather than letting the two rings cross into
    mud. The 2px outline is the series colour; the thin dashed join stays the
    caller's line, which only joins the points and asserts no trend between them.

    ``over_fill`` is for the callers that paint something under their points: the
    opaque fill is only a stand-in for hollow where the points sit on bare surface,
    and where a translucent band is painted beneath them it stops reading as hollow
    and punches a hole in the band instead (measured on the rivalry charts: 0 of 6
    band-facing half-discs matched the band on the pilot head-to-head, 1 of 67 on the
    archetype one, median channel delta 36, and every marker straddles the band edge
    by construction since the band's boundary is the line they sit on). Those callers
    get a fully transparent fill, so the ring reads against what it covers; they lose
    nothing to the occlusion the opaque fill buys, which is worth zero there (0 of 8
    markers overlap on the pilot chart at 1440 and at 390). The trade is the same one
    the faded meta lines make, where the markers are dropped outright because the
    opaque fill would chop the lines into segments.
    """
    fill = "rgba(0,0,0,0)" if over_fill else _SURFACE
    return dict(size=12, symbol="circle", color=fill, line=dict(width=2, color=colour))


def _interval_bars(
    cells: list[PerformanceCell | LandscapeCell | None], colour: str, cap: int
) -> dict:
    """The cells' 90% intervals as error bars, flipped onto the score axis (#175).

    Both charts that draw a mean ``placementNorm`` draw it flipped (higher is better)
    while the cell keeps the raw finish, so the raw **low** bound, the better finish,
    becomes the upper whisker: the arms are swapped here rather than at each call site,
    since getting that backwards would draw a plausible picture nobody could catch by
    eye. The arms are unequal wherever a bound clamped at a win or at last place, which
    is why they are sent as two arrays rather than one half-width.

    A cell of ``None``, or one whose mean was refused, contributes a ``None`` arm and
    draws no bar, keeping the arrays aligned with the trace's points. Drawn in the
    series colour at 45%, thin, so the interval reads as the point's own uncertainty
    rather than as a second series: it is wide by construction, and a heavy whisker
    would out-shout the value it qualifies. ``cap`` is the crossbar's width in pixels,
    for a chart with room for it; zero leaves a bare line.
    """
    return dict(
        type="data",
        symmetric=False,
        array=[None if c is None or c.mean_low is None else c.mean_norm - c.mean_low
               for c in cells],
        arrayminus=[None if c is None or c.mean_high is None else c.mean_high - c.mean_norm
                    for c in cells],
        color=_rgba(colour, 0.45),
        thickness=1,
        width=cap,
    )


def _midpoint_line(fig: pgo.Figure) -> None:
    """The 0.5 line every finish chart is read against, drawn once in the app's accent.

    Three charts (the pilot career, the landscape, and the rivalry pair) ask a reader to
    read a mark against the middle of the field, and each drew this line itself in the
    muted axis grey at 55%. That made the quietest mark on the chart the one three
    captions lean on: "above the 0.5 line" is the landscape's whole headline, and the
    line naming it was fainter than the gridlines around it.

    **Brighter, and deliberately not a colour.** The obvious fix, the app accent, was
    built and rejected on the drawn charts: the rivalry pair gives its second archetype
    the palette's slot-2 orange, so an accent line reads as a third series there, and
    no hue escapes that, because the meta chart draws all eight slots at once. §5-6
    settles it rather than taste: colour names entities, a line at 0.5 is no entity, and
    the palette refuses a ninth hue outright, so the neutral is the correct instrument
    and the lever for "more visible" is brightness. It moves up the neutral ramp from
    the dimmest ink in the set to :data:`_REFERENCE` at 85%, which is legible against
    the surface without competing with a series for attention.

    Still dotted and still a hairline, since it is chrome: a reference a reader checks
    a mark against, never a value in its own right.
    """
    fig.add_hline(
        y=0.5, line=dict(color=_rgba(_REFERENCE, 0.85), width=1, dash="dot")
    )


# The slider band's height on the page. Plotly takes its `thickness` as a fraction of
# the plot, which fattens the control as a chart grows: at the landscape's 640px the
# 0.12 this used to pass drew a 75px band, half again the 44 the same fraction gave at
# Plotly's 450 default. A control is furniture, and furniture does not scale with the
# data it sits under, so the fraction is derived from this per chart instead.
_SLIDER_BAND = 44

# The gap Plotly leaves between the x axis and the slider band, for the tick labels.
# It is a function of the tick font, not of the figure, so it holds at every height the
# app draws (measured 34px at 450, 620, 640 and 760).
_TICK_LABEL_ROOM = 34

# Plotly's own figure height, for the charts that state none.
_PLOTLY_DEFAULT_HEIGHT = 450


def _range_filter(fig: pgo.Figure, label: str) -> None:
    """A drag-to-slice range control under the x axis, tinted as a control and labelled.

    Shared by the charts whose x axis crowds: the two rivalry charts, where events pile
    up in the seasons the format was busiest, and the landscape, where a year's drawn
    archetypes bunch into the low-share end (in 2026, twenty of the twenty-five sit
    between 1% and 3%). Holding it here is what keeps one affordance from becoming two
    inventions, since a reader who learns the control on one chart should not have to
    learn it again on the next.

    The slider's mini-axis is fixed to an off-data band (every chart carrying this plots
    a 0-1 score, so 10-11 is empty), which parks the trace preview out of view: the
    slider stays a plain tinted control instead of a second copy of the data that reads
    as a bug. The tint marks it as a control against the neutral chart, and the label
    centred under it says so, since an unlabelled strip reads as a stray band. ``label``
    names the axis being filtered, because the two differ in what a drag means.

    The bottom margin is left to Plotly's own autoexpand, which sizes it to the band,
    the tick labels and the axis title exactly. Do not reserve room by hand: a request
    of 90px (which this held until the landscape was resized) is a floor autoexpand then
    adds its own content to, and the 55px of empty card it left under the axis title read
    as a broken card rather than as breathing room.
    """
    # `thickness` is a fraction of the plot the margins leave, so :data:`_SLIDER_BAND`
    # pixels is that many over this chart's own plot height. Read off the figure, which
    # is why the callers state their height and top margin before calling.
    margin = fig.layout.margin
    plot = (fig.layout.height or _PLOTLY_DEFAULT_HEIGHT) - margin.t - margin.b
    fig.update_xaxes(rangeslider=dict(
        visible=True, thickness=_SLIDER_BAND / plot,
        bgcolor=_rgba(_CONTROL_ACCENT, 0.12),
        bordercolor=_rgba(_CONTROL_ACCENT, 0.55),
        borderwidth=1, yaxis=dict(rangemode="fixed", range=[10, 11]),
    ))
    # Centred in the band, as a pixel offset from the bottom of the plot rather than a
    # fraction of it: the band is a fixed gap below the axis plus a fixed height, so its
    # centre is a constant number of pixels down whatever the figure's height, while the
    # equivalent fraction is not (it was -0.20 at 450px and would be -0.09 at 640). A
    # fraction has to be retuned every time a chart is resized, and a stale one drifts
    # the label to the edge of the band it names. The app's accent, matching the slider
    # tint it labels.
    fig.add_annotation(
        x=0.5, y=0, xref="paper", yref="paper", xanchor="center", yanchor="middle",
        yshift=-(_TICK_LABEL_ROOM + _SLIDER_BAND / 2),
        showarrow=False, text=label,
        font=dict(color=_rgba(_CONTROL_ACCENT, 0.95), size=11),
    )


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
    universe: list[str] | None = None,
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
    scale opens on the signed eight in slot order.

    ``universe`` is the stable entity set the hues are assigned over, which the drawn
    tags then only filter: colour follows the archetype, never its rank in this
    particular draw (§5, the reversal of ADR-0013's colour-by-position). Assigning over
    the drawn tags instead holds only while the caller's orders nest, which the cut's
    do (a narrower cut is a prefix of a wider one) and the manual panel's do not: it
    hands its picks back in pick order, so removing a middle chip slid every later pick
    up a hue (measured live: dropping Lands from Grixis/Lands/Blue Moon repainted Blue
    Moon #199e70 -> #d95926, and re-adding Lands did not put it back). One universe
    across both callers also settles the disagreement between the two charts on the
    tab, which used to draw the same archetype in two hues on one scroll.

    The cost: a hand-picked pair no longer draws slots 1 and 2, it draws the hues those
    archetypes carry everywhere else. What the universe must not cost is distinctness,
    and unfiltered it would: the scale is 32 hues over a 126-archetype universe, so four
    archetypes share each one, and the first hand-picked trio tried on the running app
    drew Artifact and Breakfast (universe 82 and 114, 32 apart) in the same
    ``#25d0d0``. :func:`palette.disambiguate` is what closes that, moving a line only
    where its hue is already taken on this chart. Left ``None``, the drawn tags are
    their own universe.

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
    hues = palette.disambiguate(palette.extended(universe or drawn), drawn)
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

    Two clauses used to follow the sample, and both have moved to ``faq-performance``
    (§14, issue #156): that the **movement** between the years is not real (permuted
    against the artifact, a pilot's drawn swing is 0.2376 against a shuffled 0.2404, so
    a dip is not a slump and the whiskers overlap for that reason rather than by
    misfortune), and that the **population** is not the race's. Both are still true and
    still stated; they are readings a reader takes once and keeps, not facts they need
    beside the number, and the caption they made was a paragraph.

    The one that came off the surface at some cost is the population clause, so it was
    fixed rather than just moved: it read "every event with a recorded finish counts
    here", which stopped being true when #191 dropped bracket-only events from this
    chart's mean. The FAQ now states the rule once, in ``faq-finish``, and this chart's
    answer points at it.

    The standing itself stays, and stays first. It is a mean over a pilot's whole record
    rather than one year of it, which is exactly the quantity this chart's sample sizes
    can support; the finding is about the movement between the points, not about the
    level they sit at.
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
        f"<span class='sample'> · {total} events over {len(scored)} scored "
        f"{years}</span></div>"
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
        # Beside the marker, not above it: the interval's upper whisker now occupies
        # the space over every point, and a label printed into it reads as a value on
        # the scale rather than as the sample behind the point (#175).
        textposition="middle right",
        textfont=dict(color=_AXIS, size=11),
        line=dict(width=1, dash="dash", color=colour),
        # The hollow ring's size carries each year's event count, so the sample size
        # is read from the marker and not only its label; a null year takes the base
        # size but draws no marker.
        marker={**_observation_marker(colour),
                "size": [_confidence_size(c.events) if c else 12 for c in drawn]},
        # Each point's own 90% interval, the whole point of the chart since the movement
        # between the points was measured as noise (#175). Capped, since a career holds
        # a handful of points and there is room to read the ends.
        error_y=_interval_bars(drawn, colour, cap=4),
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
                text="played, none counted" if not cell.events else f"{cell.events} ev, too thin",
                showarrow=False, font=dict(color=_AXIS, size=10),
            )
    # A reference line at 0.5, a random finisher's expected normalised rank (the flip
    # leaves it at 0.5): above it beat the field, below it trailed. Unlabelled, since
    # the field-standing caption above the plot already names where the pilot sits.
    _midpoint_line(fig)
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
        "<th class='score spread'>Rank CI</th>"
        f"</tr></thead><tbody>{body}</tbody></table>"
    )


def _race_caption(series: Series, drawn: int) -> str:
    """The race's caption: how much of the field is drawn, and what a score is measured on.

    A headline and one qualifier, in the field-standing form
    :func:`_performance_caption` and :func:`_landscape_caption` share (§14). The cut
    leads, because eight lines out of a field of a hundred and thirty-odd read as the
    whole field unless the caption says otherwise, and because where the cut falls is a
    palette constraint rather than a break in the data. Then what a score is measured
    on, since scoring the biggest events only is the chart's strongest assumption and is
    invisible in the picture.

    Two clauses came off in #156, both because the chart already carries them. The faded
    layer is a named legend entry ("All other contenders"), so a caption clause naming it
    again is the legend read aloud. And what a point *is* is the x-axis title ("Every
    major played up to"); the reading that follows from it, that a rising line is a
    record filling in rather than a pilot improving, is the first thing ``faq-race``
    says. The eligibility gates were never here, for the same reason.

    All app-built numerics off named constants, no user free text, so it is returned as
    trusted markup for :func:`_chart_heading`'s ``caption_html``.
    """
    contenders = len({cell.pilot for cell in series.cells})
    return (
        f"<div class='t-fieldstat'><span class='pct'>{drawn} of {contenders:,}</span> "
        "contenders drawn, best first"
        f"<span class='sample'> · scored on {series.cells[0].major_events:,} events "
        f"only, the ones with a field over {MAJOR_FIELD_SIZE} that published their "
        "standings</span></div>"
    )


def _standings_caption(series: Series, rows: int) -> str:
    """The standings table's caption: how much of the field it holds, and in what order.

    Stated as the rule the table applied rather than as the number of lines it drew, the
    same two readings :func:`_landscape_caption` keeps apart: a field larger than the cap
    is a top-N of it, and a field smaller than the cap was never cut at all and must not
    claim a ranking it did not apply.

    Then what the last column's numbers are, since "Rank CI" names an interval but not
    its confidence level, and a range with no confidence attached is not one.
    """
    contenders = len({cell.pilot for cell in series.cells})
    held = (f"top {rows:,} of {contenders:,} contenders" if contenders > rows
            else f"all {contenders:,} contenders")
    return (f"{held}, best first · Rank CI is where a pilot's rank landed in "
            f"{RACE_INTERVAL:.0%} of a thousand redraws of their own record")


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
    """The leaderboard's chart: the leading contenders traced by what their record said.

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
    """The landscape's caption: one claim about the 0.5 line, then the sample (§14).

    The reading first with its number in the accent, the sample quiet behind it, the
    field-standing form :func:`_performance_caption` already uses. The line leads,
    because "above 0.5" is only a reading if the reader is told what it means (above it,
    an archetype beat the middle of the field) and because more dots sit above it than
    below, so the count is stated rather than left to be noticed. Counted on the drawn
    dots, not asserted.

    That skew is a property of the drawn set, not of the source, and the FAQ said the
    opposite until #142 measured it: the top 25 by play average 0.4405 / 0.4756 / 0.4764
    / 0.4762 raw for 2023-2026 against 0.5408 / 0.5652 / 0.5812 / 0.5613 for the
    archetypes the cut leaves out, while the whole field sits 0.007 off the middle once
    ``_cut_only_events`` is dropped. The most-played archetypes really do finish better
    than the fringe, so the residual incompleteness is an order of magnitude too small
    to be the cause and the old wording told a reader to discount a real signal.

    **Two counts, because either alone misleads (issue #175).** The dots are means of a
    handful of decks, so counting the ones that merely sit above the line sells a
    reading the evidence does not carry: 14 to 19 of 25 land above it in each corpus
    year, but only 0 to 2 have an interval that clears it. Reporting only the settled
    count was tried first and was worse, for a reason that shows up only on the drawn
    chart: a reader looking at nineteen dots above the line was told "0 of 25", which is
    2026 today, and the caption lost an argument with the picture it was captioning. So
    the plain count
    leads, agreeing with what the eye does, and the settled count follows in the same
    breath as the qualifier it always was. The bars beside the dots are what makes the
    second number checkable, and explaining what a bar *is* is now ``faq-finish``'s job,
    with the reading of the two counts in ``faq-landscape-certainty``, rather than a
    third clause here (§14, issue #156).

    The tail is the sample, one clause: the cut and the season, the two things that
    decide what a reader may generalise from. The cut is stated as the rule it is
    (``top_n`` of the field), not as the number of dots that survived it, so it stays
    true of a year where an unscored archetype leaves a gap, and a year small enough
    that nothing was cut says so rather than claiming a ranking it never applied. The
    season says "so far" while the latest year is still filling (the corpus ends
    mid-year), so a partial year is never silently compared against a full one. All
    app-built numerics and no user free text, so it is returned as trusted markup for
    :func:`_chart_heading`'s ``caption_html``.
    """
    year = series.cells[0]
    # The flip is the chart's, so above the line reads as a raw norm below 0.5. Settled
    # is the stronger claim: an interval whose worse end is still better than the middle
    # of the field. A dot with no interval (a slice too thin to fit a spread over)
    # settles nothing, so it is counted as above but never as settled (#175).
    above = sum(1 for cell in drawn if cell.mean_norm < 0.5)
    settled = sum(
        1 for cell in drawn if cell.mean_high is not None and cell.mean_high < 0.5
    )
    field = len(series.cells)
    cut = (f"top {top_n} of {field} archetypes by share" if field > top_n
           else f"all {field} archetypes the year held")
    season = f"{year.year_events:,} events, {year.year_total:,} decks"
    season = (
        f"{year.year} so far: {season}" if in_progress
        else f"{year.year}: {season}"
    )
    return (
        f"<div class='t-fieldstat'><span class='pct'>{above} of {len(drawn)}</span> "
        "beat the middle of the field, the 0.5 line, and "
        # "none" rather than a bare "0", which is the common case here and reads as a
        # dropped number rather than as the answer it is.
        f"<span class='pct'>{settled or 'none'}</span> by more than their error bar"
        f"<span class='sample'> · {cut}, {season}</span></div>"
    )


def _label_sides(cells: list[LandscapeCell]) -> list[str]:
    """Which side of its dot each archetype's name sits on, alternating along the axis.

    Plotly has no collision avoidance, and the landscape's names overprint: a year's 25
    drawn archetypes crowd into the low-share end (in 2026, twenty of them sit between
    1% and 3%), so a single ``textposition`` stacks four or five names into the same
    patch of frame. The bars added in #175 took the space above each dot, which is where
    the names used to go, so they move beside the rings and alternate: sorted along the
    share axis they collide on, each dot takes the opposite side from its neighbour.

    A heuristic, and it is worth being plain about its limit rather than implying the
    problem is solved: two dots that are two apart in share and close in finish still
    take the same side, so a band holding four or more names can still overprint. It
    clears the common case (adjacent pairs) at the cost of no layout engine, which is
    the trade this chart's docstring already contemplated when it named a
    label-fewer fallback. That fallback stays available and unchosen: every archetype
    keeps its name here, which is what the top-25 cut exists to make possible.

    The alternation starts from the lowest share, which points the leftmost dot's name
    inward, away from the frame edge it sits against. That is the end worth protecting:
    it is where the dots crowd, and where a name hung outward would run at the axis.

    The highest-share end takes what the alternation gives it, which is inward only when
    the count is even. Forcing it inward regardless was tried and was worse: with an odd
    count, and ``_LANDSCAPE_TOP_N`` is 25, it overwrote a "middle right" and left the top
    **two** dots sharing a side, converting a clipping risk into the exact overprint this
    function exists to prevent. The trade is safe because the top dot is the most
    isolated on the axis by construction: share is long-tailed, so the gap below the
    biggest archetype runs 1.4 to 4.7 points across the corpus years against a median
    gap of 0.15, and a name with that much clear air beside it collides with nothing.
    """
    order = sorted(range(len(cells)), key=lambda i: cells[i].share)
    positions = [""] * len(cells)
    for rank, index in enumerate(order):
        positions[index] = "middle right" if rank % 2 == 0 else "middle left"
    return positions


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
    collision avoidance, and the labels did overprint once the bars took the space above
    each dot, so they sit beside the rings on alternating sides (:func:`_label_sides`)
    rather than falling back to the top 8 by share plus the top 5 and worst 3 by finish,
    the roughly 14 labels this docstring used to name: every archetype keeps its name,
    which is what the top-25 cut exists to make possible.

    Alternating sides clears colliding pairs and stops there, and a real year is worse
    than pairs: 2026 puts twenty of its twenty-five drawn archetypes between 1% and 3%
    of share, four and five names deep. The axis itself is the fix, so this chart takes
    :func:`_range_filter` over share, the control the rivalry charts already carry over
    time. A reader pulls the crowded end open instead of being handed a subset of the
    names, which is the same bargain the top-25 cut makes: bound what is drawn, and say
    what was bounded. The ring's
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
        textposition=_label_sides(cells),
        textfont=dict(color=_AXIS, size=11),
        marker={**_observation_marker(colour),
                "size": [_confidence_size(cell.events) for cell in cells]},
        # Each dot's own 90% interval (#175). Uncapped, unlike the pilot chart's: 25
        # dots and their names already fill this frame, and a crossbar on each would
        # read as a grid.
        error_y=_interval_bars(cells, colour, cap=0),
        customdata=[
            [cell.archetype, numfmt.share(cell.share),
             numfmt.count_of(cell.n, cell.year_total, "decks"),
             # The axis below already carries the sense (:data:`_SCORE_DIRECTION`), and
             # a "(1 = 1st)" inside a named half would read as part of the label.
             numfmt.score(score, sense=False), cell.scored, cell.events]
            for cell, score in zip(cells, scores)
        ],
        # Let a dot and its label at the edge of the field draw over the axis rather
        # than being clipped out of the plot.
        cliponaxis=False,
        # Named halves, because the dot's two axes are taken over different decks (ADR
        # 0022): every deck of the archetype places it horizontally, and only the ones
        # played at an event that published a field place it vertically. Each count sits
        # inside the half it belongs to, so "238 / 2,095 decks" and "224 scored" cannot
        # read as one number stated twice, or as a rounding error between them.
        hovertemplate=(
            "%{customdata[0]} · share %{customdata[1]} (%{customdata[2]}) · "
            "finish %{customdata[3]} (%{customdata[4]} scored at "
            "%{customdata[5]} events)<extra></extra>"
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
    # The one chart that states its own height (:data:`_LANDSCAPE_HEIGHT`), and it does
    # so before the filter, whose label is placed off the plot's height.
    fig.update_layout(height=_LANDSCAPE_HEIGHT)
    # The names crowd the low-share end past what any placement rule can separate, so
    # the reader gets the axis: drag to open that end up. Named for its own axis, since
    # a drag here slices share where the rivalry charts' slices time.
    _range_filter(fig, "◀ Share range filter (drag to zoom) ▶")
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
    # Ranged over the whiskers and not only the dots (#175): an interval drawn past the
    # top of the frame would show a narrower claim than the dot actually carries, which
    # is the overclaim this ticket removes, inverted. Both bounds are already clamped to
    # the ends of the score, so this can only widen the frame as far as the scale goes.
    #
    # It is paid for in exactly the currency the paragraph above spends: the dots'
    # own span drops from 62-79% of the frame to 28-56% across the corpus years, 2026
    # being the worst, because the frame is set by the widest whisker on the chart and
    # that whisker belongs to the least-evidenced dot (a one-event archetype carries a
    # half-width near 0.48). So the squashing this range was written to avoid is partly
    # reintroduced here, knowingly, and the labels sit closer together for it. The
    # trade is taken because a clipped interval misstates the evidence while a crowded
    # one only reads worse, and because the share filter above gives the reader a way
    # out of the crowding that no axis rule can give them out of a wrong bar.
    bounds = [b for cell in cells for b in (cell.mean_low, cell.mean_high)
              if b is not None]
    edges = [*scores, *(1 - b for b in bounds), 0.5]
    low, high = min(edges), max(edges)
    pad = 0.02 + (high - low) * 0.1
    fig.update_yaxes(
        tickformat=numfmt.SCORE_TICKFORMAT, autorange=False,
        range=[max(0.0, low - pad), min(1.0, high + pad * 2)],
    )
    # The reference line at 0.5: above it an archetype beat the middle of the field.
    # Unlabelled, as on the pilot charts; the caption above the plot says what crossing
    # it means, and this chart's headline is a count of dots against it.
    _midpoint_line(fig)
    return fig


def _archetype_timeline_caption(name_a: str, name_b: str | None, series: Series) -> str:
    """The timeline's headline, and the one thing the picture cannot say for itself.

    The countable claim first, in the field-standing form the performance and landscape
    captions already use: the individual points are thin, so what a reader can quote is
    the count across the whole run. With two archetypes that is the win count over the
    events both were scored at ("beat Jund at 30 of 44 shared events"), the tally the
    shape of the band only suggests; with one it is the same count against the middle of
    the field, the 0.5 line the rest of the app already reads finishes against.

    That count is gated on :func:`beats_a_coin` before it is allowed to read as a lead
    (issue #175). Each point is the mean of a median of one scored deck, so under the
    null every event is a coin flip, and 102 of the 121 headlines this surface can print
    are splits a fair coin produces at least a tenth of the time. The count itself is
    printed either way, because it is a fact about the record; what the gate governs is
    whether the sentence gets to sound like a finding, and an ungated one carries "a
    split a coin could produce" in the same breath as the number. A tie needs no gate,
    since it already asserts no leader.

    Then one qualifier, and only one (§14, issue #156): the span. The year selector
    sits directly above this chart and governs the landscape alone, so a reader who is
    not told otherwise will read these points as that year's. The two clauses this
    caption used to carry behind it are gone from the surface: that a point is a mean
    of one to three scored decks now lives in ``faq-archetype-timeline`` alone, and the
    shared-event restriction is already in the pair's own headline ("of 44 shared
    events"), so restating it under the plot said the same thing twice.

    All app-built numerics, with the two display names escaped, so it is returned as
    trusted markup for :func:`_chart_heading`'s ``caption_html``.
    """
    a, b = html.escape(name_a), html.escape(name_b) if name_b else None
    # The same definition the tool floors on, so the denominator here is the count the
    # refusal would have named.
    comparable = comparable_points(series.cells, paired=b is not None)
    span = "every year in the data"
    # A count that a fair coin produces is not a lead, and most of them are: the count
    # is printed either way (it is a fact) and only the reading is discounted (#175).
    hedge = ", a split a coin could produce"
    if b is None:
        # A lower norm is a better finish, so beating the middle of the field is a
        # mean under 0.5, the same reading the 0.5 line carries on every other chart.
        led = sum(1 for c in comparable if c.mean_norm_a < 0.5)
        headline = (f"{a} beat the middle of the field at "
                    f"<span class='pct'>{led} of {len(comparable)}</span> events")
        if not beats_a_coin(led, len(comparable)):
            headline += hedge
    else:
        # Compared at the values the chart plots, not at the raw means, so the headline
        # counts a bounded meeting to the side that was scored there. That is what the
        # record says (a bound is worse than every finish its event published, ADR
        # 0024), and it is what the reader is looking at. `comparable_points` admits a
        # point only where both sides draw, so neither value here is ever None.
        drawn = [(a_side[0], b_side[0])
                 for a_side, b_side in (c.drawn() for c in comparable)]
        wins_a = sum(1 for norm_a, norm_b in drawn if norm_a < norm_b)
        wins_b = sum(1 for norm_a, norm_b in drawn if norm_b < norm_a)
        leader, led = (a, wins_a) if wins_a >= wins_b else (b, wins_b)
        # A tie is a real answer, and naming one side the leader of a draw would not
        # be, so the two counts are stated instead. It asserts no leader, so there is
        # no lead for the coin to take away and it carries no hedge.
        if wins_a == wins_b:
            headline = (
                f"{a} and {b} each finished better at <span class='pct'>{wins_a}</span> "
                f"of {len(comparable)} shared events"
            )
        else:
            headline = (f"{leader} finished better at "
                        f"<span class='pct'>{led} of {len(comparable)}</span> "
                        "shared events")
            if not beats_a_coin(led, len(comparable)):
                headline += hedge
    caption = (
        f"<div class='t-fieldstat'>{headline}"
        f"<span class='sample'> · {span}</span></div>"
    )
    # The bound's legend, on its own muted line rather than as a second qualifier inside
    # the headline (§14 allows the headline one, and the span is it). Drawn only where a
    # caret is actually on the plot, which is the rule `_head_to_head_caption` follows
    # for the asterisk, so a legend never stands over a plot with nothing to explain.
    if _has_bounded_point(series.cells):
        caption += f"<div class='t-caption'>{html.escape(_BOUND_LEGEND)}</div>"
    return caption


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
    styler sets, with :func:`_range_filter` over it as the time-range filter, the same
    control the landscape carries over share. Then the 0-1 score (1 a win at the top),
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
    )
    # Room above the plot for the centred legend; the range filter seats itself below.
    # Set before the filter, not after: the filter's label is placed off the plot's own
    # height, which this margin takes from.
    fig.update_layout(margin=dict(t=48))
    _range_filter(fig, "◀ Time range filter (drag to slice) ▶")
    fig.update_yaxes(tickformat=numfmt.SCORE_TICKFORMAT, range=[0, 1], autorange=False)
    _midpoint_line(fig)
    fig.update_layout(legend=dict(
        title=legend_title, orientation="h",
        xanchor="center", x=0.5, yanchor="bottom", y=1.02,
    ))


def _head_to_head_caption(series: Series) -> str | None:
    """The legends for the plot's marks, as markup, or ``None`` where none is drawn.

    Every decided value the graph holds carries the rule that decided it, and until
    issue #166 no surface read one: the hover at Pats Birthday Brawl said "3 / 24"
    where 24 is Rule B's floor, a domain rule nobody counted, in the same shape
    SSWam uses for its counted 88. The mark makes the two different; one line
    says what it means, once for the plot rather than once per point (§14).

    Only drawn where a mark is, judged over the whole series. A point the line breaks
    over carries no label, so its provenance puts no asterisk on screen even where
    the graph records one (``normImputed = 'none'`` on a norm no rule recovered), and
    a legend for a mark nobody can see is chrome. The range slider is the one gap in
    that: it slices the dates client-side with no round-trip, so a reader who drags
    past the only imputed point keeps a legend for marks now off screen. Accepted
    rather than fixed, because recomputing this per drag is the server round-trip the
    slider exists to avoid, and a legend standing over an unmarked plot overstates
    nothing.

    Deliberately says the number is ours rather than which pass produced it: a reader
    needs to know they are looking at the project's arithmetic, and "Rule B" answers
    a question only the record can hold.

    Returned as trusted markup for :func:`_chart_heading`'s ``caption_html``, one
    ``t-caption`` div per legend, because the two are separate claims and have to read
    as separate lines. Joined into one escaped string they collapsed to a single
    run-on line in HTML, where the asterisk's sentence ran straight into the caret's
    and read as though it explained both. The legends are app constants with no
    free text in them, and each is escaped anyway.
    """
    marked = any(
        norm is not None
        and (c.field_imputed is not None or norm_rule is not None
             or placement_rule is not None)
        for c in series.cells
        for norm, norm_rule, placement_rule in (
            (c.norm_a, c.norm_imputed_a, c.placement_imputed_a),
            (c.norm_b, c.norm_imputed_b, c.placement_imputed_b),
        )
    )
    lines = []
    if marked:
        lines.append(
            f"{numfmt.IMPUTED_MARK} a number this project worked out, "
            "not one the source recorded"
        )
    # The bound's own line, on the same terms: drawn only where a caret is on screen,
    # and separate from the asterisk's because the two mark different claims (ADR 0024).
    if _has_bounded_point(series.cells):
        lines.append(_BOUND_LEGEND)
    # One div per legend, which is the shape the archetype timeline already gives the
    # bound, so the two rivalry charts present the same mark the same way (issue #151).
    return "".join(
        f"<div class='t-caption'>{html.escape(line)}</div>" for line in lines
    ) or None


def _has_bounded_point(cells) -> bool:
    """Whether any point on this series is drawn at a bound, so its legend line is owed.

    Asks the cell what it draws rather than testing ``bound_a is not None`` directly, so
    the legend can never claim a caret the figure did not draw: a bound is carried on a
    cell whenever the event allows one, and drawn only where the other side has a
    finish. Both rivalry cells answer ``drawn()``, so this needs to know neither which
    chart it is serving nor what its fields are called.

    It took a ``sides`` callback for as long as it re-derived the answer, and that was a
    seam things drifted through: the caption pulled four raw values off a cell and
    re-applied the rule with no idea whether the figure had drawn a b side at all, which
    is how 12 solo timelines came to offer a legend for a caret nobody could see.
    """
    return any(is_bound for c in cells for _, is_bound in c.drawn())


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
    the number of pilots who entered at those 4. The points are the data; the thin dashed line only joins them and
    asserts no direction. A translucent band fills between the two lines, tinted with
    the colour of whichever pilot is above, so the size and direction of the gap read
    at a glance; it splits at a crossing, carries through a meeting one pilot has no
    finish at where the event published enough to bound it (:func:`drawn_finish`, ADR
    0024), and breaks where nothing bounds it, which at this chart's bracket meetings is
    most of them. A dotted line at 0.5 marks a random finisher's expected
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
    # breaks over a meeting nothing bounds (ADR 0013).
    def flip(norm):
        return None if norm is None else 1 - norm

    # Where one pilot has no finish on record and the event published enough of its
    # field to bound the tail, they draw at the best they could have finished rather
    # than as a hole (ADR 0024). Most of this chart's unscored meetings are at brackets,
    # which bound nothing, so a break is still a common answer here.
    drawn_a = [c.drawn()[0] for c in cells]
    drawn_b = [c.drawn()[1] for c in cells]
    fig.add_traces(_band_traces(
        [(c.date, flip(na), flip(nb))
         for c, (na, _), (nb, _) in zip(cells, drawn_a, drawn_b)],
        colour_a, colour_b,
    ))

    # The two traces differ only in which half of each point they read, so a side is
    # a function off the point rather than a tuple rebuilt per pilot.
    def side_a(c):
        return c.placement_a, c.norm_a, c.placement_imputed_a, c.norm_imputed_a

    def side_b(c):
        return c.placement_b, c.norm_b, c.placement_imputed_b, c.norm_imputed_b

    def label(cell, side, drawn):
        """One point's hover pair: the score, and the finish over the field it was
        ranked against, with each of the three numbers marked where the project
        decided it rather than the source supplying it (issue #166). Marked one by
        one because they are decided one by one: a placement read off a deck title
        can sit against a field the source counted, and a minted norm against a
        field Rule B floored.

        A bounded point has no placement and no field ratio to state, so it takes the
        shared bounded readout instead (ADR 0024). Its one number is still marked on the
        same terms as the three here: the bound divides by the field, so it is the
        project's wherever the field is."""
        placement, norm, placement_rule, norm_rule = side(cell)
        drawn_norm, is_bound = drawn
        if is_bound or norm is None:
            return _bounded_readout(drawn_norm, is_bound,
                                    imputed=cell.field_imputed is not None)
        return [
            numfmt.score(1 - norm, imputed=norm_rule is not None),
            numfmt.count_of(placement, cell.field_size,
                            count_imputed=placement_rule is not None,
                            total_imputed=cell.field_imputed is not None),
        ]

    for group, name, colour, side, drawn in (("a", name_a, colour_a, side_a, drawn_a),
                                             ("b", name_b, colour_b, side_b, drawn_b)):
        # Over the band, so the fill goes transparent and the ring reads against
        # the tint it sits on rather than cutting a surface-coloured hole in it.
        ring = _observation_marker(colour, over_fill=True)
        symbols = _bound_symbols(drawn)
        # A first-point bound would become the legend swatch; the pin below carries
        # the legend entry in that case (:func:`_swatch_pin`, issue #216).
        pinned = symbols[0] == _BOUND_SYMBOL
        fig.add_trace(pgo.Scatter(
            x=[c.date for c in cells],
            # The finish inverted to a score (1 a win), matching the performance
            # chart. A null norm the record cannot bound is a gap the line breaks
            # across rather than a fabricated point.
            y=[flip(norm) for norm, _ in drawn],
            customdata=[label(c, side, d) for c, d in zip(cells, drawn)],
            name=name,
            legendgroup=group if pinned else None,
            showlegend=not pinned,
            mode="lines+markers",
            line=dict(width=1, dash="dash", color=colour),
            marker={**ring, "symbol": symbols},
            cliponaxis=False,
            hovertemplate=(
                f"%{{x|%d %b %Y}} · {name} · %{{customdata[0]}} · "
                "%{customdata[1]}<extra></extra>"
            ),
        ))
        if pinned:
            fig.add_trace(_swatch_pin(name, group, colour, ring))
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
    the same :func:`_between_line_polys` geometry. It carries through an event one side
    was not scored at where the record bounds that side's finish, the bound being the
    smallest gap the record allows (:func:`drawn_finish`, ADR 0024), and breaks where
    nothing bounds it. With one archetype it is a single line filled to the axis, which
    is the same read against the axis rather than against a rival, and it keeps ADR
    0013's break outright.

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

    # Each side's plotted finish: its own mean, or the best it could have finished at an
    # event that scored none of its decks (ADR 0024). Solo passes no second side, so
    # `drawn_finish` never reaches for a bound and the line keeps ADR 0013's break.
    drawn_a = [c.drawn()[0] for c in cells]
    drawn_b = [c.drawn()[1] for c in cells]

    # The band first, so the markers and the dashed joins draw over it. Solo has no
    # second line to fill against, and fills to the axis on its own trace below. Drawn
    # off the bounded values, so the band spans a bounded point rather than breaking
    # over it: at the bound the gap it shows is the smallest the record allows.
    if name_b is not None:
        fig.add_traces(_band_traces(
            [(c.date, flip(na), flip(nb))
             for c, (na, _), (nb, _) in zip(cells, drawn_a, drawn_b)],
            colour_a, colour_b,
        ))

    sides = [("a", name_a, colour_a, drawn_a, [c.decks_a for c in cells])]
    if name_b is not None:
        sides.append(("b", name_b, colour_b, drawn_b, [c.decks_b for c in cells]))
    for group, name, colour, values, deck_counts in sides:
        # Smaller than the shared observation ring: two common archetypes share most
        # of the corpus (Grixis and Lands, 59 of its 107 events), and at that spacing
        # the default 12px rings overlap into a band that buries the lines they sit
        # on. The pilot head-to-head is the same form through the same styler and
        # keeps the shared ring: it is drawn over the events one *pair of pilots*
        # both attended, and has not crowded. If it ever does, this size belongs in
        # one constant both rivalry charts read, not in two places.
        # Over a fill either way (the band with two archetypes, the tozeroy fill
        # with one), so the marker's fill goes transparent: solo measured 58 of its
        # 74 rings sitting on its own fill, none of them reading against it.
        ring = {**_observation_marker(colour, over_fill=True), "size": 9}
        symbols = _bound_symbols(values)
        # A first-point bound would become the legend swatch; the pin below carries
        # the legend entry in that case (:func:`_swatch_pin`, issue #216).
        pinned = symbols[0] == _BOUND_SYMBOL
        fig.add_trace(pgo.Scatter(
            x=[c.date for c in cells],
            y=[flip(norm) for norm, _ in values],
            # Filled to the axis only when the archetype is alone: with two lines the
            # filled region is the gap between them, and a second fill under each
            # would bury it.
            fill="tozeroy" if name_b is None else None,
            fillcolor=_rgba(colour, 0.18),
            # The deck count rides the hover on a real mean; a bounded point has no
            # scored deck to count, so it says what it is instead of printing "0 decks".
            customdata=[_bounded_readout(norm, is_bound, f"{decks} decks",
                                         imputed=cell.field_imputed is not None)
                        for (norm, is_bound), decks, cell
                        in zip(values, deck_counts, cells)],
            name=name,
            legendgroup=group if pinned else None,
            showlegend=not pinned,
            mode="lines+markers",
            line=dict(width=1, dash="dash", color=colour),
            marker={**ring, "symbol": symbols},
            cliponaxis=False,
            hovertemplate=(
                f"%{{x|%d %b %Y}} · {name} · %{{customdata[0]}} · "
                "%{customdata[1]}<extra></extra>"
            ),
        ))
        if pinned:
            fig.add_trace(_swatch_pin(name, group, colour, ring))
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

    # Key -> display label, for the callbacks that name an entity in a chart title
    # or a note. Both keyed off the full catalogue: since #119 one shared subject
    # dropdown per tab feeds every view (the full pilot/card list), so a label
    # lookup must cover every entity the dropdown offers, not a per-view subset.
    pilot_labels = {key: label for label, key in pilots}
    card_names = {canon: label for label, canon in cards}

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
    # The one entity universe both meta charts assign their hues over, so an archetype
    # keeps its colour whatever else is drawn beside it (§5). In latest-year rank order,
    # not the dropdown's alphabetical one: the extended scale stays distinct only for
    # its first 32 slots (§5's #117 amendment) and the widest cut draws 31 lines, so
    # rank order keeps exactly those 31 inside that prefix, where an alphabetical
    # universe would scatter them across the cycling 126-entry map and collide hues on
    # the chart that opens the tab. The 15 archetypes the full cut omits (cells in the
    # matrix, but no deck in the latest year) follow in the dropdown's own sorted order.
    trend_universe = list(dict.fromkeys(
        latest_year_share_cut(trend_series, 1.0) + [tag for _, tag in trend_archetypes]
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
            # Measured a no-op at all three cuts, since a cut is already a prefix of the
            # universe: passing it says so, rather than leaving that invariant to hold
            # the colours up by accident.
            _trend_figure(trend_series, tags, universe=trend_universe),
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
            # of them faded would ask them to choose the same archetypes twice. The
            # picks arrive in pick order, so the universe is what keeps a survivor's
            # colour still when a chip in the middle of the list is removed.
            gr.update(value=_trend_figure(trend_series, tags, start_raised=len(tags),
                                          universe=trend_universe),
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
        # panel is the guidance now), so this simply hides its three parts; a drawn result
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
        # different" guidance on the neighbourhood card);
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
            gr.update(
                value=_chart_heading(
                    "Head-to-head timeline",
                    caption_html=_head_to_head_caption(series)),
                visible=True,
            ),
            gr.update(value=fig, visible=True),
            gr.update(visible=False),
        )

    # The race is field-wide and carries no control at all, so it is computed once here
    # rather than behind a callback: one query at startup, and the tab below is the four
    # values it produced. Exactly one of the figure and the note is ever set.
    try:
        race = run_series(catalogue, PlayerLeaderboard())
    except NotEnoughHistory as e:
        race_heading = race_fig = race_table = race_standings = None
        # One short line in the app's voice (#114, §14), phrased from the count the
        # refusal carries, so a graph one contender short reads differently from one
        # holding no major at all.
        race_note = (
            "No pilot has" if not e.found
            else f"Only {e.found} pilot has" if e.found == 1
            else f"Only {e.found} pilots have"
        ) + " enough major events to rank here yet."
    else:
        trajectories = _race_trajectories(race)
        drawn = trajectories[:_RACE_LINES]
        race_heading = _chart_heading(
            "Score over time", caption_html=_race_caption(race, drawn=len(drawn)),
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
        subgraph = run_query(ladybug.Connection(db), spec)
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
        header = _result_header(
            view, filters, plan.node_count, _imputed_placement_note(subgraph)
        )
        if not subgraph.nodes:
            # The gem view has no filters to blame an empty result on: it is the whole
            # format, and empty means no card in it cleared the bar.
            return header + _state_message(
                "No card in the format clears the bar yet."
                if view == "meta_gems" else "No matches for these filters."
            )
        # The gem view states its evidence in a table above the picture (#176). Above,
        # not below it as the race's leaderboard sits under its chart: the graph fills
        # the frame, so a table under it is a screen away, and these numbers are the
        # ones that stop a threshold crossing reading as a settled fact.
        evidence = (
            _gem_caption(subgraph) + _gem_table(subgraph) if view == "meta_gems" else ""
        )
        return header + evidence + _embed(render_subgraph(subgraph))

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
                "Which archetypes actually win, and how many decks are on them.",
                elem_classes="t-lede",
            )
            with gr.Group(elem_classes="control-panel"):
                landscape_year = gr.Dropdown(
                    choices=[(str(y), y) for y in corpus_years],
                    value=latest_landscape_year, label="Year",
                    # The two plots below read different spans and sit under one control
                    # panel (§13), so the year says which of them it governs. Two words,
                    # and only from this side: the timeline's caption says "every year in
                    # the data" from the other, and §14 states a scope once (#156).
                    info=_YEAR_SCOPE,
                )
                timeline_a, _ = _clearable(
                    choices=timeline_archetypes, value=None, label="Archetype",
                    elem_classes="primary-control",
                )
                timeline_b, _ = _clearable(
                    choices=timeline_archetypes, value=None,
                    label="Second archetype (optional)",
                )
            # A graph with no archetype at all has no year to open on, so the tab shows
            # the same shape of refusal a thin year does rather than failing to build.
            _ls_heading, _ls_fig, _ls_note = (
                _landscape_view(latest_landscape_year) if latest_landscape_year
                else (None, None, "No archetypes to place on a landscape yet.")
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
                "How much a card is played, what it plays with, and how that changed.",
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
                    choices=cards, label="Card", value=None,
                    elem_classes="primary-control",
                )
                cards_view = gr.Dropdown(
                    choices=_picker(_CARDS_TAB), value=cards_default, label="View",
                )
                cov_board = gr.Dropdown(
                    choices=_BOARD_CHOICES, label="Board", value="", visible=cov_default,
                )
                cooc_card2, cooc_card2_row = _clearable(
                    choices=cards, value=None, label="Second card (optional)",
                    visible=cooc_default,
                )
                cooc_top_n = gr.Dropdown(
                    choices=[5, 15, 25], value=15,
                    label="Cards to show", visible=cooc_default,
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
        # §11). Since #184 it is also the app's one control-free view: the rule finds
        # the format's few genuinely concentrated rare cards, all of them fit in one
        # picture, and there is nothing left to filter by. So it draws at build time
        # like Meta's opening chart rather than behind a Draw with no choices in front
        # of it, and there is no subject line, because the subject is the format.
        with gr.Tab("Hidden gems"):
            gr.Markdown("## Hidden gems")
            gr.Markdown(
                # What the rule actually asks, in the reader's words: rare in one
                # archetype, and crowding that archetype's best decks. Not "cards that
                # overperform", which the corpus cannot support (ADR 0020).
                "Cards that are rare in an archetype yet keep turning up in its best "
                "decks.",
                elem_classes="t-lede",
            )
            gr.HTML(run_graph("meta_gems", {}), elem_classes="insight-card")

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
            # raised surface above the results. The results below open empty rather than
            # as duplicated prompt cards (§14): the label and the Draw button are the
            # invitation, and no control carries a "pick and Draw" sentence. The second
            # pilot and each view's Draw toggle with the picker (`toggle_pilots_view`).
            with gr.Group(elem_classes="control-panel"):
                pilot, _ = _clearable(
                    choices=pilots, label="Pilot", value=None,
                    elem_classes="primary-control",
                )
                pilots_view = gr.Dropdown(
                    choices=_picker(_PILOTS_TAB), value=pilots_default, label="View",
                )
                h2h_pilot_b, h2h_pilot_b_row = _clearable(
                    choices=pilots, value=None, label="Second pilot (required)",
                    visible=h2h_default,
                )
                po_go = gr.Button(DRAW_LABEL, variant="primary", visible=po_default)
                h2h_go = gr.Button(DRAW_LABEL, variant="primary", visible=h2h_default)

            # Pilot overview: one pilot, three plots, each in its own insight card. The
            # results stack is hidden until a Draw fills it (§14), so the view opens as
            # controls over empty ground rather than a row of duplicated empty-prompt
            # cards. The subject line sits above the stack.
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
                # picked (§14); with none the stack stays hidden and the panel above is
                # the whole surface. draw_performance returns (heading, plot, note).
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
                # missing pair keeps the stack hidden; a
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
        # pilot did, but who the leading pilots were and when (#135). It is field-wide, so
        # it takes the Archetypes tab's shape rather than the Pilots one: no subject
        # picker, no Draw, and nothing to click on the chart. Everything on it is fixed
        # at build time, so unlike the landscape it needs no callbacks at all.
        with gr.Tab("Player leaderboard") as race_tab:
            gr.Markdown("## Player leaderboard")
            # Held inside the 62ch reading measure (`theme.MEASURE_CH`) so it sits on
            # one row: the longer first draft wrapped, and the measure is repo-wide.
            # It used to promise "how they rose and fell", which the chart could not
            # deliver: that movement measured as noise (ADR 0017).
            gr.Markdown(
                "Who the leading pilots are, and how long the record took to say so.",
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
        # tag on each box replaces the table of contents the tab used to lead with: the
        # tagged boxes are a scannable list already, so a contents list linking to what
        # the reader can see was a page of indirection. The boxes keep their ``faq-``
        # ids, the anchors a deep link uses.
        with gr.Tab("FAQ"):
            gr.Markdown("## FAQ")
            gr.Markdown(
                "How the numbers here are worked out.",
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
