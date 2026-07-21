import argparse
import functools
import json

import pytest

from graph7ph import baseline as bl
from graph7ph.__main__ import _baseline, _build
from graph7ph.build import build_graph
from graph7ph.models import load_snapshot
from graph7ph.query import HiddenGems


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
                   "points": 0}],
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


def test_a_file_blocking_the_bundle_path_aborts_cleanly(tmp_path):
    # The other way a build cannot honestly proceed: a regular file sitting where
    # a bundle directory has to go. The build stages into `<artifact>.incoming`,
    # so that is the path a stray file actually blocks (issue #52). Same abort
    # shape as unbuildable data: a sentence naming the path, not a traceback.
    _snapshot(tmp_path / "snapshots" / "20260101T000000Z", [
        ("d1", "2026-01-01T00:00:00+00:00"),
    ])
    db = tmp_path / "graph"
    blocked = tmp_path / "graph.incoming"
    blocked.write_text("stray")

    with pytest.raises(SystemExit) as exc:
        _build(argparse.Namespace(snapshots=tmp_path / "snapshots", db=db))

    assert "Build aborted, live graph untouched" in str(exc.value)
    assert str(blocked) in str(exc.value)
    # Neither the live path nor the file in the way is touched by the refusal.
    assert not db.exists()
    assert blocked.read_text() == "stray"


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


def _ns(**kwargs):
    # Every --capture Namespace now needs `force`; default it off so each test
    # names only the flag it exercises.
    return argparse.Namespace(**{"capture": False, "force": False, **kwargs})


@pytest.fixture
def small_cases(monkeypatch):
    """Point the CLI's grader at the cases the 3-deck fixture can actually run.

    The full CASES include the two hidden-gem views, which need >=50 ranked decks
    and raise SliceTooSmall on the fixture. Issue #67 is capture-path orchestration
    (grade, count, refuse unless --force), indifferent to which cases run, so the
    tests drive the real grader and real capture over the subset the fixture
    supports rather than mocking either. Grading correctness lives in
    test_baseline.py.
    """
    subset = [c for c in bl.CASES if not isinstance(c.spec, HiddenGems)]
    monkeypatch.setattr("graph7ph.__main__.check", functools.partial(bl.check, cases=subset))
    monkeypatch.setattr("graph7ph.__main__.capture", functools.partial(bl.capture, cases=subset))


def test_capture_over_a_differing_baseline_refuses_and_leaves_it_unchanged(
    tmp_path, snapshot_dir, small_cases
):
    # The whole point of issue #67: at the moment the data moves the baseline goes
    # red wholesale, and a blind --capture would rubber-stamp a real regression.
    # A differing baseline is overwrite-refused unless --force, and refusing must
    # not touch the file at all.
    db = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), db)
    baseline = tmp_path / "baseline.json"
    _baseline(_ns(db=db, baseline=baseline, capture=True))
    tampered = json.loads(baseline.read_text())
    tampered["counts"]["pilots"] += 1
    baseline.write_text(json.dumps(tampered, indent=2) + "\n")
    before = baseline.read_bytes()

    with pytest.raises(SystemExit) as exc:
        _baseline(_ns(db=db, baseline=baseline, capture=True))

    assert "--force" in str(exc.value)
    assert baseline.read_bytes() == before


def test_first_capture_writes_without_force(tmp_path, snapshot_dir, small_cases):
    # With no oracle on disk there is nothing to grade or override, so the first
    # capture is a plain write (issue #67).
    db = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), db)
    baseline = tmp_path / "baseline.json"

    _baseline(_ns(db=db, baseline=baseline, capture=True))

    assert "catalogues" in json.loads(baseline.read_text())


def test_capture_over_an_identical_baseline_succeeds(
    tmp_path, snapshot_dir, small_cases, capsys
):
    # Capture is non-deterministic (row order and float drift, research-log), so
    # "identical" is check finding no diffs, not byte-identical files. A clean
    # recapture must still be allowed without --force.
    db = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), db)
    baseline = tmp_path / "baseline.json"
    _baseline(_ns(db=db, baseline=baseline, capture=True))
    capsys.readouterr()

    _baseline(_ns(db=db, baseline=baseline, capture=True))

    assert "0 difference(s)" in capsys.readouterr().out


def test_force_over_a_differing_baseline_writes_and_reports_the_count(
    tmp_path, snapshot_dir, small_cases, capsys
):
    db = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), db)
    baseline = tmp_path / "baseline.json"
    _baseline(_ns(db=db, baseline=baseline, capture=True))
    tampered = json.loads(baseline.read_text())
    tampered["counts"]["pilots"] += 1
    baseline.write_text(json.dumps(tampered, indent=2) + "\n")
    before = baseline.read_bytes()
    capsys.readouterr()

    _baseline(_ns(db=db, baseline=baseline, capture=True, force=True))

    # --force writes regardless, and still names a non-zero count it overrode.
    # Not the exact number: that would pin every non-count capture field to be
    # byte-stable across the two captures, which is more than this test asserts.
    out = capsys.readouterr().out
    assert "difference(s)" in out and "0 difference(s)" not in out
    assert baseline.read_bytes() != before


def test_capture_over_a_malformed_baseline_refuses_without_force(
    tmp_path, snapshot_dir, small_cases
):
    # A corrupt-but-nearly-good oracle is the failure this ticket exists to
    # prevent: it must read as a refusal requiring --force, not as a missing
    # baseline, and nothing is written.
    db = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), db)
    baseline = tmp_path / "partial.json"
    baseline.write_text(json.dumps({"counts": {}, "queries": {}}))
    before = baseline.read_bytes()

    with pytest.raises(SystemExit) as exc:
        _baseline(_ns(db=db, baseline=baseline, capture=True))

    assert "--force" in str(exc.value)
    assert baseline.read_bytes() == before


def test_capture_over_an_unparseable_baseline_refuses_without_force(
    tmp_path, snapshot_dir, small_cases
):
    # An empty or truncated baseline (an interrupted write) will not parse as JSON.
    # It is as unusable an oracle as one missing a section, so it refuses the same
    # way rather than crashing with a traceback (issue #67).
    db = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), db)
    baseline = tmp_path / "corrupt.json"
    baseline.write_text('{"counts": {')
    before = baseline.read_bytes()

    with pytest.raises(SystemExit) as exc:
        _baseline(_ns(db=db, baseline=baseline, capture=True))

    assert "--force" in str(exc.value)
    assert baseline.read_bytes() == before


def test_force_over_a_malformed_baseline_writes(tmp_path, snapshot_dir, small_cases):
    db = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), db)
    baseline = tmp_path / "partial.json"
    baseline.write_text(json.dumps({"counts": {}, "queries": {}}))

    _baseline(_ns(db=db, baseline=baseline, capture=True, force=True))

    assert "catalogues" in json.loads(baseline.read_text())


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
