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
    db = tmp_path / "graph"

    with pytest.raises(SystemExit) as exc:
        _build(argparse.Namespace(snapshots=tmp_path / "snapshots", db=db))

    assert "Build aborted, live graph untouched" in str(exc.value)
    assert "NYE" in str(exc.value)
    assert not db.exists()


def test_a_build_tells_the_developer_to_restart_a_running_app(tmp_path, capsys):
    # Promotion renames the directory a running app opened, so it goes on serving
    # the old graph with no error at all. The build is the moment the developer is
    # standing in, so it is where the restart has to be said (issue #59).
    _snapshot(tmp_path / "snapshots" / "20260101T000000Z", [
        ("d1", "2026-01-01T00:00:00+00:00"),
    ])

    _build(argparse.Namespace(snapshots=tmp_path / "snapshots", db=tmp_path / "graph"))

    assert "restart" in capsys.readouterr().out


def test_grading_a_graph_that_was_never_built_aborts_cleanly(tmp_path):
    # Running the gate before the graph exists is the likeliest first mistake, and
    # the CLI says what to do rather than spilling an engine traceback.
    with pytest.raises(SystemExit) as exc:
        _baseline(argparse.Namespace(
            db=tmp_path / "graph",
            baseline=tmp_path / "baseline.json",
            capture=False,
        ))

    assert "graph7ph build" in str(exc.value)


def test_grading_an_artifact_directory_with_no_database_aborts_cleanly(tmp_path):
    # The artifact is a directory now (issue #47), so it can exist while holding no
    # database: a half-cleared or hand-made directory. That must read as "no graph
    # here, build one", the same as no directory at all, not as an engine traceback.
    empty = tmp_path / "graph"
    empty.mkdir()

    with pytest.raises(SystemExit) as exc:
        _baseline(argparse.Namespace(
            db=empty, baseline=tmp_path / "baseline.json", capture=False
        ))

    assert "graph7ph build" in str(exc.value)


def test_grading_against_a_missing_baseline_aborts_cleanly(tmp_path, snapshot_dir):
    db = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), db)

    with pytest.raises(SystemExit) as exc:
        _baseline(argparse.Namespace(
            db=db, baseline=tmp_path / "nope.json", capture=False
        ))

    assert "nope.json" in str(exc.value)


def test_grading_a_bundle_older_than_the_sources_aborts_instead_of_passing(
    tmp_path, snapshot_dir, make_stale
):
    # The whole point of issue #55. The gate re-runs live queries, so it grades a
    # query change honestly even against a stale bundle; ingest, build, schema and
    # curation changes it never executes at all, and would report a green "no
    # regression" about them. Refusing is the only honest answer, and it must be a
    # refusal rather than a pass with a warning: a warning above a "no regression"
    # line is read as noise, which is the failure mode this ticket describes.
    db = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), db)
    built_at = make_stale(db)

    with pytest.raises(SystemExit) as exc:
        _baseline(argparse.Namespace(
            db=db, baseline=tmp_path / "baseline.json", capture=False
        ))

    assert "graph7ph build" in str(exc.value)
    assert built_at in str(exc.value)


def test_capturing_a_baseline_from_a_stale_bundle_aborts_too(
    tmp_path, snapshot_dir, make_stale
):
    # Capturing off a stale artifact is the worse mistake of the two: grading it
    # yields one bad verdict, but capturing it writes the stale graph into the
    # checked-in oracle, and every later run grades against the wrong answer.
    db = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), db)
    make_stale(db)

    with pytest.raises(SystemExit) as exc:
        _baseline(argparse.Namespace(
            db=db, baseline=tmp_path / "baseline.json", capture=True
        ))

    assert "graph7ph build" in str(exc.value)
    assert not (tmp_path / "baseline.json").exists()


def test_a_baseline_missing_a_section_aborts_cleanly(tmp_path, snapshot_dir):
    # The gate is meant to be invoked by later tickets, and --baseline takes any
    # path: a malformed oracle must read as a bad baseline, not as a crash.
    db = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), db)
    baseline = tmp_path / "partial.json"
    baseline.write_text(json.dumps({"counts": {}, "queries": {}}))

    with pytest.raises(SystemExit) as exc:
        _baseline(argparse.Namespace(db=db, baseline=baseline, capture=False))

    assert "catalogues" in str(exc.value)
