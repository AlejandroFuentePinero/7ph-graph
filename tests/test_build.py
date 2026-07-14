import kuzu

from graph7ph.build import build_graph
from graph7ph.models import load_snapshot


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
