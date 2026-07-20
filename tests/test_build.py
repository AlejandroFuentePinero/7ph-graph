import json

import kuzu
import pytest

from graph7ph.build import build_graph, reconciliation_path
from graph7ph.curation import ArchetypeOverride, Curation
from graph7ph.db import database_path
from graph7ph.models import load_snapshot


def _connect(tmp_path, snapshot_dir):
    db_path = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), db_path)
    return kuzu.Connection(kuzu.Database(str(database_path(db_path))))


def _scalar(conn, query, params=None):
    return conn.execute(query, params or {}).get_next()[0]


def _deck(deck_id, event, created_at):
    """A minimal deck record, for snapshots crafted to exercise one behaviour.

    The title carries the deck id so each recovers a distinct name: same-named
    decks join on identity (ADR 0007) and a card-identical pair then collapses,
    which would silently merge fixtures meant to stay separate registrations.
    """
    return {"deckId": deck_id, "name": deck_id, "deckName": "n", "pilot": deck_id,
            "event": event, "eventId": f"evt_{event}", "eventType": "Tournament",
            "placement": 1, "placementNorm": 0.0, "createdAt": created_at,
            "colour": "colour:U", "macro": "macro:control", "engineTags": [],
            "engineTagLabels": {}, "primaryTag": "", "primaryTagWeights": {}}


def _write_snapshot(path, decks):
    (path / "decks.json").write_text(json.dumps(decks))
    (path / "cards_index.json").write_text(json.dumps({
        "v": 2,
        "cards": [{"canon": "island", "name": "Island", "type": "Lands",
                   "manaCost": None, "manaValue": 0.0, "reserved": False,
                   "priceUsd": 0.5, "points": 0}],
        "decks": {d["deckId"]: {"m": [0], "s": []} for d in decks},
    }))


def test_build_loads_nodes_and_edges_with_source_counts(snapshot_dir, tmp_path):
    snap = load_snapshot(snapshot_dir)

    counts = build_graph(snap, tmp_path / "graph")

    # Counts are read back out of the built graph, so this asserts the graph
    # actually holds one node/edge per source record (issue-2 AC #6).
    assert counts.pilots == 2
    assert counts.decks == 3
    assert counts.cards == 121
    assert counts.piloted_by == 3
    assert counts.contains == 225

    # The full v1 model's new node types (issue-3 AC #1).
    assert counts.events == 2          # CFWAT25, PogNov25
    assert counts.archetypes == 2      # Grixis, Storm
    assert counts.macros == 2          # tempo, combo
    assert counts.colours == 5         # the five atoms, always
    assert counts.card_types == 7      # distinct card types in the fixture

    # The new fact edges (issue-3 AC #2, #3).
    assert counts.played_at == 3       # one per deck
    assert counts.has_archetype == 3   # one tag per fixture deck
    assert counts.has_macro == 3
    assert counts.deck_colour == 10    # UBR + UBR + UBRG
    assert counts.card_colour == 95    # colours derived from mana pips
    assert counts.has_type == 121      # one per card

    # The temporal dimension (issue-26 AC #3): both fixture events sit in 2025.
    assert counts.years == 1
    assert counts.in_year == 2         # one per event


def test_event_links_to_the_year_its_decks_were_created_in(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)

    assert _scalar(
        conn, "MATCH (:Event {event: 'PogNov25'})-[:IN_YEAR]->(y:Year) RETURN y.year"
    ) == 2025

    # Every Event links to exactly one Year (issue-26 AC #1): min and max both,
    # since min alone would miss an Event that had picked up two.
    assert list(_iter(
        conn,
        "MATCH (e:Event) OPTIONAL MATCH (e)-[:IN_YEAR]->(y:Year) "
        "WITH e, count(y) AS n RETURN min(n), max(n)",
    )) == [[1, 1]]


def test_event_spanning_several_days_resolves_to_one_year(tmp_path):
    # An event's decks trickle in over days, and often across a month boundary;
    # they still collapse to the single Year the event ran in (issue-26 AC #7).
    # Shaped after PogNov25, whose real decks span 2025-11-29 to 2025-12-01.
    _write_snapshot(tmp_path, [
        _deck("d1", "PogNov25", "2025-11-29T13:01:51+00:00"),
        _deck("d2", "PogNov25", "2025-11-30T12:00:00+00:00"),
        _deck("d3", "PogNov25", "2025-12-01T05:41:48+00:00"),
    ])

    counts = build_graph(load_snapshot(tmp_path), tmp_path / "graph")
    conn = kuzu.Connection(kuzu.Database(str(database_path(tmp_path / "graph"))))

    assert counts.years == 1
    assert counts.in_year == 1
    assert _scalar(
        conn, "MATCH (:Event {event: 'PogNov25'})-[:IN_YEAR]->(y:Year) RETURN y.year"
    ) == 2025


def test_the_build_writes_the_database_and_its_report_into_one_bundle(
    snapshot_dir, tmp_path
):
    # The artifact is a directory containing the database, not a directory that is
    # the database (issue #47). Everything the build produces lands inside it, so
    # a single rename of that one path promotes the graph and its reports together.
    artifact = tmp_path / "graph"

    build_graph(load_snapshot(snapshot_dir), artifact)

    assert database_path(artifact).exists()
    assert reconciliation_path(artifact).exists()
    assert reconciliation_path(artifact).parent == artifact
    # Nothing escapes the bundle: a sibling would not travel with the rename.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["graph"]


def test_events_in_different_years_get_distinct_year_nodes(tmp_path):
    _write_snapshot(tmp_path, [
        _deck("d1", "E2024", "2024-03-01T00:00:00+00:00"),
        _deck("d2", "E2026", "2026-02-01T00:00:00+00:00"),
    ])

    counts = build_graph(load_snapshot(tmp_path), tmp_path / "graph")
    conn = kuzu.Connection(kuzu.Database(str(database_path(tmp_path / "graph"))))

    assert counts.years == 2
    assert counts.in_year == 2
    assert dict(_iter(conn,
        "MATCH (e:Event)-[:IN_YEAR]->(y:Year) RETURN e.event, y.year")) == {
        "E2024": 2024, "E2026": 2026}


def test_event_straddling_two_calendar_years_fails_the_build(tmp_path):
    # createdAt only dates an event while its decks share one calendar year, so
    # a straddle must fail loudly rather than silently take the earlier year
    # (issue-26 AC #4, ADR 0006).
    _write_snapshot(tmp_path, [
        _deck("d1", "NYE", "2025-12-31T00:00:00+00:00"),
        _deck("d2", "NYE", "2026-01-01T00:00:00+00:00"),
    ])

    with pytest.raises(ValueError, match="NYE"):
        build_graph(load_snapshot(tmp_path), tmp_path / "graph")


def test_a_failed_build_leaves_the_bundle_it_was_pointed_at_untouched(
    tmp_path, snapshot_dir
):
    # Everything that can reject the data runs before the bundle is touched, so a
    # build aimed straight at a live artifact cannot damage it on the way to
    # failing. Without that ordering a straddle would clear the bundle first and
    # leave an empty directory where a good graph used to be.
    artifact = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), artifact)
    before = reconciliation_path(artifact).read_text()

    straddle = tmp_path / "straddle"
    straddle.mkdir()
    _write_snapshot(straddle, [
        _deck("d1", "NYE", "2025-12-31T00:00:00+00:00"),
        _deck("d2", "NYE", "2026-01-01T00:00:00+00:00"),
    ])

    with pytest.raises(ValueError, match="NYE"):
        build_graph(load_snapshot(straddle), artifact)

    assert database_path(artifact).exists()
    assert reconciliation_path(artifact).read_text() == before


def test_deck_carries_colour_identity_and_dimension_edges(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)
    grixis = "BsegXnsDsEWxh-vNbUrn0w"

    # Colour identity is a Deck property (ADR 0002); the atoms are edges.
    assert _scalar(
        conn, "MATCH (d:Deck {deckId: $id}) RETURN d.colourIdentity", {"id": grixis}
    ) == "UBR"

    colours = {
        r[0]
        for r in _iter(conn,
            "MATCH (:Deck {deckId: $id})-[:DECK_COLOUR]->(c:Colour) RETURN c.colour",
            {"id": grixis})
    }
    assert colours == {"U", "B", "R"}

    assert _scalar(
        conn,
        "MATCH (:Deck {deckId: $id})-[:HAS_MACRO]->(m:`Macro`) RETURN m.name",
        {"id": grixis},
    ) == "tempo"

    assert _scalar(
        conn,
        "MATCH (:Deck {deckId: $id})-[:PLAYED_AT]->(e:Event) RETURN e.event",
        {"id": grixis},
    ) == "CFWAT25"


def test_deck_to_archetype_carries_weight_and_primary_flag(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)
    grixis = "BsegXnsDsEWxh-vNbUrn0w"

    row = conn.execute(
        """MATCH (:Deck {deckId: $id})-[r:HAS_ARCHETYPE]->(a:Archetype)
           RETURN a.name, r.weight, r.isPrimary""",
        {"id": grixis},
    ).get_next()
    assert row == ["Grixis", 100, True]


def test_card_links_to_type_and_to_each_pip_colour(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)

    # A two-colour card links to each of its colours (issue-3 AC #3).
    strix_colours = {
        r[0]
        for r in _iter(conn,
            "MATCH (:Card {canon: 'baleful strix'})-[:CARD_COLOUR]->(c:Colour) "
            "RETURN c.colour")
    }
    assert strix_colours == {"U", "B"}

    # A land with no mana cost has no colour edges.
    assert _scalar(
        conn,
        "MATCH (:Card {canon: 'arid mesa'})-[:CARD_COLOUR]->(c:Colour) "
        "RETURN count(c)",
    ) == 0

    assert _scalar(
        conn,
        "MATCH (:Card {canon: 'arid mesa'})-[:HAS_TYPE]->(t:CardType) RETURN t.type",
    ) == "Lands"


def test_multi_archetype_deck_weights_and_flags_each_edge(tmp_path):
    # A deck may carry several archetypes, each weighted, with one primary
    # (CONTEXT.md / issue-3 AC #2). The shared fixture only has single-archetype
    # decks, so this hand-authored snapshot exercises the multi-archetype path.
    (tmp_path / "decks.json").write_text(json.dumps([{
        "deckId": "d1", "name": "n", "deckName": "n", "pilot": "p", "event": "E",
        "eventId": "evt_1", "eventType": "Tournament", "placement": 1,
        "placementNorm": 0.0, "createdAt": "2025-06-01T00:00:00+00:00",
        "colour": "colour:UB", "macro": "macro:control",
        "engineTags": ["engine:grixis", "engine:control"],
        "engineTagLabels": {"engine:grixis": "Grixis", "engine:control": "Control"},
        "primaryTag": "engine:grixis",
        "primaryTagWeights": {"engine:grixis": 70, "engine:control": 30},
    }]))
    (tmp_path / "cards_index.json").write_text(json.dumps({
        "v": 2,
        "cards": [{"canon": "island", "name": "Island", "type": "Lands",
                   "manaCost": None, "manaValue": 0.0, "reserved": False,
                   "priceUsd": 0.5, "points": 0}],
        "decks": {"d1": {"m": [0], "s": []}},
    }))

    db_path = tmp_path / "graph"
    build_graph(load_snapshot(tmp_path), db_path)
    conn = kuzu.Connection(kuzu.Database(str(database_path(db_path))))

    edges = {
        name: (weight, is_primary)
        for name, weight, is_primary in _iter(conn,
            """MATCH (:Deck {deckId: 'd1'})-[r:HAS_ARCHETYPE]->(a:Archetype)
               RETURN a.name, r.weight, r.isPrimary""")
    }
    # Each archetype keeps its own weight; only the primary tag is flagged.
    assert edges == {"Grixis": (70, True), "Control": (30, False)}


def test_deck_archetype_override_reclassifies_a_mistitled_deck(tmp_path):
    # A deck the source mistitled ("Blue Moon", so engine:blue_moon won primary
    # over the izzet_prowess the cards show) is corrected by a [[deck_archetype]]
    # entry, which collapses it onto the single corrected engine (issue #9).
    (tmp_path / "decks.json").write_text(json.dumps([{
        "deckId": "d1", "name": "n", "deckName": "Blue Moon", "pilot": "p",
        "event": "E", "eventId": "evt_1", "eventType": "Tournament",
        "placement": 1, "placementNorm": 0.0,
        "createdAt": "2026-04-25T00:00:00+00:00",
        "colour": "colour:UR", "macro": "macro:tempo",
        "engineTags": ["engine:blue_moon", "engine:izzet_prowess"],
        "engineTagLabels": {"engine:blue_moon": "Blue Moon",
                            "engine:izzet_prowess": "Izzet Prowess"},
        "primaryTag": "engine:blue_moon",
        "primaryTagWeights": {"engine:blue_moon": 85, "engine:izzet_prowess": 15},
    }]))
    (tmp_path / "cards_index.json").write_text(json.dumps({
        "v": 2,
        "cards": [{"canon": "island", "name": "Island", "type": "Lands",
                   "manaCost": None, "manaValue": 0.0, "reserved": False,
                   "priceUsd": 0.5, "points": 0}],
        "decks": {"d1": {"m": [0], "s": []}},
    }))
    curation = Curation(
        merges={}, rejected=frozenset(), names={}, deck_pilots={},
        deck_archetypes={"d1": ArchetypeOverride(
            deck_name="UR Prowess", engine="engine:izzet_prowess",
            engine_label="Izzet Prowess")},
    )

    db_path = tmp_path / "graph"
    build_graph(load_snapshot(tmp_path), db_path, curation)
    conn = kuzu.Connection(kuzu.Database(str(database_path(db_path))))

    # The deck now carries the corrected name and a single Izzet Prowess
    # archetype at full weight; the mistitled Blue Moon tag is gone entirely.
    assert _scalar(conn, "MATCH (d:Deck {deckId: 'd1'}) RETURN d.deckName") == "UR Prowess"
    edges = {
        name: (weight, is_primary)
        for name, weight, is_primary in _iter(conn,
            """MATCH (:Deck {deckId: 'd1'})-[r:HAS_ARCHETYPE]->(a:Archetype)
               RETURN a.name, r.weight, r.isPrimary""")
    }
    assert edges == {"Izzet Prowess": (100, True)}


def _iter(conn, query, params=None):
    res = conn.execute(query, params or {})
    while res.has_next():
        yield res.get_next()


def test_built_graph_is_queryable_with_expected_shape(tmp_path, snapshot_dir):
    snap = load_snapshot(snapshot_dir)
    db_path = tmp_path / "graph"
    build_graph(snap, db_path)

    conn = kuzu.Connection(kuzu.Database(str(database_path(db_path))))
    res = conn.execute(
        "MATCH (d:Deck {deckId: $id})-[:PILOTED_BY]->(p:Pilot) RETURN p.pilot",
        {"id": "BsegXnsDsEWxh-vNbUrn0w"},
    )
    assert res.get_next()[0] == "Jordan C"

    res = conn.execute(
        "MATCH (:Deck {deckId: $id})-[c:CONTAINS]->(:Card) "
        "RETURN c.board, count(*) ORDER BY c.board",
        {"id": "BsegXnsDsEWxh-vNbUrn0w"},
    )
    rows = {}
    while res.has_next():
        board, n = res.get_next()
        rows[board] = n
    # 60 Main + 15 Side for this deck.
    assert rows == {"Main": 60, "Side": 15}
