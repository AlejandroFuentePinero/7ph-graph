import re
from datetime import datetime

from graph7ph import palette, theme
from graph7ph.app import (
    _CARDS_TAB,
    _PLOT_LABELS,
    _adoption_figure,
    _adoption_caption,
    _adoption_cards,
    _chart_heading,
    _embed,
    _head_to_head_figure,
    _PILOTS_TAB,
    _between_line_polys,
    _performance_caption,
    _performance_figure,
    _result_header,
    _subject_line,
    _trend_figure,
)
from graph7ph.trends import (
    AdoptionCell,
    HeadToHeadPoint,
    PerformanceCell,
    Series,
    SeriesCell,
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
    # bar reads Pilots / Cards / Meta / Hidden gems and Meta holds meta share alone
    # (a single-view tab). The tab order is v1 §11's amended four-tab structure, an
    # independent source; the built app is the seam so a group left under Meta trips
    # here rather than only in the browser.
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
    assert tabs == ["Meta", "Cards", "Hidden gems", "Pilots", "FAQ"]
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
    # plots, reachable from the app but off the plot surfaces. It is the last tab, each
    # entry is its own insight-card box with a stable elem_id, and a table of contents
    # links to every box (so a box with no TOC link, or a dead link, trips here). Every
    # headline metric is explained by its stable plot name, asserted on the names rather
    # than the prose so a copy edit to an answer does not break it.
    import gradio as gr

    demo = _built_demo(tmp_path, snapshot_dir)
    tabs = [b.label for b in demo.blocks.values() if isinstance(b, gr.Tab)]
    assert tabs == ["Meta", "Cards", "Hidden gems", "Pilots", "FAQ"]

    box_ids = {b.elem_id for b in demo.blocks.values()
               if isinstance(b, gr.Group) and (b.elem_id or "").startswith("faq-")}
    assert box_ids, "each FAQ entry is its own box with a faq- elem_id"
    all_md = " ".join(b.value for b in demo.blocks.values()
                      if isinstance(b, gr.Markdown) and b.value)
    # The contents links and the boxes are the same set both ways: a box with no link,
    # and a link pointing at no box, each trip here.
    linked = set(re.findall(r"\(#(faq-[\w-]+)\)", all_md))
    assert linked == box_ids, f"contents links {linked} != boxes {box_ids}"

    body = " ".join(b.value for b in demo.blocks.values()
                    if isinstance(b, gr.Markdown) and "faq" in (b.elem_classes or []))
    for metric in ("Meta share over time", "Performance over time", "Head-to-head",
                   "Adoption over time", "Hidden gem"):
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
    # of the four subject tabs (Pilots, Cards, Meta, Hidden gems) leads with a section
    # heading. A section heading is an h2 (`## `) in the page's own type (§3), distinct
    # from the h1 page title and the bold-led plot intros, so counting them is robust
    # to copy edits while a dropped tab heading trips here rather than only in the browser.
    headings = [m for m in _markdown_values(_built_demo(tmp_path, snapshot_dir))
                if m.lstrip().startswith("## ")]
    # Four subject tabs plus the FAQ tab (#133), each led by its own h2 section heading.
    assert len(headings) == 5


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

    assert len(intros) == 5  # one per tab (Pilots, Cards, Meta, Hidden gems, FAQ)
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


def test_a_view_opens_with_dropdown_guidance_not_duplicated_prompt_cards(tmp_path, snapshot_dir):
    # AC (#132, §14, user feedback): a view no longer opens as a row of identical
    # "Pick an entity and filters, then Draw." cards. The guidance moves to the subject
    # dropdown's help text (`info`), one place at the control you drive from, and the
    # per-view results stack starts hidden so nothing empty is drawn. Keyed on the
    # guidance constants and the results-stack visibility, so a regression that revives
    # the duplicated prompt cards (or drops the dropdown guidance) trips here.
    import gradio as gr
    from graph7ph.app import _PICK_ARCHETYPE, _PICK_CARD, _PICK_PILOT

    demo = _built_demo(tmp_path, snapshot_dir)

    infos = {b.info for b in demo.blocks.values()
             if isinstance(b, gr.Dropdown) and b.info}
    assert {_PICK_PILOT, _PICK_CARD, _PICK_ARCHETYPE} <= infos

    # No result surface carries the old duplicated prompt text on open.
    surface = " ".join(
        b.value for b in demo.blocks.values()
        if isinstance(b, (gr.Markdown, gr.HTML)) and b.value
    )
    assert "Pick an entity and filters, then Draw" not in surface


def test_hidden_gems_requires_an_archetype():
    # The gem view is entered by archetype and no longer offers a format-wide default:
    # with no archetype picked `_spec` returns None (so `run_graph` shows the prompt
    # rather than drawing), and a chosen archetype builds the query. This guards the
    # mandatory-archetype behaviour the "(optional)" label used to advertise.
    from graph7ph.app import _spec
    from graph7ph.query import HiddenGems

    assert _spec("meta_gems", {"gem_archetype": ""}) is None
    assert _spec("meta_gems", {"gem_archetype": None}) is None
    assert _spec("meta_gems", {"gem_archetype": "ramp"}) == HiddenGems("ramp")


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
    assert "Cards" in msg            # names the kind flooding the view
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


def test_a_refused_year_at_the_end_of_a_career_is_an_empty_tick_not_a_missing_year():
    # A pilot who played four years but could only be averaged in two. The thin years
    # here are the first and the last, which is where they usually fall: a one-event
    # year is overwhelmingly the year someone arrived or left. The chart used to span
    # only the averaged years, so both ends vanished and it claimed a two-year career
    # (issue #101). Every year the pilot played is now a tick; the refused ones carry
    # no point and no label, so the line breaks across them instead of bridging.
    series = Series(cells=[
        PerformanceCell(year=2023, mean_norm=None, events=1),
        PerformanceCell(year=2024, mean_norm=0.4, events=3),
        PerformanceCell(year=2025, mean_norm=0.2, events=5),
        PerformanceCell(year=2026, mean_norm=None, events=1),
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
        PerformanceCell(year=2024, mean_norm=0.4, events=3),
        PerformanceCell(year=2026, mean_norm=0.2, events=2),
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
        PerformanceCell(year=2023, mean_norm=None, events=1),
        PerformanceCell(year=2024, mean_norm=0.4, events=3),
        PerformanceCell(year=2026, mean_norm=None, events=0),
    ])
    # The refused-year captions sit under the axis (yref="paper"); the midpoint
    # reference-line label rides the y-axis, so filter to the captions this test is about.
    captions = {
        (a.x, a.text) for a in _performance_figure("Ada L", series).layout.annotations
        if a.yref == "paper"
    }

    # 2025 has no cell at all (sat out), so it gets a tick and no caption.
    assert captions == {(2023, "1 ev, too thin"), (2026, "played, unscored")}


def test_performance_markers_grow_with_the_events_behind_each_year():
    # A two-event mean and a twenty-event one sit on the same line; the ring's size
    # carries the sample size so the eye discounts the thin year without hovering.
    series = Series(cells=[
        PerformanceCell(year=2024, mean_norm=0.4, events=2),
        PerformanceCell(year=2025, mean_norm=0.4, events=25),
    ])
    sizes = _performance_figure("Ada L", series).data[0].marker.size

    assert sizes[1] > sizes[0]  # the 25-event year draws a broader ring than the 2-event one


def test_performance_caption_states_the_field_share_and_the_sample():
    # The flat axis is silent on the standing, so the caption states it: the mean
    # score weighted by events, as the share of the field beaten, with the sample.
    caption = _performance_caption(Series(cells=[
        PerformanceCell(year=2024, mean_norm=0.4, events=2),   # score 0.6
        PerformanceCell(year=2025, mean_norm=0.2, events=8),   # score 0.8
        PerformanceCell(year=2026, mean_norm=None, events=1),  # refused, not in the mean
    ]))

    # Weighted mean score = (0.6*2 + 0.8*8) / 10 = 0.76, rounded to a whole percent, and
    # set in the accent span so the eye lands on it; the sample trails in its own span.
    assert "<span class='pct'>76%</span>" in caption
    assert "10 events over 2 scored years" in caption


def test_a_leading_refused_year_does_not_stretch_the_axis():
    # A leading refused year is a null-only point that draws no marker, but its refusal
    # caption is anchored to the x-axis. On a category axis the numeric-string year lands
    # at the linear coordinate 2024, off the category slots, dragging autorange out to it
    # so the real markers crush to one edge and the caption strands at the other. A linear
    # year axis with a pinned range puts caption and markers on one bounded scale.
    fig = _performance_figure("Ada L", Series(cells=[
        PerformanceCell(year=2024, mean_norm=None, events=1),
        PerformanceCell(year=2025, mean_norm=0.4, events=3),
        PerformanceCell(year=2026, mean_norm=0.2, events=5),
    ]))

    assert fig.layout.xaxis.type == "linear"
    assert fig.layout.xaxis.range == (2023.5, 2026.5)  # bounded to the years, no blowup
    # The refusal caption sits at its real year, inside the range, not flung past it.
    thin = next(a for a in fig.layout.annotations if a.text == "1 ev, too thin")
    assert thin.x == 2024


def test_head_to_head_colours_each_pilot_by_entity_from_the_shared_palette():
    # AC (§5-6): head-to-head is two lines, ≤8, so each pilot takes a direct colour
    # from the shared eight-hue set (slot 1, slot 2), not a position in a long
    # recycled wheel. Colour follows the entity: the pilot named first is blue.
    series = Series(cells=[
        HeadToHeadPoint(event="GP", date=datetime(2024, 3, 1), field_size=100,
                        placement_a=1, norm_a=0.0, placement_b=50, norm_b=0.5),
        HeadToHeadPoint(event="PT", date=datetime(2024, 6, 1), field_size=80,
                        placement_a=40, norm_a=0.5, placement_b=1, norm_b=0.0),
    ])
    fig = _head_to_head_figure("Ada L", "Bob C", series)
    by_name = {t.name: t for t in fig.data if t.name in ("Ada L", "Bob C")}

    assert by_name["Ada L"].marker.line.color == palette.CATEGORICAL[0]
    assert by_name["Bob C"].marker.line.color == palette.CATEGORICAL[1]


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


def test_more_than_eight_series_do_not_borrow_a_ninth_hue_or_crash():
    # AC / §5-6: colour tops out at eight distinguishable series. Past eight the
    # shared palette assigns nothing (the signal to switch to emphasis, a separate
    # slice), so this branch must still draw every line in a real colour rather than
    # a None the figure chokes on. The >8 fallback is out of scope for the direct-
    # colour AC, but it must not regress into a crash.
    triples = [(f"arch{i}", 2024, 0.05) for i in range(9)]
    tags = [t for t, _, _ in triples]
    fig = _trend_figure(_meta_series(*triples), tags)

    assert len(fig.data) == 9
    assert all(isinstance(t.marker.line.color, str) and t.marker.line.color
               for t in fig.data)


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
            PerformanceCell(year=2024, mean_norm=0.4, events=3),
        ])),
        _trend_figure(_meta_series(("aggro", 2024, 0.3)), {"aggro"}),
        _adoption_figure([("Sol Ring", Series(cells=[
            AdoptionCell(year=2024, count=30, share=0.03, year_total=1000),
        ]))]),
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
        PerformanceCell(year=2024, mean_norm=0.4, events=3),
        PerformanceCell(year=2025, mean_norm=0.2, events=5),
    ]))

    assert fig.layout.xaxis.gridcolor == theme.TOKENS["border"]
    assert fig.layout.yaxis.gridcolor == theme.TOKENS["border"]
    assert fig.layout.xaxis.linecolor == theme.TOKENS["text-mute"]
    assert fig.layout.font.color == theme.TOKENS["text-mute"]
    # The whole figure, serialised, must not carry the retired theme-neutral grey.
    assert "#9ca3af" not in fig.to_json()


def test_observation_markers_carry_a_surface_ring_over_a_thin_dashed_join():
    # AC (§6): the ADR-0013 read is kept: a thin dashed line that only joins the
    # points, hollow observation markers, and the markers gain a 2px surface ring
    # so two that overlap do not muddy into each other. The ring is the surface
    # colour filling the marker; the series colour is its 2px outline.
    trace = _performance_figure("Ada L", Series(cells=[
        PerformanceCell(year=2024, mean_norm=0.4, events=3),
    ])).data[0]

    assert trace.line.dash == "dash"  # joins points, asserts no trend (ADR 0013)
    assert trace.line.width == 1  # thin
    assert trace.marker.symbol == "circle"
    assert trace.marker.color == theme.TOKENS["surface"]  # the surface ring
    assert trace.marker.line.width == 2  # a 2px outline in the series colour
    assert trace.marker.line.color == palette.CATEGORICAL[0]


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


def test_every_graph_plot_shares_one_frame_height():
    # §12 / AC (user feedback): the pilot neighbourhood frame is the size the user liked,
    # and every graph plot should match it, so the frame height is a single shared
    # constant rather than scaling per node count. A dense graph and a sparse one land in
    # the same frame, reading as one coherent canvas across the tabs.
    from graph7ph.app import GRAPH_HEIGHT

    frame = _embed("<html></html>")
    assert f"{GRAPH_HEIGHT}px" in frame
    assert GRAPH_HEIGHT >= 700  # tall enough that a dense graph lays out legibly
