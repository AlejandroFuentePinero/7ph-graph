import json

import kuzu

from graph7ph.build import build_graph
from graph7ph.models import load_snapshot
from graph7ph.query import pilot_subgraph


def _connect(tmp_path, snapshot_dir):
    db_path = tmp_path / "graph.kuzu"
    build_graph(load_snapshot(snapshot_dir), db_path)
    return kuzu.Connection(kuzu.Database(str(db_path)))


def _expected_cards_for(snapshot_dir, deck_ids):
    """Independent oracle: distinct canons across the given decks, straight
    from the raw index rather than through the graph."""
    index = json.loads((snapshot_dir / "cards_index.json").read_text())
    canon = [c["canon"] for c in index["cards"]]
    cards = set()
    for did in deck_ids:
        for board in ("m", "s"):
            cards.update(canon[i] for i in index["decks"][did][board])
    return cards


def test_pilot_subgraph_is_the_pilots_decks_and_their_cards(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)

    sub = pilot_subgraph(conn, "Jordan C")

    kinds = {n.kind: [] for n in sub.nodes}
    for n in sub.nodes:
        kinds[n.kind].append(n)

    # One pilot, their two decks, and the union of cards in those decks.
    assert [n.label for n in kinds["Pilot"]] == ["Jordan C"]
    assert len(kinds["Deck"]) == 2

    jordan_decks = {"BsegXnsDsEWxh-vNbUrn0w", "pkUbzmgN3UeqaWdYQYRgRg"}
    expected_cards = _expected_cards_for(snapshot_dir, jordan_decks)
    assert {n.id for n in kinds["Card"]} == {f"card:{c}" for c in expected_cards}


def test_pilot_subgraph_edges_connect_decks_to_pilot_and_cards(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)

    sub = pilot_subgraph(conn, "Jordan C")

    piloted = [e for e in sub.edges if e.label == "PILOTED_BY"]
    contains = [e for e in sub.edges if e.label.startswith("CONTAINS")]

    # Every deck links to the pilot; each of 2 decks contributes 75 card edges.
    assert len(piloted) == 2
    assert len(contains) == 150

    node_ids = {n.id for n in sub.nodes}
    for e in sub.edges:
        assert e.source in node_ids and e.target in node_ids


def test_unknown_pilot_yields_empty_subgraph(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)

    sub = pilot_subgraph(conn, "Nobody At All")

    assert sub.nodes == []
    assert sub.edges == []
