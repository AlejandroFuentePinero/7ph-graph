"""Seam B: the ingestion gate (ADR 0003, issue #5).

Crafted snapshots exercise the union and the superset gate directly. The gate is
a pure function over parsed Snapshots, so these build Snapshot objects from the
domain models rather than going through the graph store.
"""

import json

import ladybug
import pytest

from graph7ph.ingest import (
    Flag,
    SchemaError,
    gate,
    gate_sequence,
    ingest,
    ingest_report_path,
    load_checked,
    promote,
    union_snapshots,
)
from graph7ph.build import reconciliation_path
from graph7ph.db import DB_FILENAME, database_path
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


def test_gate_sequence_flags_an_interior_immutable_fact_rewrite():
    # F2: the alice->bob pilot rewrite happens in an interior transition. A gate
    # that only compared union(s0,s1)=bob against s2=bob would call this a clean
    # promote and silently absorb the rewrite. The fold over adjacent transitions
    # must catch it.
    s0 = snap(decks=[deck("d1", pilot="alice")])
    s1 = snap(decks=[deck("d1", pilot="bob")])  # rewrite here, interior
    s2 = snap(decks=[deck("d1", pilot="bob")])

    result = gate_sequence([s0, s1, s2])

    assert result.status == "flag"
    assert result.report.flags == [Flag("changed", "deck", "d1")]


def test_gate_sequence_flags_a_change_across_a_drop_and_reappear():
    # A deck vanishes from an interim fetch, then returns with a rewritten pilot.
    # Comparing only raw adjacent snapshots sees s0->s1 as a plain drop and s1->s2
    # as a fresh addition, missing the alice->bob rewrite. Gating against the
    # accumulated union (which retains the dropped deck) catches it as a changed
    # fact and retain-old pins the pre-change pilot.
    s0 = snap(decks=[deck("d1", pilot="alice")])
    s1 = snap(decks=[])  # d1 absent from this fetch
    s2 = snap(decks=[deck("d1", pilot="bob")])  # returns, rewritten

    result = gate_sequence([s0, s1, s2])

    assert result.status == "flag"
    assert Flag("changed", "deck", "d1") in result.report.flags
    assert one(result.snapshot.decks, "d1").pilot == "alice"  # old value retained


def test_gate_sequence_retains_the_old_value_of_a_flagged_immutable_fact():
    # F9: a flagged immutable-fact rewrite must not silently reach the live graph.
    # The contract is retain-old: the union pins the flagged deck to its first-seen
    # (pre-change) pilot until a human resolves it, rather than taking the rewrite.
    s0 = snap(decks=[deck("d1", pilot="alice")])
    s1 = snap(decks=[deck("d1", pilot="bob")])

    result = gate_sequence([s0, s1])

    assert result.status == "flag"
    assert one(result.snapshot.decks, "d1").pilot == "alice"  # old value retained


def test_gate_sequence_retains_the_old_decklist_of_a_flagged_deck():
    s0 = snap(
        decks=[deck("d1")],
        conts=[Containment(deck_id="d1", canon="island", board="Main")],
    )
    s1 = snap(
        decks=[deck("d1")],
        conts=[Containment(deck_id="d1", canon="swamp", board="Main")],  # swapped
    )

    result = gate_sequence([s0, s1])

    cards = {c.canon for c in result.snapshot.containments if c.deck_id == "d1"}
    assert cards == {"island"}  # the pre-change decklist is retained


def test_gate_sequence_takes_latest_volatile_value_without_flagging():
    # A volatile change (points) is not flagged, so retain-old never fires: the
    # union still takes the latest value.
    s0 = snap(cards=[card("island", points=0)])
    s1 = snap(cards=[card("island", points=4)])

    result = gate_sequence([s0, s1])

    assert result.status == "promote"
    assert one(result.snapshot.cards, "island").points == 4


def test_gate_sequence_over_a_clean_sequence_promotes():
    s0 = snap(decks=[deck("d1")], cards=[card("island")])
    s1 = snap(decks=[deck("d1"), deck("d2")], cards=[card("island")])  # pure addition

    result = gate_sequence([s0, s1])

    assert result.status == "promote"
    assert result.report.flags == []
    assert {d.deck_id for d in result.snapshot.decks} == {"d1", "d2"}


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


def _snapshot_files(deck_ids, pilot=None):
    # Each deck gets its own pilot and its own recovered name (from the title) so
    # two decks in one snapshot stay distinct registrations: same-named decks
    # join on identity (ADR 0007) and a card-identical pair then collapses. Pass
    # ``pilot`` to give every deck the same pilot (for immutable-fact tests).
    decks = json.dumps([{
        "deckId": did, "name": did, "deckName": "n", "pilot": pilot or f"p_{did}", "event": "E",
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
    conn = ladybug.Connection(ladybug.Database(str(database_path(db))))
    res = conn.execute("MATCH (d:Deck) RETURN d.deckId")
    ids = set()
    while res.has_next():
        ids.add(res.get_next()[0])
    return ids


def test_ingest_unions_all_snapshots_and_promotes_with_a_backup(tmp_path):
    root = tmp_path / "snapshots"
    db = tmp_path / "graph"
    d0, i0 = _snapshot_files(["d1", "d2"])
    _write_snapshot_dir(root / "20260101T000000Z", decks=d0, cards_index=i0)

    # First build: two decks, clean, no backup yet.
    report, _ = ingest(root, db)
    assert report.status == "promote"
    assert _deck_ids(db) == {"d1", "d2"}
    assert not (tmp_path / "graph.backup").exists()

    # A later fetch drops d2. The build unions every snapshot, so d2 survives,
    # the drop is flagged, and the prior graph is retained as a backup.
    d1, i1 = _snapshot_files(["d1"])
    _write_snapshot_dir(root / "20260201T000000Z", decks=d1, cards_index=i1)

    report, _ = ingest(root, db)

    assert _deck_ids(db) == {"d1", "d2"}  # union across all snapshots
    assert report.status == "flag"
    assert Flag("dropped", "deck", "d2") in report.flags
    assert (tmp_path / "graph.backup").exists()
    assert json.loads(ingest_report_path(db).read_text())["status"] == "flag"

    # Rollback is self-consistent: the backup keeps its own matching reports,
    # not the newer build's. The backup is the first build, which promoted clean.
    backup = tmp_path / "graph.backup"
    assert json.loads(ingest_report_path(backup).read_text())["status"] == "promote"
    assert reconciliation_path(backup).exists()


def test_promote_moves_the_graph_and_its_reports_as_one_bundle(tmp_path):
    # F13: the reports live inside the graph directory, so the single directory
    # rename promotes graph + both reports together. A promote can never leave a
    # new graph paired with stale reports, because there is no separate report
    # rename that could be interrupted between them.
    db = tmp_path / "graph"
    backup = tmp_path / "graph.backup"
    assert db == ingest_report_path(db).parent == reconciliation_path(db).parent

    live = _db(db, "old graph")
    ingest_report_path(live).write_text('{"gen": "old"}')
    reconciliation_path(live).write_text('{"gen": "old"}')
    incoming = _db(tmp_path / "graph.incoming", "new graph")
    ingest_report_path(incoming).write_text('{"gen": "new"}')
    reconciliation_path(incoming).write_text('{"gen": "new"}')

    promote(incoming, db, backup)

    # The live graph and both its reports are the new generation, together.
    assert (db / DB_FILENAME).read_text() == "new graph"
    assert json.loads(ingest_report_path(db).read_text())["gen"] == "new"
    assert json.loads(reconciliation_path(db).read_text())["gen"] == "new"
    # The backup keeps its own matching (old) reports for a consistent rollback.
    assert (backup / DB_FILENAME).read_text() == "old graph"
    assert json.loads(ingest_report_path(backup).read_text())["gen"] == "old"
    # No report was promoted on its own: none left orphaned beside the graph dirs.
    assert not (tmp_path / "graph.ingest.json").exists()
    assert not (tmp_path / "graph.reconciliation.json").exists()


def test_the_promoted_artifact_is_a_bundle_holding_the_database_and_both_reports(
    tmp_path,
):
    # Issue #47: the artifact is a directory containing the database rather than a
    # directory that is the database, so the reports can keep living inside it once
    # the database becomes a single file. Promotion stays one rename of one path.
    root = tmp_path / "snapshots"
    artifact = tmp_path / "graph"
    d0, i0 = _snapshot_files(["d1"])
    _write_snapshot_dir(root / "20260101T000000Z", decks=d0, cards_index=i0)

    ingest(root, artifact)

    assert database_path(artifact).exists()
    assert ingest_report_path(artifact).exists()
    assert reconciliation_path(artifact).exists()
    # The build's scratch bundle is gone, and nothing leaked out beside the
    # artifact: whatever is not inside it would not survive the rename.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["graph", "snapshots"]


def test_interior_rewrite_in_a_three_snapshot_build_flags_and_retains_old(tmp_path):
    # End to end (F2 + F9): three snapshots where the interior one rewrites deck
    # d1's pilot from Alice to Bob. The build must flag the change and keep the
    # pre-change pilot, not silently absorb the rewrite the old single-transition
    # gate would have missed.
    root = tmp_path / "snapshots"
    db = tmp_path / "graph"

    for name, pilot in [("20260101T000000Z", "Alice"),
                        ("20260201T000000Z", "Bob"),   # interior rewrite
                        ("20260301T000000Z", "Bob")]:
        d, i = _snapshot_files(["d1"], pilot=pilot)
        _write_snapshot_dir(root / name, decks=d, cards_index=i)

    report, _ = ingest(root, db)

    assert report.status == "flag"
    assert Flag("changed", "deck", "d1") in report.flags
    conn = ladybug.Connection(ladybug.Database(str(database_path(db))))
    res = conn.execute("MATCH (p:Pilot) RETURN p.pilot")
    pilots = set()
    while res.has_next():
        pilots.add(res.get_next()[0])
    assert pilots == {"Alice"}  # pre-change pilot retained, not the Bob rewrite


def test_ingest_hard_fails_on_a_corrupt_snapshot_without_touching_the_graph(tmp_path):
    root = tmp_path / "snapshots"
    db = tmp_path / "graph"
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
    """A stand-in artifact bundle whose database is a known marker string.

    Promotion moves whole bundles by rename and never reads what is inside them,
    so a readable marker where the database goes is enough to tell which bundle
    ended up where. Named through ``DB_FILENAME`` rather than a literal, so the
    shape here stays the shape a real bundle has (ADR 0008).
    """
    path.mkdir()
    (path / DB_FILENAME).write_text(marker)
    return path


def test_promote_swaps_in_the_new_graph_and_retains_the_old_as_backup(tmp_path):
    live = _db(tmp_path / "graph", "old")
    incoming = _db(tmp_path / "graph.incoming", "new")
    backup = tmp_path / "graph.backup"

    promote(incoming, live, backup)

    assert (live / DB_FILENAME).read_text() == "new"   # live is now the rebuild
    assert (backup / DB_FILENAME).read_text() == "old"  # previous graph kept for rollback
    assert not incoming.exists()


def test_promote_on_first_build_leaves_no_backup(tmp_path):
    incoming = _db(tmp_path / "graph.incoming", "new")
    live = tmp_path / "graph"
    backup = tmp_path / "graph.backup"

    promote(incoming, live, backup)

    assert (live / DB_FILENAME).read_text() == "new"
    assert not backup.exists()


def test_promote_replaces_an_existing_backup(tmp_path):
    live = _db(tmp_path / "graph", "old")
    incoming = _db(tmp_path / "graph.incoming", "new")
    backup = _db(tmp_path / "graph.backup", "ancient")  # a prior backup lingers

    promote(incoming, live, backup)

    assert (backup / DB_FILENAME).read_text() == "old"  # backup is the just-replaced graph


def test_shape_shifted_response_hard_fails(tmp_path):
    # Valid JSON, wrong shape: the card index lost its 'cards' key.
    _write_snapshot_dir(
        tmp_path, decks=_VALID_DECKS,
        cards_index=json.dumps({"v": 2, "decks": {}}),
    )

    with pytest.raises(SchemaError):
        load_checked(tmp_path)
