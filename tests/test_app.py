from datetime import datetime

from graph7ph import palette, theme
from graph7ph.app import (
    _CARDS_TAB,
    _PLOT_LABELS,
    _adoption_figure,
    _adoption_heading_text,
    _adoption_cards,
    _chart_heading,
    _embed,
    _head_to_head_figure,
    _PILOTS_TAB,
    _between_line_polys,
    _performance_figure,
    _result_header,
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
    assert tabs == ["Pilots", "Cards", "Meta", "Hidden gems"]
    # Gems now has its own tab; Meta holds meta share alone. The gems query keeps
    # its plot heading (test_every_underlying_query_still_has_a_plot_heading) and its
    # _spec dispatch on `meta_gems`, so promoting the tab does not drop the view.


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


def test_card_overview_adoption_heading_carries_the_board_but_cooccurrence_carries_none():
    # AC (#126): Card overview has a board control, so its adoption heading names the
    # board the count is scoped to. Co-occurrence is board-agnostic (no board
    # control), so its adoption heading carries no board qualifier at all: the
    # string "main or side" must never reach the plot, since there is no control to
    # disambiguate it. board=None is the board-agnostic sentinel.
    assert _adoption_heading_text("") == "Card adoption over time (main or side)"
    assert _adoption_heading_text("Main") == "Card adoption over time (main)"

    agnostic = _adoption_heading_text(None)
    assert agnostic == "Card adoption over time"
    assert "main or side" not in agnostic
    assert "board" not in agnostic.lower()


def test_cooccurrence_adoption_plots_both_cards_or_the_subject_alone():
    # AC (#126): the co-occurrence adoption trend plots both cards when a second is
    # chosen and the subject alone otherwise; the subject always leads (first trace),
    # and a second card equal to the subject collapses to one line rather than two.
    assert _adoption_cards("sol-ring", None) == ["sol-ring"]
    assert _adoption_cards("sol-ring", "mana-crypt") == ["sol-ring", "mana-crypt"]
    assert _adoption_cards("sol-ring", "sol-ring") == ["sol-ring"]
    # No subject, no lines: a compare card never draws on its own.
    assert _adoption_cards("", "mana-crypt") == []


def test_a_drawn_result_is_titled_and_captioned_in_page_type():
    # Issue #110: a query result is never left as an unlabelled graph. It opens
    # under a title (the view named in reader language, then its subject) and a
    # caption (the filters, then how much came back), both in the page's own type
    # roles (§3), so the answer reads as an answer. The class names are the page-type
    # contract: a regression to plain <p> text would drop them and trip this.
    header = _result_header(
        "pilot_neighbourhood", "Ada L", ["vs Bob C"], node_count=42
    )

    assert "t-result-title" in header
    assert "t-caption" in header
    # The plot label names what was drawn (#126: a view holds several plots), the
    # subject follows it.
    assert "Neighbourhood: Ada L" in header
    # The caption carries the filters and the node count, joined as one line.
    assert "vs Bob C · 42 nodes" in header


def test_the_caption_reads_the_node_count_and_reduces_to_the_singular():
    # "How much came back" is the count of nodes drawn. With no filters the caption
    # is the count alone, and a lone node reads "1 node", not "1 nodes".
    one = _result_header("pilot_affinity", "Ada L", [], node_count=1)
    assert ">1 node</" in one

    many = _result_header("pilot_affinity", "Ada L", [], node_count=250)
    assert ">250 nodes</" in many


def test_the_subject_and_filters_are_escaped_into_the_header():
    # The subject and filter strings come from display labels, which are free text,
    # so a name carrying an angle bracket is escaped rather than injected into the
    # result markup.
    header = _result_header("card_usage", "A<b>", ["main board"], node_count=3)
    assert "A<b>" not in header
    assert "A&lt;b&gt;" in header


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

    assert trace.x == ("2023", "2024", "2025", "2026")
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

    assert trace.x == ("2024", "2025", "2026")
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
    captions = {
        (a.x, a.text) for a in _performance_figure("Ada L", series).layout.annotations
    }

    # 2025 has no cell at all (sat out), so it gets a tick and no caption.
    assert captions == {("2023", "1 ev, too thin"), ("2026", "played, unscored")}


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


def test_embed_iframe_height_is_responsive_not_a_fixed_slab():
    # §7 / AC: the graph frame scales with the viewport instead of a fixed
    # 760/700px letterbox, so a phone is not eaten by a slab and a wide desktop
    # is not letterboxed. The details panel inside stays visible without scroll.
    frame = _embed("<html></html>")

    assert "760px" not in frame  # the fixed height is retired
    assert "vh" in frame  # height is viewport-relative
