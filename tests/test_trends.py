import json
from pathlib import Path

import pytest

from graph7ph.trends import (
    MIN_CELL_DECKS,
    MetaShareOverTime,
    Series,
    SeriesCell,
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
