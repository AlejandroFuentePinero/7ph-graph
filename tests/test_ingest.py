"""Seam B: the ingestion gate (ADR 0003, issue #5).

Crafted snapshots exercise the union and the superset gate directly. The gate is
a pure function over parsed Snapshots, so these build Snapshot objects from the
domain models rather than going through Kùzu.
"""

import json

import kuzu
import pytest

from graph7ph.ingest import (
    Flag,
    SchemaError,
    gate,
    ingest,
    ingest_report_path,
    load_checked,
    promote,
    union_snapshots,
)
from graph7ph.build import reconciliation_path
from graph7ph.models import Card, Containment, Deck, Snapshot


def deck(deck_id, *, pilot="pilot", event="E", placement=1, **over) -> Deck:
    fields = dict(
        deck_id=deck_id, name=f"{deck_id} deck", deck_name="deck", pilot=pilot, event=event,
        event_type="Tournament", placement=placement, placement_norm=0.0,
        created_at="2025-06-01T00:00:00+00:00",
        colour="colour:U", macro="macro:tempo", engine_tags=[],
        engine_tag_labels={}, primary_tag="", primary_tag_weights={},
    )
    fields.update(over)
    return Deck(**fields)


def card(canon, *, name=None, type="Lands", points=0, **over) -> Card:
    fields = dict(
        canon=canon, name=name or canon.title(), type=type, mana_value=0.0,
        reserved=False, points=points,
    )
    fields.update(over)
    return Card(**fields)


def snap(*, decks=(), cards=(), conts=()) -> Snapshot:
    return Snapshot(cards=list(cards), decks=list(decks), containments=list(conts))


def one(items, key):
    """The single item whose deck_id/canon equals ``key``."""
    return next(i for i in items if getattr(i, "deck_id", None) == key
               or getattr(i, "canon", None) == key)


def test_union_retains_a_record_dropped_by_a_later_snapshot():
    s0 = snap(decks=[deck("d1"), deck("d2")])
    s1 = snap(decks=[deck("d1")])  # d2 has left the newest fetch

    u = union_snapshots([s0, s1])

    assert {d.deck_id for d in u.decks} == {"d1", "d2"}  # d2 is not lost


def test_clean_superset_promotes_with_no_flags():
    prior = snap(decks=[deck("d1")], cards=[card("island")])
    incoming = snap(decks=[deck("d1"), deck("d2")], cards=[card("island")])  # adds d2

    result = gate(prior, incoming)

    assert result.status == "promote"
    assert result.report.flags == []
    # The union is everything held plus the new deck.
    assert {d.deck_id for d in result.snapshot.decks} == {"d1", "d2"}


def test_dropped_id_flags_and_still_unions():
    prior = snap(decks=[deck("d1"), deck("d2")])
    incoming = snap(decks=[deck("d1")])  # d2 gone from the newest fetch

    result = gate(prior, incoming)

    assert result.status == "flag"
    assert result.report.flags == [Flag("dropped", "deck", "d2")]
    # Flagged, but not lost: the union still holds d2.
    assert {d.deck_id for d in result.snapshot.decks} == {"d1", "d2"}


def test_changed_immutable_fact_flags():
    prior = snap(decks=[deck("d1", pilot="alice", placement=3)])
    incoming = snap(decks=[deck("d1", pilot="bob", placement=3)])  # pilot rewritten

    result = gate(prior, incoming)

    assert result.status == "flag"
    assert result.report.flags == [Flag("changed", "deck", "d1")]


def test_changed_decklist_flags_a_deck():
    prior = snap(
        decks=[deck("d1")],
        conts=[Containment(deck_id="d1", canon="island", board="Main")],
    )
    incoming = snap(
        decks=[deck("d1")],
        conts=[Containment(deck_id="d1", canon="swamp", board="Main")],  # card swapped
    )

    result = gate(prior, incoming)

    assert result.report.flags == [Flag("changed", "deck", "d1")]


def test_changed_volatile_field_is_silent_and_takes_the_latest_value():
    # Points move between points versions: volatile, not a historical fact.
    prior = snap(cards=[card("island", points=0)])
    incoming = snap(cards=[card("island", points=4)])

    result = gate(prior, incoming)

    assert result.status == "promote"  # no flag
    assert result.report.flags == []
    assert one(result.snapshot.cards, "island").points == 4  # latest wins


def _write_snapshot_dir(path, *, decks, cards_index):
    path.mkdir(parents=True, exist_ok=True)
    (path / "decks.json").write_text(decks)
    (path / "cards_index.json").write_text(cards_index)


_VALID_DECKS = json.dumps([{
    "deckId": "d1", "name": "n", "deckName": "n", "pilot": "p", "event": "E",
    "eventType": "Tournament", "placement": 1, "placementNorm": 0.0,
    "createdAt": "2025-06-01T00:00:00+00:00",
    "colour": "colour:U", "macro": "macro:tempo", "engineTags": [],
    "engineTagLabels": {}, "primaryTag": "", "primaryTagWeights": {},
}])
_VALID_INDEX = json.dumps({
    "v": 2,
    "cards": [{"canon": "island", "name": "Island", "type": "Lands",
               "manaValue": 0.0, "reserved": False, "points": 0}],
    "decks": {"d1": {"m": [0], "s": []}},
})


def test_valid_snapshot_dir_loads_to_a_snapshot(tmp_path):
    _write_snapshot_dir(tmp_path, decks=_VALID_DECKS, cards_index=_VALID_INDEX)

    snapshot = load_checked(tmp_path)

    assert [d.deck_id for d in snapshot.decks] == ["d1"]


def test_html_body_hard_fails(tmp_path):
    # An error page served instead of JSON must not reach the graph.
    _write_snapshot_dir(
        tmp_path, decks="<!DOCTYPE html><html>502 Bad Gateway</html>",
        cards_index=_VALID_INDEX,
    )

    with pytest.raises(SchemaError):
        load_checked(tmp_path)


def _snapshot_files(deck_ids):
    decks = json.dumps([{
        "deckId": did, "name": "n", "deckName": "n", "pilot": "p", "event": "E",
        "eventId": "evt_1", "eventType": "Tournament",
        "placement": 1, "placementNorm": 0.0,
        "createdAt": "2025-06-01T00:00:00+00:00",
        "colour": "colour:U", "macro": "macro:tempo", "engineTags": [],
        "engineTagLabels": {}, "primaryTag": "", "primaryTagWeights": {},
    } for did in deck_ids])
    index = json.dumps({
        "v": 2,
        "cards": [{"canon": "island", "name": "Island", "type": "Lands",
                   "manaValue": 0.0, "reserved": False, "priceUsd": 0.5,
                   "points": 0}],
        "decks": {did: {"m": [0], "s": []} for did in deck_ids},
    })
    return decks, index


def _deck_ids(db):
    conn = kuzu.Connection(kuzu.Database(str(db)))
    res = conn.execute("MATCH (d:Deck) RETURN d.deckId")
    ids = set()
    while res.has_next():
        ids.add(res.get_next()[0])
    return ids


def test_ingest_unions_all_snapshots_and_promotes_with_a_backup(tmp_path):
    root = tmp_path / "snapshots"
    db = tmp_path / "graph.kuzu"
    d0, i0 = _snapshot_files(["d1", "d2"])
    _write_snapshot_dir(root / "20260101T000000Z", decks=d0, cards_index=i0)

    # First build: two decks, clean, no backup yet.
    report, _ = ingest(root, db)
    assert report.status == "promote"
    assert _deck_ids(db) == {"d1", "d2"}
    assert not (tmp_path / "graph.kuzu.backup").exists()

    # A later fetch drops d2. The build unions every snapshot, so d2 survives,
    # the drop is flagged, and the prior graph is retained as a backup.
    d1, i1 = _snapshot_files(["d1"])
    _write_snapshot_dir(root / "20260201T000000Z", decks=d1, cards_index=i1)

    report, _ = ingest(root, db)

    assert _deck_ids(db) == {"d1", "d2"}  # union across all snapshots
    assert report.status == "flag"
    assert Flag("dropped", "deck", "d2") in report.flags
    assert (tmp_path / "graph.kuzu.backup").exists()
    assert json.loads(ingest_report_path(db).read_text())["status"] == "flag"

    # Rollback is self-consistent: the backup keeps its own matching reports,
    # not the newer build's. The backup is the first build, which promoted clean.
    backup = tmp_path / "graph.kuzu.backup"
    assert json.loads(ingest_report_path(backup).read_text())["status"] == "promote"
    assert reconciliation_path(backup).exists()


def test_ingest_hard_fails_on_a_corrupt_snapshot_without_touching_the_graph(tmp_path):
    root = tmp_path / "snapshots"
    db = tmp_path / "graph.kuzu"
    d0, i0 = _snapshot_files(["d1"])
    _write_snapshot_dir(root / "20260101T000000Z", decks=d0, cards_index=i0)
    ingest(root, db)  # a good graph exists

    # An HTML error page arrives as the newest snapshot.
    _write_snapshot_dir(
        root / "20260201T000000Z", decks="<html>502</html>", cards_index=i0,
    )

    with pytest.raises(SchemaError):
        ingest(root, db)

    # The live graph is untouched: still the good single-deck build.
    assert _deck_ids(db) == {"d1"}


def _db(path, marker):
    """A stand-in for a Kùzu database directory holding a known marker."""
    path.mkdir()
    (path / "data").write_text(marker)
    return path


def test_promote_swaps_in_the_new_graph_and_retains_the_old_as_backup(tmp_path):
    live = _db(tmp_path / "graph.kuzu", "old")
    incoming = _db(tmp_path / "graph.kuzu.incoming", "new")
    backup = tmp_path / "graph.kuzu.backup"

    promote(incoming, live, backup)

    assert (live / "data").read_text() == "new"   # live is now the rebuild
    assert (backup / "data").read_text() == "old"  # previous graph kept for rollback
    assert not incoming.exists()


def test_promote_on_first_build_leaves_no_backup(tmp_path):
    incoming = _db(tmp_path / "graph.kuzu.incoming", "new")
    live = tmp_path / "graph.kuzu"
    backup = tmp_path / "graph.kuzu.backup"

    promote(incoming, live, backup)

    assert (live / "data").read_text() == "new"
    assert not backup.exists()


def test_promote_replaces_an_existing_backup(tmp_path):
    live = _db(tmp_path / "graph.kuzu", "old")
    incoming = _db(tmp_path / "graph.kuzu.incoming", "new")
    backup = _db(tmp_path / "graph.kuzu.backup", "ancient")  # a prior backup lingers

    promote(incoming, live, backup)

    assert (backup / "data").read_text() == "old"  # backup is the just-replaced graph


def test_shape_shifted_response_hard_fails(tmp_path):
    # Valid JSON, wrong shape: the card index lost its 'cards' key.
    _write_snapshot_dir(
        tmp_path, decks=_VALID_DECKS,
        cards_index=json.dumps({"v": 2, "decks": {}}),
    )

    with pytest.raises(SchemaError):
        load_checked(tmp_path)
