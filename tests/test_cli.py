import argparse
import json

import pytest

from graph7ph.__main__ import _baseline, _build
from graph7ph.build import build_graph
from graph7ph.models import load_snapshot


def _snapshot(path, decks):
    path.mkdir(parents=True)
    (path / "decks.json").write_text(json.dumps([
        {"deckId": d, "name": d, "deckName": "n", "pilot": d, "event": "NYE",
         "eventId": "evt_1", "eventType": "Tournament", "placement": 1,
         "placementNorm": 0.0, "createdAt": created, "colour": "colour:U",
         "macro": "macro:control", "engineTags": [], "engineTagLabels": {},
         "primaryTag": "", "primaryTagWeights": {}}
        for d, created in decks
    ]))
    (path / "cards_index.json").write_text(json.dumps({
        "v": 2,
        "cards": [{"canon": "island", "name": "Island", "type": "Lands",
                   "manaCost": None, "manaValue": 0.0, "reserved": False,
                   "priceUsd": 0.5, "points": 0}],
        "decks": {d: {"m": [0], "s": []} for d, _ in decks},
    }))


def test_data_the_build_cannot_support_aborts_cleanly(tmp_path):
    # Data that cannot be built is a user-facing abort, not a crash: ADR 0003
    # hard-fails before the live graph is touched, and the CLI says so rather
    # than spilling a traceback. A year-straddling event is that kind of data.
    _snapshot(tmp_path / "snapshots" / "20260101T000000Z", [
        ("d1", "2025-12-31T00:00:00+00:00"),
        ("d2", "2026-01-01T00:00:00+00:00"),
    ])
    db = tmp_path / "graph.kuzu"

    with pytest.raises(SystemExit) as exc:
        _build(argparse.Namespace(snapshots=tmp_path / "snapshots", db=db))

    assert "Build aborted, live graph untouched" in str(exc.value)
    assert "NYE" in str(exc.value)
    assert not db.exists()


def test_grading_a_graph_that_was_never_built_aborts_cleanly(tmp_path):
    # Running the gate before the graph exists is the likeliest first mistake, and
    # the CLI says what to do rather than spilling a Kùzu traceback.
    with pytest.raises(SystemExit) as exc:
        _baseline(argparse.Namespace(
            db=tmp_path / "graph.kuzu",
            baseline=tmp_path / "baseline.json",
            capture=False,
        ))

    assert "graph7ph build" in str(exc.value)


def test_grading_against_a_missing_baseline_aborts_cleanly(tmp_path, snapshot_dir):
    db = tmp_path / "graph.kuzu"
    build_graph(load_snapshot(snapshot_dir), db)

    with pytest.raises(SystemExit) as exc:
        _baseline(argparse.Namespace(
            db=db, baseline=tmp_path / "nope.json", capture=False
        ))

    assert "nope.json" in str(exc.value)


def test_a_baseline_missing_a_section_aborts_cleanly(tmp_path, snapshot_dir):
    # The gate is meant to be invoked by later tickets, and --baseline takes any
    # path: a malformed oracle must read as a bad baseline, not as a crash.
    db = tmp_path / "graph.kuzu"
    build_graph(load_snapshot(snapshot_dir), db)
    baseline = tmp_path / "partial.json"
    baseline.write_text(json.dumps({"counts": {}, "queries": {}}))

    with pytest.raises(SystemExit) as exc:
        _baseline(argparse.Namespace(db=db, baseline=baseline, capture=False))

    assert "catalogues" in str(exc.value)
