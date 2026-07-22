import json
from pathlib import Path

import pytest

from graph7ph.trends import (
    MIN_CELL_DECKS,
    CardAdoptionOverTime,
    MetaShareOverTime,
    Series,
    SeriesCell,
    card_adoption_over_time,
    meta_share_over_time,
    pooled_share_cut,
    run_series,
)


def _cell(tag, year, n):
    """A cell with an arbitrary but consistent share/year_total, for cut tests."""
    return SeriesCell(tag=tag, archetype=tag.title(), year=year, n=n,
                      share=None, year_total=n)


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
    """A built graph with a fat cell, two thin cells, and archetypes across years.

    2025: 6 Grixis (fat), 1 Storm (thin). 2024: 5 Storm (fat), 3 Grixis (thin).
    So Grixis is real in 2025 and a gap in 2024, Storm the mirror. The floor is 5.
    """
    decks = (
        [(f"g25-{i}", "E2025", 2025, "grixis") for i in range(6)]
        + [("s25-0", "E2025", 2025, "storm")]
        + [(f"s24-{i}", "E2024", 2024, "storm") for i in range(5)]
        + [(f"g24-{i}", "E2024", 2024, "grixis") for i in range(3)]
    )
    return built_graph(root, _write_snapshot(root, decks))


def test_meta_share_year_shares_sum_sanely_and_thin_cells_are_gaps(tmp_path, built_graph):
    conn = _meta_share_graph(tmp_path, built_graph)
    series = meta_share_over_time(conn)
    by_key = {(c.archetype, c.year): c for c in series.cells}

    # 2025 has 7 decks: 6 Grixis, 1 Storm. Every cell reports that year total.
    assert {c.year_total for c in series.cells if c.year == 2025} == {7}
    # The fat Grixis cell carries a real share; the year's cell counts partition it.
    grixis_2025 = by_key[("Grixis", 2025)]
    assert grixis_2025.n == 6
    assert grixis_2025.share == pytest.approx(6 / 7)
    assert sum(c.n for c in series.cells if c.year == 2025) == 7

    # The 1-deck Storm cell is below the floor, so it comes back a gap, not a zero:
    # its deck count is honest but its share is withheld.
    storm_2025 = by_key[("Storm", 2025)]
    assert storm_2025.n == 1
    assert storm_2025.share is None

    # Shares are only asserted where the cell clears the floor.
    for cell in series.cells:
        if cell.share is None:
            assert cell.n < MIN_CELL_DECKS
        else:
            assert cell.n >= MIN_CELL_DECKS
            assert cell.share == pytest.approx(cell.n / cell.year_total)


def test_pooled_cut_keeps_the_strongest_archetypes_until_the_share_is_reached():
    # Pooled deck counts: A=60, B=30, C=10 (total 100), spread across years so the
    # cut must pool over the whole span, not read one year.
    series = Series(cells=[
        _cell("a", 2024, 20), _cell("a", 2025, 40),
        _cell("b", 2025, 30),
        _cell("c", 2024, 10),
    ])
    # 50%: A alone is 60% >= 50%. 75%: A+B is 90% >= 75%. 25%: A alone suffices.
    assert pooled_share_cut(series, 0.50) == ["a"]
    assert pooled_share_cut(series, 0.75) == ["a", "b"]
    assert pooled_share_cut(series, 0.25) == ["a"]
    # Returned strongest-pooled first.
    assert pooled_share_cut(series, 1.0) == ["a", "b", "c"]


def test_pooled_cut_counts_thin_gap_cells_in_the_pool():
    # A gap cell (share withheld) still holds real decks, so its count is part of
    # the pooled population the cut ranks on.
    series = Series(cells=[
        SeriesCell("a", "A", 2025, 4, None, 8),   # a gap, but 4 real decks
        SeriesCell("b", "B", 2025, 4, None, 8),
    ])
    assert pooled_share_cut(series, 0.5) in (["a"], ["b"])
    assert set(pooled_share_cut(series, 1.0)) == {"a", "b"}


def test_pooled_cut_of_an_empty_series_is_empty():
    assert pooled_share_cut(Series(cells=[]), 0.5) == []


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
