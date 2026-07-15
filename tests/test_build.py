import json

import kuzu

from graph7ph.build import build_graph
from graph7ph.models import load_snapshot


def _connect(tmp_path, snapshot_dir):
    db_path = tmp_path / "graph.kuzu"
    build_graph(load_snapshot(snapshot_dir), db_path)
    return kuzu.Connection(kuzu.Database(str(db_path)))


def _scalar(conn, query, params=None):
    return conn.execute(query, params or {}).get_next()[0]


def test_build_loads_nodes_and_edges_with_source_counts(snapshot_dir, tmp_path):
    snap = load_snapshot(snapshot_dir)

    counts = build_graph(snap, tmp_path / "graph.kuzu")

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
        "placementNorm": 0.0, "colour": "colour:UB", "macro": "macro:control",
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

    db_path = tmp_path / "graph.kuzu"
    build_graph(load_snapshot(tmp_path), db_path)
    conn = kuzu.Connection(kuzu.Database(str(db_path)))

    edges = {
        name: (weight, is_primary)
        for name, weight, is_primary in _iter(conn,
            """MATCH (:Deck {deckId: 'd1'})-[r:HAS_ARCHETYPE]->(a:Archetype)
               RETURN a.name, r.weight, r.isPrimary""")
    }
    # Each archetype keeps its own weight; only the primary tag is flagged.
    assert edges == {"Grixis": (70, True), "Control": (30, False)}


def _iter(conn, query, params=None):
    res = conn.execute(query, params or {})
    while res.has_next():
        yield res.get_next()


def test_built_graph_is_queryable_with_expected_shape(tmp_path, snapshot_dir):
    snap = load_snapshot(snapshot_dir)
    db_path = tmp_path / "graph.kuzu"
    build_graph(snap, db_path)

    conn = kuzu.Connection(kuzu.Database(str(db_path)))
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
