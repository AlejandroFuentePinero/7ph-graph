import json
import math
from datetime import datetime
from pathlib import Path

import pytest

from graph7ph.build import MIN_CUT_FIELD
# The fixture titles a deck the way the app renders a placement, so it reads the app's
# own ordinal rather than keeping a second copy that can drift from it.
from graph7ph.query import _ordinal
from graph7ph.db import rows
from graph7ph.trends import (
    MIN_CAREER_MAJORS,
    MIN_RACE_CONTENDERS,
    MIN_SCORED_MAJORS,
    RACE_POINTS,
    ArchetypeLandscape,
    ArchetypeTimeline,
    CardAdoptionOverTime,
    HeadToHeadTimeline,
    MetaShareOverTime,
    NotEnoughHistory,
    PilotPerformanceOverTime,
    PlayerLeaderboard,
    Series,
    SeriesCell,
    _cut_only_events,
    _interval,
    _pooled_sd,
    _tail_bounds,
    archetype_landscape,
    archetype_timeline,
    comparable_points,
    archetypes_with_history,
    beats_a_coin,
    card_adoption_over_time,
    head_to_head_timeline,
    latest_deck_year,
    latest_year_share_cut,
    meta_share_over_time,
    pilot_performance_over_time,
    pilots_with_history,
    player_leaderboard,
    run_series,
)

# Every hand-authored fixture declares a field size no cohort it builds can
# contradict, so the build's field-size correction (issue #140) leaves it alone
# and the placementNorms these tests craft stand as written.
_FIELD_SIZE = 500

# The same, for the fixtures whose surfaces also require an event to have published
# its field rather than a bracket (:data:`trends.MIN_FIELD_COVERAGE`, ADR 0021). Small
# enough that :func:`_cover_fields` can fill it in a handful of decks, and still over
# the 8 that would read as a top-8 cut and trip the build's Rule B.
_COVERED_FIELD = 12

def _shared_win(field_size: int) -> str:
    """The rank-1 tie band every bulk fixture deck here opens its title with.

    These writers park whole cohorts at rank 1 because no trend surface reads a
    placement off them, but a cohort of exclusive "1st" titles is exactly the
    impossible bracket the build's sole-winner guard (``TwoWinners``) exists to
    refuse. So the shared rank is declared as what it is: one tie band as wide
    as the event's own declared field, which seats any cohort the field can
    hold. Its best end still reads as rank 1, agreeing with the ``placement: 1``
    column beside it.
    """
    return f"1st/{field_size}th"


def _cell(tag, year, n):
    """A cell with an arbitrary but consistent share/year_total, for cut tests."""
    return SeriesCell(tag=tag, archetype=tag.title(), year=year, n=n,
                      share=1.0, year_total=n)


def _write_snapshot(
    root: Path,
    decks: list[tuple[str, str, int, str]],
    unprimaried: set[str] = frozenset(),
) -> Path:
    """Write a minimal snapshot of ``(deck_id, event, year, archetype)`` decks.

    Each deck carries no cards (an empty decklist), which is all the trend tools
    need: they group decks by their primary archetype and their event's year. An
    event's year is derived from its decks' ``createdAt``, so every deck of one
    event is stamped in that event's year (a straddle would abort the build).

    A deck id in ``unprimaried`` is given a ``primaryTag`` that is not one of its
    ``engineTags``, so no archetype of that deck is primary: it reaches the graph
    with archetype edges but none flagged ``isPrimary``, the real-data case of a
    deck the source left without a primary archetype.
    """
    snap = root / "snap"
    snap.mkdir()
    deck_records = []
    for deck_id, event, year, archetype in decks:
        tag = f"engine:{archetype}"
        deck_records.append({
            "deckId": deck_id,
            "name": f"{_shared_win(_FIELD_SIZE)} Someone - {archetype} - {event}",
            "deckName": archetype.title(),
            "pilot": f"pilot-{deck_id}",
            "event": event,
            "eventId": f"evt_{event}",
            "eventType": "Tournament",
            "eventSize": _FIELD_SIZE,
            "placement": 1,
            "placementNorm": 0.1,
            "createdAt": f"{year}-06-01T00:00:00+00:00",
            "colour": "colour:U",
            "macro": "macro:combo",
            "engineTags": [tag],
            "engineTagLabels": {tag: archetype.title()},
            "primaryTag": "engine:__none__" if deck_id in unprimaried else tag,
            "primaryTagWeights": {tag: 100},
        })
    (snap / "decks.json").write_text(json.dumps(deck_records))
    (snap / "cards_index.json").write_text(json.dumps({
        "v": 1,
        "cards": [],
        "decks": {d[0]: {"m": [], "s": []} for d in decks},
    }))
    return snap


def _meta_share_graph(root, built_graph):
    """A built graph with fat and thin cells, and archetypes across years.

    2025: 6 Grixis, 1 Storm. 2024: 5 Storm, 3 Grixis. So each archetype is large in
    one year and down to a handful in the other, the shape a floor used to withhold.
    """
    decks = (
        [(f"g25-{i}", "E2025", 2025, "grixis") for i in range(6)]
        + [("s25-0", "E2025", 2025, "storm")]
        + [(f"s24-{i}", "E2024", 2024, "storm") for i in range(5)]
        + [(f"g24-{i}", "E2024", 2024, "grixis") for i in range(3)]
    )
    return built_graph(root, _write_snapshot(root, decks))


def test_meta_share_states_every_cell_including_the_thin_ones(tmp_path, built_graph):
    conn = _meta_share_graph(tmp_path, built_graph)
    series = meta_share_over_time(conn)
    by_key = {(c.archetype, c.year): c for c in series.cells}

    # 2025 has 7 decks: 6 Grixis, 1 Storm. Every cell reports that year total.
    assert {c.year_total for c in series.cells if c.year == 2025} == {7}
    # The fat Grixis cell carries its share; the year's cell counts partition it.
    grixis_2025 = by_key[("Grixis", 2025)]
    assert grixis_2025.n == 6
    assert grixis_2025.share == pytest.approx(6 / 7)
    assert sum(c.n for c in series.cells if c.year == 2025) == 7

    # The 1-deck Storm cell states its share too, against the whole year rather
    # than its own count: a share is a direct observation, exact whatever its size,
    # so a thin cell is a low point and not a hole.
    storm_2025 = by_key[("Storm", 2025)]
    assert storm_2025.n == 1
    assert storm_2025.share == pytest.approx(1 / 7)

    # No cell anywhere in the matrix is withheld. A reintroduced floor fails here.
    assert all(cell.share is not None for cell in series.cells)


def test_an_archetype_no_deck_leads_with_is_a_line_of_zeros_not_a_missing_line(
    tmp_path, built_graph
):
    # ``boros`` reaches the graph as an Archetype but never as anyone's primary, the
    # real case of a sub-variant every deck carries alongside a broader engine. The
    # matrix used to take its archetypes from the primary-archetype join, so such an
    # archetype had no row anywhere and no entry in the manual picker: rectangular
    # only over the archetypes the join happened to see (issue #101).
    decks = (
        [(f"s24-{i}", "E2024", 2024, "storm") for i in range(3)]
        + [(f"s25-{i}", "E2025", 2025, "storm") for i in range(2)]
        + [("b25-0", "E2025", 2025, "boros")]
    )
    conn = built_graph(tmp_path, _write_snapshot(tmp_path, decks, unprimaried={"b25-0"}))
    by_key = {(c.archetype, c.year): c for c in meta_share_over_time(conn).cells}

    assert set(by_key) == {(a, y) for a in ("Boros", "Storm") for y in (2024, 2025)}
    # Boros is a real zero in both years: no deck leads with it, which is a fact the
    # chart can state, not a reason to leave it off the axis.
    assert [by_key[("Boros", y)].n for y in (2024, 2025)] == [0, 0]
    # And the deck still dilutes its year, so 2025's shares are over 3 decks, not 2.
    assert by_key[("Storm", 2025)].share == pytest.approx(2 / 3)


def test_an_archetype_absent_in_a_year_is_a_zero_not_a_missing_row(tmp_path, built_graph):
    # 2024: 5 Storm only, so Grixis sat the year out and Boros did not exist yet.
    # 2025: 6 Grixis, 2 Boros, 2 Storm - 10 decks, so both answers are live in one
    # series: a year with no decks at all, and a year with a handful.
    decks = (
        [(f"s24-{i}", "E2024", 2024, "storm") for i in range(5)]
        + [(f"g25-{i}", "E2025", 2025, "grixis") for i in range(6)]
        + [(f"b25-{i}", "E2025", 2025, "boros") for i in range(2)]
        + [(f"s25-{i}", "E2025", 2025, "storm") for i in range(2)]
    )
    conn = built_graph(tmp_path, _write_snapshot(tmp_path, decks))
    by_key = {(c.archetype, c.year): c for c in meta_share_over_time(conn).cells}

    # Every archetype has a cell in every year: the matrix is rectangular.
    assert set(by_key) == {(a, y) for a in ("Boros", "Grixis", "Storm")
                           for y in (2024, 2025)}
    # An absent year is a real zero (a share of 0, plotted), not a missing row, so
    # the line drops to zero rather than jumping the year.
    for absent in (by_key[("Grixis", 2024)], by_key[("Boros", 2024)]):
        assert absent.n == 0
        assert absent.share == 0.0
        assert absent.year_total == 5
    # A thin year is a small share, told apart from that zero by being non-zero:
    # two decks of ten is the archetype entering the format, the signal the chart
    # is for, so it is stated rather than withheld.
    boros_2025 = by_key[("Boros", 2025)]
    assert boros_2025.n == 2
    assert boros_2025.share == pytest.approx(2 / 10)
    # And a fat year carries its share against the year's total, which counts every
    # deck that year, not just the archetype's own.
    grixis_2025 = by_key[("Grixis", 2025)]
    assert grixis_2025.n == 6
    assert grixis_2025.year_total == 10
    assert grixis_2025.share == pytest.approx(6 / 10)


def test_cut_keeps_the_strongest_archetypes_until_the_share_is_reached():
    # Latest-year (2025) counts: A=60, B=30, C=10 (total 100). The earlier years are
    # loaded the other way round so a cut that pooled them would rank differently.
    series = Series(cells=[
        _cell("a", 2024, 5), _cell("a", 2025, 60),
        _cell("b", 2025, 30),
        _cell("c", 2024, 500), _cell("c", 2025, 10),
    ])
    # 50%: A alone is 60% >= 50%. 75%: A+B is 90% >= 75%. 25%: A alone suffices.
    assert latest_year_share_cut(series, 0.50) == ["a"]
    assert latest_year_share_cut(series, 0.75) == ["a", "b"]
    assert latest_year_share_cut(series, 0.25) == ["a"]
    # Returned strongest-first, and C's fat 2024 never lifts it above A or B.
    assert latest_year_share_cut(series, 1.0) == ["a", "b", "c"]


def test_cut_follows_the_latest_year_in_the_data():
    # The latest year is read from the series, not pinned: add a newer year and the
    # ranking moves to it, so the cut tracks the meta as the graph grows.
    older = [_cell("a", 2025, 60), _cell("b", 2025, 30)]
    assert latest_year_share_cut(Series(cells=older), 0.5) == ["a"]
    newer = older + [_cell("a", 2026, 10), _cell("b", 2026, 40)]
    assert latest_year_share_cut(Series(cells=newer), 0.5) == ["b"]


def test_a_one_deck_archetype_draws_a_full_line_of_real_values(tmp_path, built_graph):
    # The case the removed floor used to blank: an archetype at a single deck in a
    # year. Every year it holds comes back a stated share, so a fringe archetype
    # drawn from the manual panel is a line of real points, never a line of holes.
    decks = (
        [(f"g24-{i}", "E2024", 2024, "grixis") for i in range(9)]
        + [("b24-0", "E2024", 2024, "boros")]
        + [(f"g25-{i}", "E2025", 2025, "grixis") for i in range(10)]
    )
    conn = built_graph(tmp_path, _write_snapshot(tmp_path, decks))
    boros = sorted(
        (c for c in meta_share_over_time(conn).cells if c.archetype == "Boros"),
        key=lambda c: c.year,
    )
    assert [(c.year, c.n, c.share) for c in boros] == [
        (2024, 1, pytest.approx(1 / 10)), (2025, 0, 0.0)
    ]


def test_cut_ranks_on_the_latest_year_with_decks_not_an_all_zero_year():
    # The rectangular matrix can hand the cut a latest year of all zeros (a newest
    # year whose decks all reached the graph without a primary archetype). The cut
    # ranks on the latest year that actually has decks, so it draws the meta rather
    # than blanking the chart.
    series = Series(cells=[
        _cell("a", 2025, 60), _cell("b", 2025, 30),
        SeriesCell("a", "A", 2026, 0, 0.0, 40),
        SeriesCell("b", "B", 2026, 0, 0.0, 40),
    ])
    assert latest_year_share_cut(series, 0.5) == ["a"]
    assert latest_deck_year(series) == 2025


def test_cut_counts_thin_cells():
    # A handful of decks is still decks, so a thin cell is part of the population
    # the cut ranks on rather than being invisible to it.
    series = Series(cells=[
        SeriesCell("a", "A", 2025, 4, 4 / 8, 8),
        SeriesCell("b", "B", 2025, 4, 4 / 8, 8),
    ])
    # Pinned to the tie-break the cut actually applies, ascending tag, rather than
    # accepting either equal: `in (["a"], ["b"])` passed whichever way the policy
    # went, so flipping it left the suite green while changing which line a future
    # chart draws.
    assert latest_year_share_cut(series, 0.5) == ["a"]
    assert set(latest_year_share_cut(series, 1.0)) == {"a", "b"}


def test_cut_landing_inside_a_band_of_equals_splits_it_on_the_tie_break():
    # The cut's only genuinely undetermined case: 2026 holds a=6 and three equals
    # at 3 each, 15 decks in all. `a` alone is 6/15, short of the 50% cut, and any
    # one of b, c and d lifts it to 9/15, so the tie-break alone decides which of
    # the three is drawn and which two are dropped. Asserting the whole kept list
    # rather than the survivor holds both halves of that split.
    #
    # Inert on the current snapshot, but not inert historically: the mechanism
    # decided the drawn set on 11 of 41 of this year's own graph states at the
    # default cut, most recently 2026-06-28 (issue #103).
    series = Series(cells=[
        _cell("a", 2026, 6), _cell("b", 2026, 3),
        _cell("c", 2026, 3), _cell("d", 2026, 3),
    ])
    assert latest_year_share_cut(series, 0.5) == ["a", "b"]


def test_cut_of_an_empty_series_is_empty():
    assert latest_year_share_cut(Series(cells=[]), 0.5) == []


def test_run_series_routes_meta_share_through_its_own_seam(tmp_path, built_graph):
    conn = _meta_share_graph(tmp_path, built_graph)
    routed = run_series(conn, MetaShareOverTime())
    direct = meta_share_over_time(conn)
    assert isinstance(routed, Series)
    assert {(c.archetype, c.year, c.n) for c in routed.cells} == {
        (c.archetype, c.year, c.n) for c in direct.cells
    }


def test_run_series_rejects_an_unknown_spec():
    with pytest.raises(TypeError):
        run_series(None, object())


def test_a_deck_without_a_primary_archetype_dilutes_the_year_rather_than_inflating(
    tmp_path, built_graph
):
    # Six Grixis decks with a primary archetype, plus one Grixis deck the source
    # left without a primary. All seven are real decks that year.
    decks = [(f"g-{i}", "E", 2025, "grixis") for i in range(6)] + [("np", "E", 2025, "grixis")]
    conn = built_graph(tmp_path, _write_snapshot(tmp_path, decks, unprimaried={"np"}))
    cells = [c for c in meta_share_over_time(conn).cells if c.year == 2025]

    # The year total counts every deck, but the primary-archetype cell counts only
    # the six with a primary, so the share is 6/7, not 7/7: the unclassified deck
    # dilutes the share rather than inflating it.
    (grixis,) = cells
    assert grixis.year_total == 7
    assert grixis.n == 6
    assert grixis.share == pytest.approx(6 / 7)
    assert sum(c.n for c in cells) < grixis.year_total


def _write_adoption_snapshot(
    root: Path, decks: list[tuple[str, str, int, str | None]]
) -> Path:
    """Write a snapshot of ``(deck_id, event, year, bolt_board)`` decks.

    One card, Lightning Bolt, sits at index 0 of the catalogue; ``bolt_board`` is
    ``"m"`` to run it in the main board, ``"s"`` in the side, or ``None`` for a deck
    that does not run it. That is all the adoption tool needs: it counts, per year,
    the decks running the card, optionally scoped to a board.

    Each deck names a distinct pilot, so the build does not fuzzy-merge them into
    one pilot and then drop the card-for-card identical Bolt lists as duplicate
    registrations (ADR 0004): here every deck is a real, separate registration.
    """
    snap = root / "snap"
    snap.mkdir()
    deck_records = [
        {
            "deckId": deck_id,
            "name": f"{_shared_win(_FIELD_SIZE)} Player {deck_id} - Deck - {event}",
            "deckName": "Deck",
            "pilot": f"pilot-{deck_id}",
            "event": event,
            "eventId": f"evt_{event}",
            "eventType": "Tournament",
            "eventSize": _FIELD_SIZE,
            "placement": 1,
            "placementNorm": 0.1,
            "createdAt": f"{year}-06-01T00:00:00+00:00",
            "colour": "colour:U",
            "macro": "macro:combo",
            "engineTags": ["engine:deck"],
            "engineTagLabels": {"engine:deck": "Deck"},
            "primaryTag": "engine:deck",
            "primaryTagWeights": {"engine:deck": 100},
        }
        for deck_id, event, year, _ in decks
    ]
    (snap / "decks.json").write_text(json.dumps(deck_records))
    (snap / "cards_index.json").write_text(json.dumps({
        "v": 1,
        "cards": [{
            "canon": "card:bolt", "name": "Lightning Bolt", "type": "Instant",
            "manaValue": 1.0, "reserved": False, "points": 0,
            "pointsCompanion": 0,
        }],
        "decks": {
            deck_id: {
                "m": [0] if bolt_board == "m" else [],
                "s": [0] if bolt_board == "s" else [],
            }
            for deck_id, _, _, bolt_board in decks
        },
    }))
    return snap


def _adoption_graph(root, built_graph):
    """A built graph tracing one card's adoption across three years.

    2023 (thin): 4 decks, 1 runs Bolt. 2024: 5 decks, none run it. 2025 (fat): 10
    decks, 6 run it. So Bolt enters as a fringe card (1/4), sits out a year (0/5),
    then climbs (6/10), with the year bases varying so a raw count could mislead.
    """
    decks = (
        [(f"a23-{i}", "E2023", 2023, "m" if i == 0 else None) for i in range(4)]
        + [(f"a24-{i}", "E2024", 2024, None) for i in range(5)]
        + [(f"a25-{i}", "E2025", 2025, "m" if i < 6 else None) for i in range(10)]
    )
    return built_graph(root, _write_adoption_snapshot(root, decks))


def test_card_adoption_returns_per_year_count_share_and_base(tmp_path, built_graph):
    conn = _adoption_graph(tmp_path, built_graph)
    series = card_adoption_over_time(conn, "card:bolt")
    by_year = {c.year: c for c in series.cells}

    # A fringe early count is returned as itself with its year base, not zeroed or
    # suppressed: 1 of 4 decks in the thin 2023, a share of that year's total.
    assert by_year[2023].count == 1
    assert by_year[2023].year_total == 4
    assert by_year[2023].share == pytest.approx(1 / 4)

    # A year the card sits out is still present, count 0 against its base, so the
    # timeline shows the card entering rather than a year silently missing.
    assert by_year[2024].count == 0
    assert by_year[2024].year_total == 5
    assert by_year[2024].share == pytest.approx(0.0)

    # The fat year: same card, a bigger base, so the share (not the raw count) is
    # what makes 6/10 comparable to 1/4.
    assert by_year[2025].count == 6
    assert by_year[2025].year_total == 10
    assert by_year[2025].share == pytest.approx(6 / 10)

    # No cell's share is ever withheld: adoption is a direct observation, not an
    # aggregate that carries a floor (ADR 0013).
    assert all(c.share is not None for c in series.cells)


def test_run_series_routes_card_adoption_through_its_own_seam(tmp_path, built_graph):
    conn = _adoption_graph(tmp_path, built_graph)
    routed = run_series(conn, CardAdoptionOverTime("card:bolt"))
    direct = card_adoption_over_time(conn, "card:bolt")
    assert isinstance(routed, Series)
    assert routed.cells == direct.cells


def test_card_adoption_board_filter_scopes_the_count_not_the_base(tmp_path, built_graph):
    # 2025: 2 decks run Bolt maindeck, 1 runs it in the side, 1 runs it nowhere.
    decks = [
        ("m1", "E", 2025, "m"), ("m2", "E", 2025, "m"),
        ("s1", "E", 2025, "s"),
        ("n1", "E", 2025, None),
    ]
    conn = built_graph(tmp_path, _write_adoption_snapshot(tmp_path, decks))

    def cell(board):
        (only,) = card_adoption_over_time(conn, "card:bolt", board).cells
        return only

    # Default counts a deck running the card in either board (3 of 4); the board
    # filter narrows the numerator, never the year base, which stays every deck.
    either, main, side = cell(None), cell("Main"), cell("Side")
    assert (either.count, either.year_total) == (3, 4)
    assert (main.count, main.year_total) == (2, 4)
    assert (side.count, side.year_total) == (1, 4)


def test_the_pooled_spread_is_taken_within_records_and_skips_the_lone_ones():
    # The interval's whole input (issue #175): how far one finish bounces from its own
    # subject's level, pooled over the field rather than read off the point's own two
    # or three finishes. Worked by hand: [.2,.4,.6] deviates -.2/0/+.2 (sum of squares
    # .08 over 2 degrees of freedom) and [.5,.7] deviates -.1/+.1 (.02 over 1), so the
    # pooled variance is .10/3 and the sd its root. The lone record carries no spread
    # of its own, so it contributes neither to the sum nor to the degrees of freedom.
    assert _pooled_sd([[0.2, 0.4, 0.6], [0.5, 0.7], [0.9]]) == pytest.approx(0.18257, rel=1e-4)
    # A field where nothing has been observed twice cannot say how much one
    # observation bounces, so there is no spread to report rather than a zero one.
    assert _pooled_sd([[0.4], [0.6]]) is None


def test_the_pooled_spread_does_not_move_with_the_order_the_rows_arrive_in():
    # Nothing here is resampled, so the interval is deterministic by construction, with
    # one hazard left: Ladybug hands the same query's rows back in different orders
    # between calls and float addition is not associative, so a record summed in row
    # order lands a few bits apart run to run. These six finishes are one such record
    # (0.24277490833333337 forwards, ...34 back), which is enough to move a bound in the
    # last decimals and break "a moved number means moved evidence" (issue #175).
    record = [0.3238, 0.1508, 0.6509, 0.0724, 0.5359, 0.3657]
    assert _pooled_sd([record]) == _pooled_sd([list(reversed(record))])


def test_the_interval_narrows_with_the_sample_and_never_leaves_the_scale():
    # 1.645 * sd / sqrt(n) either side of the mean: at a spread of 0.2, four finishes
    # carry a half-width of 0.1645 and sixteen carry half of that, so a thin point is
    # visibly less settled than a thick one rather than sitting on the same dot.
    assert _interval(0.4, 4, 0.2) == pytest.approx((0.2355, 0.5645))
    assert _interval(0.4, 16, 0.2) == pytest.approx((0.3178, 0.4823), rel=1e-3)
    # A finish is a normalised rank, so no bound can sit outside the scale: a mean of
    # 0.1 on two finishes has a half-width of 0.349, and the low end is a win, not
    # better than one.
    assert _interval(0.1, 2, 0.3) == pytest.approx((0.0, 0.4490), abs=1e-4)
    # No spread to divide, or nothing to divide it over: no interval, rather than one
    # of width zero, which would draw as a settled point.
    assert _interval(0.4, 3, None) is None
    assert _interval(0.4, 0, 0.2) is None


def test_a_short_unanimous_run_is_not_a_lead_a_coin_could_not_produce():
    # The gate is a sign test, and the normal approximation behind it is optimistic on
    # exactly the counts this surface prints most: MIN_ARCHETYPE_EVENTS is 2, so "3 of 3"
    # is drawable, and a fair coin produces a run that lopsided a quarter of the time.
    # Uncorrected, the gate certified it as a lead, which is the overclaim the gate was
    # added to remove, so the half-step continuity correction is load-bearing rather
    # than a refinement (#175).
    assert not beats_a_coin(3, 3)   # exact two-sided p = 0.25
    assert not beats_a_coin(4, 4)   # 0.125
    assert not beats_a_coin(7, 9)   # 0.18
    assert not beats_a_coin(8, 10)  # 0.109
    # A run a coin really does not produce still clears, or the gate would hedge
    # everything and say nothing.
    assert beats_a_coin(13, 14)
    assert beats_a_coin(0, 8)  # lopsided the other way, and the test is two-sided
    # An even split is the null itself.
    assert not beats_a_coin(5, 10)


def _cover_fields(deck_records: list[dict]) -> list[dict]:
    """Pad every event with filler finishes until its declared field is on record.

    An event holding one finish against a field of 500 is a published bracket, and the
    surfaces that read a level off a finish now refuse to score one
    (:data:`trends.MIN_FIELD_COVERAGE`, ADR 0021, ADR 0022). A fixture that declares
    such an event is asserting on a case it does not mean to be testing, so the field it
    declares is filled in rather than the rule being weakened to admit it.

    Each filler is a **one-off** pilot with a single mid-table finish, so it covers the
    field and clears no gate: at one event and one finish apiece, no filler reaches a
    contender pool (:data:`trends.MIN_CAREER_MAJORS`), a year of the performance chart
    (:data:`trends.MIN_PILOT_YEAR_EVENTS`) or a spread fit
    (:data:`trends.MIN_SPREAD_FINISHES`). What the fixtures assert is therefore
    untouched, which is the point: the padding exists to make the events real, not to
    take part in any measurement.

    A filler carries no **primary** archetype either, since it copies the event's first
    deck and would otherwise join whichever engine that deck plays and pad the very
    count under test. Every archetype surface reads the primary tag alone, so an
    unprimaried filler is a deck of its event and of its year, and a member of no
    archetype: the one thing the padding is allowed to move is a share's denominator.
    """
    padded = list(deck_records)
    held: dict[str, list[dict]] = {}
    for deck in deck_records:
        held.setdefault(deck["event"], []).append(deck)
    for event, decks in held.items():
        template = decks[0]
        ranked = sum(1 for d in decks if d["placementNorm"] is not None)
        # Comfortably clear of the coverage line rather than exactly on it, so a fixture
        # never turns on which side of `>=` the threshold is read at.
        for i in range(math.ceil(template["eventSize"] * 0.6) - ranked):
            padded.append({**template,
                           "deckId": f"filler-{event}-{i}",
                           "name": f"{_shared_win(template['eventSize'])} filler-{event}-{i} - Deck - {event}",
                           "pilot": f"filler-{event}-{i}",
                           "placement": 1,
                           "placementNorm": 0.5,
                           "primaryTag": "engine:__none__"})
    return padded


def _write_performance_snapshot(
    root: Path, decks: list[tuple[str, str, str, int, float | None]]
) -> Path:
    """Write a snapshot of ``(deck_id, pilot, event, year, placement_norm)`` decks.

    A pilot's per-year performance is the mean of their decks' ``placementNorm``,
    so a test needs several decks under **one** pilot in **one** year. Each deck is
    given a distinct event, since a pilot holds at most one deck per event (ADR
    0004) and two decks sharing a pilot, event and identical (empty) list would be
    dropped as one duplicate registration; distinct events in the same calendar
    year land on the same ``Year`` node without tripping that. A ``None``
    ``placement_norm`` is an unranked deck, which the mean is not taken over.

    Decks sharing a ``pilot`` key resolve to one ``Pilot`` node (that is the point);
    distinct keys stay distinct pilots as long as their recovered names differ, so
    the identical-name join (ADR 0007) does not fold them together.
    """
    snap = root / "snap"
    snap.mkdir()
    deck_records = []
    for deck_id, pilot, event, year, norm in decks:
        deck_records.append({
            "deckId": deck_id,
            # A ``norm`` of ``None`` is a deck the source scored nothing for, so its
            # title opens with no placement and neither does the deck: the build mints
            # a norm from any placement it can see, so a placement beside a null norm
            # is no longer an unranked deck anywhere in the record (issue #162).
            "name": (f"{_shared_win(_COVERED_FIELD)} {pilot} - Deck - {event}" if norm is not None
                     else f"{pilot} - Deck - {event}"),
            "deckName": "Deck",
            "pilot": pilot,
            "event": event,
            "eventId": f"evt_{event}",
            "eventType": "Tournament",
            "eventSize": _COVERED_FIELD,
            "placement": 1 if norm is not None else None,
            "placementNorm": norm,
            "createdAt": f"{year}-06-01T00:00:00+00:00",
            "colour": "colour:U",
            "macro": "macro:combo",
            "engineTags": ["engine:deck"],
            "engineTagLabels": {"engine:deck": "Deck"},
            "primaryTag": "engine:deck",
            "primaryTagWeights": {"engine:deck": 100},
        })
    deck_records = _cover_fields(deck_records)
    (snap / "decks.json").write_text(json.dumps(deck_records))
    (snap / "cards_index.json").write_text(json.dumps({
        "v": 1,
        "cards": [],
        "decks": {d["deckId"]: {"m": [], "s": []} for d in deck_records},
    }))
    return snap


def _performance_graph(root, built_graph):
    """A built graph with one multi-year pilot and one single-year pilot.

    ``ada`` (multi-year, qualifying): 2024 has 3 events (norms .2/.4/.6, mean .4),
    2025 has 4 events (norms .1/.1/.3/.3, mean .2). Both years clear the floor of 2
    events, so she has a real two-point trajectory that improves.

    ``bo`` (single-year): only 2025, so even with 2 events there he never reaches
    two qualifying years: the "not enough history" case.
    """
    norms = [0.2, 0.4, 0.6]
    decks = [(f"ada24-{i}", "ada", f"A24E{i}", 2024, norms[i]) for i in range(3)]
    decks += [(f"ada25-{i}", "ada", f"A25E{i}", 2025, [0.1, 0.1, 0.3, 0.3][i]) for i in range(4)]
    decks += [(f"bo25-{i}", "bo", f"B25E{i}", 2025, 0.5) for i in range(2)]
    return built_graph(root, _write_performance_snapshot(root, decks))


def _publish_only_a_bracket(snapshot: Path, event: str) -> None:
    """Strip ``event`` back to the finishes its real pilots recorded.

    The filler decks :func:`_cover_fields` added keep their lists and lose their
    placements, which is what an event that published a top-8 bracket looks like in the
    source: the field registered, and only the cut has a finish on record.
    """
    decks = json.loads((snapshot / "decks.json").read_text())
    for deck in decks:
        if deck["event"] == event and deck["deckId"].startswith("filler-"):
            deck["name"] = deck["name"].removeprefix(f"{_shared_win(deck['eventSize'])} ")
            deck["placement"], deck["placementNorm"] = None, None
    (snapshot / "decks.json").write_text(json.dumps(decks))


def _declare_teams(snapshot: Path, event: str, teams: int) -> None:
    """Restate ``event`` as a Teams event whose field is ``teams`` team slots.

    Teammates share one placement slot and ``eventSize`` counts teams rather than
    decks (TMCTeams25 declares 39 against 117 decks), so the declared field stands:
    the build corrects ``Tournament`` alone, by whitelist (issue #140). The decks
    keep the placements and norms the fixture stated, which it computes against the
    team-denominated field.
    """
    decks = json.loads((snapshot / "decks.json").read_text())
    for deck in decks:
        if deck["event"] == event:
            deck["eventType"], deck["eventSize"] = "Teams", teams
    (snapshot / "decks.json").write_text(json.dumps(decks))


def test_a_year_scores_only_the_events_that_published_a_field_not_a_bracket(
    tmp_path, built_graph
):
    # ADR 0021. Some events publish a top-8 bracket instead of standings, and at those
    # the only finishes on record are good ones: everyone who attended and busted is
    # absent, so a mean over them is a mean over results selected on their own answer.
    # Measured on the artifact it is worth 0.91 against a field mean of 0.507, and it
    # moved 83 of the 602 year points it still draws, one of them by 0.32.
    root = tmp_path / "bracket"
    root.mkdir()
    decks = [(f"ada24-{i}", "ada", f"A24E{i}", 2024, [0.2, 0.4, 0.6][i]) for i in range(3)]
    decks += [(f"ada25-{i}", "ada", f"A25E{i}", 2025, 0.3) for i in range(2)]
    snapshot = _write_performance_snapshot(root, decks)
    # Ada won A24E0 outright. It is also the one event of the three whose field never
    # reached the record, so the 0.2 it is worth to her is withheld and her 2024 is the
    # mean of the two that stand.
    _publish_only_a_bracket(snapshot, "A24E0")
    conn = built_graph(root, snapshot)

    by_year = {c.year: c for c in pilot_performance_over_time(conn, "ada").cells}

    assert by_year[2024].events == 2
    assert by_year[2024].mean_norm == pytest.approx(0.5)  # 0.4 and 0.6, not 0.2
    # The year is still hers and still on the axis: what the rule drops is the finish,
    # never the year, the same way a thin year keeps its place and withholds its mean.
    assert by_year[2024].mean_norm is not None
    assert [c.year for c in pilot_performance_over_time(conn, "ada").cells] == [2024, 2025]


def test_pilot_performance_returns_per_year_mean_and_n_for_qualifying_years(
    tmp_path, built_graph
):
    conn = _performance_graph(tmp_path, built_graph)
    series = pilot_performance_over_time(conn, "ada")
    by_year = {c.year: c for c in series.cells}

    # Two qualifying years, sorted, each the honest mean of that year's ranked decks
    # with its event count alongside so the reader has the sample size in hand.
    assert [c.year for c in series.cells] == [2024, 2025]
    assert by_year[2024].events == 3
    assert by_year[2024].mean_norm == pytest.approx(0.4)
    assert by_year[2025].events == 4
    assert by_year[2025].mean_norm == pytest.approx(0.2)


def test_every_drawn_year_carries_the_interval_its_own_sample_earns(
    tmp_path, built_graph
):
    # The finding #175 exists for: a year's mean rests on a median of three events, and
    # the surface presented it as a value. Every drawn point now carries the 90%
    # interval on that mean, taken from the field's own within-pilot spread over the
    # root of the year's events, so a two-event year is visibly less settled than a
    # ten-event one and the two overlap where the evidence does not separate them.
    #
    # ``twin``'s 2025 is the case that rejects the alternative: its two finishes are
    # identical, so resampling them, the method this replaces, returns an interval of
    # width zero and calls a mean of two results settled. Held below five career
    # finishes, ``twin`` is not one of the records the spread is fitted over.
    decks = [(f"ada24-{i}", "ada", f"A24E{i}", 2024, n) for i, n in enumerate([0.2, 0.4, 0.6])]
    decks += [(f"ada25-{i}", "ada", f"A25E{i}", 2025, n)
              for i, n in enumerate([0.1, 0.1, 0.3, 0.3])]
    decks += [(f"twin24-{i}", "twin", f"T24E{i}", 2024, 0.5) for i in range(2)]
    decks += [(f"twin25-{i}", "twin", f"T25E{i}", 2025, 0.5) for i in range(2)]
    conn = built_graph(tmp_path, _write_performance_snapshot(tmp_path, decks))

    # ``ada`` is the whole fit: her seven finishes deviate from her 0.2857 career mean
    # by a pooled sd of 0.1773, so two events earn 1.645 * 0.1773 / sqrt(2) = 0.206
    # either side of the mean.
    twin = {c.year: c for c in pilot_performance_over_time(conn, "twin").cells}[2025]
    assert twin.mean_norm == pytest.approx(0.5)
    assert (twin.mean_low, twin.mean_high) == pytest.approx((0.294, 0.706), abs=0.002)

    # The same spread over more events is a narrower claim, which is the reading the
    # chart could not make before: ``ada``'s four-event year is tighter than her
    # three-event one, and both are still wide enough to overlap.
    ada = {c.year: c for c in pilot_performance_over_time(conn, "ada").cells}
    assert ada[2025].mean_high - ada[2025].mean_low < ada[2024].mean_high - ada[2024].mean_low
    assert ada[2025].mean_high > ada[2024].mean_low


def test_pilot_performance_of_a_single_year_pilot_is_not_enough_history(
    tmp_path, built_graph
):
    # ``bo`` has decks in only one year, so he never reaches two qualifying years:
    # the tool refuses by name rather than drawing a lone point on an empty line
    # (ADR 0013), and says how many qualifying years it did find (issue #101).
    conn = _performance_graph(tmp_path, built_graph)
    with pytest.raises(NotEnoughHistory) as refusal:
        pilot_performance_over_time(conn, "bo")
    assert refusal.value.found == 1


def test_pilot_performance_refuses_a_thin_years_mean_but_still_states_the_year(
    tmp_path, built_graph
):
    # ``cy`` has two fat years (2024, 2025) and a thin 2023 of a single event. 2025
    # also carries an unranked deck (a null placementNorm the source never scored).
    decks = [(f"cy24-{i}", "cy", f"C24E{i}", 2024, 0.5) for i in range(3)]
    decks += [(f"cy25-{i}", "cy", f"C25E{i}", 2025, 0.2) for i in range(3)]
    decks += [("cy25-x", "cy", "C25EX", 2025, None)]  # unranked, not part of the mean
    decks += [("cy23-0", "cy", "C23E0", 2023, 0.9)]  # a lone event, below the floor
    conn = built_graph(tmp_path, _write_performance_snapshot(tmp_path, decks))
    by_year = {c.year: c for c in pilot_performance_over_time(conn, "cy").cells}

    # The thin 2023 is still a year ``cy`` played, so it comes back as a cell: the
    # mean is refused (None) and the single event that refused it is stated. Dropping
    # the row made "too thin to average" identical to "did not play", and since a thin
    # year is usually a pilot's first or last, that erased their arrival (issue #101).
    assert set(by_year) == {2023, 2024, 2025}
    assert by_year[2023].mean_norm is None
    assert by_year[2023].events == 1
    # A refused mean has nothing to bound, so it carries no interval either: a width
    # around a value the cell withholds would be a claim about a claim it never made.
    assert (by_year[2023].mean_low, by_year[2023].mean_high) == (None, None)
    # The floor still fires on the mean: no thin year is ever given a value.
    assert by_year[2024].mean_norm == pytest.approx(0.5)
    # 2025's unranked deck neither shifts the mean nor pads the event count: the mean
    # is 0.2 over the three ranked decks, and events counts only the ranked events.
    assert by_year[2025].events == 3
    assert by_year[2025].mean_norm == pytest.approx(0.2)


def test_a_year_the_source_never_scored_is_a_cell_of_zero_events_not_a_missing_year(
    tmp_path, built_graph
):
    # ``ez`` played two events in 2023 and neither was placed, then had two scored
    # years. Filtering the years by the same null test that filters the decks would
    # cut 2023 off the front and the chart would claim a 2024 debut, which is the
    # erasure of issue #101 surviving in a second form. The population it was measured
    # on (six drawable pilots with a wholly unscored year, in all six their first) is
    # empty since minting, so this fixture is now the only place the shape is
    # exercised; see `pilot_performance_over_time` for why it stays (issue #162).
    decks = [(f"ez23-{i}", "ez", f"E23E{i}", 2023, None) for i in range(2)]
    decks += [(f"ez24-{i}", "ez", f"E24E{i}", 2024, 0.4) for i in range(2)]
    decks += [(f"ez25-{i}", "ez", f"E25E{i}", 2025, 0.2) for i in range(2)]
    conn = built_graph(tmp_path, _write_performance_snapshot(tmp_path, decks))
    by_year = {c.year: c for c in pilot_performance_over_time(conn, "ez").cells}

    assert set(by_year) == {2023, 2024, 2025}
    # ``events`` counts the scored events the mean rests on, so zero is its honest
    # value for a year nobody recorded, and the year keeps its place on the axis.
    assert by_year[2023].mean_norm is None
    assert by_year[2023].events == 0


def test_a_thin_year_does_not_count_toward_the_two_qualifying_years(
    tmp_path, built_graph
):
    # ``dz`` played three years but averaged only one of them: 2024 and 2026 are a
    # single event each. Returning the thin years as cells must not smuggle them past
    # the two-qualifying-year rule, or "not enough history" would quietly weaken.
    decks = [("dz24-0", "dz", "D24E0", 2024, 0.4)]
    decks += [(f"dz25-{i}", "dz", f"D25E{i}", 2025, 0.3) for i in range(2)]
    decks += [("dz26-0", "dz", "D26E0", 2026, 0.7)]
    conn = built_graph(tmp_path, _write_performance_snapshot(tmp_path, decks))
    with pytest.raises(NotEnoughHistory) as refusal:
        pilot_performance_over_time(conn, "dz")
    assert refusal.value.found == 1


def test_pilots_with_history_offers_only_pilots_that_draw(tmp_path, built_graph):
    # ``ada`` clears two qualifying years; ``bo`` has only one. The catalogue offers
    # the drawable pilot and withholds the one that would return "not enough history".
    conn = _performance_graph(tmp_path, built_graph)
    offered = {key for _, key in pilots_with_history(conn)}
    assert "ada" in offered
    assert "bo" not in offered


def test_run_series_routes_pilot_performance_through_its_own_seam(tmp_path, built_graph):
    conn = _performance_graph(tmp_path, built_graph)
    routed = run_series(conn, PilotPerformanceOverTime("ada"))
    direct = pilot_performance_over_time(conn, "ada")
    assert isinstance(routed, Series)
    assert routed.cells == direct.cells


def _write_h2h_snapshot(
    root: Path,
    decks: list[tuple[str, str, str, str, int, float | None]],
    field_sizes: dict[str, int] | None = None,
) -> Path:
    """Write a snapshot of ``(deck_id, pilot, event, created_at, placement, norm)``.

    A shared event is one both pilots entered, so a head-to-head test needs two
    pilots' decks at the same event plus filler decks under other pilots to give
    the event a field size larger than the pair. Each deck carries its own
    ``createdAt``, the registration date the timeline reads; decks of one event
    stay inside one calendar year so the build does not abort on a straddle. A
    distinct pilot per deck keeps the fuzzy pilot merge and the duplicate-list
    drop (ADR 0004, 0007) from folding fixtures meant to stay separate.

    ``field_sizes`` gives an event its own ``eventSize``, which a fixture asserting
    on ``field_size`` needs: the norms here are written against a real field, and
    an event claiming :data:`_FIELD_SIZE` while its norms rank against 20 is a
    contradiction no event presents. Events left out of it claim
    :data:`_FIELD_SIZE`, which nothing in these fixtures contradicts.
    """
    field_sizes = field_sizes or {}
    snap = root / "snap"
    snap.mkdir()
    deck_records = [
        {
            "deckId": deck_id,
            # A deck with no placement carries none in its title either, which
            # is the shape the source ships them in ("Darcy - Mono R - Area52IQ").
            "name": (f"{placement}st {pilot} - Deck - {event}"
                     if placement is not None else f"{pilot} - Deck - {event}"),
            "deckName": "Deck",
            "pilot": pilot,
            "event": event,
            "eventId": f"evt_{event}",
            "eventType": "Tournament",
            "eventSize": field_sizes.get(event, _FIELD_SIZE),
            "placement": placement,
            "placementNorm": norm,
            "createdAt": created_at,
            "colour": "colour:U",
            "macro": "macro:combo",
            "engineTags": ["engine:deck"],
            "engineTagLabels": {"engine:deck": "Deck"},
            "primaryTag": "engine:deck",
            "primaryTagWeights": {"engine:deck": 100},
        }
        for deck_id, pilot, event, created_at, placement, norm in decks
    ]
    (snap / "decks.json").write_text(json.dumps(deck_records))
    (snap / "cards_index.json").write_text(json.dumps({
        "v": 1,
        "cards": [],
        "decks": {d[0]: {"m": [], "s": []} for d in decks},
    }))
    return snap


def _h2h_graph(root, built_graph):
    """A built graph where ``ann`` and ``bob`` share three events, plus fillers.

    E1 (registered 2025-03) holds 5 decks, placements 1..5 with norms
    ``(place-1)/8`` against its claimed field of 9 (above 8, so Rule B leaves it
    alone; ADR 0015 amended). ``ann`` finishes 1st (norm 0.0), ``bob`` 4th (norm
    0.375). EM (registered 2025-05) is a 3-deck field of 9 the pair also both
    entered. E2 (registered 2025-07, last) is a **top-cut** field: 20 real
    entrants but only four decks recorded, norms ``(place-1)/19``, so its field
    is 20 while the decks-at-event count is 4. ``ann`` finishes 2nd, ``bob``
    5th. E2 is what holds the tool to reading the event's own field rather than
    counting decks. ``ann`` also played a lone EA that ``bob`` did not, so it is
    never a shared event.
    """
    decks = [
        ("e1-ann", "ann", "E1", "2025-03-01T00:00:00+00:00", 1, 0.0),
        ("e1-bob", "bob", "E1", "2025-03-01T09:00:00+00:00", 4, 0.375),
        ("e1-f1", "e1f1", "E1", "2025-03-02T00:00:00+00:00", 2, 0.125),
        ("e1-f2", "e1f2", "E1", "2025-03-02T00:00:00+00:00", 3, 0.25),
        ("e1-f3", "e1f3", "E1", "2025-03-02T00:00:00+00:00", 5, 0.5),
        ("em-ann", "ann", "EM", "2025-05-01T00:00:00+00:00", 3, 0.25),
        ("em-bob", "bob", "EM", "2025-05-01T00:00:00+00:00", 1, 0.0),
        ("em-f1", "emf1", "EM", "2025-05-02T00:00:00+00:00", 2, 0.125),
        ("e2-ann", "ann", "E2", "2025-07-01T00:00:00+00:00", 2, 1 / 19),
        ("e2-bob", "bob", "E2", "2025-07-01T00:00:00+00:00", 5, 4 / 19),
        ("e2-f1", "e2f1", "E2", "2025-07-02T00:00:00+00:00", 1, 0.0),
        ("e2-f2", "e2f2", "E2", "2025-07-02T00:00:00+00:00", 10, 9 / 19),
        ("ea-ann", "ann", "EA", "2025-09-01T00:00:00+00:00", 1, 0.0),
    ]
    return built_graph(root, _write_h2h_snapshot(
        root, decks, field_sizes={"E1": 9, "EM": 9, "E2": 20}))


def test_head_to_head_returns_one_row_per_shared_event_with_both_pilots(
    tmp_path, built_graph
):
    conn = _h2h_graph(tmp_path, built_graph)
    series = head_to_head_timeline(conn, "ann", "bob")
    by_event = {c.event: c for c in series.cells}

    # The three shared events, ordered by registration date. EA was ann's alone, so
    # it is absent: a timeline is over shared events only.
    assert [c.event for c in series.cells] == ["E1", "EM", "E2"]

    # Each row carries both pilots' raw placement and norm and the event's field
    # size, so the chart can label a point with the finish while plotting the norm.
    e1 = by_event["E1"]
    assert e1.field_size == 9
    assert e1.date == datetime(2025, 3, 1, 0, 0)  # min createdAt across the field
    assert (e1.placement_a, e1.norm_a) == (1, pytest.approx(0.0))
    assert (e1.placement_b, e1.norm_b) == (4, pytest.approx(0.375))

    # The top-cut event: 4 decks recorded against a 20-entrant field, so field_size
    # reads the event's own field (20), not the deck count (4).
    e2 = by_event["E2"]
    assert e2.field_size == 20
    assert (e2.placement_a, e2.norm_a) == (2, pytest.approx(1 / 19))
    assert (e2.placement_b, e2.norm_b) == (5, pytest.approx(4 / 19))


def test_head_to_head_field_size_is_the_events_own_field_not_its_deck_count(
    tmp_path, built_graph
):
    # Area52IQ's shape, and the one case no norm can be inverted for: the only deck
    # the event ranked is its winner, whose norm is 0.0, and 0 yields no field back.
    # So the field is read off the Event node, which is where the build now stores
    # it outright (issue #162). EZ holds 3 decks against a 24-entrant field, so the
    # deck count the old recovery fell back to is a plausible wrong number rather
    # than an obvious one, and would have labelled a win "1st of 3".
    decks = [
        ("ez-ann", "ann", "EZ", "2025-03-01T00:00:00+00:00", 1, None),
        ("ez-bob", "bob", "EZ", "2025-03-01T00:00:00+00:00", None, None),
        ("ez-f1", "ezf1", "EZ", "2025-03-02T00:00:00+00:00", None, None),
        # Two more shared events, both scored on both sides, so the pair clears
        # MIN_SHARED_EVENTS on comparisons rather than on attendance: EZ ranked only
        # its winner, which bounds nothing (it is a bracket), so that meeting settles
        # nothing and is not one of the two the floor asks for.
        ("e1-ann", "ann", "E1", "2025-05-01T00:00:00+00:00", 1, 0.0),
        ("e1-bob", "bob", "E1", "2025-05-01T00:00:00+00:00", 4, 0.75),
        ("e1-f1", "e1f1", "E1", "2025-05-02T00:00:00+00:00", 5, 1.0),
        ("e2-ann", "ann", "E2", "2025-06-01T00:00:00+00:00", 1, 0.0),
        ("e2-bob", "bob", "E2", "2025-06-01T00:00:00+00:00", 3, 0.5),
        ("e2-f1", "e2f1", "E2", "2025-06-02T00:00:00+00:00", 5, 1.0),
    ]
    conn = built_graph(tmp_path, _write_h2h_snapshot(
        tmp_path, decks, field_sizes={"EZ": 24, "E1": 5, "E2": 5}))

    by_event = {c.event: c for c in head_to_head_timeline(conn, "ann", "bob").cells}

    assert by_event["EZ"].field_size == 24
    # The win draws at all because the build minted its norm: the source ranked it
    # nowhere, and every reader here gates on the norm (issue #162).
    assert (by_event["EZ"].placement_a, by_event["EZ"].norm_a) == (1, 0.0)
    # Nothing recoverable for bob at EZ, so that half of the point stays a gap.
    assert (by_event["EZ"].placement_b, by_event["EZ"].norm_b) == (None, None)


def test_head_to_head_field_size_reads_the_corrected_field_not_the_deck_count(live_graph):
    # The two events the deleted deck-count fallback used to answer for, and the
    # reason deleting it was the follow-on to #140: the fallback read Pats Birthday
    # Brawl as 8 and Area52IQ as 7 while the build had already corrected both to 24
    # (a claimed field of 8 and of 1 against 8 and 7 decks held). That disagreement
    # was harmless only while neither event drew a marker, because neither carried a
    # norm; minting their norms is what makes them draw, so Area52IQ's win would
    # have been labelled "1st of 7" (issue #162).
    #
    # DeckaDiceIQ, the third such event, is unreachable through this seam and so is
    # not pinned here: 5 pilots played it and no two of them share a second event,
    # so no pair clears MIN_SHARED_EVENTS and the tool refuses before it can return
    # the row.
    for (a, b), event in {
        ("CleverAzureFalcon", "CleverCyanStag"): "Pats Birthday Brawl",
        ("AmberAmberPanda", "BraveJadeEagle"): "Area52IQ",
    }.items():
        by_event = {c.event: c.field_size for c in head_to_head_timeline(live_graph, a, b).cells}
        assert by_event[event] == MIN_CUT_FIELD


def test_head_to_head_carries_which_of_a_points_numbers_the_project_decided(
    tmp_path, built_graph
):
    # Issue #166: the point already knows its field size and its norms, but not
    # which of them the source gave. EB claims a field of 6 with a deepest finish
    # of 4th, the top-8-cut signature Rule B corrects to MIN_CUT_FIELD, and neither
    # deck was scored, so the build mints both norms against that floor. EC is
    # untouched on both counts, so the pair reads as the contrast the disclosure
    # exists to draw.
    decks = [
        ("eb-ann", "ann", "EB", "2025-03-01T00:00:00+00:00", 1, None),
        ("eb-bob", "bob", "EB", "2025-03-01T00:00:00+00:00", 4, None),
        ("eb-f1", "ebf1", "EB", "2025-03-02T00:00:00+00:00", None, None),
        ("ec-ann", "ann", "EC", "2025-05-01T00:00:00+00:00", 2, 1 / 19),
        ("ec-bob", "bob", "EC", "2025-05-01T00:00:00+00:00", 5, 4 / 19),
        ("ec-f1", "ecf1", "EC", "2025-05-02T00:00:00+00:00", 20, 1.0),
    ]
    conn = built_graph(tmp_path, _write_h2h_snapshot(
        tmp_path, decks, field_sizes={"EB": 6, "EC": 20}))

    by_event = {c.event: c for c in head_to_head_timeline(conn, "ann", "bob").cells}

    eb = by_event["EB"]
    assert eb.field_size == MIN_CUT_FIELD
    assert eb.field_imputed == "B"
    assert (eb.norm_imputed_a, eb.norm_imputed_b) == ("minted", "minted")
    # The placements are the source's own at both events: a null there is the claim
    # that the source recorded the rank, which is what the label leaves unmarked.
    assert (eb.placement_imputed_a, eb.placement_imputed_b) == (None, None)

    ec = by_event["EC"]
    assert ec.field_size == 20
    assert ec.field_imputed is None
    assert (ec.norm_imputed_a, ec.norm_imputed_b) == (None, None)
    assert (ec.placement_imputed_a, ec.placement_imputed_b) == (None, None)


def test_head_to_head_provenance_separates_the_floored_field_from_the_counted_one(
    live_graph
):
    # The two cases issue #166 was filed on, read off the record rather than a
    # fixture: Pats Birthday Brawl's 24 is Rule B's floor, a domain rule nobody
    # counted, and SSWam's 88 is the source's own entrant count. Unmarked they
    # render identically, which is the whole complaint.
    floored = {
        c.event: c
        for c in head_to_head_timeline(
            live_graph, "CleverAzureFalcon", "CleverCyanStag").cells
    }["Pats Birthday Brawl"]
    assert (floored.field_size, floored.field_imputed) == (MIN_CUT_FIELD, "B")

    counted = {
        c.event: c
        for c in head_to_head_timeline(
            live_graph, "BraveJadeEagle", "HiddenTealOtter").cells
    }["SSWam"]
    assert (counted.field_size, counted.field_imputed) == (88, None)


def test_head_to_head_field_size_is_a_top_cuts_whole_field_not_its_seven_held_decks(
    live_graph
):
    # A real top cut, where the field and the deck count are furthest apart: SSWam
    # holds 7 decks against the 88-entrant field its norms are ranked in. Pinned
    # because the alternative is not an exception but a plausible wrong number on
    # every one of the event's labels.
    held = next(rows(live_graph.execute(
        "MATCH (d:Deck)-[:PLAYED_AT]->(:Event {event: 'SSWam'}) RETURN count(d)")))[0]
    assert held == 7

    by_event = {
        c.event: c.field_size
        for c in head_to_head_timeline(live_graph, "BraveJadeEagle", "HiddenTealOtter").cells
    }
    assert by_event["SSWam"] == 88


def test_head_to_head_of_a_pair_sharing_one_event_is_refused(tmp_path, built_graph):
    # A pair needs at least two shared events or it is a dot, not a timeline, so a
    # one-event pair is refused rather than drawn as a lone point (ADR 0013). Here
    # ``ann`` and ``bob`` share only E1; E2 and E3 are ann's alone. The refusal counts
    # the meeting it found, so a pair who met once stays distinguishable from a pair
    # who never met: both used to come back as the same empty series (issue #101).
    decks = [
        ("e1-ann", "ann", "E1", "2025-03-01T00:00:00+00:00", 1, 0.0),
        ("e1-bob", "bob", "E1", "2025-03-01T00:00:00+00:00", 2, 0.2),
        ("e2-ann", "ann", "E2", "2025-07-01T00:00:00+00:00", 1, 0.0),
        ("e3-ann", "ann", "E3", "2025-09-01T00:00:00+00:00", 1, 0.0),
        # ``cal`` plays the two events bob was not at, so the pair never met at all.
        ("e2-cal", "cal", "E2", "2025-07-01T00:00:00+00:00", 3, 0.4),
        ("e3-cal", "cal", "E3", "2025-09-01T00:00:00+00:00", 3, 0.4),
    ]
    conn = built_graph(tmp_path, _write_h2h_snapshot(tmp_path, decks))
    with pytest.raises(NotEnoughHistory) as met_once:
        head_to_head_timeline(conn, "ann", "bob")
    assert met_once.value.found == 1

    with pytest.raises(NotEnoughHistory) as never_met:
        head_to_head_timeline(conn, "bob", "cal")
    assert never_met.value.found == 0


def test_head_to_head_of_a_pilot_against_themselves_is_refused(tmp_path, built_graph):
    # A pilot has no rivalry with themselves, so a == b is refused rather than drawing
    # two identical lines. Guarded in the tool (not only the app) since the tool is
    # the agent-facing seam a direct caller reaches without the UI's a != b check.
    # Not as NotEnoughHistory: a malformed question is not a thin answer, and saying
    # "no history" here would tell the caller these two never met (issue #101).
    conn = _h2h_graph(tmp_path, built_graph)
    with pytest.raises(ValueError) as refusal:
        head_to_head_timeline(conn, "ann", "ann")
    assert not isinstance(refusal.value, NotEnoughHistory)


def test_run_series_routes_head_to_head_through_its_own_seam(tmp_path, built_graph):
    conn = _h2h_graph(tmp_path, built_graph)
    routed = run_series(conn, HeadToHeadTimeline("ann", "bob"))
    direct = head_to_head_timeline(conn, "ann", "bob")
    assert isinstance(routed, Series)
    assert routed.cells == direct.cells


def _write_landscape_snapshot(
    root: Path, decks: list[tuple[str, str, str, int, float | None]]
) -> Path:
    """Write a snapshot of ``(deck_id, archetype, event, year, placement_norm)`` decks.

    The landscape needs both axes at once: several archetypes inside one year, each
    with its own decks spread over its own events, and a ``placementNorm`` per deck
    (``None`` for a deck the source never scored, which the mean is not taken over).
    Each deck names a distinct pilot, so neither the fuzzy pilot merge nor the
    one-deck-per-pilot-per-event drop (ADR 0004, 0007) folds fixtures meant to stay
    separate.

    Every event's declared field is covered by ``filler`` decks
    (:func:`_cover_fields`), because the landscape's finish axis reads only the events
    that published one (ADR 0022): uncovered, these fixtures would describe brackets and
    every dot would lose its mean. The fillers hold a year's share denominators well
    above its named decks, which is the shape of a real year anyway.
    """
    snap = root / "snap"
    snap.mkdir()
    deck_records = [
        {
            "deckId": deck_id,
            # A ``norm`` of ``None`` is a deck the source scored nothing for, so its
            # title opens with no placement and neither does the deck: the build mints
            # a norm from any placement it can see, so a placement beside a null norm
            # is no longer an unranked deck anywhere in the record (issue #162).
            "name": (f"{_shared_win(_COVERED_FIELD)} {deck_id} - {archetype} - {event}" if norm is not None
                     else f"{deck_id} - {archetype} - {event}"),
            "deckName": archetype.title(),
            "pilot": f"pilot-{deck_id}",
            "event": event,
            "eventId": f"evt_{event}",
            "eventType": "Tournament",
            "eventSize": _COVERED_FIELD,
            "placement": 1 if norm is not None else None,
            "placementNorm": norm,
            "createdAt": f"{year}-06-01T00:00:00+00:00",
            "colour": "colour:U",
            "macro": "macro:combo",
            "engineTags": [f"engine:{archetype}"],
            "engineTagLabels": {f"engine:{archetype}": archetype.title()},
            "primaryTag": f"engine:{archetype}",
            "primaryTagWeights": {f"engine:{archetype}": 100},
        }
        for deck_id, archetype, event, year, norm in decks
    ]
    deck_records = _cover_fields(deck_records)
    (snap / "decks.json").write_text(json.dumps(deck_records))
    (snap / "cards_index.json").write_text(json.dumps({
        "v": 1,
        "cards": [],
        "decks": {d["deckId"]: {"m": [], "s": []} for d in deck_records},
    }))
    return snap


def _landscape_graph(root, built_graph):
    """A built graph holding one full year beside an earlier one.

    2025 (8 named decks over two events, E25A and E25B): ``storm`` has 2 decks, one at
    each event, norms .1 and .3 (mean .2). ``grixis`` has 5 decks, four scored at E25A
    (.4/.6/.4/.6, mean .5) and one at E25B the source never scored, so its deck count
    spans both events while its mean rests on one. ``oracle`` has the single remaining
    deck, so the year clears :data:`MIN_LANDSCAPE_ARCHETYPES` with a field of three.
    2024 holds 3 ``lands`` decks, an archetype 2025 never saw.

    Each event also holds the filler decks :func:`_cover_fields` adds to put its
    declared field on record. They carry no primary archetype, so they are decks of the
    year and members of nothing: the year's totals count them and no cell does.
    """
    decks = [
        ("s1", "storm", "E25A", 2025, 0.1),
        ("s2", "storm", "E25B", 2025, 0.3),
        ("g1", "grixis", "E25A", 2025, 0.4),
        ("g2", "grixis", "E25A", 2025, 0.6),
        ("g3", "grixis", "E25A", 2025, 0.4),
        ("g4", "grixis", "E25A", 2025, 0.6),
        ("g5", "grixis", "E25B", 2025, None),
        ("o1", "oracle", "E25B", 2025, 0.5),
        ("l1", "lands", "E24", 2024, 0.5),
        ("l2", "lands", "E24", 2024, 0.5),
        ("l3", "lands", "E24", 2024, 0.5),
    ]
    return built_graph(root, _write_landscape_snapshot(root, decks))


def test_landscape_pairs_each_archetypes_share_with_its_mean_finish(
    tmp_path, built_graph
):
    conn = _landscape_graph(tmp_path, built_graph)
    cells = {c.tag: c for c in archetype_landscape(conn, 2025).cells}

    # Only the archetypes the chosen year holds: ``lands`` played 2024 alone, so it
    # is not a dot on 2025's landscape (unlike the meta-share matrix, which is
    # rectangular over every year so a line can drop to a real zero).
    assert set(cells) == {"storm", "grixis", "oracle"}

    # Share is the archetype's decks over every deck that year, the same base the
    # meta-share trend divides by, so the year's shares sum to one.
    storm, grixis = cells["storm"], cells["grixis"]
    assert (storm.n, grixis.n) == (2, 5)
    assert storm.year_total == grixis.year_total == 17  # 8 named decks and 9 fillers
    assert storm.share == pytest.approx(2 / 17)
    assert grixis.share == pytest.approx(5 / 17)

    # The finish is the mean placementNorm of the archetype's **scored** decks, left
    # raw (0 is a win), the codebase convention; the chart flips it for the eye.
    # Grixis's unscored fifth deck neither shifts the mean nor pads the event count.
    assert storm.mean_norm == pytest.approx(0.2)
    assert grixis.mean_norm == pytest.approx(0.5)

    # ``events`` is the distinct events the mean rests on, the independent trials
    # behind it: Storm's two decks sat at two events, Grixis's four scored decks at
    # one, even though its unscored fifth was at a second.
    assert (storm.events, grixis.events) == (2, 1)

    # Each cell carries the year it describes and that year's own shape, so a caption
    # can state how much of a season the landscape rests on without a second query.
    assert {c.year for c in cells.values()} == {2025}
    assert storm.year_events == 2


def test_the_landscape_reads_a_finish_only_from_events_that_published_a_field(
    tmp_path, built_graph
):
    # ADR 0022. A bracket publishes only the decks that cut, so a mean taken over it is
    # a mean over decks selected on their own answer: measured on the artifact, 69 of
    # the 140 archetype-years with 8 or more scored decks move once the brackets come
    # out, by up to 0.095, and all 69 move the same way, the archetype's finish getting
    # worse. The share axis keeps them, because the decks were genuinely played and the
    # bias there tops out at 0.14pp.
    root = tmp_path / "bracket"
    root.mkdir()
    decks = [
        ("s1", "storm", "E25A", 2025, 0.1),
        ("s2", "storm", "E25B", 2025, 0.3),
        ("g1", "grixis", "E25A", 2025, 0.4),
        ("g2", "grixis", "E25A", 2025, 0.6),
        ("g3", "grixis", "E25A", 2025, 0.4),
        ("g4", "grixis", "E25A", 2025, 0.6),
        ("o1", "oracle", "E25B", 2025, 0.5),
        # A third archetype at the covered event, so the year still has a landscape
        # once Grixis loses the only finishes it had (:data:`MIN_LANDSCAPE_ARCHETYPES`).
        ("l1", "lands", "E25B", 2025, 0.7),
    ]
    snapshot = _write_landscape_snapshot(root, decks)
    # E25A published its bracket alone: the five decks it holds a finish for are the
    # named ones, against a field of 12.
    _publish_only_a_bracket(snapshot, "E25A")
    conn = built_graph(root, snapshot)

    cells = {c.tag: c for c in archetype_landscape(conn, 2025).cells}
    storm, grixis = cells["storm"], cells["grixis"]

    # The .1 Storm took at the bracket is not a finish the landscape can read, so its
    # dot rests on the one event that published who finished where.
    assert storm.mean_norm == pytest.approx(0.3)
    assert (storm.scored, storm.events) == (1, 1)

    # And Storm is still two decks of the year's meta: the share axis counts every deck
    # the source shipped, at the bracket as anywhere else.
    assert storm.n == 2
    assert storm.year_total == 16  # 8 named decks and 8 fillers
    assert storm.share == pytest.approx(2 / 16)
    assert storm.year_events == 2

    # Grixis played the bracket and nothing else, so it is an archetype the year held
    # and knows no finish for: a share with no dot, the same shape as an archetype the
    # source never scored, rather than a mean over four results that were all wins.
    assert grixis.mean_norm is None
    assert (grixis.scored, grixis.events) == (0, 0)
    assert grixis.n == 4


def test_every_landscape_dot_carries_an_interval_widened_by_events_not_decks(
    tmp_path, built_graph
):
    # The same claim defect as the pilot chart, on the same statistic (#175): the dots
    # are means of a handful of decks and the caption reads which side of 0.5 they sit.
    # Each one now carries the 90% interval on its mean, fitted over the year's own
    # within-archetype spread: Storm (.1/.3) and Grixis (.4/.6/.4/.6) pool to an sd of
    # 0.1225 over 4 degrees of freedom (the fixture's fillers carry no primary
    # archetype, so they are no part of a within-archetype spread).
    conn = _landscape_graph(tmp_path, built_graph)
    cells = {c.tag: c for c in archetype_landscape(conn, 2025).cells}

    # Storm's two decks sat at two events, so 1.645 * 0.1225 / sqrt(2) either side.
    assert (cells["storm"].mean_low, cells["storm"].mean_high) == pytest.approx(
        (0.0575, 0.3425), abs=0.001
    )
    # Grixis has twice the decks and a **wider** interval, because all four sat at one
    # event: several decks of one archetype at one event are one trial of the field it
    # met, not four, so the events divide the spread and the decks do not.
    assert (cells["grixis"].mean_low, cells["grixis"].mean_high) == pytest.approx(
        (0.2985, 0.7015), abs=0.001
    )


def test_a_year_with_no_field_to_place_a_dot_in_is_refused_by_name(tmp_path, built_graph):
    # 2024 holds one archetype, so there is no landscape: a dot is only "niche and
    # winning" against a field, and one (or two) dots is not one. The tool refuses by
    # name and says how many it found, the way the pilot trends refuse a thin history
    # rather than drawing a lone point (ADR 0013, issue #101), so the app can tell a
    # refused year from a year that drew nothing.
    conn = _landscape_graph(tmp_path, built_graph)
    with pytest.raises(NotEnoughHistory) as refusal:
        archetype_landscape(conn, 2024)
    assert refusal.value.found == 1
    assert "2024" in str(refusal.value)

    # A year the graph holds no deck in refuses the same way rather than dividing a
    # share by a zero base.
    with pytest.raises(NotEnoughHistory) as empty:
        archetype_landscape(conn, 2019)
    assert empty.value.found == 0


def test_an_archetype_the_year_never_scored_does_not_count_toward_the_floor(
    tmp_path, built_graph
):
    # Three archetypes played 2025, but two of them were never scored, so only one
    # dot can be placed on the finish axis. The floor counts the archetypes that can
    # be plotted, not the ones that turned up, so this refuses rather than drawing a
    # "landscape" of a single dot with two names missing from it.
    decks = [
        ("s1", "storm", "E", 2025, 0.1),
        ("g1", "grixis", "E", 2025, None),
        ("l1", "lands", "E", 2025, None),
    ]
    conn = built_graph(tmp_path, _write_landscape_snapshot(tmp_path, decks))
    with pytest.raises(NotEnoughHistory) as refusal:
        archetype_landscape(conn, 2025)
    assert refusal.value.found == 1


def test_run_series_routes_the_landscape_through_its_own_seam(tmp_path, built_graph):
    conn = _landscape_graph(tmp_path, built_graph)
    routed = run_series(conn, ArchetypeLandscape(2025))
    direct = archetype_landscape(conn, 2025)
    assert isinstance(routed, Series)
    assert routed.cells == direct.cells


def _write_timeline_snapshot(
    root: Path, decks: list[tuple[str, str, str, str, float | None]]
) -> Path:
    """Write a snapshot of ``(deck_id, archetype, event, created_at, norm)`` decks.

    The archetype timeline needs several decks of one archetype at one event (a
    point is their mean, not one result) and its own ``createdAt`` per deck, since
    the point's date is the earliest registration across the event's whole field.
    Decks of one event stay inside one calendar year so the build does not abort on
    a straddle, and each deck names a distinct pilot so neither the fuzzy pilot merge
    nor the one-deck-per-pilot-per-event drop (ADR 0004, 0007) folds fixtures meant
    to stay separate.

    Every event's declared field is covered by ``filler`` decks
    (:func:`_cover_fields`), for the reason :func:`_write_landscape_snapshot` gives:
    the timeline draws no point at an event that published only a bracket (ADR 0022),
    so an uncovered fixture would describe a chart with nothing on it.

    A row may carry a sixth member, its **placement**, where the fixture is asserting on
    something that reads one. The default is the shared rank-1 band every bulk fixture
    here declares (:func:`_shared_win`), which no surface read until the tail bound did (ADR
    0024): a bound counts off the last published slot, so a fixture whose every finish
    claims an exclusive 1st describes an event that could not exist and bounds its tail
    at a near-win; the build's sole-winner guard now refuses that shape outright.
    Fixtures that assert on a bound state a placement their norm agrees with, exactly as
    ADR 0022 had these fixtures declare a field their deck count could fill.
    """
    snap = root / "snap"
    snap.mkdir()
    deck_records = [
        {
            "deckId": deck_id,
            # A ``norm`` of ``None`` is a deck the source scored nothing for, so its
            # title opens with no placement and neither does the deck: the build mints
            # a norm from any placement it can see, so a placement beside a null norm
            # is no longer an unranked deck anywhere in the record (issue #162).
            # The title states the same placement the record does, so the build's own
            # title reader cannot disagree with the column beside it.
            "name": ((f"{_shared_win(_COVERED_FIELD)} {deck_id} - {archetype} - {event}"
                      if placement == 1
                      else f"{_ordinal(placement)} {deck_id} - {archetype} - {event}")
                     if norm is not None else f"{deck_id} - {archetype} - {event}"),
            "deckName": archetype.title(),
            "pilot": f"pilot-{deck_id}",
            "event": event,
            "eventId": f"evt_{event}",
            "eventType": "Tournament",
            "eventSize": _COVERED_FIELD,
            "placement": placement if norm is not None else None,
            "placementNorm": norm,
            "createdAt": created_at,
            "colour": "colour:U",
            "macro": "macro:combo",
            "engineTags": [f"engine:{archetype}"],
            "engineTagLabels": {f"engine:{archetype}": archetype.title()},
            "primaryTag": f"engine:{archetype}",
            "primaryTagWeights": {f"engine:{archetype}": 100},
        }
        for deck_id, archetype, event, created_at, norm, placement in (
            (*row, 1)[:6] for row in decks
        )
    ]
    deck_records = _cover_fields(deck_records)
    (snap / "decks.json").write_text(json.dumps(deck_records))
    (snap / "cards_index.json").write_text(json.dumps({
        "v": 1,
        "cards": [],
        "decks": {d["deckId"]: {"m": [], "s": []} for d in deck_records},
    }))
    return snap


def _timeline_graph(root, built_graph):
    """A built graph over four events, two archetypes with an uneven attendance.

    E1 (registered from 2025-03-01, a filler registering first): ``storm`` brings two
    decks (.1 and .3, so a mean of .2) and ``jund`` one (.5). E2 (2025-05): ``storm``
    one (.6), ``jund`` two (.2 and .4, mean .3). E3 (2025-07): ``storm`` alone, so it
    is never a shared event. E4 (2025-09): both attend, but ``storm``'s only deck is
    one the source never scored, so the pair shares the event with nothing to compare.
    """
    decks = [
        ("e1-s1", "storm", "E1", "2025-03-05T00:00:00+00:00", 0.1),
        ("e1-s2", "storm", "E1", "2025-03-09T00:00:00+00:00", 0.3),
        ("e1-j1", "jund", "E1", "2025-03-12T00:00:00+00:00", 0.5),
        ("e1-f1", "lands", "E1", "2025-03-01T00:00:00+00:00", 0.9),
        ("e2-s1", "storm", "E2", "2025-05-01T00:00:00+00:00", 0.6),
        ("e2-j1", "jund", "E2", "2025-05-01T00:00:00+00:00", 0.2),
        ("e2-j2", "jund", "E2", "2025-05-02T00:00:00+00:00", 0.4),
        ("e3-s1", "storm", "E3", "2025-07-01T00:00:00+00:00", 0.4),
        ("e4-s1", "storm", "E4", "2025-09-01T00:00:00+00:00", None),
        ("e4-j1", "jund", "E4", "2025-09-01T00:00:00+00:00", 0.8),
    ]
    return built_graph(root, _write_timeline_snapshot(root, decks))


def test_one_archetypes_timeline_averages_its_decks_at_every_event_it_attended(
    tmp_path, built_graph
):
    conn = _timeline_graph(tmp_path, built_graph)
    points = archetype_timeline(conn, "storm").cells

    # Every event the archetype attended, in date order, whether or not the source
    # scored it: E4 is a real attendance with no finish, not an absence.
    assert [p.event for p in points] == ["E1", "E2", "E3", "E4"]

    # A point is the mean of that archetype's ranked decks at that event, with the
    # count of the decks behind it, since a mean of one deck and a mean of three read
    # alike on the line and only the count tells them apart.
    assert [p.mean_norm_a for p in points] == [
        pytest.approx(0.2), pytest.approx(0.6), pytest.approx(0.4), None,
    ]
    assert [p.decks_a for p in points] == [2, 1, 1, 0]

    # The date is the earliest registration across the event's **whole** field, not
    # this archetype's earliest: an event spreads over days, and both sides of a
    # shared event have to sit at one x.
    assert points[0].date == datetime(2025, 3, 1, 0, 0)

    # With one archetype there is no second side to any point.
    assert [(p.mean_norm_b, p.decks_b) for p in points] == [(None, 0)] * 4


def test_a_second_archetype_restricts_the_timeline_to_the_events_both_attended(
    tmp_path, built_graph
):
    conn = _timeline_graph(tmp_path, built_graph)
    points = archetype_timeline(conn, "storm", "jund").cells

    # E3 was Storm's alone, so adding Jund drops it: every drawn point now has a
    # counterpart, which is what makes the band between the two lines continuous.
    # The restriction visibly reshapes Storm's line, and the surface says so.
    assert [p.event for p in points] == ["E1", "E2", "E4"]

    # Each point carries both sides' mean and the decks behind it, so the hover can
    # state the sample per side rather than only the value.
    assert [(p.mean_norm_a, p.decks_a) for p in points] == [
        (pytest.approx(0.2), 2), (pytest.approx(0.6), 1), (None, 0),
    ]
    assert [(p.mean_norm_b, p.decks_b) for p in points] == [
        (pytest.approx(0.5), 1), (pytest.approx(0.3), 2), (pytest.approx(0.8), 1),
    ]

    # E4 is a shared attendance the source scored on one side only: kept as a point
    # both lines break over, not dropped and not half-drawn against nothing.
    assert points[-1].date == datetime(2025, 9, 1, 0, 0)


def test_a_shared_event_that_scored_one_side_bounds_the_other_rather_than_leaving_a_hole(
    tmp_path, built_graph
):
    # ADR 0024. An event that published part of its field leaves the decks it left out
    # in the slots below the last published one, so their finish is not unknown, it is
    # bounded. E4 publishes down to 9th of a field of 12, so Storm, scored at none of
    # it, finished 10th at best.
    root = tmp_path / "bounded"
    root.mkdir()
    decks = [
        ("e1-s1", "storm", "E1", "2025-03-01T00:00:00+00:00", 0.0, 1),
        ("e1-j1", "jund", "E1", "2025-03-01T00:00:00+00:00", 1 / 11, 2),
        ("e2-s1", "storm", "E2", "2025-05-01T00:00:00+00:00", 0.0, 1),
        ("e2-j1", "jund", "E2", "2025-05-01T00:00:00+00:00", 1 / 11, 2),
        ("e4-s1", "storm", "E4", "2025-09-01T00:00:00+00:00", None, None),
        ("e4-j1", "jund", "E4", "2025-09-01T00:00:00+00:00", 2 / 11, 3),
        # The worst finish E4 published, and so the slot the bound counts off from.
        ("e4-x1", "lands", "E4", "2025-09-01T00:00:00+00:00", 8 / 11, 9),
    ]
    conn = built_graph(root, _write_timeline_snapshot(root, decks))

    (*_, e4) = archetype_timeline(conn, "storm", "jund").cells

    # Derived from the record, not read back off the code: the last slot E4 published
    # is 9th, so the first it did not is 10th, and 10th of 12 is (10 - 1) / (12 - 1).
    assert e4.mean_norm_a is None
    assert e4.bound_a == pytest.approx(9 / 11)

    # The side that was scored takes no bound: it has a finish, and a bound beside it
    # would be a second answer to a question already answered.
    assert e4.mean_norm_b == pytest.approx(2 / 11)
    assert e4.bound_b is None

    # The bound is worse than every finish the event published, which is what makes it
    # safe to draw: it is the first slot below the last published one, so a mean over
    # published finishes can never sit below it and the bounded side always loses.
    assert e4.bound_a > 8 / 11 > e4.mean_norm_b


def test_the_bound_counts_slots_filled_rather_than_the_worst_label_when_the_tail_ties(
    tmp_path, built_graph
):
    # A tie band is recorded at its best end (ADR 0014), so the worst label an event
    # published can name a slot well above the last one it filled. E2 places eight decks
    # and its last four share 5th, so they fill slots 5 to 8 and the tail starts at 9th.
    # Counting off the label alone would bound at 6th and draw every unpublished deck
    # three slots better than the record allows, which is the direction that can show
    # the wrong winner. The count of placed decks is the second floor, binding here.
    root = tmp_path / "tied"
    root.mkdir()
    decks = [
        ("e1-s1", "storm", "E1", "2025-03-01T00:00:00+00:00", 0.0, 1),
        ("e1-j1", "jund", "E1", "2025-03-01T00:00:00+00:00", 1 / 11, 2),
        ("e2-s1", "storm", "E2", "2025-05-01T00:00:00+00:00", None, None),
        ("e2-j1", "jund", "E2", "2025-05-01T00:00:00+00:00", 0.0, 1),
        ("e2-x1", "lands", "E2", "2025-05-01T00:00:00+00:00", 1 / 11, 2),
        ("e2-x2", "lands", "E2", "2025-05-01T00:00:00+00:00", 2 / 11, 3),
        ("e2-x3", "lands", "E2", "2025-05-01T00:00:00+00:00", 3 / 11, 4),
        # The tail, four decks deep on one label: 5th, 5th, 5th, 5th fills 5, 6, 7, 8.
        ("e2-x4", "lands", "E2", "2025-05-01T00:00:00+00:00", 4 / 11, 5),
        ("e2-x5", "lands", "E2", "2025-05-01T00:00:00+00:00", 4 / 11, 5),
        ("e2-x6", "lands", "E2", "2025-05-01T00:00:00+00:00", 4 / 11, 5),
        ("e2-x7", "lands", "E2", "2025-05-01T00:00:00+00:00", 4 / 11, 5),
    ]
    conn = built_graph(root, _write_timeline_snapshot(root, decks))

    # Derived from the record: eight decks are placed, so the first slot none of them
    # holds is the 9th, and 9th of 12 is (9 - 1) / (12 - 1). The worst label is 5, which
    # counted alone would have said (6 - 1) / 11.
    assert _tail_bounds(conn, _cut_only_events(conn))["E2"] == pytest.approx(8 / 11)
    assert _tail_bounds(conn, _cut_only_events(conn))["E2"] > 5 / 11


def test_a_solo_timeline_carries_no_bound_for_the_side_it_does_not_have(
    tmp_path, built_graph
):
    # ADR 0024 keeps the solo line out of scope, and this is where that holds. On a solo
    # series `mean_b` is None at every point because there is no second archetype, not
    # because a side went unscored, so bounding "the side with no mean" put a bound on
    # every point of every solo line. No figure draws it (there is no b trace), but
    # `_has_bounded_point` still read it, and 12 of the 121 solo timelines offered the
    # caret's legend under a plot carrying no caret.
    root = tmp_path / "solo"
    root.mkdir()
    decks = [
        ("e1-s1", "storm", "E1", "2025-03-01T00:00:00+00:00", 0.0, 1),
        ("e1-j1", "jund", "E1", "2025-03-01T00:00:00+00:00", 1 / 11, 2),
        ("e2-s1", "storm", "E2", "2025-05-01T00:00:00+00:00", 2 / 11, 3),
        ("e2-j1", "jund", "E2", "2025-05-01T00:00:00+00:00", 3 / 11, 4),
        # The event that bounds: storm attended and was never scored at it.
        ("e3-s1", "storm", "E3", "2025-07-01T00:00:00+00:00", None, None),
        ("e3-j1", "jund", "E3", "2025-07-01T00:00:00+00:00", 2 / 11, 3),
        ("e3-x1", "lands", "E3", "2025-07-01T00:00:00+00:00", 8 / 11, 9),
    ]
    conn = built_graph(root, _write_timeline_snapshot(root, decks))

    solo = archetype_timeline(conn, "storm").cells

    # The event really does bound: asked as a pair, storm's missing finish takes one.
    (*_, paired_e3) = archetype_timeline(conn, "storm", "jund").cells
    assert paired_e3.bound_a == pytest.approx(9 / 11)
    # Alone, the b side takes none: it is not a side, so "the side with no mean" must
    # not reach it. The a side keeps its own bound, which is carried and simply never
    # drawn, since a caret needs a counterpart to be compared against.
    assert [p.bound_b for p in solo] == [None] * len(solo)
    assert solo[-1].event == "E3" and solo[-1].mean_norm_a is None


def test_an_event_that_published_too_little_bounds_nothing_and_keeps_its_break(
    tmp_path, built_graph
):
    # ADR 0024's gate. A bracket publishes so little that the bound stops meaning
    # anything: an event that published one placement of 12 bounds its unscored decks
    # only by "not the winner", which would draw them near the top of the axis. The
    # cut is the one `_cut_only_events` already makes, so a bracket yields no bound.
    root = tmp_path / "gated"
    root.mkdir()
    decks = [
        ("e1-s1", "storm", "E1", "2025-03-01T00:00:00+00:00", 0.0, 1),
        ("e1-j1", "jund", "E1", "2025-03-01T00:00:00+00:00", 1 / 11, 2),
        ("e2-s1", "storm", "E2", "2025-05-01T00:00:00+00:00", 2 / 11, 3),
        ("e2-j1", "jund", "E2", "2025-05-01T00:00:00+00:00", 3 / 11, 4),
        ("e3-s1", "storm", "E3", "2025-07-01T00:00:00+00:00", 0.0, 1),
        ("e3-j1", "jund", "E3", "2025-07-01T00:00:00+00:00", 1 / 11, 2),
    ]
    snapshot = _write_timeline_snapshot(root, decks)
    _publish_only_a_bracket(snapshot, "E2")
    conn = built_graph(root, snapshot)

    assert _tail_bounds(conn, _cut_only_events(conn)).get("E2") is None
    # And the whole event still leaves the timeline, as ADR 0022 has it: a bound is not
    # a way back in for an event whose published finishes are the ones that cut.
    assert [c.event for c in archetype_timeline(conn, "storm", "jund").cells] == [
        "E1", "E3",
    ]


def test_a_teams_event_measures_its_coverage_in_slots_and_not_in_decks(
    tmp_path, built_graph
):
    # Issue #215. At a Teams event teammates share one placement slot and the field
    # counts teams, so N ranked decks reach roughly N/3 slots. A field of 24 teams
    # publishing its top 5 slots as 15 decks has 5/24 of its field on record, a
    # bracket, yet counted in decks it clears the coverage gate (15 >= 12) and mints
    # a bound of 15/23, claiming 16th of 24 at best where the record allows 6th.
    # Measured in slots it is cut-only: no bound, and the event leaves the timeline.
    root = tmp_path / "teams"
    root.mkdir()
    decks = [
        ("e1-s1", "storm", "E1", "2025-03-01T00:00:00+00:00", 0.0, 1),
        ("e1-j1", "jund", "E1", "2025-03-01T00:00:00+00:00", 1 / 11, 2),
        # The Teams event: slots 1..5, three teammates apiece, plus storm unscored.
        ("e2-s1", "storm", "E2", "2025-05-01T00:00:00+00:00", None, None),
        ("e2-j1", "jund", "E2", "2025-05-01T00:00:00+00:00", 1 / 23, 2),
        *(
            (f"e2-x{i}", "lands", "E2", "2025-05-01T00:00:00+00:00",
             (slot - 1) / 23, slot)
            for i, slot in enumerate([1, 1, 1, 2, 2, 3, 3, 3, 4, 4, 4, 5, 5, 5])
        ),
        ("e3-s1", "storm", "E3", "2025-07-01T00:00:00+00:00", 0.0, 1),
        ("e3-j1", "jund", "E3", "2025-07-01T00:00:00+00:00", 1 / 11, 2),
    ]
    snapshot = _write_timeline_snapshot(root, decks)
    _declare_teams(snapshot, "E2", teams=24)
    conn = built_graph(root, snapshot)

    assert "E2" in _cut_only_events(conn)
    assert _tail_bounds(conn, _cut_only_events(conn)).get("E2") is None
    assert [c.event for c in archetype_timeline(conn, "storm", "jund").cells] == [
        "E1", "E3",
    ]


def test_the_bound_at_a_teams_event_counts_the_slots_its_decks_share(
    tmp_path, built_graph
):
    # Issue #215's other half. A Teams event that did publish enough of its field
    # still deserves a bound, and in slot units: 8 teams publishing slots 1..5 as
    # 15 decks covers 5/8 of the field, and a deck it never scored finished 6th of
    # 8 at best. Counted in decks the tail floor lands at 15, past the field's
    # last slot, and the event silently bounds nothing where ADR 0024 says it must.
    root = tmp_path / "teamsbound"
    root.mkdir()
    decks = [
        ("e1-s1", "storm", "E1", "2025-03-01T00:00:00+00:00", 0.0, 1),
        ("e1-j1", "jund", "E1", "2025-03-01T00:00:00+00:00", 1 / 11, 2),
        ("e2-s1", "storm", "E2", "2025-05-01T00:00:00+00:00", 0.0, 1),
        ("e2-j1", "jund", "E2", "2025-05-01T00:00:00+00:00", 1 / 11, 2),
        # The Teams event: slots 1..5, three teammates apiece, plus storm unscored.
        ("e3-s1", "storm", "E3", "2025-07-01T00:00:00+00:00", None, None),
        ("e3-j1", "jund", "E3", "2025-07-01T00:00:00+00:00", 1 / 7, 2),
        *(
            (f"e3-x{i}", "lands", "E3", "2025-07-01T00:00:00+00:00",
             (slot - 1) / 7, slot)
            for i, slot in enumerate([1, 1, 1, 2, 2, 3, 3, 3, 4, 4, 4, 5, 5, 5])
        ),
    ]
    snapshot = _write_timeline_snapshot(root, decks)
    _declare_teams(snapshot, "E3", teams=8)
    conn = built_graph(root, snapshot)

    # Derived from the record, not read back off the code: the last team slot E3
    # published is 5th, so the first it did not is 6th, and 6th of 8 is
    # (6 - 1) / (8 - 1). Five slots of eight is enough field to count from.
    assert "E3" not in _cut_only_events(conn)
    assert _tail_bounds(conn, _cut_only_events(conn))["E3"] == pytest.approx(5 / 7)

    # And the pair chart draws storm's side at it, against jund's real finish.
    (*_, e3) = archetype_timeline(conn, "storm", "jund").cells
    assert e3.mean_norm_a is None
    assert e3.bound_a == pytest.approx(5 / 7)
    assert e3.mean_norm_b == pytest.approx(1 / 7)


def test_the_timeline_draws_no_point_at_an_event_that_published_only_a_bracket(
    tmp_path, built_graph
):
    # ADR 0022. Every point here is a per-event mean, and at a bracket that mean is
    # taken over the decks that cut, so the event is worth no point at all. Not a
    # break either: a break says the archetype turned up and the source scored none of
    # it, and this event scored plenty, none of it readable.
    root = tmp_path / "bracket"
    root.mkdir()
    decks = [
        ("e1-s1", "storm", "E1", "2025-03-01T00:00:00+00:00", 0.5),
        ("e2-s1", "storm", "E2", "2025-05-01T00:00:00+00:00", 0.1),
        ("e3-s1", "storm", "E3", "2025-07-01T00:00:00+00:00", 0.4),
    ]
    snapshot = _write_timeline_snapshot(root, decks)
    _publish_only_a_bracket(snapshot, "E2")
    conn = built_graph(root, snapshot)

    points = archetype_timeline(conn, "storm").cells

    assert [p.event for p in points] == ["E1", "E3"]
    assert [p.mean_norm_a for p in points] == [pytest.approx(0.5), pytest.approx(0.4)]


def test_an_archetype_scored_at_one_event_is_refused_rather_than_drawn_as_a_dot(
    tmp_path, built_graph
):
    # ``lands`` turned up once, so there is no line to draw: one point is not a
    # history. Refused by name with the count that caused it, as the pilot trends
    # refuse a thin one (ADR 0013, issue #101), so the app can say how thin it was.
    conn = _timeline_graph(tmp_path, built_graph)
    with pytest.raises(NotEnoughHistory) as refusal:
        archetype_timeline(conn, "lands")
    assert refusal.value.found == 1
    assert "Lands" in str(refusal.value) or "lands" in str(refusal.value)


def test_a_pair_is_refused_on_the_meetings_the_record_settles(tmp_path, built_graph):
    # The pair floor counts the meetings the record settles, not the ones both attended:
    # here they turn up together twice, but at E2 the source scored neither of them, so
    # nothing bounds one against the other and the chart breaks over it. One comparison,
    # one gap, and a refusal. Counting the attendance would clear the floor on a chart
    # holding a single drawable point.
    #
    # A meeting where **one** side is scored does settle, and counts: see
    # `test_a_bounded_meeting_counts_toward_the_floor_that_admits_it`.
    decks = [
        ("e1-s1", "storm", "E1", "2025-03-01T00:00:00+00:00", 0.1),
        ("e1-j1", "jund", "E1", "2025-03-01T00:00:00+00:00", 0.5),
        ("e2-s1", "storm", "E2", "2025-05-01T00:00:00+00:00", None),
        ("e2-j1", "jund", "E2", "2025-05-01T00:00:00+00:00", None),
        # Storm alone still clears its own floor, so the refusal below is a fact about
        # the pair rather than about Storm having too little history of its own.
        ("e5-s1", "storm", "E5", "2025-09-01T00:00:00+00:00", 0.3),
        # ``lands`` never turns up beside either of them, so a pair with it never met.
        ("e3-l1", "lands", "E3", "2025-07-01T00:00:00+00:00", 0.2),
        ("e4-l1", "lands", "E4", "2025-08-01T00:00:00+00:00", 0.4),
    ]
    conn = built_graph(tmp_path, _write_timeline_snapshot(tmp_path, decks))

    with pytest.raises(NotEnoughHistory) as one_comparison:
        archetype_timeline(conn, "storm", "jund")
    assert one_comparison.value.found == 1

    # A pair who never met comes back a zero, not the same refusal as a pair who met
    # once: the count is what keeps the two answers apart (issue #101).
    with pytest.raises(NotEnoughHistory) as never_met:
        archetype_timeline(conn, "storm", "lands")
    assert never_met.value.found == 0

    # Each on its own still draws: the restriction is a property of the pair. Storm's
    # three cells are its three attendances, the middle one the break at E2.
    assert len(archetype_timeline(conn, "storm").cells) == 3


def test_the_pilot_floor_counts_comparisons_rather_than_attendances(
    tmp_path, built_graph
):
    # The same rule on the pilot chart, and it used to differ. `MIN_SHARED_EVENTS`
    # counted events both pilots entered, on the reasoning that a pilot brings one deck
    # so a shared event is a comparison by construction. A pilot can turn up and go
    # unscored, so it is not: EZ ranked only its winner, which is a bracket and bounds
    # nothing, so Ann and Bob share two events and can be compared at one. That is a dot
    # with a gap beside it, which is the shape this floor exists to refuse.
    decks = [
        ("ez-ann", "ann", "EZ", "2025-03-01T00:00:00+00:00", 1, None),
        ("ez-bob", "bob", "EZ", "2025-03-01T00:00:00+00:00", None, None),
        ("ez-f1", "ezf1", "EZ", "2025-03-02T00:00:00+00:00", None, None),
        ("e1-ann", "ann", "E1", "2025-05-01T00:00:00+00:00", 1, 0.0),
        ("e1-bob", "bob", "E1", "2025-05-01T00:00:00+00:00", 4, 0.75),
        ("e1-f1", "e1f1", "E1", "2025-05-02T00:00:00+00:00", 5, 1.0),
    ]
    conn = built_graph(tmp_path, _write_h2h_snapshot(
        tmp_path, decks, field_sizes={"EZ": 24, "E1": 5}))

    with pytest.raises(NotEnoughHistory) as refused:
        head_to_head_timeline(conn, "ann", "bob")

    # The count it names is the comparisons, not the attendances, so the message and the
    # rule that produced it cannot disagree (issue #101).
    assert refused.value.found == 1
    assert "compared at 1 event(s)" in str(refused.value)


def test_a_bounded_meeting_counts_toward_the_floor_that_admits_it(tmp_path, built_graph):
    # The floor and the headline read one definition, so moving the headline onto what
    # the record settles moves the floor with it. Storm and Jund are scored together
    # only at E1; at E2 Jund went unscored and E2 published enough to bound its tail, so
    # the record settles that meeting too and the pair has the two it needs.
    #
    # This is the case `test_a_pair_is_refused_on_the_meetings_the_record_settles` used
    # to refuse, and refusing it was the same defect as the headline's: a meeting drawn
    # as a comparison was not counted as one.
    root = tmp_path / "settled"
    root.mkdir()
    decks = [
        ("e1-s1", "storm", "E1", "2025-03-01T00:00:00+00:00", 0.0, 1),
        ("e1-j1", "jund", "E1", "2025-03-01T00:00:00+00:00", 1 / 11, 2),
        ("e2-s1", "storm", "E2", "2025-05-01T00:00:00+00:00", 2 / 11, 3),
        ("e2-j1", "jund", "E2", "2025-05-01T00:00:00+00:00", None, None),
        ("e2-x1", "lands", "E2", "2025-05-01T00:00:00+00:00", 8 / 11, 9),
    ]
    conn = built_graph(root, _write_timeline_snapshot(root, decks))

    cells = archetype_timeline(conn, "storm", "jund").cells
    settled = comparable_points(cells, paired=True)

    assert [c.event for c in settled] == ["E1", "E2"]
    # E2 is settled by the bound rather than by a finish: Jund has no mean there, and
    # Storm's 3rd is better than anything that event can have left Jund.
    e2 = settled[-1]
    assert e2.mean_norm_b is None and e2.bound_b is not None
    assert e2.mean_norm_a < e2.bound_b


def test_an_archetype_compared_against_itself_is_refused(tmp_path, built_graph):
    # An archetype has no rivalry with itself, so a == b is refused here rather than
    # returning every event twice as two identical sides, a zero-width band and a
    # "0 each" win count. The guard lives in the tool for the reason
    # ``head_to_head_timeline`` gives for its own: the tool is the seam an agent reaches
    # without the app's distinct-archetype check (the app collapses the pick to the solo
    # line before it gets here, as the adoption chart collapses a repeated card).
    # A plain ValueError, not NotEnoughHistory: a malformed question is not a thin
    # answer, and reporting it as no history would say these two never met.
    conn = _timeline_graph(tmp_path, built_graph)
    with pytest.raises(ValueError) as refusal:
        archetype_timeline(conn, "storm", "storm")
    assert not isinstance(refusal.value, NotEnoughHistory)


def test_run_series_routes_the_archetype_timeline_through_its_own_seam(
    tmp_path, built_graph
):
    conn = _timeline_graph(tmp_path, built_graph)
    solo = run_series(conn, ArchetypeTimeline("storm"))
    pair = run_series(conn, ArchetypeTimeline("storm", "jund"))
    assert isinstance(solo, Series)
    # The second archetype is optional on the spec, so the seam carries both shapes.
    assert solo.cells == archetype_timeline(conn, "storm").cells
    assert pair.cells == archetype_timeline(conn, "storm", "jund").cells


def test_the_timeline_catalogue_offers_every_archetype_that_draws_with_its_count(
    tmp_path, built_graph
):
    # This plot is the escape hatch for everything the landscape's top 25 hides, so
    # the catalogue is filtered by one rule only: can a line be drawn. ``lands`` was
    # ranked once, so it cannot; ``storm`` and ``jund`` were ranked at three events
    # each. The event count rides along so a label can show thinness before the
    # pick rather than a refusal after it.
    conn = _timeline_graph(tmp_path, built_graph)
    offered = archetypes_with_history(conn)

    assert offered == [("Jund", "jund", 3), ("Storm", "storm", 3)]


def test_the_catalogue_counts_the_events_the_timeline_can_actually_draw(
    tmp_path, built_graph
):
    # The catalogue's whole promise is that a pick never lands on a refusal, so it has
    # to count what the timeline draws: events that published a field, not a bracket
    # (ADR 0022). ``jund`` was ranked at two events and one of them is a bracket, so it
    # holds a single drawable point and is not offered at all.
    root = tmp_path / "bracket"
    root.mkdir()
    decks = [
        ("e1-s1", "storm", "E1", "2025-03-01T00:00:00+00:00", 0.5),
        ("e1-j1", "jund", "E1", "2025-03-01T00:00:00+00:00", 0.6),
        ("e2-s1", "storm", "E2", "2025-05-01T00:00:00+00:00", 0.1),
        ("e2-j1", "jund", "E2", "2025-05-01T00:00:00+00:00", 0.2),
        ("e3-s1", "storm", "E3", "2025-07-01T00:00:00+00:00", 0.4),
    ]
    snapshot = _write_timeline_snapshot(root, decks)
    _publish_only_a_bracket(snapshot, "E2")
    conn = built_graph(root, snapshot)

    # Storm is offered on the two events it can be drawn at, not the three it was
    # ranked at, so the label states the evidence a reader will actually see.
    assert archetypes_with_history(conn) == [("Storm", "storm", 2)]

    # And the count it withheld Jund on is the count the timeline refuses it on.
    with pytest.raises(NotEnoughHistory) as refusal:
        archetype_timeline(conn, "jund")
    assert refusal.value.found == 1


# The player leaderboard (#135). Its fixtures declare their own field sizes rather than
# taking `_FIELD_SIZE`, because the whole tool turns on which events are majors. Both
# sit just past the boundary they are there to cross, since `_cover_fields` has to fill
# every one of these seats with a deck and a hundred-seat major would dominate the
# build time of every test in this section.
_RACE_MAJOR_FIELD = 66  # over MAJOR_FIELD_SIZE, so these events are majors
_RACE_LOCAL_FIELD = 20  # under it, so these are locals and never scored on


def _write_race_snapshot(
    root: Path,
    events: dict[str, tuple[str, int]],
    entries: list[tuple[str, str, float]],
) -> Path:
    """A snapshot of dated, sized ``events`` and ``(pilot, event, norm)`` entries.

    The race turns on two things the other trend fixtures do not carry: an event's
    field size (which decides whether it is a major) and its date at day granularity
    (which decides which window it falls in). Both are properties of the *event*, so
    they are declared once per event here rather than per deck, which is also what
    keeps every deck of an event on one date: a straddle across a year boundary would
    abort the build.

    Each field size is declared above the top-8 cut-off and above every placement the
    fixture records, so the build's field-size correction leaves it alone (issue #140)
    and the norms these tests craft stand as written. It is then filled in by
    :func:`_cover_fields`, because a major the source published a bracket of is not
    scored at all (ADR 0021) and every event here is meant to be a real one.
    """
    snap = root / "snap"
    snap.mkdir()
    deck_records = []
    for pilot, event, norm in entries:
        date, field = events[event]
        deck_records.append({
            "deckId": f"{pilot}-{event}",
            "name": f"{_shared_win(field)} {pilot} - Deck - {event}",
            "deckName": "Deck",
            "pilot": pilot,
            "event": event,
            "eventId": f"evt_{event}",
            "eventType": "Tournament",
            "eventSize": field,
            "placement": 1,
            "placementNorm": norm,
            "createdAt": f"{date}T00:00:00+00:00",
            "colour": "colour:U",
            "macro": "macro:combo",
            "engineTags": ["engine:deck"],
            "engineTagLabels": {"engine:deck": "Deck"},
            "primaryTag": "engine:deck",
            "primaryTagWeights": {"engine:deck": 100},
        })
    deck_records = _cover_fields(deck_records)
    (snap / "decks.json").write_text(json.dumps(deck_records))
    (snap / "cards_index.json").write_text(json.dumps({
        "v": 1,
        "cards": [],
        "decks": {d["deckId"]: {"m": [], "s": []} for d in deck_records},
    }))
    return snap


# Eight majors spanning three and a half years, the newest of them the anchor every
# window is measured back from, plus two locals inside the same span.
_RACE_EVENTS = {
    "M1": ("2023-02-01", _RACE_MAJOR_FIELD),
    "M2": ("2023-08-01", _RACE_MAJOR_FIELD),
    "M3": ("2024-02-01", _RACE_MAJOR_FIELD),
    "M4": ("2024-08-01", _RACE_MAJOR_FIELD),
    "M5": ("2025-02-01", _RACE_MAJOR_FIELD),
    "M6": ("2025-08-01", _RACE_MAJOR_FIELD),
    "M7": ("2026-02-01", _RACE_MAJOR_FIELD),
    "M8": ("2026-06-01", _RACE_MAJOR_FIELD),
    "L1": ("2025-03-01", _RACE_LOCAL_FIELD),
    "L2": ("2026-03-01", _RACE_LOCAL_FIELD),
}
_RACE_MAJORS = [f"M{i}" for i in range(1, 9)]


def _race_graph(root, built_graph, entries=None):
    """A built graph holding two contenders and three pilots each gated out once.

    ``ace`` (all eight majors, and two locals besides) and ``solid`` (the last five)
    both clear the two gates. Each of the other three fails exactly one of them, so a
    gate that stopped firing shows up as one named pilot joining the field:

    - ``rookie`` plays four majors, one short of a career;
    - ``veteran`` plays six, but only one after the recency line, so he is a retiree;
    - ``grinder`` plays five events that are all locals, so he has no majors at all.
    """
    entries = entries or []
    entries += [("ace", e, 0.1) for e in _RACE_MAJORS]
    entries += [("ace", e, 0.1) for e in ("L1", "L2")]
    entries += [("solid", e, 0.3) for e in _RACE_MAJORS[3:]]
    entries += [("rookie", e, 0.2) for e in _RACE_MAJORS[4:]]
    entries += [("veteran", e, 0.2) for e in _RACE_MAJORS[:6]]
    entries += [("grinder", e, 0.1) for e in ("L1", "L2")]
    return built_graph(root, _write_race_snapshot(root, _RACE_EVENTS, entries))


def test_the_race_ranks_the_pilots_who_clear_both_gates_on_their_majors_alone(
    tmp_path, built_graph
):
    # Who is ranked (#135): a contender needs MIN_CAREER_MAJORS majors behind them and
    # MIN_RECENT_MAJORS of them inside the recency window, so the field is current
    # rather than a hall of fame. ``rookie`` misses the first gate, ``veteran`` the
    # second, and ``grinder`` never played a major at all.
    conn = _race_graph(tmp_path, built_graph)
    series = player_leaderboard(conn)

    assert {c.pilot for c in series.cells} == {"ace", "solid"}
    # The majors count is the score's own sample, so ``ace``'s two locals are no part
    # of it: a career of 8 majors, not 10 events.
    majors = {c.pilot: c.majors for c in series.cells}
    assert majors == {"ace": 8, "solid": 5}
    # Every cell carries the graph's own major count, the pool the field is drawn
    # from, so a caption can state it without a second query.
    assert {c.major_events for c in series.cells} == {8}


def test_a_major_that_published_only_its_bracket_is_worth_no_finish(
    tmp_path, built_graph
):
    # ADR 0021. A major whose record is a top-8 cut records no bad finish, so a pilot
    # who attended it either gained a near-perfect score or left no trace, and
    # attending became a free roll. Two of the artifact's 21 majors are like this
    # (SSWam holds 7 finishes of an 88 field, ANZSS10 8 of 81, neither recording a
    # finish deeper than 5th), and a cut-only finish is worth 0.967 against 0.506 at a
    # major that published its standings.
    #
    # A ranking cannot hold both kinds at once, so the event is dropped whole rather
    # than discounted: no count of the good finishes on record says how many bad ones
    # were withheld.
    root = tmp_path / "bracket"
    root.mkdir()
    entries = [("ace", e, 0.1) for e in _RACE_MAJORS]
    entries += [("solid", e, 0.3) for e in _RACE_MAJORS]
    snapshot = _write_race_snapshot(root, _RACE_EVENTS, entries)
    _publish_only_a_bracket(snapshot, "M8")
    conn = built_graph(root, snapshot)

    series = player_leaderboard(conn)

    # M8 is still a major by field size and still the newest event in the graph, so it
    # goes on anchoring the sample dates. It is simply not one the race scores.
    assert {c.major_events for c in series.cells} == {7}
    assert {c.pilot: c.majors for c in series.cells} == {"ace": 7, "solid": 7}
    assert max(c.as_of for c in series.cells) == datetime.fromisoformat(
        _RACE_EVENTS["M8"][0]
    )


# Twelve majors ending at the anchor, the newest six of them inside the recency
# window, so a pilot given the newest N events is a contender whatever N is.
_RACE_SPAN = [
    "2023-07-01", "2023-11-01", "2024-03-01", "2024-07-01", "2024-11-01",
    "2025-03-01", "2025-07-01", "2025-11-01", "2026-01-01", "2026-03-01",
    "2026-05-01", "2026-06-01",
]


def _scored_race_graph(root, built_graph, records: dict[str, list[float]]):
    """A graph where each pilot's flipped finishes land on the newest majors.

    ``records`` maps a pilot to the finishes of their career, best-is-1. A list is
    dealt onto the tail of :data:`_RACE_SPAN`, oldest first, so a record is as long as
    the list given and always reaches the right edge of the chart; a dict places them
    by event number instead, for a career with a shape (a gap, a late return).
    """
    events = {f"MJ{i}": (date, _RACE_MAJOR_FIELD) for i, date in enumerate(_RACE_SPAN, 1)}
    entries = []
    for pilot, record in records.items():
        placed = (
            record.items() if isinstance(record, dict)
            else zip(range(len(_RACE_SPAN) - len(record) + 1, len(_RACE_SPAN) + 1), record)
        )
        entries += [(pilot, f"MJ{number}", 1 - finish) for number, finish in placed]
    return built_graph(root, _write_race_snapshot(root, events, entries))


def test_a_thin_record_is_shrunk_toward_the_field_and_can_lose_to_a_thicker_one(
    tmp_path, built_graph
):
    # The spec's worked example, in the shape it makes the case for: ``streak`` has the
    # better raw career mean (0.800 over 5 majors) than ``steady`` (0.746 over 12), but
    # 0.800 over 5 is not the stronger evidence, so the score shrinks each toward the
    # field average by how little of it there is and ``steady`` ends up ahead. The
    # ``pack`` is the field they are shrunk toward: four ordinary contenders whose own
    # finishes bounce far more than their means differ, which is what makes the pull
    # worth applying at all.
    streak = [1.0, 0.60, 0.95, 0.55, 0.90]
    steady = [1.0, 0.50, 0.90, 0.60, 0.95, 0.55, 0.85, 0.65, 1.0, 0.45, 0.90, 0.60]
    records = {"streak": streak, "steady": steady}
    records |= {f"pack{i}": [0.9, 0.1, 0.7, 0.3, 0.6, 0.4] for i in range(4)}
    conn = _scored_race_graph(tmp_path, built_graph, records)
    scores = {c.pilot: c.score for c in player_leaderboard(conn).cells}

    raw_streak = sum(streak) / len(streak)
    raw_steady = sum(steady) / len(steady)
    assert raw_streak > raw_steady  # the raw means rank one way ...
    assert scores["steady"] > scores["streak"]  # ... and the scores the other

    # Both are above the field, so both are pulled down, and the thinner record is
    # pulled further: that is the whole of what the shrinkage does, stated without
    # reference to the estimated constants (which are re-derived per rebuild).
    assert scores["streak"] < raw_streak
    assert scores["steady"] < raw_steady
    assert raw_streak - scores["streak"] > raw_steady - scores["steady"]


def _race_field(**records):
    """``records`` over an ordinary four-pilot field, the pack a score is shrunk toward."""
    return records | {f"pack{i}": [0.9, 0.1, 0.7, 0.3, 0.6, 0.4] for i in range(4)}


def test_the_sample_dates_step_back_from_the_newest_event_and_floor_a_thin_start(
    tmp_path, built_graph
):
    # The x axis (#135, as ADR 0017 rebuilt it): five sample dates stepped 6 months, the
    # newest at the newest event in the graph so the chart re-anchors itself on every
    # rebuild. A point counts every major up to its date, so ``late``, who played only
    # the newest five majors, has nothing at the first three dates, one by the fourth,
    # and their whole record by the fifth.
    conn = _scored_race_graph(tmp_path, built_graph, _race_field(
        late=[0.9, 0.9, 0.9, 0.9, 0.9],
    ))
    cells = [c for c in player_leaderboard(conn).cells if c.pilot == "late"]

    assert len(cells) == RACE_POINTS
    assert [c.as_of for c in cells] == [
        datetime(2024, 6, 1),
        datetime(2024, 12, 1),
        datetime(2025, 6, 1),
        datetime(2025, 12, 1),
        datetime(2026, 6, 1),  # the newest event in the graph
    ]
    # The floor is on the value, not the row: a date under MIN_SCORED_MAJORS states the
    # majors that refused it and withholds only the score they cannot carry, so a pilot
    # who had not started and one a single event into their record stay different
    # answers. A running count can only rise, and its last value is the whole career.
    assert [c.as_of_majors for c in cells] == [0, 0, 0, 1, 5]
    assert [c.as_of_score is None for c in cells] == [True, True, True, True, False]
    assert cells[-1].as_of_majors == cells[-1].majors


def test_a_two_major_start_does_not_draw_the_sharpest_point_on_the_chart(
    tmp_path, built_graph
):
    # A point's score is the career statistic applied to the record so far, not a raw
    # mean over it (#135). By the fourth sample date ``spike`` had won both majors they
    # had played, a raw 1.0 on two events, while ``late`` was five events in averaging
    # 0.9. Unshrunk, ``spike`` would draw the highest point on the chart off the least
    # evidence on it, which is exactly what the two-major floor alone cannot prevent.
    conn = _scored_race_graph(tmp_path, built_graph, _race_field(
        spike={7: 1.0, 8: 1.0, 10: 0.5, 11: 0.4, 12: 0.5},
        late={4: 1.0, 5: 0.8, 6: 0.95, 7: 0.85, 8: 0.9},
    ))
    fourth = {
        c.pilot: c for c in player_leaderboard(conn).cells
        if c.as_of == datetime(2025, 12, 1)
    }

    assert fourth["spike"].as_of_majors == 2   # a raw mean of 1.0 ...
    assert fourth["late"].as_of_majors == 5    # ... against a raw mean of 0.9
    assert fourth["spike"].as_of_score < 1.0
    assert fourth["spike"].as_of_score < fourth["late"].as_of_score


def test_the_rank_is_recomputed_at_each_date_over_whoever_had_a_record_by_then(
    tmp_path, built_graph
):
    # The hover states a pilot's standing among the contenders, and that standing is a
    # property of the date rather than of the career: on the evidence up to 2024 the
    # ``veteran`` led, and the rest of the record revised that down while the
    # ``climber``'s rose past them. It counts only the contenders scored by that date,
    # so an early date nobody but those two had a record at ranks two pilots, not the
    # whole field.
    conn = _scored_race_graph(tmp_path, built_graph, _race_field(
        veteran=[0.9, 0.95, 0.9, 0.85, 0.9, 0.6, 0.55, 0.5, 0.6, 0.5, 0.55, 0.6],
        climber={4: 0.5, 5: 0.55, 6: 0.6, 7: 0.9, 8: 0.95, 9: 0.9, 10: 0.85,
                 11: 0.95, 12: 0.9},
        late=[0.75, 0.8, 0.75, 0.7, 0.75],
    ))
    cells = player_leaderboard(conn).cells
    early = {c.pilot: c for c in cells if c.as_of == datetime(2024, 12, 1)}
    newest = {c.pilot: c for c in cells if c.as_of == datetime(2026, 6, 1)}

    # Early: the veteran leads the climber, and ``late`` had not started, so they carry
    # no score and no rank rather than a last place they never played for.
    assert (early["veteran"].as_of_rank, early["climber"].as_of_rank) == (1, 2)
    assert early["late"].as_of_score is None
    assert early["late"].as_of_rank is None
    # Newest: on the whole record the climber leads and the veteran sits behind both.
    assert newest["climber"].as_of_rank == 1
    assert newest["late"].as_of_rank == 2
    assert newest["veteran"].as_of_rank == 3


def test_the_last_point_of_every_line_is_that_pilots_career_score(
    tmp_path, built_graph
):
    # The property that makes the running score honest at the right edge (ADR 0017): the
    # newest sample date is the newest event in the graph, so by then a pilot's record so
    # far *is* their record, and the last point of a line is the score the leaderboard
    # ranks them on. The rolling version this replaced had no such tie, which is what let
    # a contender outside the drawn eight hold the highest point on the chart.
    conn = _scored_race_graph(tmp_path, built_graph, _race_field(
        best=[1.0, 0.95, 1.0, 0.9, 0.95, 1.0, 0.9, 0.95, 1.0, 0.95, 0.9, 1.0],
        second=[0.8, 0.75, 0.85, 0.8, 0.75, 0.8, 0.85, 0.75, 0.8, 0.85, 0.8, 0.75],
        late=[0.9, 0.9, 0.9, 0.9, 0.9],
    ))
    cells = player_leaderboard(conn).cells
    newest = max(c.as_of for c in cells)
    final = {c.pilot: c for c in cells if c.as_of == newest}

    assert len(final) == len({c.pilot for c in cells})   # every line reaches the edge
    assert all(c.as_of_score == c.score for c in final.values())
    assert all(c.as_of_rank == c.rank for c in final.values())
    assert all(c.as_of_majors == c.majors for c in final.values())


def test_the_career_rank_carries_the_interval_the_evidence_actually_supports(
    tmp_path, built_graph
):
    # A rank is an ordering the record may not support, so every cell carries the
    # resampled bounds on it (ADR 0017). Here two pilots are genuinely apart and four
    # are a pack drawn from one distribution: the pack's bounds have to span most of the
    # field, because which of them ranks where is the luck of which events fell in their
    # record, while a real leader's bounds cannot reach the bottom of it.
    conn = _scored_race_graph(tmp_path, built_graph, _race_field(
        best=[1.0, 0.95, 1.0, 0.9, 0.95, 1.0, 0.9, 0.95, 1.0, 0.95, 0.9, 1.0],
        worst=[0.1, 0.05, 0.1, 0.15, 0.1, 0.05, 0.1, 0.15, 0.1, 0.05, 0.1, 0.15],
    ))
    cells = {c.pilot: c for c in player_leaderboard(conn).cells}
    field = len(cells)

    # The bounds bracket the rank they qualify, for everyone.
    assert all(c.rank_low <= c.rank <= c.rank_high for c in cells.values())
    assert all(1 <= c.rank_low and c.rank_high <= field for c in cells.values())
    # A separable leader stays near the top across resamples; the pack does not.
    assert cells["best"].rank == 1
    assert cells["best"].rank_high < field
    assert cells["worst"].rank_low > 1
    # The pack are four draws from one distribution, so none of them holds a place: each
    # one's bounds have to span the pack, rather than fixing them in the order this
    # record happened to put them in.
    packed = [cells[f"pack{i}"] for i in range(4)]
    assert all(c.rank_high - c.rank_low >= len(packed) - 1 for c in packed)


def test_the_whole_race_is_the_same_on_every_run_of_the_same_graph(
    tmp_path, built_graph
):
    # Seeded (RACE_RESAMPLE_SEED), so a rebuild of the same artifact draws the same
    # bounds and a moved bound means moved evidence rather than a fresh roll. The app
    # computes the race once at startup and the oracle grades it, and neither can tell a
    # real change from resampling noise unless this holds.
    #
    # The seed alone does not deliver it, which is why this asserts the whole cell rather
    # than the bounds: Ladybug hands the same query's rows back in a different order
    # between calls, so an unsorted walk over the field feeds the seeded stream to a
    # different pilot each run, and every consumer that sums a record (the shrinkage, the
    # score, each running point) lands a fraction apart. Both orders are settled in
    # `_major_finishes` and `_rank_intervals`. This caught a real regression.
    conn = _scored_race_graph(tmp_path, built_graph, _race_field(
        best=[1.0, 0.95, 1.0, 0.9, 0.95, 1.0, 0.9, 0.95, 1.0, 0.95, 0.9, 1.0],
        second=[0.8, 0.75, 0.85, 0.8, 0.75, 0.8, 0.85, 0.75, 0.8, 0.85, 0.8, 0.75],
        late=[0.9, 0.9, 0.9, 0.9, 0.9],
    ))
    assert player_leaderboard(conn).cells == player_leaderboard(conn).cells


def test_a_field_of_one_is_not_a_race_and_is_refused_by_name(tmp_path, built_graph):
    # A race is relative: one line says what a pilot scored, not who was best, which is
    # what the single-pilot performance chart is already for. So a field short of
    # MIN_RACE_CONTENDERS is refused by name carrying the contenders it found (ADR
    # 0013's typed refusal), rather than coming back as an empty series the surface
    # cannot tell from a graph with no majors in it.
    conn = _scored_race_graph(tmp_path, built_graph, {
        "alone": [0.9, 0.8, 0.85, 0.9, 0.75, 0.8],
        "rookie": [0.5, 0.5],  # two majors: never a contender
    })
    with pytest.raises(NotEnoughHistory) as refusal:
        player_leaderboard(conn)
    assert refusal.value.found == 1

    # A graph whose events are all locals has no contenders at all, and says so with
    # the same refusal rather than dividing by an empty field.
    root = tmp_path / "locals"
    root.mkdir()
    locals_only = _write_race_snapshot(
        root,
        {f"L{i}": (date, _RACE_LOCAL_FIELD) for i, date in enumerate(_RACE_SPAN, 1)},
        [("grinder", f"L{i}", 0.2) for i in range(1, 13)],
    )
    with pytest.raises(NotEnoughHistory) as refusal:
        player_leaderboard(built_graph(root, locals_only))
    assert refusal.value.found == 0


def test_a_field_the_data_cannot_separate_scores_everyone_at_the_field_average(
    tmp_path, built_graph
):
    # The shrinkage weight is estimated, so the estimate can come back saying the field
    # has no separable spread at all: these three swing further inside their own records
    # than their records differ from each other, which is what "no evidence anybody is
    # better" looks like arithmetically. Every score is then the field average, so the
    # race draws with nothing between its runners rather than ranking noise. The real
    # record is far from this, but a young graph can sit here.
    records = {
        "swing": [0.9, 0.95, 0.9, 0.85, 0.9, 0.4, 0.35, 0.3, 0.4, 0.3, 0.35, 0.4],
        "surge": {4: 0.3, 5: 0.35, 6: 0.4, 7: 0.9, 8: 0.95, 9: 0.9, 10: 0.85,
                  11: 0.95, 12: 0.9},
        "steady": [0.6, 0.65, 0.6, 0.55, 0.6],
    }
    conn = _scored_race_graph(tmp_path, built_graph, records)
    cells = player_leaderboard(conn).cells

    finishes = [f for record in records.values()
                for f in (record.values() if isinstance(record, dict) else record)]
    field_average = sum(finishes) / len(finishes)
    assert {round(c.score, 9) for c in cells} == {round(field_average, 9)}
    # The plotted points collapse onto it too, so no date can rank a pilot the career
    # score says nothing about.
    assert {round(c.as_of_score, 9) for c in cells if c.as_of_score is not None} == {
        round(field_average, 9)
    }


def test_run_series_routes_the_player_leaderboard_through_its_own_seam(
    tmp_path, built_graph
):
    conn = _scored_race_graph(tmp_path, built_graph, _race_field())
    series = run_series(conn, PlayerLeaderboard())
    assert isinstance(series, Series)
    # The race takes no argument: it is one question about the whole field, the way
    # the meta-share matrix is one question about the whole meta.
    assert series.cells == player_leaderboard(conn).cells


def test_the_race_comes_back_in_standing_order_with_each_career_oldest_first(
    tmp_path, built_graph
):
    # The legend, the leaderboard and the colour assignment all read this order, so the
    # tool settles it rather than leaving each caller to re-sort: contenders best first,
    # and inside a contender the sample dates oldest first, which is the x axis's own
    # order. The query's row order is not stable between calls, so an unsorted result
    # also made two runs of the same graph disagree.
    conn = _scored_race_graph(tmp_path, built_graph, _race_field(
        best=[1.0, 0.95, 1.0, 0.9, 0.95, 1.0, 0.9, 0.95, 1.0, 0.95, 0.9, 1.0],
        second=[0.8, 0.75, 0.85, 0.8, 0.75, 0.8, 0.85, 0.75, 0.8, 0.85, 0.8, 0.75],
    ))
    cells = player_leaderboard(conn).cells

    assert [c.pilot for c in cells[:RACE_POINTS]] == ["best"] * RACE_POINTS
    assert [c.pilot for c in cells[RACE_POINTS:2 * RACE_POINTS]] == ["second"] * RACE_POINTS
    dates = [c.as_of for c in cells[:RACE_POINTS]]
    assert dates == sorted(dates)
    scores = [c.score for c in cells]
    assert scores == sorted(scores, reverse=True)


def test_the_race_over_the_real_record_ends_where_the_leaderboard_starts(live_graph):
    # A claim about the whole record, not a fixture. The newest sample date is the newest
    # event in the graph, so every contender's record so far is their whole record there:
    # the right edge of the chart is the leaderboard, line for line, and the drawn eight
    # cannot be crossed by a contender the cut left out. The career gate then guarantees
    # the point clears the per-date floor, so no line stops short of the edge.
    assert MIN_CAREER_MAJORS >= MIN_SCORED_MAJORS
    series = player_leaderboard(live_graph)
    newest = max(c.as_of for c in series.cells)
    final = [c for c in series.cells if c.as_of == newest]

    assert all(c.majors >= MIN_CAREER_MAJORS for c in series.cells)
    assert len(final) == len({c.pilot for c in series.cells})
    assert all(c.as_of_score == c.score and c.as_of_rank == c.rank for c in final)
    # Every score is a finish, so it stays on the finish's own 0-to-1 scale whatever
    # the shrinkage did to it.
    assert all(0.0 <= c.score <= 1.0 for c in series.cells)
    assert all(0.0 <= c.as_of_score <= 1.0
               for c in series.cells if c.as_of_score is not None)
    # The rank interval brackets the rank it qualifies, and on a record this thin it is
    # wide: the top of the board is separated by thousandths, so if these bounds ever
    # collapsed onto the rank the reader would be being told the order is settled.
    assert all(c.rank_low <= c.rank <= c.rank_high for c in series.cells)
    assert max(c.rank_high - c.rank_low for c in final) > len(final) / 4
    # Majors are the top slice of the record by size, not most of it.
    assert len(final) > MIN_RACE_CONTENDERS
    ((events,),) = rows(live_graph.execute("MATCH (e:Event) RETURN count(e)"))
    assert 0 < series.cells[0].major_events < events / 2


def test_the_career_standing_rides_on_the_cell_and_a_tie_shares_its_place(
    tmp_path, built_graph
):
    # The leaderboard's rank column is data, not a row number: it is computed here so a
    # tie shares its place rather than being split by whatever order the table happened
    # to iterate in. In an ordinary field it counts 1, 2, 3 down the standings.
    conn = _scored_race_graph(tmp_path, built_graph, _race_field(
        best=[1.0, 0.95, 1.0, 0.9, 0.95, 1.0, 0.9, 0.95, 1.0, 0.95, 0.9, 1.0],
        second=[0.8, 0.75, 0.85, 0.8, 0.75, 0.8, 0.85, 0.75, 0.8, 0.85, 0.8, 0.75],
    ))
    standings = {c.pilot: c.rank for c in player_leaderboard(conn).cells}

    assert standings["best"] == 1
    assert standings["second"] == 2
    # The four pack pilots have identical records, so they hold one place between them
    # rather than four consecutive ones.
    assert {standings[f"pack{i}"] for i in range(4)} == {3}


def test_an_event_with_no_field_size_is_no_major_rather_than_a_crash(
    tmp_path, built_graph
):
    # `Event.fieldSize` is nullable: the build only fills a missing one for a
    # `Tournament` (`corrected_field`'s Rule C), so a non-Tournament event the source
    # ships without a size reaches the graph with none. The Cypher half of the tool
    # already reads that correctly, since a NULL fails `fieldSize > $major`, and the
    # Python half comparing it here has to agree or the race raises TypeError, which
    # `build_app` does not catch and so takes down every tab rather than this one.
    root = tmp_path / "sizeless"
    root.mkdir()
    snapshot = _write_race_snapshot(
        root,
        {f"MJ{i}": (date, _RACE_MAJOR_FIELD) for i, date in enumerate(_RACE_SPAN, 1)},
        [(pilot, f"MJ{i}", 0.2 + n / 10)
         for n, pilot in enumerate(("ace", "rival"))
         for i in range(1, 13)],
    )
    decks = json.loads((snapshot / "decks.json").read_text())
    for deck in decks:
        if deck["event"] == "MJ1":
            deck["eventType"], deck["eventSize"] = "Teams", None
    (snapshot / "decks.json").write_text(json.dumps(decks))
    conn = built_graph(root, snapshot)

    # The sizeless event is simply not a major: the pool is the other eleven.
    assert {c.major_events for c in player_leaderboard(conn).cells} == {11}


def test_an_as_of_rank_carries_the_contenders_it_was_taken_over(tmp_path, built_graph):
    # A rank without its denominator is not a standing. The field a window ranks over is
    # whoever scored *that* window, which is far fewer than the contenders at the old end
    # of the chart (67 of 139 on the real record), so a surface reading "#5" against the
    # caption's whole-field count would overstate it. Carried beside the rank the way
    # ``majors`` is carried beside the score.
    conn = _scored_race_graph(tmp_path, built_graph, _race_field(
        veteran=[0.9, 0.95, 0.9, 0.85, 0.9, 0.6, 0.55, 0.5, 0.6, 0.5, 0.55, 0.6],
        climber={4: 0.5, 5: 0.55, 6: 0.6, 7: 0.9, 8: 0.95, 9: 0.9, 10: 0.85,
                 11: 0.95, 12: 0.9},
        late=[0.75, 0.8, 0.75, 0.7, 0.75],
    ))
    cells = player_leaderboard(conn).cells
    early = [c for c in cells if c.as_of == datetime(2024, 12, 1)]
    newest = [c for c in cells if c.as_of == datetime(2026, 6, 1)]

    # Only the veteran and the climber had a record by the early date; the pack and
    # ``late`` are contenders who had not started, so they are no part of its standings.
    assert {c.as_of_contenders for c in early} == {2}
    assert {c.as_of_rank for c in early if c.as_of_rank} == {1, 2}
    # By the newest date the whole field of seven is in it.
    assert {c.as_of_contenders for c in newest} == {7}
