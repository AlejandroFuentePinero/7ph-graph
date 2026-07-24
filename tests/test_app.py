from graph7ph.app import (
    _CARDS_TAB,
    _META_TAB,
    _PILOTS_TAB,
    _between_line_polys,
    _performance_figure,
)
from graph7ph.trends import PerformanceCell, Series


def test_the_three_subject_tabs_carry_exactly_the_nine_modality_views():
    # Issue #119 regroups by subject, not by render pipeline, but preserves every
    # view: the Explore/Trends split held nine views, and the Pilots/Cards/Meta
    # split must hold the same nine, none added, dropped, or filed under the wrong
    # subject. The expectations are v1 §11's table, an independent source: a future
    # edit that drops a view, duplicates one, or moves it tabs trips this.
    per_tab = {"Pilots": set(_PILOTS_TAB), "Cards": set(_CARDS_TAB), "Meta": set(_META_TAB)}
    # §11's table splits the views 4 / 3 / 2 across the three tabs.
    assert [len(per_tab[t]) for t in ("Pilots", "Cards", "Meta")] == [4, 3, 2]

    all_ids = set().union(*per_tab.values())
    assert len(all_ids) == 9  # no view id shared across tabs
    assert all_ids == {
        "pilot_neighbourhood", "pilot_affinity", "pilot_performance", "pilot_h2h_timeline",
        "card_usage", "card_cooccurrence", "card_adoption",
        "meta_share", "meta_gems",
    }
    # §11's placement note: hidden gems sits under Meta (beside meta share), so the
    # Meta tab is not a single-view tab, not under Cards.
    assert "meta_gems" in per_tab["Meta"]


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
