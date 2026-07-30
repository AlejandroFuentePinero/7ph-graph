import json
import math
import re
from datetime import datetime

import plotly.colors as pc
import pytest

from graph7ph import numfmt, palette, theme
from graph7ph.app import (
    CLEAR_LABEL,
    DRAWING_LABEL,
    DRAW_LABEL,
    _CARDS_TAB,
    _FAQ_ENTRIES,
    LEADERBOARD_SCORE_PLACES,
    _LANDSCAPE_HEIGHT,
    _LEADERBOARD_ROWS,
    _LEGEND_BELOW_HEIGHT,
    _SLIDER_BAND,
    _TICK_LABEL_ROOM,
    _RACE_LINES,
    _PLOT_LABELS,
    _draw_with_progress,
    _adoption_figure,
    _adoption_caption,
    _adoption_cards,
    _archetype_timeline_caption,
    _archetype_timeline_figure,
    _chart_heading,
    _embed,
    _gem_caption,
    _gem_table,
    _head_to_head_caption,
    _head_to_head_figure,
    _imputed_placement_note,
    _confidence_size,
    _landscape_caption,
    _landscape_figure,
    _landscape_top,
    _observation_marker,
    _PILOTS_TAB,
    _between_line_polys,
    _performance_caption,
    _performance_figure,
    _provenance_html,
    _result_header,
    _leaderboard_html,
    _race_caption,
    _race_hues,
    _standings_caption,
    _race_figure,
    _race_trajectories,
    _rgba,
    _subject_line,
    _trend_figure,
)
from graph7ph.query import (
    GEM_TOP_CUT,
    MAX_GEM_LUCK,
    Coverage,
    Edge,
    Node,
    Subgraph,
)
from graph7ph.trends import (
    MAJOR_FIELD_SIZE,
    MIN_CAREER_MAJORS,
    MIN_RECENT_MAJORS,
    RACE_INTERVAL,
    RACE_POINTS,
    RACE_RECENCY_MONTHS,
    AdoptionCell,
    ArchetypeTimelinePoint,
    HeadToHeadPoint,
    LandscapeCell,
    PerformanceCell,
    RaceCell,
    Series,
    SeriesCell,
    _months_before,
)


def _meta_series(*tag_year_share):
    """A meta-share Series from ``(tag, year, share)`` triples, one SeriesCell each."""
    return Series(cells=[
        SeriesCell(tag=t, archetype=t.title(), year=y, n=int(s * 1000),
                   share=s, year_total=1000)
        for t, y, s in tag_year_share
    ])


def test_pilots_and_cards_collapse_to_two_views_each():
    # Issue #126 fuses each subject's graph and trend behind one Draw: Pilots goes
    # 4 views -> 2 (Pilot overview, Head-to-head) and Cards 3 -> 2 (Card overview,
    # Co-occurrence). The expectations are v1 §11's amended table, an independent
    # source: a future edit that re-splits a tab or drops a view trips this.
    per_tab = {"Pilots": set(_PILOTS_TAB), "Cards": set(_CARDS_TAB)}
    assert [len(per_tab[t]) for t in ("Pilots", "Cards")] == [2, 2]

    assert set(_PILOTS_TAB) == {"pilot_overview", "pilot_head_to_head"}
    assert set(_CARDS_TAB) == {"card_overview", "card_cooccurrence"}


def test_hidden_gems_is_its_own_tab_and_meta_holds_meta_share_alone(tmp_path, snapshot_dir):
    # Issue #125 promotes hidden gems out of Meta to its own top-level tab, so the
    # bar reads Meta / Cards / Hidden gems / Pilots and Meta holds meta share alone
    # (a single-view tab). The tab order is v1 §11's amended four-tab structure, an
    # independent source; the built app is the seam so a group left under Meta trips
    # here rather than only in the browser. Archetypes (#145) sits between Meta and
    # Cards, splitting "who wins" off from Meta's "who is played".
    import gradio as gr
    from graph7ph.app import build_app
    from graph7ph.build import build_graph
    from graph7ph.models import load_snapshot

    artifact = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), artifact)
    demo = build_app(artifact)

    tabs = [b.label for b in demo.blocks.values() if isinstance(b, gr.Tab)]
    # Cold start lands on the drawn Meta view (#114): Meta leads and Pilots trails, with
    # no default pilot. Meta draws its cut chart at build time, so the opening tab shows a
    # real result rather than an empty canvas, and no single pilot is anointed a default.
    assert tabs == ["Meta", "Archetypes", "Cards", "Hidden gems", "Pilots",
                    "Best player race", "FAQ"]
    # Gems still has its own tab; Meta holds meta share alone. The gems query keeps
    # its plot heading (test_every_underlying_query_still_has_a_plot_heading) and its
    # _spec dispatch on `meta_gems`, so promoting the tab does not drop the view.


def test_cold_start_opens_on_the_drawn_meta_view(tmp_path, snapshot_dir):
    # AC (#114): cold start shows a real drawn result, never an empty canvas, and with no
    # default pilot. The Meta view draws its cut chart at build time (a subject-free query),
    # so making it the opening tab lands the app on a drawn answer with zero pilot
    # defaulting. Asserted at the build seam: the first tab is Meta, and its opening cut
    # plot carries a value (a figure), so a regression that reorders Pilots back to the
    # front (opening on an empty canvas) trips here.
    import gradio as gr

    demo = _built_demo(tmp_path, snapshot_dir)
    first_tab = next(b for b in demo.blocks.values() if isinstance(b, gr.Tab))
    assert first_tab.label == "Meta"
    # The Meta cut chart is drawn on open (not hidden behind a Draw), so it carries a
    # figure value at build time: cold start is a drawn result.
    cut_plots = [b for b in demo.blocks.values()
                 if isinstance(b, gr.Plot) and b.value is not None]
    assert cut_plots, "the opening Meta view draws a chart at build time"


def test_faq_tab_is_last_with_linked_boxes_for_each_headline_metric(tmp_path, snapshot_dir):
    # AC (#133): a FAQ tab homes the how-it-is-calculated notes #132 strips off the
    # plots, reachable from the app but off the plot surfaces. It is the last tab, and
    # each entry is its own insight-card box with a stable elem_id (the anchor a deep
    # link uses). Every headline metric is explained by its stable plot name, asserted
    # on the names rather than the prose so a copy edit to an answer does not break it.
    import gradio as gr

    demo = _built_demo(tmp_path, snapshot_dir)
    tabs = [b.label for b in demo.blocks.values() if isinstance(b, gr.Tab)]
    assert tabs == ["Meta", "Archetypes", "Cards", "Hidden gems", "Pilots",
                    "Best player race", "FAQ"]

    box_ids = {b.elem_id for b in demo.blocks.values()
               if isinstance(b, gr.Group) and (b.elem_id or "").startswith("faq-")}
    # One box per entry, ids and all: a column that dropped or doubled an entry when the
    # entries are split into two trips here.
    assert box_ids == {eid for eid, _, _, _ in _FAQ_ENTRIES}

    # Every box is tagged with its category, and every category is one the stylesheet
    # tints: a tag named here and nowhere in `theme.FAQ_TAGS` would render untinted.
    tags = [b.value for b in demo.blocks.values()
            if isinstance(b, gr.HTML) and "faq-tag" in (b.value or "")]
    assert len(tags) == len(_FAQ_ENTRIES)
    for category in {cat for _, cat, _, _ in _FAQ_ENTRIES}:
        assert category in theme.FAQ_TAGS
        assert any(f"faq-tag-{category.lower()}'>{category}<" in tag for tag in tags)

    body = " ".join(b.value for b in demo.blocks.values()
                    if isinstance(b, gr.Markdown) and "faq" in (b.elem_classes or []))
    # The three graph views joined the list in #142's review pass: each prints a rate
    # whose denominator appeared on no surface at all. The archetype timeline was already
    # covered at that point; it is listed here because nothing else pinned its name.
    for metric in ("Meta share over time", "Metagame landscape", "Performance over time",
                   "Head-to-head", "Adoption over time", "Hidden gem",
                   "Best player race", "Finishes over time", "Usage", "Co-occurrence",
                   "Archetype affinity"):
        assert metric in body, metric


def _built_demo(tmp_path, snapshot_dir):
    """Build a real artifact and the app over it, for the structural tab tests."""
    from graph7ph.app import build_app
    from graph7ph.build import build_graph
    from graph7ph.models import load_snapshot

    artifact = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), artifact)
    return build_app(artifact)


def _markdown_values(demo):
    import gradio as gr
    return [b.value for b in demo.blocks.values()
            if isinstance(b, gr.Markdown) and b.value]


def test_each_subject_tab_opens_with_a_section_heading(tmp_path, snapshot_dir):
    # AC (#113, user story 4): no tab opens as a bare dropdown over blank space; each
    # of the five subject tabs (Meta, Archetypes, Cards, Hidden gems, Pilots) leads with a section
    # heading. A section heading is an h2 (`## `) in the page's own type (§3), distinct
    # from the h1 page title and the bold-led plot intros, so counting them is robust
    # to copy edits while a dropped tab heading trips here rather than only in the browser.
    headings = [m for m in _markdown_values(_built_demo(tmp_path, snapshot_dir))
                if m.lstrip().startswith("## ")]
    # Six subject tabs (the best-player race joins them, #135) plus the FAQ tab (#133),
    # each led by its own h2 section heading.
    assert len(headings) == 7


def _all_surface_text(demo):
    """Every Markdown and HTML value the built app shows on its surface at open."""
    import gradio as gr
    return [b.value for b in demo.blocks.values()
            if isinstance(b, (gr.Markdown, gr.HTML)) and b.value]


def test_no_on_surface_methodology_or_plot_description_remains(tmp_path, snapshot_dir):
    # AC (#132, §14): the per-plot description paragraphs and the on-surface
    # "How this is measured" methodology blocks both leave the surface (the methodology
    # moves to the FAQ / Methodology tab, #133). None of it survives on any Markdown or
    # HTML surface: no `<details>` collapsible, no "How this is measured" summary, and
    # none of the caveat prose (the Meta classification-drift "723..." note, the Hidden
    # gems "over-index" caveat) the demoted blocks used to carry.
    surface = " ".join(_all_surface_text(_built_demo(tmp_path, snapshot_dir)))

    assert "<details" not in surface
    assert "How this is measured" not in surface
    assert "723" not in surface  # the Meta classification-drift caveat is gone
    assert "over-index" not in surface  # the Hidden gems over-indexing caveat is gone


def test_tab_intros_are_a_single_sentence(tmp_path, snapshot_dir):
    # AC (#132, §14): a tab intro is one short descriptive sentence, no more (the old
    # multi-line Pilots lede becomes one). The intros are the Markdowns carrying the
    # `t-lede` class; each must read as a single sentence: it ends with a period and
    # carries no mid-string sentence break, so a regression to a multi-sentence
    # paragraph trips here.
    import gradio as gr

    intros = [b.value for b in _built_demo(tmp_path, snapshot_dir).blocks.values()
              if isinstance(b, gr.Markdown) and b.value
              and "t-lede" in (b.elem_classes or [])]

    # One per tab: Meta, Archetypes, Cards, Hidden gems, Pilots, Best player race, FAQ.
    assert len(intros) == 7
    for intro in intros:
        assert intro.rstrip().endswith("."), intro
        assert ". " not in intro, intro  # no second sentence


def test_meta_focus_control_sits_above_its_plot_not_below(tmp_path, snapshot_dir):
    # AC (#132): no plot-affecting control is placed below its plot. The Meta
    # archetype-focus multiselect used to sit under the cut chart; it moves up with
    # the cut control. Asserted at the build seam by insertion order: the focus
    # dropdown is created before the cut plot that its sibling control drives, so a
    # regression that strands it below the plot again trips here.
    import gradio as gr

    blocks = list(_built_demo(tmp_path, snapshot_dir).blocks.values())

    def index_where(pred):
        return next(i for i, b in enumerate(blocks) if pred(b))

    cut_radio_i = index_where(
        lambda b: isinstance(b, gr.Radio) and "Archetypes to show" in (b.label or "")
    )
    focus_i = index_where(
        lambda b: isinstance(b, gr.Dropdown)
        and (b.label or "") == "Or focus on specific archetypes"
    )
    # The cut plot is the first Plot created after the cut radio (the Meta cut chart).
    cut_plot_i = min(i for i, b in enumerate(blocks)
                     if isinstance(b, gr.Plot) and i > cut_radio_i)

    assert focus_i < cut_plot_i


def test_a_view_opens_with_no_prompt_cards_and_no_pick_and_draw_help_text(tmp_path, snapshot_dir):
    # AC (#132/#156, §14): a view no longer opens as a row of identical "Pick an entity
    # and filters, then Draw." cards, and since #156 it does not open with that sentence
    # on the subject dropdown either. Help text exists to change the reader's choice, and
    # "pick a pilot, then Draw" only restates the label and the Draw button beside it, so
    # it is deleted rather than shortened. The per-view results stack starts hidden, so
    # nothing empty is drawn. The one `info` the app keeps is the Archetypes year's
    # scope, which is a fact about the panel the control itself cannot give.
    import gradio as gr
    from graph7ph.app import _YEAR_SCOPE

    demo = _built_demo(tmp_path, snapshot_dir)

    infos = {b.info for b in demo.blocks.values()
             if isinstance(b, gr.Dropdown) and b.info}
    assert infos == {_YEAR_SCOPE}
    # No help text anywhere tells the reader to press the button next to it, or
    # describes what the control does when changed (§14, #156).
    for info in infos:
        assert "Draw" not in info
        assert "draws" not in info

    # No result surface carries the old duplicated prompt text on open.
    surface = " ".join(
        b.value for b in demo.blocks.values()
        if isinstance(b, (gr.Markdown, gr.HTML)) and b.value
    )
    assert "Pick an entity and filters, then Draw" not in surface


def test_the_gem_view_takes_no_archetype(tmp_path, snapshot_dir):
    # Issue #184 removes the archetype dropdown: the tab draws every gem in the format
    # at once, so there is nothing to pick and nothing to wait for. The spec is
    # parameterless, no control anywhere in the app asks for a gem archetype, and the
    # view is a drawn result at build time rather than a prompt.
    import gradio as gr
    from graph7ph.app import _spec, build_app
    from graph7ph.build import build_graph
    from graph7ph.models import load_snapshot
    from graph7ph.query import HiddenGems

    assert _spec("meta_gems", {}) == HiddenGems()

    artifact = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), artifact)
    demo = build_app(artifact)

    guidance = " ".join(b.info for b in demo.blocks.values()
                        if isinstance(b, gr.Dropdown) and b.info)
    assert "gem" not in guidance.lower()
    surface = " ".join(b.value for b in demo.blocks.values()
                       if isinstance(b, gr.HTML) and isinstance(b.value, str))
    assert "Hidden gems" in surface  # drawn on open, not behind a Draw


def test_every_underlying_query_still_has_a_plot_heading():
    # The views collapse but the queries do not: one Draw now fans out to several
    # plots, each titled by the plot it draws rather than by the view it sits in.
    # Every graph query the two-view shape still reaches (neighbourhood, affinity,
    # usage, co-occurrence, gems) must keep a heading label, so the "all queries
    # preserved" AC cannot silently drop one.
    assert set(_PLOT_LABELS) == {
        "pilot_neighbourhood", "pilot_affinity",
        "card_usage", "card_cooccurrence",
        "meta_gems",
    }


def test_card_overview_adoption_caption_carries_the_board_but_cooccurrence_carries_none():
    # AC (#126/#132): the adoption plot title is always the plot type ("Adoption over
    # time", §14); the board is a filter and rides the caption. Card overview has a
    # board control, so its caption names the board the count is scoped to.
    # Co-occurrence is board-agnostic (no board control), so its caption is None: no
    # board qualifier reaches the plot, since there is no control to disambiguate it.
    # board=None is the board-agnostic sentinel.
    assert _adoption_caption("") == "Main or side board"
    assert _adoption_caption("Main") == "Main board"

    assert _adoption_caption(None) is None
    # The plot title never carries the subject or the board; only the plot type.
    assert "Adoption over time" in _chart_heading("Adoption over time", _adoption_caption("Main"))


def test_cooccurrence_adoption_plots_both_cards_or_the_subject_alone():
    # AC (#126): the co-occurrence adoption trend plots both cards when a second is
    # chosen and the subject alone otherwise; the subject always leads (first trace),
    # and a second card equal to the subject collapses to one line rather than two.
    assert _adoption_cards("sol-ring", None) == ["sol-ring"]
    assert _adoption_cards("sol-ring", "mana-crypt") == ["sol-ring", "mana-crypt"]
    assert _adoption_cards("sol-ring", "sol-ring") == ["sol-ring"]
    # No subject, no lines: a compare card never draws on its own.
    assert _adoption_cards("", "mana-crypt") == []


def test_a_drawn_result_is_titled_by_its_plot_type_and_captioned():
    # Issue #110/#132: a query result is never left as an unlabelled graph. It opens
    # under a title (the plot type alone, §14, the subject stated once above the cards)
    # and a caption (the filters, then how much came back), both in the page's own type
    # roles (§3), so the answer reads as an answer. The class names are the page-type
    # contract: a regression to plain <p> text would drop them and trip this.
    header = _result_header("pilot_neighbourhood", ["vs Bob C"], node_count=42)

    assert "t-result-title" in header
    assert "t-caption" in header
    # The title is the plot type only; the subject (a pilot name) never appears in it.
    assert "Neighbourhood" in header
    assert "Ada L" not in header
    # The caption carries the filters and the node count, joined as one line.
    assert "vs Bob C · 42 nodes" in header


def test_the_caption_reads_the_node_count_and_reduces_to_the_singular():
    # "How much came back" is the count of nodes drawn. With no filters the caption
    # is the count alone, and a lone node reads "1 node", not "1 nodes".
    one = _result_header("pilot_affinity", [], node_count=1)
    assert ">1 node</" in one

    many = _result_header("pilot_affinity", [], node_count=250)
    assert ">250 nodes</" in many


def test_the_filters_are_escaped_into_the_header():
    # The filter strings come from display labels, which are free text, so a filter
    # carrying an angle bracket is escaped rather than injected into the result markup.
    header = _result_header("card_usage", ["with A<b>"], node_count=3)
    assert "A<b>" not in header
    assert "A&lt;b&gt;" in header


def test_the_neighbourhood_explains_a_decided_placement_only_where_one_is_drawn():
    # Issue #199: a rank this project decided now carries the app's imputed mark, and
    # the mark alone does not say what it means. One legend line for
    # the whole picture, and only when the picture actually carries a mark: the
    # head-to-head's rule (§14), applied to the graph card.
    def sub(*labels):
        return Subgraph(
            nodes=[Node(f"placement:d{i}", lbl, "Placement")
                   for i, lbl in enumerate(labels)],
            edges=[],
        )

    note = _imputed_placement_note(sub("5th", f"12th{numfmt.IMPUTED_MARK}"))
    assert note is not None and note.startswith(numfmt.IMPUTED_MARK)

    assert _imputed_placement_note(sub("5th", "12th")) is None
    assert _imputed_placement_note(Subgraph(nodes=[], edges=[])) is None

    # The line lands under the filters and the count as its own caption row, so the
    # legend never reads as part of what was asked for.
    header = _result_header("pilot_neighbourhood", ["vs Bob C"], 9, note)
    assert header.count("t-caption") == 2
    assert header.index("vs Bob C") < header.index("worked out")


def test_a_drawn_neighbourhood_carries_the_decided_mark_and_its_legend(
    tmp_path, snapshot_dir
):
    # AC (#199): the whole path, record to rendered card, through the app's own Draw
    # rather than the two halves separately: a query that marks the rank and a header
    # that explains it still leave a reader with nothing if the two are never joined.
    # The fixture is copied with one deck's source placement removed, which is the real
    # case (Pats Birthday Brawl numbers nothing though every title opens with a rank),
    # so "12th" is recovered from the title alone and the card must say so.
    decks = json.loads((snapshot_dir / "decks.json").read_text())
    for deck in decks:
        if deck["deckId"] == "pkUbzmgN3UeqaWdYQYRgRg":
            deck["placement"] = deck["placementNorm"] = None
    (tmp_path / "snapshot").mkdir()
    (tmp_path / "snapshot" / "decks.json").write_text(json.dumps(decks))
    (tmp_path / "snapshot" / "cards_index.json").write_text(
        (snapshot_dir / "cards_index.json").read_text()
    )

    demo = _built_demo(tmp_path / "app", tmp_path / "snapshot")
    draw = next(dep.fn for dep in demo.fns.values()
                if getattr(dep.fn, "__name__", "") == "draw_pilot_overview")
    # The Pilots Draw fans out to three plots; the neighbourhood graph is the third
    # value back (subject line, results stack, neighbourhood, ...).
    card = draw("Jordan C")[2]["value"]

    assert f"12th{numfmt.IMPUTED_MARK}" in card    # the rank reads as decided
    assert "a placement this project worked out" in card  # and the card says what that means
    # Jordan C's other deck is the source's own 5th, and it stays unmarked, so the mark
    # separates the two rather than labelling every rank on the card.
    assert f"5th{numfmt.IMPUTED_MARK}" not in card


def test_a_state_message_reads_in_the_app_type_not_an_inline_styled_div():
    # AC (#114): the states with nothing (or not yet) to draw share one on-theme
    # treatment. `_state_message` is that treatment for the four rendered ones (nothing
    # picked, empty result, SliceTooSmall, too-large-to-draw); the fifth, running, is the
    # framework's own progress indicator (AC2), not a message this helper renders. It
    # retires the hand-styled inline divs (`style='padding:1rem;font-family:sans-serif'`)
    # that overrode the theme font with a system sans, rendering instead in the app's own
    # body type role on the tokens, with no inline style. The message is free text, so escaped.
    from graph7ph.app import _state_message

    msg = _state_message("No decks match these filters.")
    assert "t-state" in msg  # the shared state class
    assert "t-body" in msg   # the app's own body type role, not a system sans
    assert "font-family" not in msg  # the retired inline sans-serif is gone
    assert "style=" not in msg       # the class carries the treatment, not inline style
    assert "No decks match these filters." in msg

    escaped = _state_message("A<b> matched")
    assert "A<b>" not in escaped
    assert "A&lt;b&gt;" in escaped


def test_the_too_large_state_refuses_in_one_line_through_the_shared_treatment():
    # AC (#114): the too-large-to-draw state (`_refine_alert`) speaks in one short line
    # in the app's voice through the shared on-theme treatment, not the old multi-
    # paragraph inline-styled div with a <ul> of suggestions. It states the count, the
    # kind flooding the view, and what to do (narrow and Draw again), and shares the
    # state class so it reads the same as every other state.
    from graph7ph.explore import RenderPlan
    from graph7ph.app import _refine_alert

    plan = RenderPlan(
        render=False, node_count=1234, threshold=250,
        by_kind={"Card": 900, "Deck": 334},
    )
    msg = _refine_alert(plan)

    assert "t-state" in msg          # the same on-theme treatment as the other states
    assert "font-family" not in msg  # not the retired inline-styled div
    assert "<ul>" not in msg and "<li>" not in msg  # one line, not a bulleted paragraph
    assert "<p>" not in msg          # no multi-paragraph body
    assert "1,234" in msg            # the count, with a thousands separator
    assert "cards" in msg            # names the kind flooding the view
    assert "250" in msg              # the draw limit
    assert "Draw" in msg             # says what to do


def test_the_subject_is_stated_once_and_escaped():
    # AC (#132, §14): the subject is stated once for the whole result, above the cards,
    # never echoed in each plot title. The kind prefix reads in reader language and the
    # names are free-text display labels, escaped into the markup.
    one = _subject_line("Pilot", "Ada L")
    assert "subject-line" in one
    assert "Ada L" in one

    pair = _subject_line("Head-to-head", "Ada L", "Bob C")
    assert "Ada L" in pair and "Bob C" in pair and " vs " in pair

    escaped = _subject_line("Card", "A<b>")
    assert "A<b>" not in escaped
    assert "A&lt;b&gt;" in escaped


def _gems(*cards, luck=1.0):
    """A gem subgraph from ``(archetype, canon, decks, top, pilots, chance)`` tuples."""
    nodes, edges = [], []
    for tag, canon, decks, top, pilots, chance in cards:
        arch, card = f"arch:{tag}", f"card:{tag}:{canon}"
        if arch not in {n.id for n in nodes}:
            nodes.append(Node(arch, tag.title(), "Archetype", decks=100))
        nodes.append(Node(card, canon.title(), "Card", decks=decks, total_decks=100,
                          top_decks=top, pilots=pilots, gem_luck=chance))
        edges.append(Edge(arch, card, "IN"))
    nodes.append(Node("deck:d1", "p - Storm", "Deck"))
    return Subgraph(nodes=nodes, edges=edges, expected_by_luck=luck)


def test_every_gem_states_the_whole_claim_beside_its_odds():
    # Issue #184: the picture draws which best decks run which card and carries no
    # number at all, so the table is where the claim is stated. Each row is the claim
    # in the order it is built: the card, the archetype every term was measured inside,
    # how rare it is there, how much of it is in that archetype's best third, how many
    # pilots those decks belong to, and how often chance alone does that much.
    table = _gem_table(_gems(
        ("lands", "port", 21, 13, 10, 0.0039),
        ("bant", "leyline", 24, 18, 3, 0.00001),
    ))

    assert table.count("<tr") == 3  # the header row and one per gem
    # The archetype is named on every row: two gems from two archetypes are two
    # claims, and the list spans the format now.
    assert ">Lands<" in table and ">Bant<" in table
    # The decks the graph draws are not rows in it.
    assert "Storm" not in table
    assert ">21<" in table and ">13<" in table and ">10<" in table
    assert numfmt.share(0.0039) in table

    # Grouped by archetype, and within one archetype the longest odds lead: a gem is
    # a claim about its own archetype, so a flat ranking would invite reading two
    # archetypes' cards against each other.
    rows = _gem_table(_gems(
        ("lands", "port", 21, 13, 10, 0.0039),
        ("bant", "leyline", 24, 18, 3, 0.00001),
        ("lands", "vault", 10, 8, 6, 0.0002),
    ))
    assert [c for c in ("Leyline", "Vault", "Port") if c in rows] == [
        "Leyline", "Vault", "Port"
    ]
    assert rows.index("Leyline") < rows.index("Vault") < rows.index("Port")


def test_alternate_archetypes_are_banded_so_each_block_reads_apart():
    # The rows are grouped, and a group is only useful if a reader can see where one
    # archetype's gems stop. Alternate archetypes are shaded whole, so the blocks read
    # apart without a rule or a hue: every row of one block carries the band, no row of
    # its neighbours does, and the archetype stays named on each row.
    table = _gem_table(_gems(
        ("lands", "port", 21, 13, 10, 0.0039),
        ("lands", "vault", 10, 8, 6, 0.0002),
        ("bant", "leyline", 24, 18, 3, 0.00001),
        ("storm", "ritual", 9, 7, 5, 0.0001),
    ))
    rows = re.findall(r"<tr[^>]*>.*?</tr>", table)[1:]  # past the header

    banded = ["band" in row for row in rows]
    named = [re.findall(r"<td[^>]*>([^<]*)</td>", row)[1] for row in rows]
    assert named == ["Bant", "Lands", "Lands", "Storm"]
    assert banded == [False, True, True, False]  # one band per archetype, alternating
    assert table.count(">Lands<") == 2  # named on each of its rows, not once a block


def test_every_class_the_gem_table_emits_is_one_the_stylesheet_draws():
    # The table is raw markup the app builds, so a class named here and nowhere in the
    # stylesheet renders as an unstyled browser default table on a dark card: the
    # grouping would simply not be drawn.
    table = _gem_table(_gems(
        ("lands", "port", 21, 13, 10, 0.0039),
        ("lands", "vault", 10, 8, 6, 0.0002),
    ))
    css = theme.build_css()

    classes = set(re.findall(r"class='([^']+)'", table))
    for name in {c for group in classes for c in group.split()}:
        assert f".{name}" in css, name


def test_a_vanishing_chance_reads_as_below_the_smallest_share_not_as_zero():
    # The strongest gems on the built graph sit far under 0.005%, which the app's share
    # convention rounds to a flat "0%". "Chance alone would never do this" is a
    # different claim from "chance alone does this less often than we can write", and
    # only the second one is true.
    table = _gem_table(_gems(("bant", "leyline", 24, 18, 3, 1.8e-11)))

    assert ">0%<" not in table
    assert f"&lt;{numfmt.share(0.0001)}" in table

    # And only where the convention truly cannot write it: a chance it can write is
    # written, since an "under" that swallowed writable values would overstate them.
    writable = _gem_table(_gems(("lands", "vault", 10, 8, 6, 0.00007)))
    assert f">{numfmt.share(0.00007)}<" in writable
    assert "&lt;" not in writable


def test_the_gem_caption_says_what_the_list_is_and_nothing_else():
    # One clause: how many gems, and the cut they were found in. The false-positive
    # count the rule carries is real and stays on the page, but not here: it is a
    # property of the list and of no row, so raising it beside the table asks "which
    # ones?" at the one place with no room to answer. It lives in the FAQ instead,
    # inside the answer to that question (`faq-gems-certainty`).
    caption = _gem_caption(_gems(
        ("lands", "port", 21, 13, 10, 0.0039),
        ("bant", "leyline", 24, 18, 3, 0.00001),
        ("jeskai", "noon", 29, 17, 15, 0.0023),
        luck=8.45,
    ))

    assert "3 gems" in caption
    assert f"best {GEM_TOP_CUT:.0%}" in caption
    assert "chance" not in caption and "8" not in caption

def test_band_over_a_non_crossing_segment_is_one_trapezoid_tinted_by_the_upper_line():
    # a stays above b across the segment, so a single polygon carries a_above True.
    polys = list(_between_line_polys([(0, 0.8, 0.2), (1, 0.9, 0.3)]))

    assert len(polys) == 1
    xs, ys, a_above = polys[0]
    assert a_above is True
    assert xs == [0, 1, 1, 0]
    assert ys == [0.8, 0.9, 0.3, 0.2]


def test_band_splits_at_a_crossing_so_each_half_takes_the_line_above_it_there():
    # a starts below b and ends above: two halves meeting at the crossing, the first
    # tinted for b (a_above False), the second for a (a_above True).
    polys = list(_between_line_polys([(0, 0.2, 0.8), (2, 0.8, 0.2)]))

    assert len(polys) == 2
    (xs0, ys0, a_above0), (xs1, ys1, a_above1) = polys
    assert (a_above0, a_above1) == (False, True)
    # The crossing is the shared apex of both triangles: midway here, y = 0.5.
    assert xs0[1] == 1 and ys0[1] == 0.5
    assert xs1[0] == 1 and ys1[0] == 0.5


def test_a_null_end_on_either_line_breaks_the_band_over_that_segment():
    # b is unscored at the middle event, so neither adjoining segment fills.
    polys = list(_between_line_polys([(0, 0.8, 0.2), (1, 0.5, None), (2, 0.6, 0.3)]))

    assert polys == []


def test_two_lines_equal_across_a_segment_draw_no_band():
    assert list(_between_line_polys([(0, 0.5, 0.5), (1, 0.5, 0.5)])) == []


def _performance_cell(year, mean_norm, events, half=0.1):
    """One performance cell, its interval ``half`` either side of its mean (#175).

    Stated flat rather than re-derived from the field's spread, for the reason
    :func:`_landscape_cells` states it flat: the width is what the tool hands the
    surface, and these tests are about what the surface does with it.
    """
    return PerformanceCell(
        year=year, mean_norm=mean_norm, events=events,
        mean_low=None if mean_norm is None else max(0.0, mean_norm - half),
        mean_high=None if mean_norm is None else min(1.0, mean_norm + half),
    )


def test_a_refused_year_at_the_end_of_a_career_is_an_empty_tick_not_a_missing_year():
    # A pilot who played four years but could only be averaged in two. The thin years
    # here are the first and the last, which is where they usually fall: a one-event
    # year is overwhelmingly the year someone arrived or left. The chart used to span
    # only the averaged years, so both ends vanished and it claimed a two-year career
    # (issue #101). Every year the pilot played is now a tick; the refused ones carry
    # no point and no label, so the line breaks across them instead of bridging.
    series = Series(cells=[
        _performance_cell(2023, None, 1),
        _performance_cell(2024, 0.4, 3),
        _performance_cell(2025, 0.2, 5),
        _performance_cell(2026, None, 1),
    ])
    trace = _performance_figure("Ada L", series).data[0]

    assert trace.x == (2023, 2024, 2025, 2026)  # numeric years on a linear axis
    # The score inverts the finish (1 is a win), and a refused year plots as a null.
    assert trace.y == (None, 0.6, 0.8, None)
    assert trace.text == ("", "3 ev", "5 ev", "")


def test_a_year_the_pilot_sat_out_still_holds_the_axis_open():
    # The series covers only the years the pilot played, so a year they skipped has no
    # cell at all. It still gets a tick, or 2024 and 2026 would sit adjacent and read
    # as consecutive seasons.
    series = Series(cells=[
        _performance_cell(2024, 0.4, 3),
        _performance_cell(2026, 0.2, 2),
    ])
    trace = _performance_figure("Ada L", series).data[0]

    assert trace.x == (2024, 2025, 2026)
    assert trace.y == (0.6, None, 0.8)


def test_a_refused_year_is_captioned_and_a_sat_out_year_is_not():
    # Both leave an empty tick, so without the caption the chart re-creates the very
    # conflation the tool was changed to end: 2023 (the pilot turned up once) would
    # read exactly like 2025 (the pilot did not play). The caption sits under the
    # axis, so it can never be read as a position on the score.
    series = Series(cells=[
        _performance_cell(2023, None, 1),
        _performance_cell(2024, 0.4, 3),
        _performance_cell(2026, None, 0),
    ])
    # The refused-year captions sit under the axis (yref="paper"); the midpoint
    # reference-line label rides the y-axis, so filter to the captions this test is about.
    captions = {
        (a.x, a.text) for a in _performance_figure("Ada L", series).layout.annotations
        if a.yref == "paper"
    }

    # 2025 has no cell at all (sat out), so it gets a tick and no caption.
    # "none counted" rather than "unscored" (#142): a year whose only events published a
    # bracket was scored by the source, just not at anything this chart's mean can read.
    assert captions == {(2023, "1 ev, too thin"), (2026, "played, none counted")}


def test_every_drawn_year_carries_its_interval_beside_the_point():
    # The finding #175 exists for: the movement between these points is noise, so the
    # honest content of the chart is each year's value and how settled it is. The
    # interval is drawn on the surface, not left to the FAQ, and it is flipped with the
    # score, so the raw low bound (the better finish) becomes the upper whisker. 2024's
    # interval is clamped at a win (raw 0.0 to 0.4), which is what makes the two arms
    # different lengths and shows the flip is not a symmetric half-width in disguise.
    series = Series(cells=[
        _performance_cell(2024, 0.1, 2, half=0.3),
        _performance_cell(2025, 0.5, 8, half=0.15),
        _performance_cell(2026, None, 1),
    ])
    bars = _performance_figure("Ada L", series).data[0].error_y

    assert bars.type == "data" and bars.symmetric is False
    assert bars.array == pytest.approx([0.1, 0.15, None])       # up to the better bound
    assert bars.arrayminus == pytest.approx([0.3, 0.15, None])  # down to the worse one


def test_performance_markers_grow_with_the_events_behind_each_year():
    # A two-event mean and a twenty-event one sit on the same line; the ring's size
    # carries the sample size so the eye discounts the thin year without hovering.
    series = Series(cells=[
        _performance_cell(2024, 0.4, 2),
        _performance_cell(2025, 0.4, 25),
    ])
    sizes = _performance_figure("Ada L", series).data[0].marker.size

    assert sizes[1] > sizes[0]  # the 25-event year draws a broader ring than the 2-event one


def test_performance_caption_states_the_field_share_and_the_sample():
    # The flat axis is silent on the standing, so the caption states it: the mean
    # score weighted by events, as the share of the field beaten, with the sample.
    caption = _performance_caption(Series(cells=[
        _performance_cell(2024, 0.4, 2),   # score 0.6
        _performance_cell(2025, 0.2, 8),   # score 0.8
        _performance_cell(2026, None, 1),  # refused, not in the mean
    ]))

    # Weighted mean score = (0.6*2 + 0.8*8) / 10 = 0.76, rounded to a whole percent, and
    # set in the accent span so the eye lands on it; the sample trails in its own span.
    assert "<span class='pct'>76%</span>" in caption
    assert "10 events over 2 scored years" in caption
    # One claim and one qualifier, and nothing else (§14, #156): the two readings this
    # caption used to trail (that the movement between years is noise, and that this
    # population is not the race's) are the FAQ's now, so the tail holds one clause.
    assert caption.count("·") == 1
    assert "not a slump" not in caption
    assert "race" not in caption


def test_a_leading_refused_year_does_not_stretch_the_axis():
    # A leading refused year is a null-only point that draws no marker, but its refusal
    # caption is anchored to the x-axis. On a category axis the numeric-string year lands
    # at the linear coordinate 2024, off the category slots, dragging autorange out to it
    # so the real markers crush to one edge and the caption strands at the other. A linear
    # year axis with a pinned range puts caption and markers on one bounded scale.
    fig = _performance_figure("Ada L", Series(cells=[
        _performance_cell(2024, None, 1),
        _performance_cell(2025, 0.4, 3),
        _performance_cell(2026, 0.2, 5),
    ]))

    assert fig.layout.xaxis.type == "linear"
    assert fig.layout.xaxis.range == (2023.5, 2026.5)  # bounded to the years, no blowup
    # The refusal caption sits at its real year, inside the range, not flung past it.
    thin = next(a for a in fig.layout.annotations if a.text == "1 ev, too thin")
    assert thin.x == 2024


def test_the_in_figure_range_control_takes_the_app_accent_not_a_stray_amber():
    # AC (#85, criterion 1): the design direction exists and "every surface follows it".
    # §2 commits the app to one accent, and the range slider plus the label centred under
    # it were still wearing a Tailwind amber (`rgba(245,158,11,…)`) left from the
    # light-theme era, so the one orange thing drawn inside a chart was a different orange
    # from every orange outside it. Read off both charts that carry the control.
    series = Series(cells=[
        _h2h_point(event="GP", date=datetime(2024, 3, 1), field_size=100,
                        placement_a=1, norm_a=0.0, placement_b=50, norm_b=0.5),
        _h2h_point(event="PT", date=datetime(2024, 6, 1), field_size=80,
                        placement_a=40, norm_a=0.5, placement_b=1, norm_b=0.0),
    ])
    for fig in (
        _head_to_head_figure("Ada L", "Bob C", series),
        _archetype_timeline_figure(
            "Storm", None, _timeline_points((1, 0.2, 3), (2, 0.6, 1)),
        ),
    ):
        assert "245,158,11" not in fig.to_json()  # the retired amber, in any opacity
        accent = theme.TOKENS["accent-bright"]
        slider = fig.layout.xaxis.rangeslider
        assert slider.bgcolor == _rgba(accent, 0.12)
        assert slider.bordercolor == _rgba(accent, 0.55)
        # The label under the slider is drawn in the same accent as the band it names,
        # or it reads as a stray caption rather than that control's own label.
        label = next(a for a in fig.layout.annotations if "Time range filter" in a.text)
        assert label.font.color == _rgba(accent, 0.95)
        # The legend takes the room above the plot and the shared styler's tight bottom
        # survives underneath, from two separate calls: a layout update that replaced
        # rather than merged would cramp the chart without changing a colour this test
        # reads. Nothing reserves room for the band by hand; Plotly's autoexpand sizes
        # the bottom to the band, the ticks and the axis title exactly.
        assert (fig.layout.margin.b, fig.layout.margin.t) == (8, 48)
    # AC (§5-6): head-to-head is two lines, ≤8, so each pilot takes a direct colour
    # from the shared eight-hue set (slot 1, slot 2), not a position in a long
    # recycled wheel. Colour follows the entity: the pilot named first is blue.
    series = Series(cells=[
        _h2h_point(event="GP", date=datetime(2024, 3, 1), field_size=100,
                        placement_a=1, norm_a=0.0, placement_b=50, norm_b=0.5),
        _h2h_point(event="PT", date=datetime(2024, 6, 1), field_size=80,
                        placement_a=40, norm_a=0.5, placement_b=1, norm_b=0.0),
    ])
    fig = _head_to_head_figure("Ada L", "Bob C", series)
    by_name = {t.name: t for t in fig.data if t.name in ("Ada L", "Bob C")}

    assert by_name["Ada L"].marker.line.color == palette.CATEGORICAL[0]
    assert by_name["Bob C"].marker.line.color == palette.CATEGORICAL[1]


def _h2h_point(
    event, date, field_size, placement_a=3, norm_a=2 / 23,
    placement_b=5, norm_b=4 / 23, **decided,
) -> HeadToHeadPoint:
    """A shared event's point, every value the source's own unless ``decided`` says.

    The point requires a rule beside each of its three numbers, since a null there
    is the claim that the source's own value stands rather than an absence (ADR
    0016). Only the provenance tests have anything to say about them, so this
    spells the null case once for the rest.
    """
    return HeadToHeadPoint(
        event=event, date=date, field_size=field_size,
        placement_a=placement_a, norm_a=norm_a,
        placement_b=placement_b, norm_b=norm_b,
        **{"field_imputed": None, "placement_imputed_a": None,
           "norm_imputed_a": None, "placement_imputed_b": None,
           "norm_imputed_b": None, **decided},
    )


def _h2h_provenance_series() -> Series:
    # One event of each kind. At PBB the field is Rule B's floor of 24, Ada's third
    # place was read off her deck's title, and neither norm was scored by the source,
    # so all three of her numbers are the project's. SSWam's 88 is the source's own
    # count and every number on that point is the source's.
    return Series(cells=[
        _h2h_point("PBB", datetime(2024, 3, 1), 24,
                   field_imputed="B", placement_imputed_a="title-range",
                   norm_imputed_a="minted", norm_imputed_b="minted"),
        _h2h_point("SSWam", datetime(2024, 6, 1), 88),
    ])


def test_head_to_head_hover_marks_the_numbers_the_project_decided():
    # AC (#166): a value the project decided renders identically to a counted one, so
    # the hover asserts a domain rule as a measurement. The mark lands on each decided
    # number and only on it, PBB carrying all three and SSWam none.
    fig = _head_to_head_figure("Ada L", "Bob C", _h2h_provenance_series())
    ada = next(t for t in fig.data if t.name == "Ada L")
    bob = next(t for t in fig.data if t.name == "Bob C")

    assert list(ada.customdata[0]) == ["0.91* (1 = 1st)", "3* / 24*"]
    assert list(ada.customdata[1]) == ["0.91 (1 = 1st)", "3 / 88"]
    # Bob's placement at PBB is the source's own, so only his score and the field
    # they share carry a mark: the three numbers are decided one at a time.
    assert list(bob.customdata[0]) == ["0.83* (1 = 1st)", "5 / 24*"]


def test_head_to_head_caption_explains_the_mark_only_where_one_is_drawn():
    # One legend line for the whole plot, and only when the plot actually carries a
    # mark: a caption explaining an asterisk nobody can see is chrome (§14).
    caption = _head_to_head_caption(_h2h_provenance_series())
    assert caption is not None and caption.startswith(numfmt.IMPUTED_MARK)

    clean = Series(cells=[_h2h_point("SSWam", datetime(2024, 6, 1), 88)])
    assert _head_to_head_caption(clean) is None


def test_head_to_head_caption_ignores_a_mark_on_a_point_that_never_draws():
    # A value no rule could recover is `none` on a null norm: the point is a gap in
    # the line, so it puts no mark on screen and must not summon a legend for one.
    unscored = Series(cells=[
        HeadToHeadPoint(event="GGWAD", date=datetime(2024, 6, 1), field_size=28,
                        field_imputed="A",
                        placement_a=None, norm_a=None,
                        placement_b=None, norm_b=None,
                        placement_imputed_a="none", norm_imputed_a="none",
                        placement_imputed_b="none", norm_imputed_b="none"),
    ])
    assert _head_to_head_caption(unscored) is None


def test_adoption_colours_each_card_by_entity_from_the_shared_palette():
    # AC (§5): the cards on one adoption axis (subject first, then compares) each take
    # a direct hue from the shared set in fixed order, the subject blue.
    def one(count):
        return Series(cells=[AdoptionCell(year=2024, count=count, share=count / 1000,
                                          year_total=1000)])
    fig = _adoption_figure([("Sol Ring", one(30)), ("Mana Crypt", one(20))])
    by_name = {t.name: t for t in fig.data}

    assert by_name["Sol Ring"].marker.line.color == palette.CATEGORICAL[0]
    assert by_name["Mana Crypt"].marker.line.color == palette.CATEGORICAL[1]


def test_a_narrower_cut_does_not_repaint_the_archetypes_it_shares_with_a_wider_one():
    # AC (§5): colour follows the entity, never its rank, so a filter that changes the
    # series count must not repaint the survivors. The cut returns tags strongest-first,
    # so a narrower cut is a prefix of a wider one; both draw the shared archetypes in
    # the same shared-palette colours. This is the reversal of ADR-0013's colour-by-
    # position, tested at the seam that used to repaint on every re-cut.
    series = _meta_series(
        ("aggro", 2024, 0.4), ("control", 2024, 0.3), ("combo", 2024, 0.2),
    )

    def colour_by_archetype(tags):
        fig = _trend_figure(series, tags)
        return {t.name: t.marker.line.color for t in fig.data}

    wider = colour_by_archetype(["aggro", "control", "combo"])
    narrower = colour_by_archetype(["aggro", "control"])

    # The two survivors keep the exact hue they had in the wider cut.
    assert narrower["Aggro"] == wider["Aggro"] == palette.CATEGORICAL[0]
    assert narrower["Control"] == wider["Control"] == palette.CATEGORICAL[1]


def test_dropping_a_pick_from_the_middle_does_not_repaint_the_picks_after_it():
    # AC (§5): the manual panel hands its picks back in pick order, which does not nest
    # the way the cut's rank order does, so dropping a chip from the middle slid every
    # later pick up a hue and re-adding it did not undo the swap. Drawn over the stable
    # universe the hue follows the archetype: the survivor holds its colour, and the
    # re-added pick returns the whole mapping to what it was before the removal.
    series = _meta_series(
        ("aggro", 2024, 0.4), ("control", 2024, 0.3), ("combo", 2024, 0.2),
    )
    universe = ["aggro", "control", "combo"]

    def colour_by_archetype(tags):
        fig = _trend_figure(series, tags, universe=universe)
        return {t.name: t.marker.line.color for t in fig.data}

    picked = colour_by_archetype(["aggro", "control", "combo"])
    without_middle = colour_by_archetype(["aggro", "combo"])
    re_added = colour_by_archetype(["aggro", "combo", "control"])

    assert without_middle["Combo"] == picked["Combo"] == palette.CATEGORICAL[2]
    assert re_added == picked


def test_two_picks_that_share_a_universe_hue_are_still_drawn_apart():
    # AC (§5): stability never costs distinctness. The tracing scale is 32 hues and the
    # universe is longer, so archetypes exactly a scale-length apart in it take the same
    # hue: drawn straight off the filtered map, the first trio tried on the running app
    # put two of its three lines in one colour. The colliding line moves to the first
    # free hue instead, and only the colliding one moves.
    series = _meta_series(("aggro", 2024, 0.4), ("control", 2024, 0.3),
                          ("combo", 2024, 0.2))
    # "control" and "combo" sit a full scale apart, so `extended` hands them one hue.
    universe = ["aggro", "control", *[f"filler{i}" for i in range(len(palette.EXTENDED) - 1)],
                "combo"]
    assert palette.extended(universe)["combo"] == palette.extended(universe)["control"]

    def colour_by_archetype(tags):
        fig = _trend_figure(series, tags, universe=universe)
        return {t.name: t.marker.line.color for t in fig.data}

    both = colour_by_archetype(["aggro", "control", "combo"])
    assert len(set(both.values())) == 3, both
    # The one that holds the shared hue is the one the universe reaches first, so which
    # of the pair moves does not depend on the order the reader picked them in.
    assert both["Control"] == palette.EXTENDED[1]
    assert colour_by_archetype(["aggro", "combo"])["Combo"] == palette.EXTENDED[1]


def test_adding_a_pick_does_not_repaint_a_line_already_on_the_chart():
    # AC (§5): the fix above must not smuggle the failure back in. A displaced line takes
    # "the first free hue", and in a single walk a hue is free only until the universe
    # reaches the archetype that owns it, so resolving as we go handed a new chip a hue
    # belonging to a line already drawn and repainted it. Every line that can hold its own
    # hue is placed first, and the displaced one takes what is genuinely left over.
    series = _meta_series(("aggro", 2024, 0.4), ("control", 2024, 0.3),
                          ("combo", 2024, 0.2))
    # "combo" collides with "aggro" a scale later; "control" follows it holding EXTENDED[1]
    # outright, which is the hue a one-pass walk would have given away to "combo".
    universe = ["aggro", *[f"filler{i}" for i in range(len(palette.EXTENDED) - 1)],
                "combo", "control"]

    def colour_by_archetype(tags):
        fig = _trend_figure(series, tags, universe=universe)
        return {t.name: t.marker.line.color for t in fig.data}

    before = colour_by_archetype(["aggro", "control"])
    after = colour_by_archetype(["aggro", "control", "combo"])

    assert after["Control"] == before["Control"] == palette.EXTENDED[1]
    assert len(set(after.values())) == 3, after


def _emphasis_layers(fig):
    """A figure's (context, raised) traces, split on which carries a legend entry."""
    return (
        [t for t in fig.data if t.showlegend is False],
        [t for t in fig.data if t.showlegend is not False],
    )


def _wide_meta(n):
    """A meta-share chart of ``n`` equal archetypes, and the tags it was drawn from."""
    triples = [(f"arch{i}", 2024, 0.05) for i in range(n)]
    tags = [t for t, _, _ in triples]
    return _trend_figure(_meta_series(*triples), tags), tags


def test_every_line_is_drawn_faded_and_paired_with_a_full_strength_twin():
    # AC (#117, ADR-0013's #116 amendment as revised by user review): past eight series
    # the chart switches to emphasis. Every archetype is drawn twice: a faded context
    # line on screen from the first paint, and a full-strength twin in the same hue
    # that carries the legend entry and starts hidden. Faded *in its own colour*, not
    # in one grey: at fourteen lines a single grey shape is untraceable, so hue stays
    # as the cue that lets the eye follow one line across the years, while the raise
    # is what names it.
    fig, tags = _wide_meta(9)
    context, raised = _emphasis_layers(fig)

    assert len(context) == len(raised) == 9
    # Each pair is one hue at two strengths: the context line is the raised line's own
    # colour, faded, so raising a line cannot change which line it is. The exact opacity
    # is a design dial settled by eye, so what is pinned here is that the hue survives
    # the fade and that the fade is deep enough to read as context.
    for faded, full in zip(context, raised):
        r, g, b = pc.hex_to_rgb(full.line.color)
        assert faded.line.color.startswith(f"rgba({r}, {g}, {b},")
        assert float(faded.line.color.rsplit(",", 1)[1].rstrip(" )")) <= 0.5
        assert faded.marker.line.color == faded.line.color
    # Every archetype has its own hue, and the first eight are the signed set, so a
    # re-cut to eight or fewer lines does not repaint the survivors.
    assert len({t.line.color for t in raised}) == 9
    assert [t.line.color for t in raised][:8] == list(palette.CATEGORICAL)
    # The faded layer is on screen from the first paint whatever the raise does, so no
    # click can leave the reader with a line missing rather than a line receded.
    assert all(t.visible is None or t.visible is True for t in context)
    # It draws as line only. The observation marker's fill is the *opaque* surface, so
    # at this width thirty-one archetypes' worth of them would tile over each other and
    # chop every faded line into segments, which is the tracing the fade exists to keep.
    # The raised line, which is the one being read closely, keeps its observations.
    assert all(t.mode == "lines" for t in context)
    assert all(t.mode == "lines+markers" for t in raised)
    assert [t.name for t in raised] == [t.title() for t in tags]


def test_a_small_cut_fades_and_raises_on_the_signed_hues_too():
    # AC (#117, user review): emphasis is how this chart reads at every width, not a
    # mode that switches on past eight lines. The narrow cut (five archetypes) fades
    # and raises exactly as the wide one does, so moving the cut changes how many lines
    # are drawn and nothing else. Its hues are the signed eight in slot order, which is
    # also the extended scale's opening, so a line keeps its colour across every cut.
    fig, _ = _wide_meta(5)
    context, raised = _emphasis_layers(fig)

    assert len(context) == len(raised) == 5
    assert [t.line.color for t in raised] == list(palette.CATEGORICAL[:5])
    assert [t.visible is True for t in raised] == [True, True, True, False, False]
    assert fig.layout.legend.itemclick == "toggle"


def test_the_cut_opens_on_its_leading_archetypes_not_on_a_blank_field():
    # AC (#117, user review): a cold start must show the reader something. The cut
    # passes its tags strongest-first, so the leading few open raised and the rest of
    # the field sits faded behind them. Without this the first thing a reader ever sees
    # is a chart of nothing but context, which reads as broken rather than as an
    # invitation to click.
    fig, tags = _wide_meta(9)
    _, raised = _emphasis_layers(fig)
    open_on = [t.name for t in raised if t.visible is True]

    assert open_on == [t.title() for t in tags[:3]]  # the strongest three, in rank order
    assert all(t.visible == "legendonly" for t in raised[3:])  # the field stays behind


def test_hand_picked_archetypes_all_open_raised():
    # AC (#117, user review): the manual panel is not a cut. Every line in it was named
    # by the reader, so all of them open raised and the click fades: opening a
    # hand-picked set behind a leading-three rule would ask the reader to choose the
    # same archetypes twice.
    series = _meta_series(("aggro", 2024, 0.4), ("control", 2024, 0.3),
                          ("combo", 2024, 0.2), ("burn", 2024, 0.1))
    tags = ["aggro", "control", "combo", "burn"]
    context, raised = _emphasis_layers(
        _trend_figure(series, tags, start_raised=len(tags))
    )

    assert len(context) == len(raised) == 4
    assert all(t.visible is True for t in raised)


def test_the_raise_is_a_legend_click_not_a_point_hover():
    # AC (#117): the raise is Plotly-native legend interaction, because gr.Plot gives
    # no point-level hover callback (#78). The legend says it is clickable, since a
    # column of identical accent swatches over a hidden layer is otherwise not
    # obviously a control.
    #
    # Both handlers are pinned away from `toggleothers`, which reads like the isolate
    # this chart wants but does the opposite from a hidden start: its handler branches
    # on the *clicked* trace's own visibility, and a `legendonly` one takes
    # `case"legendonly": q(He,!0)` (plotly.min.js), turning every trace in the legend
    # on. Every raise starts hidden, so that is the reader's first click, and it would
    # draw all fifteen accent lines at once. `itemdoubleclick` defaults to
    # `toggleothers`, so it has to be switched off for the same reason.
    fig, _ = _wide_meta(9)
    title = fig.layout.legend.title.text

    assert fig.layout.legend.itemclick == "toggle"
    assert fig.layout.legend.itemdoubleclick is False
    # The hint sits on its own line under the legend's name and in the palette's blue,
    # so it reads as an invitation to act rather than as part of the label.
    name, _, hint = title.partition("<br>")
    assert name == "Archetype"
    assert "click" in hint.lower()
    assert palette.CATEGORICAL[0] in hint


def test_the_widest_cut_draws_no_look_alike_pair_and_no_borrowed_wheel():
    # AC (#117): what the cut used to do was recycle a 32-entry qualitative wheel
    # borrowed from Plotly, at full strength, so two archetypes could land on the same
    # hue and none of the fifteen was traceable. The widest cut the app offers is 31
    # lines, and across it every archetype must hold a hue of its own, all of them from
    # this repo's own scale. Read off the serialised figure, so a borrowed hue that
    # sneaks into a marker or a legend swatch trips this too. The claim is cut-scoped:
    # since the meta hues are assigned over the whole 126-archetype universe, a
    # hand-picked set that reaches past its thirty-second slot can share a hue.
    fig, _ = _wide_meta(31)
    _, raised = _emphasis_layers(fig)

    assert len({t.line.color for t in raised}) == 31  # no two archetypes share a hue
    assert set(t.line.color for t in raised) <= set(palette.EXTENDED)
    drawn = fig.to_json().lower()
    assert not [c for c in pc.qualitative.Dark24 + pc.qualitative.Light24
                if c.lower() in drawn and c.lower() not in
                {h.lower() for h in palette.EXTENDED}]


def test_share_chart_ticks_and_hovers_speak_the_one_numeric_convention():
    # AC (§4): every share axis tick and every hovered share is the one convention,
    # produced by numfmt, not a second ad-hoc format. Read the tick format off the
    # axis and the share off the hover data, both against numfmt's own output, so a
    # chart that hardcoded `.2%` or a bare float trips this.
    from graph7ph import numfmt

    fig = _trend_figure(_meta_series(("aggro", 2024, 0.0673)), ["aggro"])
    trace = fig.data[0]

    assert fig.layout.yaxis.tickformat == numfmt.SHARE_TICKFORMAT
    # The hover carries the share and the count/sample-size, both via numfmt.
    assert trace.customdata[0][0] == numfmt.share(0.0673) == "6.73%"
    assert trace.customdata[0][1] == numfmt.count_of(67, 1000, "decks")


def test_a_chart_title_is_a_page_heading_not_plotly_font_inside_the_image():
    # AC (§6): a chart's title leaves the Plotly figure and becomes a page heading in
    # the app's own type, so a result reads as a heading on the page rather than font
    # baked into an image. The heading helper carries the result-title type role (the
    # same §3 role the graph results title in), and the figures themselves draw no
    # Plotly title.
    heading = _chart_heading("Pilot performance: Ada L")
    assert "t-result-title" in heading
    assert "Pilot performance: Ada L" in heading

    figures = [
        _performance_figure("Ada L", Series(cells=[
            _performance_cell(2024, 0.4, 3),
        ])),
        _trend_figure(_meta_series(("aggro", 2024, 0.3)), {"aggro"}),
        _adoption_figure([("Sol Ring", Series(cells=[
            AdoptionCell(year=2024, count=30, share=0.03, year_total=1000),
        ]))]),
        _race_figure(*_race_figure_args()),
    ]
    for fig in figures:
        assert not fig.layout.title.text  # no Plotly-font title baked into the image


def test_a_chart_heading_escapes_a_subject_that_carries_markup():
    # The subject in a heading is a free-text display label, so an angle bracket in a
    # name is escaped into the markup rather than injected, as the graph result header
    # already does.
    heading = _chart_heading("Card adoption over time: A<b>")
    assert "A<b>" not in heading
    assert "A&lt;b&gt;" in heading


def test_chart_gridlines_and_axes_ride_the_design_tokens_not_a_stray_grey():
    # AC (issue #112, §2/§6): the committed dark theme retires the theme-neutral
    # greys. Gridlines take the hairline border token, axis/tick text the muted
    # token, and the `#9ca3af` the theme-neutral era set the font to is gone. Read
    # off the figure the chart actually draws, so a regression to a hardcoded grey
    # trips this.
    fig = _performance_figure("Ada L", Series(cells=[
        _performance_cell(2024, 0.4, 3),
        _performance_cell(2025, 0.2, 5),
    ]))

    assert fig.layout.xaxis.gridcolor == theme.TOKENS["border"]
    assert fig.layout.yaxis.gridcolor == theme.TOKENS["border"]
    assert fig.layout.xaxis.linecolor == theme.TOKENS["text-mute"]
    assert fig.layout.font.color == theme.TOKENS["text-mute"]
    # The whole figure, serialised, must not carry the retired theme-neutral grey.
    assert "#9ca3af" not in fig.to_json()


def test_chart_chrome_is_set_in_the_app_face_not_plotly_default():
    # AC (#85, criterion 1): "a written design direction exists ... every surface
    # follows it". A figure that never names a font renders its axis titles, ticks,
    # legend and hovers in Plotly's own `"Open Sans", verdana, arial` -- a stack
    # nothing else on the page uses, so the charts read as embedded images rather than
    # as part of the page. Asserted on every figure the app draws, since the styling
    # they share is the only place this can be set once.
    figures = [
        _performance_figure("Ada L", Series(cells=[
            _performance_cell(2024, 0.4, 3),
        ])),
        _trend_figure(_meta_series(("aggro", 2024, 0.3)), {"aggro"}),
        _adoption_figure([("Sol Ring", Series(cells=[
            AdoptionCell(year=2024, count=30, share=0.03, year_total=1000),
        ]))]),
    ]
    for fig in figures:
        assert fig.layout.font.family == theme.FONT_STACK
        assert "verdana" not in fig.to_json().lower()


def test_a_wrapping_legend_costs_the_plot_no_width():
    # AC (#85, criterion 12): "graph and charts fill their space from phone width to a
    # wide monitor". Plotly's default legend sits to the right *inside the figure's
    # width*, so at a phone's 390px the meta chart's fourteen archetype names took
    # roughly half of it and crushed the lines into a strip. A figure has one layout
    # and no media queries reach inside it, so the legend has to sit where it costs no
    # width at any size: laid out horizontally, anchored below the plot.
    def _adoption(*names):
        return _adoption_figure([
            (name, Series(cells=[
                AdoptionCell(year=2024, count=30, share=0.03, year_total=1000),
            ]))
            for name in names
        ])

    many = _trend_figure(
        _meta_series(*[(t, 2024, 0.03) for t in ("aggro", "control", "combo")]),
        {"aggro", "control", "combo"},
    )
    for fig in (many, _adoption("Sol Ring", "Mana Crypt")):
        assert fig.layout.legend.orientation == "h"
        assert fig.layout.legend.y < 0  # below the plot, not overlapping it
        assert fig.layout.legend.yanchor == "top"
        # And the figure is given the height that legend needs. Told to put the legend
        # below without the room for it, Plotly lays it out past the figure's own
        # bottom edge and clips the tail of the list -- which on the meta chart is an
        # archetype the reader then cannot raise, since the legend is the control.
        assert fig.layout.height == _LEGEND_BELOW_HEIGHT

    # A chart Plotly draws no legend for (one series) keeps its natural height rather
    # than holding that room open under the plot for a legend that never arrives.
    assert _adoption("Sol Ring").layout.height is None


def test_observation_markers_carry_a_surface_ring_over_a_thin_dashed_join():
    # AC (§6): the ADR-0013 read is kept: a thin dashed line that only joins the
    # points, hollow observation markers, and the markers gain a 2px surface ring
    # so two that overlap do not muddy into each other. The ring is the surface
    # colour filling the marker; the series colour is its 2px outline.
    trace = _performance_figure("Ada L", Series(cells=[
        _performance_cell(2024, 0.4, 3),
    ])).data[0]

    assert trace.line.dash == "dash"  # joins points, asserts no trend (ADR 0013)
    assert trace.line.width == 1  # thin
    assert trace.marker.symbol == "circle"
    assert trace.marker.color == theme.TOKENS["surface"]  # the surface ring
    assert trace.marker.line.width == 2  # a 2px outline in the series colour
    assert trace.marker.line.color == palette.CATEGORICAL[0]


def test_the_markers_that_sit_on_a_tinted_fill_drop_their_opaque_ring():
    # AC (#172): the surface fill only reads as hollow over bare surface. The two
    # rivalry charts paint a tint under their points (the band between two lines, or
    # one line filled to the axis), and there the fill punched a surface-coloured hole
    # in it: 0 of 6 band-facing half-discs matched the band on the pilot chart, 1 of 67
    # on the archetype one, median channel delta 36. Those points go fully transparent
    # so the ring reads against what it covers. Every chart that paints nothing under
    # its points keeps the opaque fill and the occlusion it buys.
    def marker_fills(fig):
        return {t.marker.color for t in fig.data if "markers" in (t.mode or "")}

    transparent = {"rgba(0,0,0,0)"}
    assert marker_fills(_head_to_head_figure("Ada L", "Bob C", Series(cells=[
        _h2h_point("SSWam", datetime(2024, 3, 1), 88),
        _h2h_point("PBB", datetime(2024, 6, 1), 24),
    ]))) == transparent
    assert marker_fills(_archetype_timeline_figure(
        "Storm", "Lands", _timeline_points((1, 0.2, 3, 0.4, 2), (2, 0.6, 1, 0.3, 4)),
    )) == transparent
    # Solo too: it fills its one line to the axis, so its points sit on a tint as well.
    assert marker_fills(_archetype_timeline_figure(
        "Storm", None, _timeline_points((1, 0.4, 1), (2, 0.6, 2)),
    )) == transparent

    surface = {theme.TOKENS["surface"]}
    assert marker_fills(_performance_figure("Ada L", Series(cells=[
        _performance_cell(2024, 0.4, 3),
    ]))) == surface
    assert marker_fills(_landscape_figure(_landscape_cells(
        ("grixis", 30, 0.45), ("storm", 20, 0.4),
    ))) == surface
    assert marker_fills(_race_figure(*_race_figure_args())) == surface
    assert marker_fills(_adoption_figure([("Sol Ring", Series(cells=[
        AdoptionCell(year=2024, count=30, share=0.03, year_total=1000),
    ]))])) == surface


def test_the_app_builds_end_to_end_over_a_real_artifact(tmp_path, snapshot_dir):
    # A smoke test over the whole wiring: build_app opens a real artifact and
    # constructs every tab, which runs draw_cut at build time (the meta chart, its
    # page heading, palette and marker chrome, all end to end). A broken heading
    # thread or a figure that no longer builds trips here rather than only in the
    # browser.
    from graph7ph.app import build_app
    from graph7ph.build import build_graph
    from graph7ph.models import load_snapshot

    artifact = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), artifact)
    demo = build_app(artifact)

    import gradio as gr
    assert isinstance(demo, gr.Blocks)


class _RecordingButton:
    """A stand-in for a ``gr.Button`` that records the event chain wired onto it.

    ``click``/``then`` on a real button register with the Blocks being built and return
    a chainable handle, so a stub that records the calls and hands itself back is
    enough to read the wiring off without standing up Gradio.
    """

    def __init__(self):
        self.chain: list[tuple[str, object, dict]] = []

    def click(self, fn, **kwargs):
        self.chain.append(("click", fn, kwargs))
        return self

    def then(self, fn, **kwargs):
        self.chain.append(("then", fn, kwargs))
        return self


def test_a_draw_says_it_is_running_and_comes_back_even_when_the_query_fails():
    # AC (#85, criterion 9): "every query-running action shows progress while it runs".
    # Gradio's own indicator paints over the *output* components, and since #132/#138
    # a view's results stack is hidden until its callback fills it, so on a Draw there
    # is nothing on screen for it to sit on and the click reads as a no-op. The button
    # carries the state instead: busy before the query, back to rest after.
    button = _RecordingButton()

    def query():
        raise AssertionError("never called in this test")

    results = ["the results stack"]
    _draw_with_progress(button, query, inputs=["the subject"], outputs=results)

    # The query runs, and it is the real one: not wrapped, and still reading and writing
    # the view's own inputs and outputs.
    ran = next(s for s in button.chain if s[1] is query)
    assert ran[2]["inputs"] == ["the subject"] and ran[2]["outputs"] == results

    # Before it, the button says it is running; after it, the button comes back. Both
    # write to the button itself, which is the component the state has to land on.
    before = [s for s in button.chain if s[1] is not query][0]
    after = [s for s in button.chain if s[1] is not query][-1]
    assert button.chain.index(before) < button.chain.index(ran) < button.chain.index(after)
    assert before[2]["outputs"] is button and after[2]["outputs"] is button

    busy, idle = before[1](), after[1]()
    assert busy["value"] == DRAWING_LABEL and busy["interactive"] is False
    assert idle["value"] == DRAW_LABEL and idle["interactive"] is True
    # The two labels have to differ, or the "progress" is invisible.
    assert DRAW_LABEL != DRAWING_LABEL
    # The button comes back through `then`, not `success`: a query that raises must not
    # strand it disabled with no way back. `then` runs either way; `success` would not.
    assert after[0] == "then"


def test_every_graph_plot_shares_one_responsive_frame():
    # §12 / AC (#85, criterion 12): "graph and charts fill their space from phone width
    # to a wide monitor, with no fixed 700/760px letterbox". Every graph plot shares one
    # frame, so a dense graph and a sparse one read as one canvas across the tabs rather
    # than each scaling to its own node count -- and that frame is a share of the
    # viewport between a floor and a ceiling, not the flat 760px that was most of a
    # phone's screen.
    from graph7ph.app import GRAPH_HEIGHT, GRAPH_MIN_HEIGHT, GRAPH_VIEWPORT_SHARE

    frame = _embed("<html></html>")
    assert (
        f"height:clamp({GRAPH_MIN_HEIGHT}px, {GRAPH_VIEWPORT_SHARE}, {GRAPH_HEIGHT}px)"
        in frame
    )
    # The ceiling is the tuned size the pilot neighbourhood renders well at, which a
    # desktop still gets; the floor sits under it so a dense graph stays legible where
    # the proportion alone would squeeze it.
    assert GRAPH_MIN_HEIGHT < GRAPH_HEIGHT
    assert GRAPH_HEIGHT >= 700
    # And no fixed height survives beside it: the letterbox the criterion names is a
    # `height:760px`, which a clamp carrying the same ceiling must not reintroduce.
    assert f"height:{GRAPH_HEIGHT}px" not in frame


def test_provenance_surface_states_coverage_the_update_date_and_the_contact():
    # AC (#115): a coverage row names events/pilots/decks/distinct-cards and the year
    # range, thousands-comma'd (§4); below it, when the graph was last updated and who
    # to reach. Pure formatter, so the content is asserted without standing up Gradio.
    html = _provenance_html(
        Coverage(events=108, pilots=1083, decks=4591, cards=4995,
                 first_year=2023, last_year=2026),
        "2026-07-23T23:05:46.403758+00:00",
    )

    for figure in ("108", "1,083", "4,591", "4,995"):
        assert figure in html
    assert "2023" in html and "2026" in html  # the year span, both ends
    assert "2026-07-23" in html  # the last update, to the day
    assert "Alejandro de la Fuente" in html
    assert "mailto:alejandrofuentepinero@gmail.com" in html  # reachable, not just named
    assert "alejandrofp92" in html  # the Discord handle


def test_provenance_surface_collapses_a_single_year_and_survives_an_unknown_build():
    # A one-year graph reads as that year, not "2025–2025"; and a bundle with no
    # readable stamp (built_at is None) says so rather than printing "None".
    one_year = _provenance_html(
        Coverage(events=2, pilots=2, decks=3, cards=121,
                 first_year=2025, last_year=2025),
        None,
    )
    assert "2025" in one_year
    assert "2025 – 2025" not in one_year and "2025–2025" not in one_year
    assert "None" not in one_year


def test_built_app_shows_the_provenance_surface_fed_real_coverage(tmp_path, snapshot_dir):
    # Wiring: the built app actually renders the surface, fed this graph's own counts
    # (2 events, 2 pilots, 3 decks, 121 cards for the fixture) and the contact line, so
    # a surface left unwired or fed stale numbers trips here rather than in the browser.
    surface = " ".join(_all_surface_text(_built_demo(tmp_path, snapshot_dir)))

    assert "121" in surface  # the fixture's distinct card count
    assert "alejandrofuentepinero@gmail.com" in surface


def _landscape_cells(*rows, year=2026, total=100, year_events=10, half=0.1):
    """Landscape cells from ``(tag, decks, mean_norm[, events[, scored]])``, one year.

    Every scored cell carries an interval ``half`` either side of its mean (#175),
    stated flat rather than re-derived from the field's spread: what the surface owes a
    reader is the width the tool handed it, and a test that recomputed the width would
    be asserting the tool's arithmetic twice.

    ``scored`` defaults to the deck count, the case where every deck of the archetype
    was played at an event that published a field; a row states it only where the two
    populations differ (ADR 0022).
    """
    return [
        LandscapeCell(tag=row[0], archetype=row[0].title(), year=year, n=row[1],
                      share=row[1] / total, year_total=total, year_events=year_events,
                      mean_norm=row[2], events=row[3] if len(row) > 3 else row[1],
                      scored=row[4] if len(row) > 4 else row[1],
                      mean_low=None if row[2] is None else max(0.0, row[2] - half),
                      mean_high=None if row[2] is None else min(1.0, row[2] + half))
        for row in rows
    ]


def test_the_landscape_draws_the_top_archetypes_by_share_of_the_chosen_year():
    # The chart is bounded to its top N by share, recomputed for whichever year is
    # selected. Ranked strongest first, ties broken on the tag, so the same year
    # always draws the same set rather than leaving it to dict order.
    series = Series(cells=_landscape_cells(
        ("grixis", 30, 0.45), ("zoo", 20, 0.5), ("lands", 20, 0.4),
        ("storm", 10, 0.3), ("oracle", 5, 0.6),
    ))
    assert [c.tag for c in _landscape_top(series, 3)] == ["grixis", "lands", "zoo"]
    # A cut wider than the year's field draws the whole field, never pads it.
    assert len(_landscape_top(series, 25)) == 5


def test_an_archetype_the_year_never_scored_is_not_a_dot_without_a_finish():
    # A dot needs both axes. An archetype played but never scored has a share and no
    # finish, so it cannot be placed. It is dropped after the cut, not before: the cut
    # is over every archetype the year held, so the gap it leaves is a dot missing from
    # the top 2 rather than the third archetype quietly promoted into it, which would
    # make the caption's "top 2 by share" describe a set it does not describe.
    series = Series(cells=_landscape_cells(
        ("grixis", 30, None), ("zoo", 20, 0.5), ("lands", 10, 0.4), ("storm", 5, 0.3),
    ))
    assert [c.tag for c in _landscape_top(series, 2)] == ["zoo"]
    # It is still one of the archetypes the year held, which the surface counts.
    assert len(series.cells) == 4


def test_the_landscape_plots_share_against_finish_with_every_dot_named():
    # The quadrant read is the point: share on a linear x, finish on y, one named dot
    # per archetype. The finish is flipped for the eye (higher is better) while the
    # tool keeps the raw placementNorm, as the pilot charts do.
    from graph7ph import numfmt

    fig = _landscape_figure(_landscape_cells(
        ("grixis", 30, 0.45, 12, 26), ("storm", 6, 0.25),
    ))
    (trace,) = fig.data

    assert fig.layout.xaxis.type == "linear"  # not the shared styler's category axis
    assert fig.layout.xaxis.tickformat == numfmt.SHARE_TICKFORMAT
    assert list(trace.x) == [0.30, 0.06]
    assert list(trace.y) == [pytest.approx(0.55), pytest.approx(0.75)]
    # Every dot carries its archetype name on the chart, so it reads in a screenshot
    # rather than only under a cursor.
    assert list(trace.text) == ["Grixis", "Storm"]
    assert "text" in trace.mode

    # The hover carries the archetype, its share as both a percent and a deck count,
    # its finish, and the decks and distinct events behind that finish, all through
    # numfmt. Both counts, because the two axes read different decks: 30 were played
    # and 26 of them at an event that published a field, so the mean is over 26 (ADR
    # 0022). Stated where a reader is already looking rather than left to be inferred,
    # and each half named, so which count belongs to which axis is not a guess.
    grixis = trace.customdata[0]
    assert list(grixis) == ["Grixis", numfmt.share(0.30),
                            numfmt.count_of(30, 100, "decks"),
                            numfmt.score(0.55, sense=False), 26, 12]
    assert "share" in trace.hovertemplate and "finish" in trace.hovertemplate
    assert "scored" in trace.hovertemplate and "events" in trace.hovertemplate
    # The y axis already says which end is good, so the readout beside it does not.
    assert "1 = 1st" not in trace.hovertemplate
    assert not any("1 = 1st" in str(v) for row in trace.customdata for v in row)


def test_landscape_marker_size_carries_the_events_not_the_deck_count():
    # Share is already the x axis (within a year it is monotone in decks), so sizing
    # by decks would say the same thing twice; the marker carries the independent
    # trials instead, on the same scale the pilot chart's rings use.
    # Grixis: many decks, few events. Storm: few decks, many events.
    cells = _landscape_cells(("grixis", 30, 0.45, 4), ("storm", 6, 0.25, 30))
    sizes = _landscape_figure(cells).data[0].marker.size

    assert sizes == (_confidence_size(4), _confidence_size(30))


def test_every_landscape_dot_carries_its_interval_and_the_frame_holds_it():
    # The same claim defect as the pilot chart (#175): the dots are means of a handful
    # of decks and the caption reads which side of 0.5 each sits. Every dot carries its
    # own 90% interval, flipped with the score as the pilot chart flips it, so a reader
    # can see that most of them do not settle which side they belong on.
    cells = _landscape_cells(("grixis", 30, 0.45), ("storm", 6, 0.25), half=0.2)
    fig = _landscape_figure(cells)
    bars = fig.data[0].error_y

    assert bars.array == pytest.approx([0.2, 0.2])
    assert bars.arrayminus == pytest.approx([0.2, 0.2])
    # The frame holds the whiskers: a bar drawn past the top of the axis would show a
    # narrower interval than the dot actually carries, which is the overclaim inverted.
    low, high = fig.layout.yaxis.range
    assert low <= 0.35 and high >= 0.95


def test_the_landscape_keeps_the_half_way_reference_line_in_frame():
    # The caption explains what being above 0.5 means, so the line has to be on the
    # chart to explain. A year whose dots all beat the middle of the field would
    # autorange the line out of view, so the range covers the data and 0.5 both.
    fig = _landscape_figure(_landscape_cells(
        ("storm", 30, 0.2), ("oracle", 20, 0.25), ("grixis", 10, 0.3),
    ))
    (line,) = fig.layout.shapes

    assert (line.y0, line.y1) == (0.5, 0.5)
    low, high = fig.layout.yaxis.range
    assert low < 0.5 < high
    assert high >= 0.8  # the best finish (1 - 0.2) still sits inside the frame


def test_every_chart_that_reads_finishes_against_the_middle_draws_one_neutral_line():
    # The 0.5 line is the one thing three captions ask a reader to read a dot against,
    # and drawn in the dimmest ink in the set it was the quietest mark on the chart. It
    # moves up the neutral ramp, off the token rather than a stray hex.
    #
    # Neutral rather than the accent, and that is the load-bearing half: the rivalry
    # pair draws its second archetype in the palette's slot-2 orange, so an accent line
    # reads as a third series there, and no hue is safe because the meta chart draws all
    # eight slots at once. §5-6 spends colour on entities and refuses a ninth hue, so a
    # line that names no entity stays neutral and gets visible by brightness instead.
    figures = [
        _performance_figure("Ada L", Series(cells=[
            _performance_cell(2024, 0.4, 3), _performance_cell(2025, 0.2, 5),
        ])),
        _landscape_figure(_landscape_cells(
            ("storm", 30, 0.2), ("oracle", 20, 0.25), ("grixis", 10, 0.3),
        )),
        _archetype_timeline_figure("Storm", None, _timeline_points((1, 0.4, 1))),
    ]

    for fig in figures:
        midpoints = [s for s in fig.layout.shapes if (s.y0, s.y1) == (0.5, 0.5)]
        assert len(midpoints) == 1, "each chart draws the middle of the field once"
        colour = midpoints[0].line.color
        assert colour == _rgba(theme.TOKENS["text-dim"], 0.85)
        # Brighter than the axis ink it used to share, or the change bought nothing.
        assert colour != _rgba(theme.TOKENS["text-mute"], 0.55)
        # And on no series' hue, on any chart, at any opacity.
        for hue in palette.CATEGORICAL + (theme.TOKENS["accent-bright"],):
            assert not colour.startswith(_rgba(hue, 1).rsplit(",", 1)[0])


def test_the_landscape_carries_a_share_range_filter_to_pull_the_crowded_end_apart():
    # Alternating sides cleared the adjacent pairs and no more: a real year puts twenty
    # of its twenty-five archetypes between 1% and 3% of share, which is four or five
    # names deep in one band, and no label placement rule survives that. The reader gets
    # the axis instead, as a range filter on share, the same control the rivalry charts
    # give the date axis. Landscape only: it is the one chart whose x crowds.
    fig = _landscape_figure(_landscape_cells(
        ("grixis", 30, 0.45), ("storm", 20, 0.4), ("lands", 10, 0.55),
    ))
    slider = fig.layout.xaxis.rangeslider
    accent = theme.TOKENS["accent-bright"]

    assert slider.visible
    # Tinted as a control, matching the filter the rivalry charts already carry, so the
    # two read as the same affordance rather than as two inventions.
    assert slider.bgcolor == _rgba(accent, 0.12)
    assert slider.bordercolor == _rgba(accent, 0.55)
    # The preview is parked off the 0-1 score band, or the slider draws a second copy
    # of the dots and reads as a broken plot rather than as a control.
    assert tuple(slider.yaxis.range) == (10, 11)
    label = next(a for a in fig.layout.annotations if "range filter" in a.text)
    assert "Share" in label.text  # its own axis named, not the rivalry charts' time
    assert label.font.color == _rgba(accent, 0.95)
    # The band, its label and the axis title are sized by Plotly's own autoexpand, not
    # reserved by hand: a hand-set floor is added to rather than absorbed, and the one
    # this carried left 55px of empty card under the axis title.
    assert fig.layout.margin.b == 8


def test_the_landscape_draws_tall_enough_that_its_furniture_does_not_own_the_frame():
    # The one chart whose chrome crowded out its own plot: at Plotly's 450px default the
    # share range filter's band, the tick labels and the axis title claimed 166px and
    # left 25 named dots and their intervals 276px, 61 percent of the frame. Stated on
    # the figure, because a Gradio plot renders at the height its figure carries.
    fig = _landscape_figure(_landscape_cells(
        ("grixis", 30, 0.45), ("storm", 20, 0.4), ("lands", 10, 0.55),
    ))
    assert fig.layout.height == _LANDSCAPE_HEIGHT
    # Taller than the default it is overriding, or the override says nothing.
    assert _LANDSCAPE_HEIGHT > 450


def test_the_range_filter_is_the_same_band_on_every_chart_whatever_its_height():
    # The control is furniture, so it does not grow with the chart it sits under: Plotly
    # takes its thickness as a fraction of the plot, and the flat 0.12 this passed drew
    # 44px under a 450px figure but 75 under the landscape's 640, a strip half again as
    # deep as the one the rivalry charts carry. Its label rode a paper fraction tuned at
    # the same 450, which at 640 dropped it onto the bottom edge of the band it names.
    # Read off both charts, which draw at different heights, so a band or a label that
    # ignores height cannot pass twice.
    figures = (
        _landscape_figure(_landscape_cells(("grixis", 30, 0.45), ("storm", 20, 0.4))),
        _head_to_head_figure("Ada L", "Bob C", Series(cells=[
            _h2h_point(event="GP", date=datetime(2024, 3, 1), field_size=100,
                            placement_a=1, norm_a=0.0, placement_b=50, norm_b=0.5),
            _h2h_point(event="PT", date=datetime(2024, 6, 1), field_size=80,
                            placement_a=40, norm_a=0.5, placement_b=1, norm_b=0.0),
        ])),
    )
    heights = set()
    for fig in figures:
        margin = fig.layout.margin
        heights.add(fig.layout.height)
        # Plotly's own rule for the band: `thickness` of the plot the margins leave.
        band = fig.layout.xaxis.rangeslider.thickness * (
            (fig.layout.height or 450) - margin.t - margin.b
        )
        assert band == pytest.approx(_SLIDER_BAND)
        label = next(a for a in fig.layout.annotations if "range filter" in a.text)
        # Anchored to the bottom of the plot and offset in pixels, since that is the
        # coordinate the band's own geometry is in, and centred in it: a label against
        # either edge reads as a stray caption beside the control rather than its label.
        assert (label.yref, label.y) == ("paper", 0)
        assert label.yshift == pytest.approx(-(_TICK_LABEL_ROOM + _SLIDER_BAND / 2))
    # And the two charts really do draw at different heights, or one band proves nothing
    # about the other.
    assert len(heights) == 2


def test_landscape_labels_alternate_sides_so_neighbouring_dots_do_not_overprint():
    # 25 archetype names crowd into the low-share end of the axis and overprint each
    # other, and the bars now own the space above every dot, so the names sit beside
    # the rings and adjacent dots take opposite sides. Ordered by share, since that is
    # the axis they collide along.
    # Both parities, because the real chart draws an odd count: _LANDSCAPE_TOP_N is 25,
    # and an earlier version forced the highest-share dot inward unconditionally, which
    # on an odd count overwrote its alternated side and left the top two dots sharing
    # one. An even-count test never sees it.
    for count in (4, 5, 25):
        cells = _landscape_cells(*(
            (f"deck{i:02d}", 30 - i, 0.4 + i / 100) for i in range(count)
        ))
        sides = dict(zip([c.tag for c in cells],
                         _landscape_figure(cells).data[0].textposition))

        by_share = [c.tag for c in sorted(cells, key=lambda c: c.share)]
        for left, right in zip(by_share, by_share[1:]):
            assert sides[left] != sides[right], f"{left} and {right} overprint at n={count}"
        # The low-share end points inward: that is where the dots crowd the frame edge.
        assert sides[by_share[0]] == "middle right"


def test_the_landscape_caption_states_the_cut_the_season_and_the_reference_line():
    # One claim and one sample clause (§14, #156): what beating the 0.5 line means and
    # how many dots did, then which archetypes were drawn out of how many the year held
    # and how much of a season they rest on. What an error bar *is* went to the FAQ.
    series = Series(cells=_landscape_cells(
        ("grixis", 30, 0.45), ("zoo", 20, 0.5), ("lands", 20, 0.4),
        ("storm", 10, 0.3), ("oracle", 5, 0.6),
        year=2025, total=2095, year_events=51, half=0.03,
    ))
    caption = _landscape_caption(series, _landscape_top(series, 3), 3, in_progress=False)

    assert "top 3 of 5 archetypes by share" in caption
    assert "51 events" in caption
    assert "2,095 decks" in caption  # the one numeric convention, thousands-comma'd
    assert "0.5" in caption and "middle of the field" in caption
    # Counted on the drawn dots, not asserted: Grixis (.45) and Lands (.40) beat the
    # middle of the field and Zoo (.50) sits exactly on it, so two of the three are
    # above. Both of those two also settle it, their intervals (.42-.48 and .37-.43)
    # clearing the line (#175).
    sentence = re.sub(r"<[^>]+>", "", caption)
    assert "2 of 3 beat the middle of the field" in sentence
    assert "2 by more than their error bar" in sentence
    # One qualifier: the cut and the season read as one sample clause, and nothing
    # trails behind them (§14, #156).
    assert caption.count("·") == 1
    # A finished year makes no partial-season claim.
    assert "so far" not in caption


def test_the_caption_counts_the_dots_above_the_line_and_says_how_many_settle_it():
    # Two numbers, because either alone misleads. The gated count on its own read as a
    # contradiction of the picture: a reader seeing twenty dots over the line was told
    # "1 of 25", and the caption lost to the chart. The plain count on its own is the
    # overclaim #175 exists to remove. So the plain count leads, matching what the eye
    # does, and the settled count qualifies it.
    #
    # Grixis (.30, interval .20 to .40) settles it. Storm's mean beats the middle of the
    # field but its interval (.38 to .58 raw) crosses the line, so it is above and not
    # settled. An archetype the year never scored has no interval and settles nothing.
    series = Series(cells=_landscape_cells(
        ("grixis", 30, 0.30), ("storm", 20, 0.48), ("ghost", 10, None), half=0.1,
    ))
    caption = _landscape_caption(series, _landscape_top(series, 25), 25, in_progress=False)
    # Read as the sentence a reader sees, since the two numbers are set in their own
    # accent spans and the markup falls between them.
    sentence = re.sub(r"<[^>]+>", "", caption)

    assert "2 of 2 beat the middle of the field" in sentence  # both means beat it
    assert "1 by more than their error bar" in sentence       # one of the two settles it


def test_the_landscape_caption_says_when_the_year_is_still_running():
    # The latest year is partial (the newest deck is mid-year), so a reader comparing
    # it against a full season has to be told, with the counts it has so far.
    series = Series(cells=_landscape_cells(
        ("grixis", 30, 0.45), ("zoo", 20, 0.5), ("lands", 10, 0.4),
        year=2026, total=1363, year_events=32,
    ))
    caption = _landscape_caption(series, _landscape_top(series, 25), 25, in_progress=True)

    assert "2026 so far" in caption
    assert "32 events" in caption
    assert "1,363 decks" in caption
    # The year holds fewer archetypes than the cut, so nothing was cut: the caption
    # says that rather than claiming a top-25 ranking it never applied.
    assert "all 3 archetypes the year held" in caption
    assert "top 25" not in caption


def _timeline_points(*rows):
    """Timeline points from ``(day, mean_a, decks_a[, mean_b, decks_b])`` in one month."""
    return Series(cells=[
        ArchetypeTimelinePoint(
            event=f"E{row[0]}", date=datetime(2025, 3, row[0]),
            mean_norm_a=row[1], decks_a=row[2],
            mean_norm_b=row[3] if len(row) > 3 else None,
            decks_b=row[4] if len(row) > 4 else 0,
        )
        for row in rows
    ])


def test_the_timeline_headline_counts_the_events_each_archetype_led():
    # AC (#151): the individual points are thin, so the countable claim across the
    # whole run is the headline. Storm's mean beats Lands's at two of the three
    # events they can be compared at; the fourth is shared but unscored on one
    # side, so it is not one of them.
    series = _timeline_points(
        (1, 0.2, 3, 0.4, 2), (2, 0.6, 1, 0.3, 4), (3, 0.1, 2, 0.5, 1), (4, 0.4, 1, None, 0),
    )
    caption = _archetype_timeline_caption("Storm", "Lands", series)

    # The headline names the shared-event denominator itself, so the restriction needs
    # no clause of its own behind it (§14, #156).
    assert "Storm" in caption and "2 of 3 shared events" in re.sub(r"<[^>]+>", "", caption)
    # One qualifier, and it is the span: the year selector sits directly above this
    # chart and governs the landscape alone, so a reader must be told this is not that
    # year. How a point is averaged is `faq-archetype-timeline`'s job now.
    assert caption.count("·") == 1
    assert "every year in the data" in caption
    assert "1 to 3" not in caption


def test_a_headline_count_a_coin_could_produce_is_printed_with_its_discount():
    # Each point is the mean of a median of one ranked deck, so under the null each
    # event is a coin flip and the headline count is a sign test nobody ran: over the
    # 121 archetypes the dropdown offers, 102 of the counts are indistinguishable from a
    # fair coin at 90% (#175). "8 of 14" is one of them (a coin produces a split at
    # least that lopsided about 40% of the time), so the count is printed, because it
    # is a fact, and the reading is what gets discounted.
    coin = _archetype_timeline_caption("Abzan", None, _timeline_points(
        *[(day, 0.4 if day <= 8 else 0.6, 1) for day in range(1, 15)]
    ))
    assert "8 of 14" in coin
    assert "a split a coin could produce" in coin

    # 13 of 14 is a lead a coin does not produce, so it is stated without the hedge.
    lead = _archetype_timeline_caption("Abzan", None, _timeline_points(
        *[(day, 0.4 if day <= 13 else 0.6, 1) for day in range(1, 15)]
    ))
    assert "13 of 14" in lead
    assert "coin" not in lead


def test_a_pair_headline_is_gated_against_the_same_coin():
    # The pair's win count is the same sign test on the same thin points, so it carries
    # the same gate. A tie already asserts no leader, so it needs none.
    coin = _archetype_timeline_caption("Storm", "Lands", _timeline_points(
        *[(day, 0.4 if day <= 8 else 0.6, 1, 0.5, 1) for day in range(1, 15)]
    ))
    assert "8 of 14" in coin
    assert "a split a coin could produce" in coin

    tie = _archetype_timeline_caption("Storm", "Lands", _timeline_points(
        (1, 0.4, 1, 0.6, 1), (2, 0.6, 1, 0.4, 1),
    ))
    assert "each finished better at" in tie and "1" in tie
    assert "coin" not in tie


def test_a_solo_timeline_states_its_span_and_nothing_else():
    # The year selector sits directly above this chart and governs the landscape alone
    # (§14, #156: a scope is stated once, and the control says "Landscape only" from the
    # other side). So the one qualifier the caption keeps is the span. What picking a
    # second archetype does, and what a point averages, are the FAQ's now.
    series = _timeline_points((1, 0.2, 3), (2, 0.6, 1), (3, 0.4, 2))
    caption = _archetype_timeline_caption("Storm", None, series)

    assert "Storm beat the middle of the field" in re.sub(r"<[^>]+>", "", caption)
    assert caption.count("·") == 1
    assert "every year in the data" in caption
    assert "second archetype" not in caption
    assert "1 to 3" not in caption


def test_a_solo_timeline_is_one_line_filled_down_to_the_axis():
    # AC (#151): with one archetype the plot shows every event it attended, filled
    # down to the axis. The finish is flipped for the eye as on every other chart, and
    # an event the source scored none of its decks at breaks the line rather than
    # dropping to a fabricated zero.
    fig = _archetype_timeline_figure(
        "Storm", None, _timeline_points((1, 0.2, 3), (2, 0.6, 1), (3, None, 0)),
    )
    (trace,) = fig.data

    assert trace.fill == "tozeroy"
    assert list(trace.y) == [pytest.approx(0.8), pytest.approx(0.4), None]
    # A registration-date x, not the shared styler's category-Year axis: this plot
    # spans the whole corpus and two events in one year must not share an x.
    assert fig.layout.xaxis.type == "date"
    assert list(trace.x) == [datetime(2025, 3, d) for d in (1, 2, 3)]

    # The decks behind each point ride in the hover, not in the marker: this plot draws
    # a point per event, so a size channel over hundreds of overlapping rings buried the
    # lines. Uniform rings, one size for every point (#151's sized markers, reverted),
    # and smaller than the shared ring, which overlaps into a band at this plot's density.
    assert trace.marker.size == 9 < _observation_marker("#000")["size"]
    assert "decks" in trace.hovertemplate
    assert list(trace.customdata[0]) == [numfmt.score(0.8), 3]


def test_a_pair_draws_both_lines_with_the_band_shaded_toward_whoever_leads():
    # AC (#151): with two archetypes both lines are drawn and the band between them is
    # tinted with the colour of whichever is ahead, matching the pilot head-to-head.
    # Storm leads at the first point, Lands at the second, so the band splits.
    series = _timeline_points((1, 0.2, 3, 0.4, 2), (2, 0.6, 1, 0.3, 4))
    fig = _archetype_timeline_figure("Storm", "Lands", series)

    lines = [t for t in fig.data if t.fill != "toself"]
    bands = [t for t in fig.data if t.fill == "toself"]
    assert [t.name for t in lines] == ["Storm", "Lands"]
    # Neither line fills to the axis: the filled region is the gap between them.
    assert {t.fill for t in lines} == {None}
    assert [list(t.y) for t in lines] == [
        [pytest.approx(0.8), pytest.approx(0.4)], [pytest.approx(0.6), pytest.approx(0.7)],
    ]

    # One band trace per leader, each in that archetype's own colour at the shared
    # translucency, the same two hues the head-to-head takes by position.
    assert {t.fillcolor for t in bands} == {
        _rgba(palette.CATEGORICAL[0], 0.18), _rgba(palette.CATEGORICAL[1], 0.18),
    }

    # AC: the hover gives the deck count per side, so a point resting on one deck is
    # readable as such on the side it is thin on.
    assert [list(t.customdata[0]) for t in lines] == [
        [numfmt.score(0.8), 3], [numfmt.score(0.6), 2],
    ]
    # ... and only in the hover: both lines draw uniform rings, so the eye follows the
    # lines and the band rather than a field of differently sized markers.
    assert [t.marker.size for t in lines] == [9, 9]


def _tab_blocks(demo, label):
    """The blocks created inside one tab: from that tab up to the next one."""
    import gradio as gr

    blocks = list(demo.blocks.values())
    start = next(i for i, b in enumerate(blocks)
                 if isinstance(b, gr.Tab) and b.label == label)
    ends = [i for i, b in enumerate(blocks) if isinstance(b, gr.Tab) and i > start]
    return blocks[start:ends[0]] if ends else blocks[start:]


def _cover_fields(decks: list[dict]) -> list[dict]:
    """Fill every event's declared field with finishes, so none of them reads as a cut.

    These demos declare a 31- and a 19-player event holding a handful of decks each,
    which is the shape of an event that published its top cut and nothing else, and the
    archetype surfaces read no finish from one (:data:`trends.MIN_FIELD_COVERAGE`, ADR
    0022). Rather than weaken the rule to admit a fixture, the fixture is made a
    possible tournament, as ``test_trends``'s own ``_cover_fields`` does for the trend
    fixtures.

    Each filler names its own pilot, finishes at the deep end of the field it covers,
    and carries a ``primaryTag`` that is no engine of its own, the real shape of a deck
    the source left unclassified. Every archetype surface reads the primary tag alone,
    so a filler joins no archetype and the only number it moves is a share's
    denominator.

    The event is part of the **title** and not only of the pilot key, which is what
    keeps a filler one-off: a name is recovered from the title and identical names are
    one pilot (ADR 0007), so a bare "Filler 0" at two events reached the graph as one
    pilot with a two-event career, which is exactly the shape :data:`MIN_PILOT_YEAR_EVENTS`
    reads. Padding must never enter a pilot surface.
    """
    padded = list(decks)
    held: dict[str, list[dict]] = {}
    for deck in decks:
        held.setdefault(deck["event"], []).append(deck)
    for event, entries in held.items():
        template = entries[0]
        size = template["eventSize"]
        ranked = sum(1 for d in entries if d["placementNorm"] is not None)
        # Comfortably clear of the coverage line rather than exactly on it, so a demo
        # never turns on which side of `>=` the threshold is read at.
        for i in range(math.ceil(size * 0.6) - ranked):
            placement = size - i
            padded.append({**template,
                           "deckId": f"filler-{event}-{i}",
                           "name": f"{placement}th filler-{event}-{i} - Deck - {event}",
                           "deckName": "Deck",
                           "pilot": f"filler-{event}-{i}",
                           "placement": placement,
                           "placementNorm": (placement - 1) / (size - 1),
                           "primaryTag": "engine:__none__"})
    return padded


def _write_covered(snap, decks: list[dict]) -> None:
    """Cover every event's field (:func:`_cover_fields`) and write the snapshot back.

    The card index is filled for the decks it does not already hold, so a filler reaches
    the graph with an empty list rather than overwriting what the shared fixture says
    about the decks it does hold.
    """
    decks = _cover_fields(decks)
    (snap / "decks.json").write_text(json.dumps(decks))
    index = json.loads((snap / "cards_index.json").read_text())
    index["decks"].update({d["deckId"]: {"m": [], "s": []} for d in decks
                           if d["deckId"] not in index["decks"]})
    (snap / "cards_index.json").write_text(json.dumps(index))


def _landscape_demo(tmp_path, snapshot_dir):
    """A built app over a snapshot fat enough for a landscape to draw.

    The shared fixture holds two archetypes, one short of
    :data:`MIN_LANDSCAPE_ARCHETYPES`, so it is copied and three more decks are added
    to its 31-player event (distinct pilots, placements it cannot contradict, norms
    ranked against that field), and both its events are given the field they declare
    (:func:`_cover_fields`). The fixture itself is left alone: a hundred other tests
    count its decks.
    """
    import json
    import shutil

    from graph7ph.app import build_app
    from graph7ph.build import build_graph
    from graph7ph.models import load_snapshot

    snap = tmp_path / "snap"
    shutil.copytree(snapshot_dir, snap)
    decks = json.loads((snap / "decks.json").read_text())
    template = decks[0]
    for i, (archetype, placement) in enumerate(
        [("lands", 5), ("oracle", 10), ("jund", 15)]
    ):
        tag = f"engine:{archetype}"
        decks.append({
            **template,
            "deckId": f"extra-{i}",
            "name": f"{placement}th Extra {i} - {archetype.title()} - PogNov25",
            "deckName": archetype.title(),
            "pilot": f"ExtraPilot{i}",
            "placement": placement,
            "placementNorm": (placement - 1) / (template["eventSize"] - 1),
            "engineTags": [tag],
            "engineTagLabels": {tag: archetype.title()},
            "primaryTag": tag,
            "primaryTagWeights": {tag: 100},
        })
    _write_covered(snap, decks)

    artifact = tmp_path / "graph"
    build_graph(load_snapshot(snap), artifact)
    return build_app(artifact)


def test_the_archetypes_tab_follows_meta_as_one_view_drawn_on_open(tmp_path, snapshot_dir):
    # AC (#145): Archetypes sits directly after Meta and owns "who wins" while Meta
    # keeps "who is played". It is a single view, so no view picker, and it draws
    # Plotly aggregates rather than a pyvis graph, so it follows the Meta precedent of
    # no Draw button: the scatter is already drawn when the tab is opened.
    import gradio as gr

    demo = _landscape_demo(tmp_path, snapshot_dir)
    tabs = [b.label for b in demo.blocks.values() if isinstance(b, gr.Tab)]
    assert tabs == ["Meta", "Archetypes", "Cards", "Hidden gems", "Pilots",
                    "Best player race", "FAQ"]

    inside = _tab_blocks(demo, "Archetypes")
    # Only the clear buttons on the archetype pickers, never a Draw.
    assert not [b for b in inside if isinstance(b, gr.Button) and b.value != CLEAR_LABEL]
    assert not [b for b in inside if isinstance(b, gr.Dropdown) and b.label == "View"]
    # Drawn on open: the scatter carries a figure at build time, as the Meta cut does.
    drawn = [b for b in inside if isinstance(b, gr.Plot) and b.value is not None]
    assert len(drawn) == 1


def test_the_year_selector_offers_every_year_and_opens_on_the_latest(tmp_path, snapshot_dir):
    # AC (#145): the year is a dropdown over every year in the data, defaulting to the
    # latest, read from the graph rather than pinned. The fixture holds 2025 alone, so
    # the assertion is against the years the graph actually has.
    import gradio as gr

    demo = _landscape_demo(tmp_path, snapshot_dir)
    (year,) = [b for b in _tab_blocks(demo, "Archetypes")
               if isinstance(b, gr.Dropdown) and b.label == "Year"]

    offered = [value for _, value in year.choices]
    assert offered == sorted(offered, reverse=True)  # newest first, the reader's default
    assert set(offered) == {2025}
    assert year.value == max(offered)


def test_a_year_too_thin_for_a_landscape_refuses_on_the_surface(tmp_path, snapshot_dir):
    # AC (#145): a year too thin refuses with a readable state rather than a
    # misleading plot. The shared fixture holds two archetypes in its only year, one
    # short of a field, so the tab opens on the refusal: a note, no figure.
    import gradio as gr

    demo = _built_demo(tmp_path, snapshot_dir)
    inside = _tab_blocks(demo, "Archetypes")

    assert not [b for b in inside if isinstance(b, gr.Plot) and b.value is not None]
    (note,) = [b for b in inside
               if isinstance(b, gr.Markdown) and b.visible and b.value
               and "t-lede" not in (b.elem_classes or [])
               and not b.value.lstrip().startswith("## ")]
    assert "2025" in note.value


def test_changing_the_year_redraws_the_scatter(tmp_path, snapshot_dir):
    # AC (#145): with no Draw button, the year selector is what re-draws the chart, so
    # the wiring is the feature. Asserted on the built app's own event table: some
    # event takes the year dropdown as its input and writes the landscape's plot.
    import gradio as gr

    demo = _landscape_demo(tmp_path, snapshot_dir)
    inside = _tab_blocks(demo, "Archetypes")
    (year,) = [b for b in inside if isinstance(b, gr.Dropdown) and b.label == "Year"]
    # The scatter is the first of the tab's two plots; the timeline below it (#151)
    # has its own selectors and is deliberately not wired to the year.
    plot, _timeline = [b for b in inside if isinstance(b, gr.Plot)]

    redraws = [fn for fn in demo.fns.values()
               if year in fn.inputs and plot in fn.outputs]
    assert len(redraws) == 1

    # AC (#85, criterion 9): "every query-running action shows progress while it runs".
    # Changing the year runs a query and this view has no Draw button, so a step ahead
    # of the redraw puts the card into the shared running state: the stale figure taken
    # down (a plot from the old year beside the new year in the control reads as the
    # answer to the new one) and the note reading the same word the Draw buttons show.
    writers = [fn.fn for fn in demo.fns.values() if plot in fn.outputs]
    busy = next(f for f in writers if f.__name__ == "landscape_drawing")
    _heading, busy_plot, note = busy()
    assert not busy_plot["visible"]
    assert note["value"] == DRAWING_LABEL and note["visible"]


def test_each_archetype_picker_has_a_clear_button_that_names_itself(tmp_path, snapshot_dir):
    # A picked archetype has to be un-pickable, or a comparison is a one-way door: each
    # data filter carries a button wired to write the empty value back to its dropdown.
    # The button is labelled with a word, not the bare "×" it wears: a Gradio button's
    # text is its whole accessible name, so a glyph would reach a screen reader as
    # "multiplication sign" and name neither the action nor the field. The stylesheet is
    # what turns the word into the glyph, so the two must agree.
    import gradio as gr

    demo = _timeline_demo(tmp_path, snapshot_dir)
    inside = _tab_blocks(demo, "Archetypes")
    picks = [b for b in inside if isinstance(b, gr.Dropdown) and b.label != "Year"]
    buttons = [b for b in inside if isinstance(b, gr.Button)]

    assert [b.value for b in buttons] == [CLEAR_LABEL] * len(picks)
    for pick in picks:
        clears = [fn for fn in demo.fns.values()
                  if list(fn.outputs) == [pick]
                  and any((b._id, "click") in fn.targets for b in buttons)]
        assert len(clears) == 1
    assert ".clear-btn::before" in theme.build_css()


def _timeline_demo(tmp_path, snapshot_dir):
    """A built app whose graph holds archetypes the timeline can and cannot compare.

    The shared fixture's two events (PogNov25 and CFWAT25) already give ``grixis`` two
    ranked events and ``storm`` one, so Grixis is the only archetype in it that can
    draw a line. Two more are added without touching the fixture: ``jund`` at both of
    those events, so Grixis and Jund share a run to compare, and ``lands`` at two events
    of its own, so Grixis and Lands are a drawable pair that never met. ``storm`` is
    left as it is, the archetype too thin for the catalogue to offer at all.

    Every event is then given the field it declares (:func:`_cover_fields`), since the
    timeline draws no point at an event that published only its cut (ADR 0022).
    """
    import json
    import shutil

    from graph7ph.app import build_app
    from graph7ph.build import build_graph
    from graph7ph.models import load_snapshot

    snap = tmp_path / "snap"
    shutil.copytree(snapshot_dir, snap)
    decks = json.loads((snap / "decks.json").read_text())
    by_event = {d["event"]: d for d in decks}
    extras = [
        # (deck id, archetype, event template, event, field size, placement, day)
        ("x-j1", "jund", by_event["PogNov25"], "PogNov25", 31, 8, None),
        ("x-j2", "jund", by_event["CFWAT25"], "CFWAT25", 19, 9, None),
        ("x-l1", "lands", by_event["PogNov25"], "LandsCupA", 10, 3, 2),
        ("x-l2", "lands", by_event["PogNov25"], "LandsCupB", 10, 4, 3),
    ]
    for deck_id, archetype, template, event, size, placement, day in extras:
        tag = f"engine:{archetype}"
        decks.append({
            **template,
            "deckId": deck_id,
            "name": f"{placement}th {deck_id} - {archetype.title()} - {event}",
            "deckName": archetype.title(),
            "pilot": f"Pilot{deck_id}",
            "event": event,
            "eventId": f"evt_{event}",
            "eventSize": size,
            "placement": placement,
            "placementNorm": (placement - 1) / (size - 1),
            **({"createdAt": f"2025-12-0{day}T00:00:00+00:00"} if day else {}),
            "engineTags": [tag],
            "engineTagLabels": {tag: archetype.title()},
            "primaryTag": tag,
            "primaryTagWeights": {tag: 100},
        })
    _write_covered(snap, decks)

    artifact = tmp_path / "graph"
    build_graph(load_snapshot(snap), artifact)
    return build_app(artifact)


def test_the_timeline_sits_under_the_scatter_and_draws_off_its_own_two_selectors(
    tmp_path, snapshot_dir
):
    # AC (#151): the timeline is the tab's second plot, it draws as soon as one
    # archetype is picked (so no Draw button), and it spans the whole timeline: the
    # year selector governs the scatter alone and is not an input to it.
    import gradio as gr

    demo = _timeline_demo(tmp_path, snapshot_dir)
    inside = _tab_blocks(demo, "Archetypes")
    dropdowns = [b for b in inside if isinstance(b, gr.Dropdown)]
    (year,) = [b for b in dropdowns if b.label == "Year"]
    picks = [b for b in dropdowns if b.label != "Year"]
    _, timeline_plot = [b for b in inside if isinstance(b, gr.Plot)]

    # Only the clear buttons on the archetype pickers, never a Draw.
    assert not [b for b in inside if isinstance(b, gr.Button) and b.value != CLEAR_LABEL]
    assert len(picks) == 2
    # AC: the second archetype is optional, so nothing is preselected in either slot
    # and the plot waits, hidden, rather than opening on an arbitrary archetype.
    assert [p.value for p in picks] == [None, None]
    assert timeline_plot.value is None

    draws = [fn for fn in demo.fns.values() if timeline_plot in fn.outputs]
    assert {tuple(fn.inputs) for fn in draws} == {tuple(picks)}
    assert not any(year in fn.inputs for fn in draws)


def test_both_selectors_offer_the_archetypes_that_draw_with_their_event_count(
    tmp_path, snapshot_dir
):
    # AC (#151): both selectors offer every archetype with at least two ranked events,
    # each label carrying its count so a reader sees how thin one is before picking it
    # rather than hitting a refusal after. The fixture's Grixis is ranked at both of
    # its events; every other archetype in it turned up once, so it is the only one
    # that can draw a line.
    import gradio as gr

    demo = _timeline_demo(tmp_path, snapshot_dir)
    picks = [b for b in _tab_blocks(demo, "Archetypes")
             if isinstance(b, gr.Dropdown) and b.label != "Year"]

    offered = [("Grixis (2 events)", "grixis"), ("Jund (2 events)", "jund"),
               ("Lands (2 events)", "lands")]
    assert [p.choices for p in picks] == [offered] * 2


def test_picking_one_archetype_draws_the_timeline_and_a_thin_pair_refuses(
    tmp_path, snapshot_dir
):
    # The callback itself, since with no Draw button it is the whole interaction: one
    # archetype draws a figure inside a shown card, and a pair too thin to compare
    # comes back as a readable note naming the count instead of a misleading line.
    import gradio as gr

    demo = _timeline_demo(tmp_path, snapshot_dir)
    inside = _tab_blocks(demo, "Archetypes")
    _, timeline_plot = [b for b in inside if isinstance(b, gr.Plot)]
    # Each selector wires two steps onto the same outputs: the running state (AC 9),
    # then the query. This test is about the query, so it is named rather than taken
    # positionally out of the chain.
    writers = [fn.fn for fn in demo.fns.values() if timeline_plot in fn.outputs]
    draw = next(f for f in writers if f.__name__ == "draw_archetype_timeline")
    busy = next(f for f in writers if f.__name__ == "timeline_drawing")

    # AC (#85, criterion 9): "every query-running action shows progress while it runs".
    # This view has no Draw button to carry it, so the running state speaks in the card
    # itself: shown, with the plot down and the note reading the shared running word.
    card, heading, plot, note = busy("grixis", None)
    assert card["visible"] and not plot["visible"]
    assert note["value"] == DRAWING_LABEL and note["visible"]
    # Nothing picked stays down rather than flashing a card the query is about to hide.
    assert not busy(None, None)[0]["visible"]

    card, heading, plot, note = draw("grixis", None)
    assert plot["visible"] and plot["value"] is not None
    assert not note["visible"]
    assert "Grixis" in heading["value"]

    # Nothing picked draws nothing: the card stays away rather than sitting empty.
    assert not draw(None, None)[0]["visible"]

    # Lands drew its two events on its own, so the pair has nothing to compare:
    # refused inside the card with a line naming that, not drawn as one point.
    card, heading, plot, note = draw("grixis", "lands")
    assert card["visible"] and not plot["visible"]
    assert "Lands" in note["value"] and "never" in note["value"]

    # A pair that did meet draws both lines rather than refusing.
    assert draw("grixis", "jund")[2]["visible"]


def test_the_landscape_never_ranges_past_the_ends_of_the_score():
    # The finish is bounded at 0 and 1, so headroom above a near-perfect year would be
    # axis that no dot could ever occupy. 2023's best archetype finishes at 0.93, and
    # the padding used to carry the axis to 1.09.
    high = _landscape_figure(_landscape_cells(
        ("storm", 30, 0.07), ("oracle", 20, 0.25), ("grixis", 10, 0.45),
    )).layout.yaxis.range[1]
    low = _landscape_figure(_landscape_cells(
        ("storm", 30, 0.95), ("oracle", 20, 0.9), ("grixis", 10, 0.55),
    )).layout.yaxis.range[0]

    assert 0.93 <= high <= 1.0
    assert 0.0 <= low <= 0.05


def test_a_graph_with_no_archetype_still_builds_the_archetypes_tab(tmp_path, snapshot_dir):
    # The Archetypes tab reads its year selector off the meta-share matrix, which is
    # empty for a graph holding no Archetype node at all (a snapshot whose decks carry
    # no engine tag builds into exactly that). The tab has no year to open on, so it
    # opens on a refusal rather than taking the whole app down at construction, the way
    # `latest_deck_year` already returns None for the Meta cut on the same series.
    import gradio as gr
    import json
    import shutil

    from graph7ph.app import build_app
    from graph7ph.build import build_graph
    from graph7ph.models import load_snapshot

    snap = tmp_path / "snap"
    shutil.copytree(snapshot_dir, snap)
    decks = [{**d, "engineTags": [], "engineTagLabels": {}, "primaryTag": "",
              "primaryTagWeights": {}}
             for d in json.loads((snap / "decks.json").read_text())]
    (snap / "decks.json").write_text(json.dumps(decks))
    artifact = tmp_path / "graph"
    build_graph(load_snapshot(snap), artifact)

    inside = _tab_blocks(build_app(artifact), "Archetypes")
    assert not [b for b in inside if isinstance(b, gr.Plot) and b.value is not None]
    assert [b for b in inside if isinstance(b, gr.Markdown) and b.visible and b.value
            and "landscape" in b.value]


# The five sample dates of a race, the x axis the chart draws (#135, ADR 0017).
_RACE_ENDS = [datetime(2024, 7, 1), datetime(2025, 1, 1), datetime(2025, 7, 1),
              datetime(2026, 1, 1), datetime(2026, 7, 1)]


def _race_cells(*records, major_events=21, spread=2):
    """Race cells from ``(pilot, score, majors, [(as_of_score, as_of_majors)...])``.

    An as-of score of ``None`` is a date under the floor. Ranks are dealt inside each
    date by score, best first, the way the tool deals them, so a hover test reads a rank
    that means what it says. Records are given best first, the standing order the tool
    returns. ``spread`` is how far the rank interval reaches either side of the rank, so
    a display test has bounds to render without a bootstrap behind it.
    """
    scored = [
        {r[0]: r[3][at][0] for r in records if r[3][at][0] is not None}
        for at in range(len(_RACE_ENDS))
    ]
    ranks = [
        {pilot: place for place, (pilot, _) in enumerate(
            sorted(step.items(), key=lambda kv: -kv[1]), start=1)}
        for step in scored
    ]
    places = {
        pilot: place
        for place, (pilot, *_) in enumerate(records, start=1)
    }
    return [
        RaceCell(
            pilot=pilot, rank=places[pilot], score=score, majors=majors,
            major_events=major_events,
            rank_low=max(1, places[pilot] - spread),
            rank_high=min(len(records), places[pilot] + spread),
            as_of=at_date,
            as_of_score=point[0], as_of_majors=point[1],
            as_of_rank=ranks[at].get(pilot) if point[0] is not None else None,
            as_of_contenders=len(ranks[at]),
        )
        for pilot, score, majors, trajectory in records
        for at, (at_date, point) in enumerate(zip(_RACE_ENDS, trajectory))
    ]


def _race_series(*records, major_events=21, spread=2):
    return Series(cells=_race_cells(*records, major_events=major_events, spread=spread))


def _flat(score, majors=6):
    """One contender's five sample dates, all scored, all the same."""
    return [(score, majors)] * len(_RACE_ENDS)


def test_the_race_draws_the_leading_few_and_the_leaderboard_lists_the_rest():
    # Two display cuts over one table (#135): the chart draws _RACE_LINES lines because
    # that is where the palette's named hues stop, and the leaderboard carries the
    # standings past them, so the eighth-to-ninth gap is visible on the surface rather
    # than hidden by the cut. Neither cut reaches the tool, which returns the whole field.
    series = _race_series(*[
        (f"p{i}", 0.9 - i / 100, 10, _flat(0.7)) for i in range(12)
    ])

    trajectories = _race_trajectories(series)
    assert [line[0].pilot for line in trajectories[:3]] == ["p0", "p1", "p2"]
    # Every contender is grouped, best first; the cut is the caller's slice.
    assert len(trajectories) == 12
    # Each line is one contender's whole trajectory, oldest sample first.
    assert [c.as_of for c in trajectories[0]] == _RACE_ENDS


def _race_names(*pilots):
    return {p: p.title() for p in pilots}


def test_the_race_draws_a_line_per_pilot_in_its_own_hue_broken_where_a_window_is_thin():
    # The chart is the trend-chart grammar the rest of the app uses: a thin dashed join
    # that only connects observations, hollow markers whose size is the majors behind
    # each point, and a direct hue per pilot from the shared eight (§5). A window under
    # the floor is a gap, not a bridged point: the tool withheld the value, so the line
    # breaks over it exactly as a refused year breaks the performance chart.
    series = _race_series(
        ("ace", 0.78, 12, [(None, 1), (0.70, 4), (0.72, 6), (0.75, 8), (0.76, 9)]),
        ("rival", 0.74, 9, [(0.66, 2), (0.68, 3), (0.71, 5), (0.70, 6), (0.72, 7)]),
    )
    lines = _race_trajectories(series)
    fig = _race_figure(lines, [], _race_names("ace", "rival"))
    drawn = {t.name: t for t in fig.data if t.name in ("Ace", "Rival")}

    assert len(drawn) == 2
    assert drawn["Ace"].line.color == palette.CATEGORICAL[0]
    assert drawn["Rival"].line.color == palette.CATEGORICAL[1]
    # A gap, not a bridge: the thin first window carries no y at all.
    assert list(drawn["Ace"].y) == [None, 0.70, 0.72, 0.75, 0.76]
    assert drawn["Ace"].line.dash == "dash"
    assert drawn["Ace"].marker.color == theme.TOKENS["surface"]  # hollow ring
    # The ring's area carries the majors the point rests on, the same scale every other
    # chart's sample-size ring uses.
    assert list(drawn["Rival"].marker.size) == [_confidence_size(n) for n in (2, 3, 5, 6, 7)]
    # Five sample points, labelled by the month each window ends in.
    assert list(drawn["Ace"].x) == ["Jul 2024", "Jan 2025", "Jul 2025", "Jan 2026", "Jul 2026"]


def test_the_race_locks_its_legend_and_puts_the_windows_in_time_order():
    # The race is a read, not an exploration surface (#135): there are no controls, and
    # the legend is a key rather than a control, so both its click handlers are pinned
    # off. A reader must not be able to tick a pilot out of a race. The x axis is five
    # discrete windows in time order, which the shared styler's alphabetical category
    # order would scramble ("Jan 2025" before "Jul 2024"), and the y axis is a score,
    # not the shared styler's share percentage, fitted to the field rather than zeroed.
    series = _race_series(
        ("ace", 0.78, 12, _flat(0.76, 8)), ("rival", 0.74, 9, _flat(0.71, 6)),
        ("other", 0.60, 7, _flat(0.58, 4)),
    )
    trajectories = _race_trajectories(series)
    fig = _race_figure(trajectories[:2], trajectories[2:],
                       _race_names("ace", "rival", "other"))

    assert fig.layout.legend.itemclick is False
    assert fig.layout.legend.itemdoubleclick is False
    assert fig.layout.legend.orientation == "h"
    assert fig.layout.legend.y < 0
    assert fig.layout.height == _LEGEND_BELOW_HEIGHT
    assert list(fig.layout.xaxis.categoryarray) == [
        "Jul 2024", "Jan 2025", "Jul 2025", "Jan 2026", "Jul 2026",
    ]
    assert fig.layout.yaxis.tickformat == numfmt.SCORE_TICKFORMAT
    assert fig.layout.yaxis.rangemode != "tozero"
    # The context layer is named in the legend and sorted below the pilots, so the
    # ground the lines are read against is labelled without displacing the standings.
    ranks = {t.name: t.legendrank for t in fig.data if t.showlegend is not False}
    assert ranks["Ace"] < ranks["Rival"] < ranks["All other contenders"]


def test_a_race_point_hovers_the_record_so_far_its_standing_and_its_sample():
    # Every point on this chart is a shrunk mean over the pilot's record up to that date,
    # so the hover has to carry what it rests on: which date, and that it is everything
    # *by* then rather than a span ending there; the score in the one numeric convention;
    # where that stood among the contenders; and how many majors were behind it. The rank
    # is the tool's own, taken over the whole field rather than the drawn eight.
    series = _race_series(
        ("ace", 0.78, 12, [(0.70, 4), (0.72, 6), (0.75, 8), (0.76, 9), (0.74, 7)]),
        ("rival", 0.74, 9, [(0.80, 3), (0.68, 3), (0.71, 5), (0.70, 6), (0.72, 7)]),
        ("other", 0.60, 7, _flat(0.58, 4)),
    )
    trajectories = _race_trajectories(series)
    fig = _race_figure(trajectories[:2], trajectories[2:],
                       _race_names("ace", "rival", "other"))
    ace = next(t for t in fig.data if t.name == "Ace")

    # The oldest date: the rival's 0.80 leads it, so the ace is second there. "by", not a
    # span, because the point counts the whole record up to it (ADR 0017).
    # The score is written to the standings' precision, not the charts', since the right
    # edge of this chart is the leaderboard itself.
    assert list(ace.customdata[0]) == [
        "by Jul 2024", numfmt.score(0.70, LEADERBOARD_SCORE_PLACES), "2 of 3", 4]
    # And the newest, where the ace leads. The rank carries the contenders it was taken
    # over, since an early date has far fewer of them with a record than the caption's
    # whole-field count.
    assert list(ace.customdata[-1])[1:] == [
        numfmt.score(0.74, LEADERBOARD_SCORE_PLACES), "1 of 3", 7]
    assert "Ace" in ace.hovertemplate
    assert "major events so far" in ace.hovertemplate
    # The context layer is chart ground, so it answers no hover of its own and never
    # steals one from a drawn pilot.
    context = next(t for t in fig.data if t.name == "All other contenders")
    assert context.hoverinfo == "skip"


def test_the_race_hover_reads_a_score_to_the_precision_that_separates_contenders():
    # The running score makes the right edge of the chart the leaderboard exactly
    # (ADR 0017), so the hover and the table state the same quantity there and must not
    # state it two ways. The table already writes three decimals because the top of the
    # board is separated by thousandths; at two the drawn eight collide in pairs and
    # triples on the real record, so hovering two lines to compare them shows one number.
    series = _race_series(
        ("ace", 0.762, 12, _flat(0.762, 9)),
        ("rival", 0.761, 9, _flat(0.761, 8)),
    )
    trajectories = _race_trajectories(series)
    fig = _race_figure(trajectories, [], _race_names("ace", "rival"))
    hovered = {t.name: t.customdata[-1][1] for t in fig.data}

    # A thousandth apart is a real gap in this field, and the hover has to show it.
    assert hovered["Ace"] != hovered["Rival"]
    # And each reads the digits its own leaderboard row prints, so a reader moving
    # between the chart and the table is comparing one number, not two roundings.
    table = _leaderboard_html(series, _race_names("ace", "rival"), {}, rows=50)
    for cell_score, name in ((0.762, "Ace"), (0.761, "Rival")):
        assert f"{cell_score:.3f}" in table
        assert f"{cell_score:.3f}" in hovered[name]


def test_the_race_caption_states_the_cut_and_what_a_score_is_measured_on():
    # A headline and one qualifier (§14, #156): that it draws a few lines out of a much
    # larger field (so the eight are a cut, not the whole story), and what a score is
    # measured on, which is the chart's strongest assumption and invisible in the
    # picture. Stated in the same field-standing form the landscape and performance
    # captions use, the reading in the accent and the sample quiet behind it.
    #
    # The two clauses this used to trail are gone because the picture already carries
    # them: the faint layer is a named legend entry, and what a point counts is the
    # x-axis title. Both readings still live in `faq-race`.
    series = _race_series(*[
        (f"p{i}", 0.9 - i / 100, 10, _flat(0.7)) for i in range(139)
    ], major_events=21)
    caption = _race_caption(series, drawn=8)

    assert "8 of 139" in caption
    assert "21" in caption                           # the majors a score rests on
    assert str(MAJOR_FIELD_SIZE) in caption          # what makes an event a major one
    assert caption.count("·") == 1
    assert "traced faintly" not in caption
    assert "improve" not in caption


def test_the_leaderboard_lists_the_standings_and_marks_the_plotted_eight():
    # The table beside the chart carries the standings past the eight lines, so a reader
    # can see how close rank 9 was to rank 8 rather than taking the cut on trust. Each
    # drawn pilot is marked in the hue their line is drawn in, which is what ties the
    # two surfaces together; the rest carry no hue, since the palette names eight.
    series = _race_series(*[
        (f"p{i}", 0.9 - i / 100, 20 - i, _flat(0.7)) for i in range(12)
    ])
    lines = _race_trajectories(series)[:3]
    table = _leaderboard_html(series, {f"p{i}": f"Pilot {i}" for i in range(12)},
                              _race_hues(lines), rows=5)

    assert table.count("<tr") == 6  # a header row and five standings
    assert "Pilot 0" in table and "Pilot 4" in table
    assert "Pilot 5" not in table  # capped, not the whole field
    # Rank, score and the major events the score rests on, all on the leading row.
    assert "0.900" in table and ">20<" in table
    # The three drawn pilots carry their line's hue; the fourth is listed unmarked.
    for slot, pilot in enumerate(("Pilot 0", "Pilot 1", "Pilot 2")):
        assert palette.CATEGORICAL[slot] in table.split(pilot)[0]
    assert table.split("Pilot 3")[0].count("swatch-hue") == 3


def test_the_race_faq_says_the_majors_cut_is_unique_to_this_plot():
    # The race is the only surface in the app that leaves events out for being small;
    # every other one counts every scored event (maintainer's call, #135). That has to be
    # said where the scoring is explained, because the two populations genuinely
    # disagree: the same estimator over all events moves the median contender 10 places
    # of 139, and 26 of them by more than 20. A reader who takes this ranking as "the
    # app's view of a pilot" will otherwise find the pilot tab contradicting it and have
    # nothing to tell them why. Asserted on the claim, not the phrasing of the rest of
    # the answer, which the FAQ tab's own test deliberately leaves free to be re-edited.
    (answer,) = [a for eid, _, _, a in _FAQ_ENTRIES if eid == "faq-race"]

    assert "only chart here that drops events for being small" in answer
    assert "different questions" in answer


def test_the_bracket_only_rule_is_defined_once_and_pointed_at():
    # One quantity, one description (§14, #156). Four charts drop the events that
    # published a top-eight bracket instead of standings, and each used to explain the
    # rule in its own words: the landscape called them "events that published only their
    # top eight", the race spent a paragraph on them, and the pilot performance caption
    # said the opposite outright ("every event with a recorded finish counts here"),
    # which stopped being true when #191 dropped them from that mean too. The rule is
    # defined once, in `faq-finish`, which names every surface that applies it; the rest
    # point at it rather than re-deriving it.
    answers = {eid: a for eid, _, _, a in _FAQ_ENTRIES}

    finish = answers["faq-finish"]
    assert "top eight" in finish
    for surface in ("landscape", "archetype timeline", "performance chart",
                    "best player race"):
        assert surface in finish, surface
    # And the two that keep those events are named as keeping them, since "every chart"
    # would be wrong: the head-to-head plots single placements and the gems compare
    # within one event.
    assert "head-to-head" in finish and "hidden gems" in finish

    for eid in ("faq-landscape", "faq-archetype-timeline", "faq-performance",
                "faq-race"):
        assert "see the finish question above" in answers[eid], eid


def test_the_faq_says_what_each_surfaces_interval_covers_and_what_it_leaves_unsettled():
    # The three surfaces that draw a mean placementNorm all present it more honestly
    # than their captions have room to explain (#175), so the FAQ carries the method:
    # what the bar is, why the year-to-year movement on a career is not a story, what an
    # unsettled dot on the landscape means, and that a timeline headline is only a lead
    # when it clears chance. Asserted on the claims, not on the phrasing around them.
    answers = {eid: a for eid, _, _, a in _FAQ_ENTRIES}

    performance = answers["faq-performance"]
    assert "90%" in performance
    # The permutation test is why the movement is not a reading the chart offers.
    assert "shuffling" in performance.lower()
    assert "slump" in performance

    # The landscape's "how settled is it" half is its own sibling entry (#142), the shape
    # `faq-race-certainty` and `faq-gems-certainty` already had, so `faq-landscape` is
    # left to say how a dot is placed and the reading of the bars lives next door.
    landscape = answers["faq-landscape-certainty"]
    assert "90%" in landscape
    assert "cross the 0.5 line" in landscape

    timeline = answers["faq-archetype-timeline"]
    assert "coin" in timeline


def test_the_faq_states_the_gem_rule_the_luck_in_it_and_why_nothing_is_filtered():
    # AC (#184). Three entries, because the tab now raises three questions and the old
    # pair answers none of them: what the rule is now that the performance bar is gone,
    # how much of the list is coincidence (the honesty requirement the design rests on,
    # since it has no validation route), and why a tab that used to demand an archetype
    # now offers nothing to pick.
    entries = {eid: (cat, q, a) for eid, cat, q, a in _FAQ_ENTRIES}

    _, _, rule = entries["faq-gems"]
    # Measured inside one archetype, against that archetype's own best decks; the
    # absolute top-third-of-the-format bar it replaces must not survive in the copy.
    assert "archetype" in rule
    assert "top third of the format" not in rule
    # The cut is named off the constant, never spelled out in prose: the sweep moved it
    # from a third to a fifth, and copy that restates a swept number drifts silently
    # the next time it moves.
    assert f"best {GEM_TOP_CUT:.0%}" in rule
    assert "third" not in rule
    # The picture draws every top-cut deck, so the copy promises the reader they can
    # count the deck nodes against the column rather than warning that they cannot.
    assert "every one of the best decks" in rule
    # One quantity, one name (§14, #156): the cut is "best decks" throughout the prose,
    # never "top decks" in one sentence and "best decks" in the next.
    assert "top decks" not in rule

    category, question, certainty = entries["faq-gems-certainty"]
    assert category == "Cards"
    assert "settled" in question.lower()
    # The count, and that nothing says which cards it applies to. This is its only
    # home now that the caption states the list and stops, so it states the bar itself
    # rather than pointing at a line that no longer carries it.
    assert "chance" in certainty and "which" in certainty
    assert "caption" not in certainty
    assert f"{MAX_GEM_LUCK:.0%}" in certainty

    _, question, unfiltered = entries["faq-gems-unfiltered"]
    assert "filter" in (question + unfiltered).lower()


def test_the_two_answers_the_review_pass_found_wrong_state_what_was_measured():
    # #142 found two answers that were not thin but wrong, so both are pinned on the
    # claim rather than the phrasing around it.
    #
    # The landscape blamed its above-the-line skew on the source ("a quirk of the source,
    # which records top finishers more completely than the rest of the field"). Measured
    # on the artifact: the drawn top 25 average 0.4405 / 0.4756 / 0.4764 / 0.4762 raw for
    # 2023-2026 against 0.5408 / 0.5652 / 0.5812 / 0.5613 for the archetypes the cut
    # leaves out, and the whole field sits 0.007 off the middle once `_cut_only_events` is
    # dropped. So the skew is a property of the display cut, and the old wording told a
    # reader to discount a real signal as a data artefact.
    #
    # And the gem odds described the per-pilot count ADR 0020 measured and rejected,
    # which overstated the app's own rigour: the code ships a proportional discount
    # (`query.PILOT_ICC`), charging seven decks by three pilots as about five and a half
    # independent results rather than three.
    answers = {eid: a for eid, _, _, a in _FAQ_ENTRIES}
    corpus = " ".join(answers.values())

    assert "quirk of the source" not in corpus
    certainty = answers["faq-landscape-certainty"]
    assert "most-played" in certainty and "the cut leaves out" in certainty

    gems = answers["faq-gems-certainty"]
    assert "three pieces of evidence" not in gems
    assert "five and a half" in gems


def test_the_quantities_with_no_description_anywhere_got_one():
    # The coverage half of #142. Four quantities were on a surface and in no answer: the
    # year every axis reads, the rates on the two card graph views and on the archetype
    # affinity map, and that the race score is nearly blind to winning, which ADR 0017
    # assigned to the FAQ outright ("the metric stands and the FAQ carries the caveat").
    # Pilot identity and the gem board question were the same shape: on the surface,
    # nowhere in the corpus.
    answers = {eid: a for eid, _, _, a in _FAQ_ENTRIES}
    assert {"faq-year", "faq-usage", "faq-cooccurrence", "faq-affinity",
            "faq-race-winning", "faq-pilot-identity", "faq-gems-board"} <= set(answers)

    # The year is a shared primitive like the finish, so the two entries with a date axis
    # point at it rather than half-stating the proxy twice.
    for eid in ("faq-archetype-timeline", "faq-head-to-head"):
        assert "see the year question above" in answers[eid], eid


def test_the_pilot_identity_answer_counts_what_the_graph_holds(live_graph):
    # The answer quotes two figures about the pilots, and both are prose: nothing else
    # recomputes them, so a rebuild that moves either leaves the FAQ asserting a number
    # the app no longer holds. Graded against the real record for that reason.
    #
    # The two are opposite failures, and the answer owes a reader both. The numbered
    # careers are one id the project read as several people; the paired names are
    # several ids it has not yet read as one person, which is the error a reader is
    # likeliest to find about themselves (issue #142's review pass said "the one
    # place", which was only ever true of the first).
    from graph7ph.db import artifact_path, rows

    answers = {eid: a for eid, _, _, a in _FAQ_ENTRIES}
    identity = answers["faq-pilot-identity"]

    def count(cypher):
        return list(rows(live_graph.execute(cypher)))[0][0]

    numbered = count("MATCH (p:Pilot) WHERE p.pilot =~ '.*#[0-9]+' RETURN count(*)")
    stems = count("MATCH (p:Pilot) WHERE p.displayName =~ '.* 1' RETURN count(*)")
    assert f"{numbered + stems} of the" in identity

    report = json.loads((artifact_path() / "reconciliation.json").read_text())
    paired = len({pid for e in report["under_merges"] for pid in e["pilots"]})
    assert f"{paired} of the names offered" in identity


def test_every_leaderboard_row_qualifies_its_rank_with_the_interval_behind_it():
    # The column that stops the table overclaiming (ADR 0017). A score printed to three
    # decimals over a median eight majors reads as a settled order and is not one, so
    # every row carries the range its rank actually landed in across resamples, beside
    # the rank rather than in a footnote: rank 4 that could be 17 is a different claim
    # from rank 4, and the reader has to see the two together to make it.
    series = _race_series(*[
        (f"p{i}", 0.9 - i / 100, 10, _flat(0.7)) for i in range(12)
    ], spread=3)
    table = _leaderboard_html(series, {f"p{i}": f"Pilot {i}" for i in range(12)},
                              {}, rows=12)

    assert "Rank CI" in table
    # Rank 1's interval is clamped at the top of the field, rank 5's reaches both ways.
    assert "1&ndash;4" in table
    assert "2&ndash;8" in table
    # One interval per standing row, never fewer: a row without one is a rank presented
    # as a fact.
    assert table.count("class='score spread'") == 1 + 12


def test_a_leaderboard_name_carrying_markup_is_escaped():
    # Pilot display names come from the source, so they are free text on a surface the
    # app builds as raw HTML.
    series = _race_series(("p", 0.8, 9, _flat(0.7)))
    table = _leaderboard_html(series, {"p": "<b>Ada</b>"}, {}, _LEADERBOARD_ROWS)

    assert "<b>Ada</b>" not in table
    assert "&lt;b&gt;Ada&lt;/b&gt;" in table


def test_every_class_the_leaderboard_emits_is_one_the_stylesheet_draws():
    # The table is raw markup the app builds, so nothing in the theme forces it to be
    # styled: a class named here and nowhere in the stylesheet renders as an unstyled
    # browser default table on a dark card.
    series = _race_series(("p", 0.8, 9, _flat(0.7)), ("q", 0.7, 9, _flat(0.6)))
    table = _leaderboard_html(series, {"p": "P", "q": "Q"},
                              _race_hues(_race_trajectories(series)[:1]), _LEADERBOARD_ROWS)
    css = theme.build_css()

    classes = set(re.findall(r"class='([^']+)'", table))
    for name in {c for group in classes for c in group.split()}:
        assert f".{name}" in css, name


def test_the_race_is_its_own_tab_after_pilots_drawn_on_open(tmp_path, snapshot_dir):
    # AC (#135): a new top-level tab after Pilots. It asks a question about the whole
    # field rather than about a subject, so it takes the Archetypes tab's shape and not
    # the Pilots one: no subject picker and no Draw button, drawn at build time.
    import gradio as gr

    demo = _built_demo(tmp_path, snapshot_dir)
    tabs = [b.label for b in demo.blocks.values() if isinstance(b, gr.Tab)]
    assert tabs == ["Meta", "Archetypes", "Cards", "Hidden gems", "Pilots",
                    "Best player race", "FAQ"]

    # The fixture graph holds three decks at one small event, so nobody is a contender
    # and the tab draws the refusal rather than an empty chart or a crash. Refused with
    # the count it found, in the app's own voice.
    notes = [b.value for b in demo.blocks.values()
             if isinstance(b, gr.Markdown) and b.value and "major events" in b.value]
    assert notes == ["No pilot has enough major events to race here yet."]


def _race_figure_args():
    """A two-contender race, as the arguments :func:`_race_figure` takes."""
    series = _race_series(
        ("ace", 0.78, 12, _flat(0.76, 8)), ("rival", 0.74, 9, _flat(0.71, 6)),
    )
    return _race_trajectories(series), [], _race_names("ace", "rival")


def test_the_race_tab_draws_its_chart_and_standings_over_the_real_record(tmp_path):
    # The tab's own wiring, which no hand-built Series reaches: over a graph that really
    # holds a race, the card fills with a chart and the standings beside it and the
    # refusal note stays down. Graded on the real record because that is the only graph
    # in the repo with contenders in it; skipped, not failed, when it is absent or built
    # from other sources, exactly as `live_graph` is.
    import gradio as gr
    from graph7ph.app import build_app
    from graph7ph.db import artifact_path, database_path
    from graph7ph.provenance import staleness

    artifact = artifact_path()
    if not database_path(artifact).exists():
        pytest.skip(f"no graph artifact at {artifact}; build one with `graph7ph build`")
    if staleness(artifact):
        pytest.skip(f"cannot grade the real record: {staleness(artifact)}")
    demo = build_app(artifact)

    # Found by the race's own column, not by the shared table class: the gem table
    # wears that class too and is drawn at build time since #184.
    (table,) = [b.value for b in demo.blocks.values()
                if isinstance(b, gr.HTML) and "<th class='score spread'>Rank CI</th>"
                in (b.value or "")]
    # Capped at the display cut, and every drawn line is marked in the table.
    assert table.count("<tr") == _LEADERBOARD_ROWS + 1
    assert table.count("swatch-hue") == _RACE_LINES

    # A built app holds its figures serialised, so the race chart is found by the axis
    # title only it carries.
    plots = [json.loads(b.value["plot"]) for b in demo.blocks.values()
             if isinstance(b, gr.Plot) and b.value]
    (fig,) = [f for f in plots
              if "played up to" in str(f["layout"]["xaxis"].get("title"))]
    # The eight lines, plus the one trace holding every other contender behind them.
    assert len(fig["data"]) == _RACE_LINES + 1
    assert all(len(trace["x"]) == RACE_POINTS for trace in fig["data"][1:])
    # The right edge is the leaderboard (ADR 0017): the running score means the drawn
    # eight hold the eight highest points at the newest sample, so nothing in the faded
    # layer behind them crosses above the lowest of them there. This is the property the
    # rolling version could not offer, and the reason the chart was rebuilt.
    context, *drawn = fig["data"]
    lowest = min(trace["y"][-1] for trace in drawn)
    # Each contender's run is five points then a null separator, so the newest sample of
    # every one of them is the fifth of each six.
    behind = [y for y in context["y"][RACE_POINTS - 1::RACE_POINTS + 1] if y is not None]
    assert behind and max(behind) <= lowest
    # Nothing on the tab says the race was refused.
    notes = [b.value for b in demo.blocks.values()
             if isinstance(b, gr.Markdown) and "major events to race" in (b.value or "")]
    assert notes == []


def test_the_standings_caption_states_what_the_table_actually_holds():
    # The table is capped, so its caption cannot claim to hold every contender; and on a
    # field smaller than the cap nothing is cut, so it must not claim a top-50 ranking it
    # never applied. The same two readings `_landscape_caption` keeps apart. Then it says
    # what the interval column's numbers are, since a range with no confidence attached
    # is not one.
    big = _race_series(*[(f"p{i}", 0.9 - i / 100, 9, _flat(0.7)) for i in range(12)])
    small = _race_series(*[(f"p{i}", 0.9 - i / 100, 9, _flat(0.7)) for i in range(3)])

    assert _standings_caption(big, rows=5).startswith("top 5 of 12 contenders, best first")
    assert _standings_caption(small, rows=5).startswith("all 3 contenders, best first")
    assert f"{RACE_INTERVAL:.0%} of a thousand redraws" in _standings_caption(big, rows=5)
    # Named exactly as the column header it explains, so the two read as one thing.
    assert "Rank CI" in _standings_caption(big, rows=5)


def test_every_other_contender_is_drawn_behind_the_race_as_one_faded_layer():
    # The field is drawn rather than summarised (user call, replacing a p25-p75 band):
    # every contender the cut left out is traced behind the eight in one neutral tint, so
    # the reader sees the real spread, and where a drawn line sits inside it, instead of a
    # box that stands for it. It is the emphasis model §6 already uses on the meta chart,
    # with one difference: there hue traces each faded line, here the context is one
    # colour, because 131 lines cannot each be named and the legend says so with a single
    # entry.
    series = _race_series(*[
        (f"p{i}", 0.9 - i / 100, 9, _flat(0.8 - i / 100)) for i in range(12)
    ])
    trajectories = _race_trajectories(series)
    fig = _race_figure(trajectories[:3], trajectories[3:],
                       {f"p{i}": f"Pilot {i}" for i in range(12)})

    # Read in the order the legend lays them out, which is legendrank, not trace order:
    # the context is added first so every drawn line paints over it, and sorted last so
    # the legend still reads as the standings.
    named = sorted((t for t in fig.data if t.showlegend is not False),
                   key=lambda t: t.legendrank)
    # Three drawn pilots and one entry for all the rest: four legend entries, not twelve.
    assert [t.name for t in named] == ["Pilot 0", "Pilot 1", "Pilot 2",
                                       "All other contenders"]
    context = named[-1]
    # One trace holds all nine, so the legend cannot fill with them. Each pilot's run is
    # closed with a null, or the last window of one would join the first of the next.
    assert context.y.count(None) == 9
    assert len([v for v in context.y if v is not None]) == 9 * len(_RACE_ENDS)
    # Lines only: an observation marker's fill is the opaque surface, so nine of them
    # per window would tile over each other and over the drawn lines beneath.
    assert context.mode == "lines"
    # It is chart ground, so it answers no hover and never steals one from a drawn pilot.
    assert context.hoverinfo == "skip"
    # Neutral, not a ninth hue: the palette's eight name the race, this names nobody.
    assert not any(hue in str(context.line.color) for hue in palette.CATEGORICAL)
    # And sorted below the standings, as the band it replaces was.
    assert context.legendrank > max(t.legendrank for t in named[:3])


def test_a_field_small_enough_to_draw_whole_advertises_no_context_layer():
    # Every contender fits inside the cut, so there is no "other" contender to trace
    # behind them. The layer is dropped rather than added empty, which would put a
    # legend entry on the chart for a set with nothing in it.
    series = _race_series(("ace", 0.78, 12, _flat(0.76, 8)))
    fig = _race_figure(_race_trajectories(series), [], _race_names("ace"))

    assert [t.name for t in fig.data] == ["Ace"]
