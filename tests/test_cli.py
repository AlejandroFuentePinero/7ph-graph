import argparse
import functools
import json

import pytest

from graph7ph import baseline as bl
from graph7ph.__main__ import _baseline, _build
from graph7ph.build import build_graph
from graph7ph.curation import load_curation
from graph7ph.db import database_path
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
                   "points": 0, "pointsCompanion": 0}],
        "decks": {d: {"m": [0], "s": []} for d, _ in decks},
    }))


def _titled_snapshot(path, decks):
    """A snapshot whose deck titles carry recoverable pilot names.

    ``_snapshot`` seats every deck at rank 1 under its own deck id as the pilot,
    which recovers no name and can never put two ids in one event. Curation
    tests need both, so each deck here names its pilot, title, event and rank.
    """
    path.mkdir(parents=True)
    (path / "decks.json").write_text(json.dumps([
        {"deckId": deck, "name": title, "deckName": "n", "pilot": pilot,
         "event": event, "eventId": f"evt_{event}", "eventType": "Tournament",
         "placement": placement, "placementNorm": 0.0, "eventSize": 2,
         "createdAt": "2026-01-01T00:00:00+00:00", "colour": "colour:U",
         "macro": "macro:control", "engineTags": [], "engineTagLabels": {},
         "primaryTag": "", "primaryTagWeights": {}}
        for deck, pilot, title, event, placement in decks
    ]))
    (path / "cards_index.json").write_text(json.dumps({
        "v": 2,
        "cards": [{"canon": "island", "name": "Island", "type": "Lands",
                   "manaCost": None, "manaValue": 0.0, "reserved": False,
                   "points": 0, "pointsCompanion": 0}],
        "decks": {deck: {"m": [0], "s": []} for deck, *_ in decks},
    }))


def test_a_build_announces_the_holds_the_data_has_now_moved(
    tmp_path, capsys, monkeypatch
):
    # A hold records the condition that would settle it, so the build re-evaluates
    # that condition every ingestion and growing data does the review work instead
    # of a human re-reading the list (issue #230). A settled pair is an available
    # decision sitting unmade, so it gets the banner a scrolling build log cannot
    # bury; a hold whose evidence merely moved is softer and gets a counted line.
    snapshots = tmp_path / "snapshots"
    ids = ["NimbleBlackEagle", "FrostyBlueOtter"]
    apart = [
        ("d1", ids[0], "01st Joe M - Grixis - E1", "E1", 1),
        ("d2", ids[1], "01st Joel M - Grixis - E2", "E2", 1),
    ]
    _titled_snapshot(snapshots / "20260101T000000Z", apart)
    monkeypatch.setattr("graph7ph.build.load_curation",
                        functools.partial(load_curation, tmp_path / "pilots.toml"))
    (tmp_path / "pilots.toml").write_text(
        f'[[hold]]\nids = {json.dumps(ids)}\n'
        'settles_on = "shared_event"\nas_of = "20260101T000000Z"\n'
    )

    # Nothing has moved since the reasoning was written: the pair is held, and
    # the build says nothing further about it.
    _build(argparse.Namespace(snapshots=snapshots, db=tmp_path / "g1"))
    out = capsys.readouterr().out
    assert "1 held, 0 unexamined" in out
    assert "DECISION AVAILABLE" not in out
    assert "evidence moved" not in out

    # A later fetch gives one side a deck the reasoning never saw. Nothing is
    # settled, so this is a line, not a banner.
    _titled_snapshot(snapshots / "20260201T000000Z", apart + [
        ("d3", ids[1], "01st Joel M - Walks - E3", "E3", 1),
    ])
    _build(argparse.Namespace(snapshots=snapshots, db=tmp_path / "g2"))
    out = capsys.readouterr().out
    assert "1 hold(s) whose evidence moved" in out
    assert "DECISION AVAILABLE" not in out

    # A third fetch puts both ids at one event. Two entries at one event are two
    # people (ADR 0005), so the pair is decided and the build says so loudly.
    _titled_snapshot(snapshots / "20260301T000000Z", apart + [
        ("d3", ids[1], "01st Joel M - Walks - E3", "E3", 1),
        ("d4", ids[1], "02nd Joel M - Walks - E1", "E1", 2),
    ])
    _build(argparse.Namespace(snapshots=snapshots, db=tmp_path / "g3"))
    out = capsys.readouterr().out
    assert "DECISION AVAILABLE" in out
    assert "1 hold(s) settled" in out
    # Decided, so the deck delta is noise and is not re-reported beside it.
    assert "evidence moved" not in out

    # An `as_of` naming a snapshot this build never read dates the reasoning
    # against a corpus nobody can reproduce. Load time cannot ask that, the
    # stamps differing in every clone (issue #228); the build can, and it reports
    # rather than aborts, because a hold applies nothing to the graph and a
    # pruned snapshot must not become a build failure.
    (tmp_path / "pilots.toml").write_text(
        f'[[hold]]\nids = {json.dumps(ids)}\n'
        'settles_on = "shared_event"\nas_of = "20190101T000000Z"\n'
    )
    _build(argparse.Namespace(snapshots=snapshots, db=tmp_path / "g4"))
    report = json.loads((tmp_path / "g4" / "reconciliation.json").read_text())

    assert [(d["kind"], d["key"]) for d in report["dead_entries"]] == [
        ("hold", "20190101T000000Z")]


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


def test_a_curation_decision_the_build_cannot_honour_aborts_cleanly(tmp_path, monkeypatch):
    # The third way a build cannot honestly proceed, and the one a maintainer
    # reaches by hand: a dictionary entry the data refuses. `build_graph` lists
    # "a bad curation dictionary" beside a year straddle as an abort, so it owes
    # the same sentence rather than a traceback. Shaped as the mistake a
    # [[deck_event]] entry invites, a typo'd event code (issue #167).
    _snapshot(tmp_path / "snapshots" / "20260101T000000Z", [
        ("d1", "2026-01-01T00:00:00+00:00"),
    ])
    (tmp_path / "pilots.toml").write_text(
        '[[deck_event]]\ndeck = "d1"\nevent = "NYE_TYPO"\n'
    )
    monkeypatch.setattr("graph7ph.build.load_curation",
                        functools.partial(load_curation, tmp_path / "pilots.toml"))
    db = tmp_path / "graph"

    with pytest.raises(SystemExit) as exc:
        _build(argparse.Namespace(snapshots=tmp_path / "snapshots", db=db))

    assert "Build aborted, live graph untouched" in str(exc.value)
    assert "NYE_TYPO" in str(exc.value)
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


def test_a_build_counts_held_pairs_apart_from_the_ones_awaiting_a_human(
    tmp_path, capsys, monkeypatch
):
    # The line a maintainer reads to decide whether there is curation work to do
    # (issue #228). A held pair has been reasoned about and cannot be settled yet,
    # so counting it with the unexamined ones overstates the queue every build
    # until the evidence arrives. The two states are printed apart, and the
    # unexamined figure is the one that means "open this many questions".
    # Through `_titled_snapshot` rather than `_snapshot`, which seats every deck
    # at rank 1: two ids have to place differently to sit in one event at all,
    # and they have to sit in one event to recover the two names this needs.
    ids = ["01st Joe M - Grixis - NYE", "02nd Joel M - Grixis - NYE"]
    _titled_snapshot(tmp_path / "snapshots" / "20260101T000000Z", [
        (title, title, title, "NYE", rank + 1) for rank, title in enumerate(ids)
    ])
    monkeypatch.setattr("graph7ph.build.load_curation",
                        functools.partial(load_curation, tmp_path / "pilots.toml"))

    # First with nothing recorded: the pair is a live question.
    (tmp_path / "pilots.toml").write_text("")
    _build(argparse.Namespace(snapshots=tmp_path / "snapshots", db=tmp_path / "g1"))
    assert "0 held, 1 unexamined" in capsys.readouterr().out

    # Then held: the same pair, off the queue and still undecided.
    (tmp_path / "pilots.toml").write_text(
        f'[[hold]]\nids = {json.dumps(ids)}\n'
        'settles_on = "shared_event"\nas_of = "20260101T000000Z"\n'
    )
    _build(argparse.Namespace(snapshots=tmp_path / "snapshots", db=tmp_path / "g2"))
    out = capsys.readouterr().out

    assert "1 held, 0 unexamined" in out
    # Held is not curated: the pair is parked, not decided.
    assert "0 already curated" in out

    # A malformed `as_of` is a bad dictionary, so it aborts the build with the
    # sentence every other curation fault gets rather than a traceback.
    (tmp_path / "pilots.toml").write_text(
        f'[[hold]]\nids = {json.dumps(ids)}\n'
        'settles_on = "shared_event"\nas_of = "yesterday"\n'
    )
    with pytest.raises(SystemExit) as exc:
        _build(argparse.Namespace(snapshots=tmp_path / "snapshots", db=tmp_path / "g3"))
    assert "Build aborted, live graph untouched" in str(exc.value)
    assert "yesterday" in str(exc.value)


def test_a_build_reports_every_value_it_decided_rather_than_accepted(tmp_path, capsys):
    # A value the build decided is an assumption it made about the data, so it says
    # what it decided and under which rule rather than leaving it to be discovered
    # in the report (issue #140). Read off the provenance columns, so a rule the
    # build has not learnt yet still announces itself here (issue #162). This
    # fixture ships no eventSize at all, so the defensive field rule corrects its
    # one event and the deck's norm is re-ranked against the corrected field.
    _snapshot(tmp_path / "snapshots" / "20260101T000000Z", [
        ("d1", "2026-01-01T00:00:00+00:00"),
    ])

    _build(argparse.Namespace(snapshots=tmp_path / "snapshots", db=tmp_path / "graph"))

    out = capsys.readouterr().out
    assert "Deck.placementNorm: rescaled=1" in out
    assert "Event.fieldSize: C=1" in out


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


def test_grading_a_database_the_engine_cannot_open_aborts_cleanly(tmp_path, snapshot_dir):
    # A bundle carrying something that is not this engine's database: a placeholder,
    # a copy from another engine, a file truncated in transit. The engine raises a
    # bare RuntimeError out of the open, which reads as a traceback rather than as a
    # verdict on the bundle, and `db.UnopenableGraph` is what names it. The deploy
    # path runs this as a preflight (issue #71), so it is exactly where the promise
    # above holds or breaks: a crash and a regression must not look alike.
    db = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), db)
    database_path(db).write_bytes(b"not this engine's database")

    with pytest.raises(SystemExit) as exc:
        _baseline(argparse.Namespace(
            db=db, baseline=tmp_path / "baseline.json", capture=False
        ))

    assert "Cannot open" in str(exc.value)
    assert str(database_path(db)) in str(exc.value)


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

    The full CASES include the hidden-gem view, whose rule needs archetypes of at
    least MIN_GEM_SLICE ranked decks and which the 3-deck fixture can only answer
    empty. Issue #67 is capture-path orchestration
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
