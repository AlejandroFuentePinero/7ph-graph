import json
from datetime import datetime
from pathlib import Path

import pytest

from graph7ph.trends import (
    CardAdoptionOverTime,
    HeadToHeadTimeline,
    MetaShareOverTime,
    PilotPerformanceOverTime,
    Series,
    SeriesCell,
    card_adoption_over_time,
    head_to_head_timeline,
    latest_deck_year,
    latest_year_share_cut,
    meta_share_over_time,
    pilot_performance_over_time,
    pilots_with_history,
    run_series,
)


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
            "name": f"1st Someone - {archetype} - {event}",
            "deckName": archetype.title(),
            "pilot": f"pilot-{deck_id}",
            "event": event,
            "eventId": f"evt_{event}",
            "eventType": "Tournament",
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
    assert latest_year_share_cut(series, 0.5) in (["a"], ["b"])
    assert set(latest_year_share_cut(series, 1.0)) == {"a", "b"}


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
            "name": f"1st Player {deck_id} - Deck - {event}",
            "deckName": "Deck",
            "pilot": f"pilot-{deck_id}",
            "event": event,
            "eventId": f"evt_{event}",
            "eventType": "Tournament",
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
            "name": f"1st {pilot} - Deck - {event}",
            "deckName": "Deck",
            "pilot": pilot,
            "event": event,
            "eventId": f"evt_{event}",
            "eventType": "Tournament",
            "placement": 1,
            "placementNorm": norm,
            "createdAt": f"{year}-06-01T00:00:00+00:00",
            "colour": "colour:U",
            "macro": "macro:combo",
            "engineTags": ["engine:deck"],
            "engineTagLabels": {"engine:deck": "Deck"},
            "primaryTag": "engine:deck",
            "primaryTagWeights": {"engine:deck": 100},
        })
    (snap / "decks.json").write_text(json.dumps(deck_records))
    (snap / "cards_index.json").write_text(json.dumps({
        "v": 1,
        "cards": [],
        "decks": {d[0]: {"m": [], "s": []} for d in decks},
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


def test_pilot_performance_of_a_single_year_pilot_is_not_enough_history(
    tmp_path, built_graph
):
    # ``bo`` has decks in only one year, so he never reaches two qualifying years:
    # the tool returns nothing rather than a lone point on an empty line (ADR 0013).
    conn = _performance_graph(tmp_path, built_graph)
    assert pilot_performance_over_time(conn, "bo").cells == []


def test_pilot_performance_drops_a_thin_year_and_means_over_ranked_decks_only(
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

    # The thin 2023 (one event, below the floor) is a gap: absent, not a false point.
    assert set(by_year) == {2024, 2025}
    # 2025's unranked deck neither shifts the mean nor pads the event count: the mean
    # is 0.2 over the three ranked decks, and events counts only the ranked events.
    assert by_year[2025].events == 3
    assert by_year[2025].mean_norm == pytest.approx(0.2)


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
    root: Path, decks: list[tuple[str, str, str, str, int, float | None]]
) -> Path:
    """Write a snapshot of ``(deck_id, pilot, event, created_at, placement, norm)``.

    A shared event is one both pilots entered, so a head-to-head test needs two
    pilots' decks at the same event plus filler decks under other pilots to give
    the event a field size larger than the pair. Each deck carries its own
    ``createdAt``, the registration date the timeline reads; decks of one event
    stay inside one calendar year so the build does not abort on a straddle. A
    distinct pilot per deck keeps the fuzzy pilot merge and the duplicate-list
    drop (ADR 0004, 0007) from folding fixtures meant to stay separate.
    """
    snap = root / "snap"
    snap.mkdir()
    deck_records = [
        {
            "deckId": deck_id,
            "name": f"{placement}st {pilot} - Deck - {event}",
            "deckName": "Deck",
            "pilot": pilot,
            "event": event,
            "eventId": f"evt_{event}",
            "eventType": "Tournament",
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

    E1 (registered 2025-03) is a full 5-deck field, placements 1..5 with norms
    ``(place-1)/4``, so its norm-implied field and its deck count both read 5.
    ``ann`` finishes 1st (norm 0.0), ``bob`` 4th (norm 0.75). EM (registered
    2025-05) is a 3-deck field the pair also both entered. E2 (registered 2025-07,
    last) is a **top-cut** field: 20 real entrants but only four decks recorded,
    norms ``(place-1)/19``, so the norm implies a field of 20 while the
    decks-at-event count is 4. ``ann`` finishes 2nd, ``bob`` 5th. E2 is what holds
    the tool to reading the field off the norm rather than counting decks. ``ann``
    also played a lone EA that ``bob`` did not, so it is never a shared event.
    """
    decks = [
        ("e1-ann", "ann", "E1", "2025-03-01T00:00:00+00:00", 1, 0.0),
        ("e1-bob", "bob", "E1", "2025-03-01T09:00:00+00:00", 4, 0.75),
        ("e1-f1", "e1f1", "E1", "2025-03-02T00:00:00+00:00", 2, 0.25),
        ("e1-f2", "e1f2", "E1", "2025-03-02T00:00:00+00:00", 3, 0.5),
        ("e1-f3", "e1f3", "E1", "2025-03-02T00:00:00+00:00", 5, 1.0),
        ("em-ann", "ann", "EM", "2025-05-01T00:00:00+00:00", 3, 1.0),
        ("em-bob", "bob", "EM", "2025-05-01T00:00:00+00:00", 1, 0.0),
        ("em-f1", "emf1", "EM", "2025-05-02T00:00:00+00:00", 2, 0.5),
        ("e2-ann", "ann", "E2", "2025-07-01T00:00:00+00:00", 2, 1 / 19),
        ("e2-bob", "bob", "E2", "2025-07-01T00:00:00+00:00", 5, 4 / 19),
        ("e2-f1", "e2f1", "E2", "2025-07-02T00:00:00+00:00", 1, 0.0),
        ("e2-f2", "e2f2", "E2", "2025-07-02T00:00:00+00:00", 10, 9 / 19),
        ("ea-ann", "ann", "EA", "2025-09-01T00:00:00+00:00", 1, 0.0),
    ]
    return built_graph(root, _write_h2h_snapshot(root, decks))


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
    assert e1.field_size == 5
    assert e1.date == datetime(2025, 3, 1, 0, 0)  # min createdAt across the field
    assert (e1.placement_a, e1.norm_a) == (1, pytest.approx(0.0))
    assert (e1.placement_b, e1.norm_b) == (4, pytest.approx(0.75))

    # The top-cut event: 4 decks recorded but the norm was ranked against 20
    # entrants, so field_size reads the norm's field (20), not the deck count (4).
    e2 = by_event["E2"]
    assert e2.field_size == 20
    assert (e2.placement_a, e2.norm_a) == (2, pytest.approx(1 / 19))
    assert (e2.placement_b, e2.norm_b) == (5, pytest.approx(4 / 19))


def test_head_to_head_of_a_pair_sharing_one_event_is_refused(tmp_path, built_graph):
    # A pair needs at least two shared events or it is a dot, not a timeline, so a
    # one-event pair comes back empty rather than a lone point (ADR 0013). Here
    # ``ann`` and ``bob`` share only E1; E2 and E3 are ann's alone.
    decks = [
        ("e1-ann", "ann", "E1", "2025-03-01T00:00:00+00:00", 1, 0.0),
        ("e1-bob", "bob", "E1", "2025-03-01T00:00:00+00:00", 2, 0.2),
        ("e2-ann", "ann", "E2", "2025-07-01T00:00:00+00:00", 1, 0.0),
        ("e3-ann", "ann", "E3", "2025-09-01T00:00:00+00:00", 1, 0.0),
    ]
    conn = built_graph(tmp_path, _write_h2h_snapshot(tmp_path, decks))
    assert head_to_head_timeline(conn, "ann", "bob").cells == []


def test_head_to_head_of_a_pilot_against_themselves_is_refused(tmp_path, built_graph):
    # A pilot has no rivalry with themselves, so a == b is refused rather than drawing
    # two identical lines. Guarded in the tool (not only the app) since the tool is
    # the agent-facing seam a direct caller reaches without the UI's a != b check.
    conn = _h2h_graph(tmp_path, built_graph)
    assert head_to_head_timeline(conn, "ann", "ann").cells == []


def test_run_series_routes_head_to_head_through_its_own_seam(tmp_path, built_graph):
    conn = _h2h_graph(tmp_path, built_graph)
    routed = run_series(conn, HeadToHeadTimeline("ann", "bob"))
    direct = head_to_head_timeline(conn, "ann", "bob")
    assert isinstance(routed, Series)
    assert routed.cells == direct.cells
