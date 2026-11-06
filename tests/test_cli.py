import argparse
import functools
import json

import pytest

from graph7ph import baseline as bl
from graph7ph.__main__ import _baseline, _build, _curation_report
from graph7ph.build import build_graph
from graph7ph.curation import load_curation
from graph7ph.db import database_path
from graph7ph.models import load_snapshot
from graph7ph.provenance import provenance_path
from graph7ph.query import HiddenGems, PilotNeighbourhood
from graph7ph.repairs import load_repairs


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


def test_a_build_announces_the_holds_the_data_has_now_settled(
    tmp_path, capsys, monkeypatch
):
    # A hold on one id records the condition that would settle it, so the build
    # re-evaluates that condition every ingestion and growing data does the
    # review work instead of a human re-reading the list (issue #230). A settled
    # id is an available decision sitting unmade, so it gets the banner a
    # scrolling build log cannot bury.
    snapshots = tmp_path / "snapshots"
    two_names = [
        ("d1", "P", "01st Tom H - Storm - E1", "E1", 1),
        ("d2", "P", "01st Tom M - Lands - E2", "E2", 1),
    ]
    _titled_snapshot(snapshots / "20260101T000000Z", two_names)
    monkeypatch.setattr("graph7ph.build.load_curation",
                        functools.partial(load_curation, tmp_path / "pilots.toml"))
    (tmp_path / "pilots.toml").write_text(
        '[[hold]]\nids = ["P"]\n'
        'settles_on = "event_split"\nas_of = "20260101T000000Z"\n'
    )

    # Nothing has moved since the reasoning was written: the id is held, and
    # the build says nothing further about it.
    _build(argparse.Namespace(snapshots=snapshots, db=tmp_path / "g1"))
    out = capsys.readouterr().out
    assert "names under one id: 1 held, 0 unexamined" in out
    assert "DECISION AVAILABLE" not in out

    # A later fetch has both surnames register at E1, where only one person can
    # have entered (ADR 0005), so the id is decided and the build says so loudly.
    _titled_snapshot(snapshots / "20260201T000000Z", two_names + [
        ("d3", "P", "02nd Tom M - Lands - E1", "E1", 2),
    ])
    _build(argparse.Namespace(snapshots=snapshots, db=tmp_path / "g2"))
    out = capsys.readouterr().out
    assert "DECISION AVAILABLE" in out
    assert "1 hold(s) settled" in out

    # An `as_of` naming a snapshot this build never read dates the reasoning
    # against a corpus nobody can reproduce. Load time cannot ask that, the
    # stamps differing in every clone (issue #228); the build can, and it reports
    # rather than aborts, because a hold applies nothing to the graph and a
    # pruned snapshot must not become a build failure.
    (tmp_path / "pilots.toml").write_text(
        '[[hold]]\nids = ["P"]\n'
        'settles_on = "event_split"\nas_of = "20190101T000000Z"\n'
    )
    _build(argparse.Namespace(snapshots=snapshots, db=tmp_path / "g3"))
    report = json.loads((tmp_path / "g3" / "reconciliation.json").read_text())

    assert [(d["kind"], d["key"]) for d in report["dead_entries"]] == [
        ("hold", "20190101T000000Z")]


def test_a_round_that_settles_many_holds_caps_the_banner_rather_than_scrolling(
    tmp_path, capsys, monkeypatch
):
    # The banner exists so a scrolling build log cannot bury an available decision
    # (issue #230), and a line per settled hold turns it into the thing it guards
    # against: a fetch landing several collisions settles them together, pushing
    # the flagged-record banner above it off the top of the terminal. So the
    # count stays exact and the first few carry the fact that fired them, while
    # the whole list stays the brief's job (#233).
    snapshots = tmp_path / "snapshots"
    decks, holds = [], []
    for i in range(8):
        decks += [(f"d{i}a", f"Id{i}", f"01st Joe M{i} - Grixis - E{i}", f"E{i}", 1),
                  (f"d{i}b", f"Id{i}", f"02nd Joe N{i} - Walks - E{i}", f"E{i}", 2)]
        holds.append(f'[[hold]]\nids = ["Id{i}"]\n'
                     'settles_on = "event_split"\nas_of = "20260101T000000Z"\n')
    _titled_snapshot(snapshots / "20260101T000000Z", decks)
    monkeypatch.setattr("graph7ph.build.load_curation",
                        functools.partial(load_curation, tmp_path / "pilots.toml"))
    (tmp_path / "pilots.toml").write_text("".join(holds))

    _build(argparse.Namespace(snapshots=snapshots, db=tmp_path / "g"))
    out = capsys.readouterr().out

    # The number is the whole truth; the enumeration is a sample of it.
    assert "8 hold(s) settled" in out
    assert len([ln for ln in out.splitlines() if "registered at" in ln]) == 5
    assert "... and 3 more" in out
    assert "graph7ph curation-report" in out


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


def test_an_event_registered_five_months_apart_aborts_cleanly(tmp_path):
    # Same treatment for the direct signal of an upstream merge (issue #269):
    # two decks of one event registered five months apart, inside one calendar
    # year so the year guard stays quiet and this is the guard that spoke.
    _snapshot(tmp_path / "snapshots" / "20260701T000000Z", [
        ("d1", "2026-01-10T00:00:00+00:00"),
        ("d2", "2026-06-10T00:00:00+00:00"),
    ])
    db = tmp_path / "graph"

    with pytest.raises(SystemExit) as exc:
        _build(argparse.Namespace(snapshots=tmp_path / "snapshots", db=db))

    assert "Build aborted, live graph untouched" in str(exc.value)
    assert "NYE registered decks 151 days apart (2026-01-10 to 2026-06-10)" in str(exc.value)
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
        'as_of = "20260101T000000Z"\nnames = { "d1" = "d1" }\n'
    )
    monkeypatch.setattr("graph7ph.build.load_curation",
                        functools.partial(load_curation, tmp_path / "pilots.toml"))
    db = tmp_path / "graph"

    with pytest.raises(SystemExit) as exc:
        _build(argparse.Namespace(snapshots=tmp_path / "snapshots", db=db))

    assert "Build aborted, live graph untouched" in str(exc.value)
    assert "NYE_TYPO" in str(exc.value)
    assert not db.exists()


def test_a_build_announces_a_repair_upstream_has_moved_under(tmp_path, capsys, monkeypatch):
    # The row lives in the reconciliation report, but the build is where the
    # owner is standing, and a ruling upstream has overtaken is the one thing
    # the repairs file hides from the gate (issue #278): counted on the
    # terminal, never only in the report.
    _snapshot(tmp_path / "snapshots" / "20260101T000000Z", [
        ("d1", "2026-01-01T00:00:00+00:00"), ("d2", "2026-01-01T00:00:00+00:00"),
    ])
    _snapshot(tmp_path / "snapshots" / "20260201T000000Z", [
        ("d1", "2026-01-01T00:00:00+00:00"),
    ])
    repairs = tmp_path / "repairs.toml"
    repairs.write_text('''
[[hold_out]]
decks = ["d2"]
reason = "extra deck"
as_of = "20260101T000000Z"
''')
    monkeypatch.setattr("graph7ph.ingest.load_repairs",
                        functools.partial(load_repairs, repairs))

    _build(argparse.Namespace(snapshots=tmp_path / "snapshots", db=tmp_path / "graph"))

    out = capsys.readouterr().out
    assert "1 deck(s) repaired by" in out
    assert "1 of them upstream has moved under since the ruling (read source_repairs.moved in the report)" in out


def test_a_build_tells_the_developer_to_restart_a_running_app(tmp_path, capsys):
    # Promotion renames the directory a running app opened, so it goes on serving
    # the old graph with no error at all. The build is the moment the developer is
    # standing in, so it is where the restart has to be said (issue #59).
    _snapshot(tmp_path / "snapshots" / "20260101T000000Z", [
        ("d1", "2026-01-01T00:00:00+00:00"),
    ])

    _build(argparse.Namespace(snapshots=tmp_path / "snapshots", db=tmp_path / "graph"))

    assert "restart" in capsys.readouterr().out


def test_a_build_counts_the_three_states_a_pair_can_be_in(
    tmp_path, capsys, monkeypatch
):
    # The line a maintainer reads to know what the rules did this build (issue
    # #273): pairs merged by rule, pairs a shared event separated, and pairs
    # watching, which are two nodes, the graph's default. A pair the dictionary
    # decided is counted apart as curated. Through `_titled_snapshot` rather
    # than `_snapshot`, which seats every deck at rank 1: two ids have to place
    # differently to sit in one event at all.
    ids = ["01st Joe M - Grixis - NYE", "02nd Joel M - Grixis - NYE"]
    _titled_snapshot(tmp_path / "snapshots" / "20260101T000000Z", [
        (title, title, title, "NYE", rank + 1) for rank, title in enumerate(ids)
    ])
    monkeypatch.setattr("graph7ph.build.load_curation",
                        functools.partial(load_curation, tmp_path / "pilots.toml"))

    # Both at NYE with different decks: the structural fact separates them.
    (tmp_path / "pilots.toml").write_text("")
    _build(argparse.Namespace(snapshots=tmp_path / "snapshots", db=tmp_path / "g1"))
    out = capsys.readouterr().out
    assert "pilot identity: 0 merged by rule, 1 separated, 0 watching, 0 already curated" in out

    # Rejected by a human: off the rules' list and counted as curated.
    (tmp_path / "pilots.toml").write_text(
        f'[[reject]]\nids = {json.dumps(ids)}\nas_of = "20260101T000000Z"\n'
        f'names = {{ {json.dumps(ids[0])} = "Joe M", {json.dumps(ids[1])} = "Joel M" }}\n'
    )
    _build(argparse.Namespace(snapshots=tmp_path / "snapshots", db=tmp_path / "g2"))
    out = capsys.readouterr().out
    assert "pilot identity: 0 merged by rule, 0 separated, 0 watching, 1 already curated" in out

    # A malformed `as_of` is a bad dictionary, so it aborts the build with the
    # sentence every other curation fault gets rather than a traceback.
    (tmp_path / "pilots.toml").write_text(
        '[[hold]]\nids = ["P"]\n'
        'settles_on = "event_split"\nas_of = "yesterday"\n'
    )
    with pytest.raises(SystemExit) as exc:
        _build(argparse.Namespace(snapshots=tmp_path / "snapshots", db=tmp_path / "g3"))
    assert "Build aborted, live graph untouched" in str(exc.value)
    assert "yesterday" in str(exc.value)


def test_a_build_counts_held_multi_name_ids_apart_and_announces_a_settled_one(
    tmp_path, capsys, monkeypatch
):
    # The same three states on the other identity queue (issue #232). An id whose
    # decks recovered two surnames is one id that may be two people, and once a
    # human has read it, counting it with the ids nobody has read overstates the
    # queue every build. And when the corpus settles one -- two of its surnames at
    # one event, where only one person can have entered -- that is a decision
    # sitting unmade, so it takes the banner rather than a line in the log.
    snapshots = tmp_path / "snapshots"
    two_names = [
        ("d1", "P", "01st Tom H - Storm - E1", "E1", 1),
        ("d2", "P", "01st Tom M - Lands - E2", "E2", 1),
    ]
    _titled_snapshot(snapshots / "20260101T000000Z", two_names)
    monkeypatch.setattr("graph7ph.build.load_curation",
                        functools.partial(load_curation, tmp_path / "pilots.toml"))

    # Nothing recorded: the id is a live question.
    (tmp_path / "pilots.toml").write_text("")
    _build(argparse.Namespace(snapshots=snapshots, db=tmp_path / "g1"))
    assert "0 held, 1 unexamined" in capsys.readouterr().out

    # Held: read, undecided, off the queue.
    (tmp_path / "pilots.toml").write_text(
        '[[hold]]\nids = ["P"]\n'
        'settles_on = "event_split"\nas_of = "20260101T000000Z"\n'
    )
    _build(argparse.Namespace(snapshots=snapshots, db=tmp_path / "g2"))
    out = capsys.readouterr().out
    assert "1 held, 0 unexamined" in out
    assert "DECISION AVAILABLE" not in out

    # A later fetch has both surnames register at E1.
    _titled_snapshot(snapshots / "20260201T000000Z", two_names + [
        ("d3", "P", "02nd Tom M - Lands - E1", "E1", 2),
    ])
    _build(argparse.Namespace(snapshots=snapshots, db=tmp_path / "g3"))
    out = capsys.readouterr().out

    assert "DECISION AVAILABLE" in out
    assert "1 hold(s) settled" in out
    assert "P:" in out and "E1" in out


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


def test_a_capture_records_the_digest_of_the_code_it_was_proven_under(
    tmp_path, snapshot_dir, small_cases
):
    # The oracle carries the digest of the code whose answers it holds, so a later
    # recapture can tell a data move from a code change (issue #272).
    db = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), db)
    baseline = tmp_path / "baseline.json"

    _baseline(_ns(db=db, baseline=baseline, capture=True))

    assert json.loads(baseline.read_text())["code_digest"] == bl.code_digest()


def test_a_green_capture_after_a_code_change_records_the_digest_and_moves_nothing_else(
    tmp_path, snapshot_dir, monkeypatch
):
    # The proof step before a fetch: the gate is green, so the oracle's answers
    # stand, and all that is out of date is which code they were proven under. A
    # capture then updates the digest alone. Rewriting the answers too would bury
    # that one line under the row-order and float churn of a fresh capture
    # (research-log 2026-07-21), and the proof could no longer ride in the same
    # commit as the change it proves (issue #272). The churn is staged here as
    # the grader meets it: an order-insensitive case whose rows the oracle holds
    # in an order the engine does not emit.
    db = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), db)
    cases = [bl.Case("pilot", PilotNeighbourhood("Jordan C"))]
    monkeypatch.setattr("graph7ph.__main__.check", functools.partial(bl.check, cases=cases))
    monkeypatch.setattr("graph7ph.__main__.capture", functools.partial(bl.capture, cases=cases))
    baseline = tmp_path / "baseline.json"
    _baseline(_ns(db=db, baseline=baseline, capture=True))
    held = json.loads(baseline.read_text())
    assert len(held["queries"]["pilot"]["nodes"]) > 1, "the case must have rows to reorder"
    held["queries"]["pilot"]["nodes"].reverse()
    held["code_digest"] = "0" * 64
    stale = json.dumps(held, indent=2) + "\n"
    baseline.write_text(stale)

    _baseline(_ns(db=db, baseline=baseline, capture=True))

    # Byte for byte, not parsed: the promise is a one-line diff, which a rewrite
    # that re-indented, sorted keys or moved the digest would break while still
    # parsing to the same dict.
    assert baseline.read_text() == stale.replace("0" * 64, bl.code_digest())


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


def _moved_data(baseline):
    """Re-stage the oracle as the gate meets it after a fetch: an answer moved."""
    held = json.loads(baseline.read_text())
    held["counts"]["pilots"] += 1
    return held


def test_force_after_an_unproven_code_change_refuses_and_names_the_order(
    tmp_path, snapshot_dir, small_cases
):
    # The gate is red and the code moved since the oracle was last proven, so
    # nobody can say whether the difference is the data or the code. The remedy
    # is the order: prove the code on the previous bundle, then recapture (#272).
    db = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), db)
    baseline = tmp_path / "baseline.json"
    _baseline(_ns(db=db, baseline=baseline, capture=True))
    held = _moved_data(baseline) | {"code_digest": "0" * 64}
    baseline.write_text(json.dumps(held, indent=2) + "\n")
    before = baseline.read_bytes()

    with pytest.raises(SystemExit) as exc:
        _baseline(_ns(db=db, baseline=baseline, capture=True, force=True))

    assert "run the gate on the previous bundle first" in str(exc.value)
    assert baseline.read_bytes() == before


def test_an_unforced_capture_over_moved_code_names_the_order_not_a_force_that_would_refuse(
    tmp_path, snapshot_dir, small_cases
):
    # Without --force the old refusal said to pass it; after a code change that
    # advice leads to a second refusal, so the first one names the order instead.
    db = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), db)
    baseline = tmp_path / "baseline.json"
    _baseline(_ns(db=db, baseline=baseline, capture=True))
    held = _moved_data(baseline) | {"code_digest": "0" * 64}
    baseline.write_text(json.dumps(held, indent=2) + "\n")

    with pytest.raises(SystemExit) as exc:
        _baseline(_ns(db=db, baseline=baseline, capture=True))

    assert "run the gate on the previous bundle first" in str(exc.value)
    assert "Pass --force" not in str(exc.value)


def test_force_over_an_oracle_that_records_no_proof_refuses_too(
    tmp_path, snapshot_dir, small_cases
):
    # An oracle from before #272 carries no digest, so nothing says what code it
    # was proven under: the same refusal, and the same remedy records one.
    db = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), db)
    baseline = tmp_path / "baseline.json"
    _baseline(_ns(db=db, baseline=baseline, capture=True))
    held = _moved_data(baseline)
    del held["code_digest"]
    baseline.write_text(json.dumps(held, indent=2) + "\n")

    with pytest.raises(SystemExit) as exc:
        _baseline(_ns(db=db, baseline=baseline, capture=True, force=True))

    assert "run the gate on the previous bundle first" in str(exc.value)


def test_force_with_unchanged_code_accepts_a_data_move_without_printing_the_diff(
    tmp_path, snapshot_dir, small_cases, capsys
):
    # The round after a fetch: the code is the one the oracle was proven under, so
    # the difference is the data and nobody has to read it (issue #272).
    db = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), db)
    baseline = tmp_path / "baseline.json"
    _baseline(_ns(db=db, baseline=baseline, capture=True))
    baseline.write_text(json.dumps(_moved_data(baseline), indent=2) + "\n")
    capsys.readouterr()

    _baseline(_ns(db=db, baseline=baseline, capture=True, force=True))

    out = capsys.readouterr().out
    assert "counts.pilots" not in out, "the difference was printed for a human"
    assert json.loads(baseline.read_text())["code_digest"] == bl.code_digest()


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


def _held_bundle(tmp_path, monkeypatch, snapshots, db):
    """A built bundle whose dictionary holds the one two-surname id its snapshot carries."""
    ids = ["NimbleBlackEagle"]
    _titled_snapshot(snapshots / "20260101T000000Z", [
        ("d1", ids[0], "01st Tom H - Grixis - E1", "E1", 1),
        ("d2", ids[0], "01st Tom M - Grixis - E2", "E2", 1),
    ])
    (tmp_path / "pilots.toml").write_text(
        f'[[hold]]\nids = {json.dumps(ids)}\n'
        'settles_on = "event_split"\nas_of = "20260101T000000Z"\n'
    )
    curation = functools.partial(load_curation, tmp_path / "pilots.toml")
    monkeypatch.setattr("graph7ph.build.load_curation", curation)
    monkeypatch.setattr("graph7ph.__main__.load_curation", curation)
    _build(argparse.Namespace(snapshots=snapshots, db=db))
    return ids


def test_the_curation_report_writes_the_review_brief_for_the_promoted_bundle(
    tmp_path, monkeypatch, capsys
):
    # The command a review round starts from (issue #233): the post-ingestion
    # state as a page to read, not a `reconciliation.json` to re-derive. Pure
    # read, so it names the snapshot it is about and touches nothing.
    snapshots, db = tmp_path / "snapshots", tmp_path / "graph"
    ids = _held_bundle(tmp_path, monkeypatch, snapshots, db)
    capsys.readouterr()

    _curation_report(argparse.Namespace(snapshots=snapshots, db=db))

    out = capsys.readouterr().out
    assert "20260101T000000Z" in out
    assert "## Unchanged holds (1)" in out
    assert all(pid in out for pid in ids)


def test_the_curation_report_tells_this_round_s_changes_from_the_previous_round_s(
    tmp_path, monkeypatch, capsys
):
    # The brief says what changed (issues #271, #273): a pair whose state the
    # snapshot before this one already derived is not listed again, and the
    # command reads that off the snapshot sequence it was pointed at, with
    # nothing filed anywhere to consult. Every fixture deck carries the same
    # one-card list, so each nickname pair merges by rule on it.
    snapshots, db = tmp_path / "snapshots", tmp_path / "graph"
    _titled_snapshot(snapshots / "20260101T000000Z", [
        ("d1", "P1", "01st Joe M - Grixis - E1", "E1", 1),
        ("d2", "P2", "01st Joel M - Grixis - E2", "E2", 1),
    ])
    _titled_snapshot(snapshots / "20260201T000000Z", [
        ("d3", "P3", "01st Ann K - Grixis - E3", "E3", 1),
        ("d4", "P4", "01st Anne K - Grixis - E4", "E4", 1),
    ])
    (tmp_path / "pilots.toml").write_text("")
    curation = functools.partial(load_curation, tmp_path / "pilots.toml")
    monkeypatch.setattr("graph7ph.build.load_curation", curation)
    monkeypatch.setattr("graph7ph.__main__.load_curation", curation)
    _build(argparse.Namespace(snapshots=snapshots, db=db))
    capsys.readouterr()

    _curation_report(argparse.Namespace(snapshots=snapshots, db=db))

    out = capsys.readouterr().out
    assert out.startswith("Curation review for snapshot `20260201T000000Z`")
    assert "## Merged by rule (1)" in out
    assert "`P3` + `P4`" in out and "`P1`" not in out
    assert "- merged by rule: 2 (changed: 1)" in out


def test_reporting_on_a_graph_that_was_never_built_aborts_cleanly(tmp_path):
    # Nothing to report against is a sentence naming the remedy, not a traceback:
    # the command is a pipe into `gh issue create`, where a crash would file the
    # traceback as the review brief.
    with pytest.raises(SystemExit) as exc:
        _curation_report(argparse.Namespace(
            snapshots=tmp_path / "snapshots", db=tmp_path / "graph"))

    assert "graph7ph build" in str(exc.value)


def test_reporting_on_a_bundle_older_than_the_sources_aborts_instead_of_reporting(
    tmp_path, monkeypatch, make_stale
):
    # A bundle built from other sources describes a graph today's code would not
    # produce, and the brief becomes an issue a human works from, so a wrong one
    # costs a round of curation rather than a re-run.
    snapshots, db = tmp_path / "snapshots", tmp_path / "graph"
    _held_bundle(tmp_path, monkeypatch, snapshots, db)
    built_at = make_stale(db)

    with pytest.raises(SystemExit) as exc:
        _curation_report(argparse.Namespace(snapshots=snapshots, db=db))

    assert "graph7ph build" in str(exc.value)
    assert built_at in str(exc.value)


def test_reporting_against_a_snapshot_the_bundle_predates_aborts(
    tmp_path, monkeypatch, capsys
):
    # A fetch with no build behind it leaves a bundle that no longer matches
    # `snapshots/`, and the brief would report on the corpus before the one it
    # names. Refuse, rather than file a round of review about last week's data.
    snapshots, db = tmp_path / "snapshots", tmp_path / "graph"
    _held_bundle(tmp_path, monkeypatch, snapshots, db)
    _titled_snapshot(snapshots / "20260301T000000Z", [
        ("d1", "NimbleBlackEagle", "01st Joe M - Grixis - E1", "E1", 1),
    ])
    # The bundle was built against the January snapshot, before March was
    # fetched. Its stamp is dated back to say so, the digest left valid so the
    # refusal under test is the freshness one and not issue #55's.
    stamp = json.loads(provenance_path(db).read_text())
    provenance_path(db).write_text(
        json.dumps(stamp | {"built_at": "2026-01-05T00:00:00+00:00"}))
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc:
        _curation_report(argparse.Namespace(snapshots=snapshots, db=db))

    assert "20260301T000000Z" in str(exc.value)
    assert "graph7ph build" in str(exc.value)


def test_a_directory_no_fetch_wrote_is_not_read_as_a_snapshot(
    tmp_path, monkeypatch, capsys
):
    # The corpus the brief names is read as a time, and this command's other end
    # is a `gh issue create` that files whatever it is handed. So a directory
    # sitting beside the snapshots (a scratch copy, a half-restored backup) must
    # be ignored rather than parsed, or the review issue is a traceback. Shaped
    # like a stamp is not enough: a name can carry a date that never happened and
    # still sort above every real one.
    snapshots, db = tmp_path / "snapshots", tmp_path / "graph"
    _held_bundle(tmp_path, monkeypatch, snapshots, db)
    (snapshots / "scratch-copy").mkdir()
    (snapshots / "20260231T000000Z").mkdir()
    capsys.readouterr()

    _curation_report(argparse.Namespace(snapshots=snapshots, db=db))

    assert "20260101T000000Z" in capsys.readouterr().out


def test_a_repairs_file_the_build_cannot_honour_aborts_cleanly(tmp_path, monkeypatch):
    # The fourth way a build cannot honestly proceed: a repairs file that
    # contradicts itself or the data (issue #268). Same sentence as a bad
    # dictionary, since the maintainer reaches both by hand.
    _snapshot(tmp_path / "snapshots" / "20260101T000000Z", [
        ("d1", "2026-01-01T00:00:00+00:00"),
    ])
    (tmp_path / "repairs.toml").write_text(
        '[[hold_out]]\ndecks = ["d1"]\nreason = "r"\nas_of = "20260101T000000Z"\n'
        '[[adopt]]\ndecks = ["d1"]\nfact = "pilot"\nvalue = "x"\nreason = "r"\n'
        'as_of = "20260101T000000Z"\n'
    )
    monkeypatch.setattr("graph7ph.ingest.load_repairs",
                        functools.partial(load_repairs, tmp_path / "repairs.toml"))
    db = tmp_path / "graph"

    with pytest.raises(SystemExit) as exc:
        _build(argparse.Namespace(snapshots=tmp_path / "snapshots", db=db))

    assert "Build aborted, live graph untouched" in str(exc.value)
    assert "held out and also adopts" in str(exc.value)
    assert not db.exists()


def test_a_build_announces_an_entry_whose_id_upstream_handed_to_someone_else(
    tmp_path, capsys, monkeypatch
):
    # An entry keyed on a reused id keeps applying, to the wrong person, with no
    # dead-entry report, because the id is still present (issue #261). The build
    # refuses it and says so with both names, under a banner a scrolling log
    # cannot bury, since a refused entry is a decision that has stopped applying
    # and a maintainer has to retire or re-key it (issue #283).
    snapshots = tmp_path / "snapshots"
    # Alex W at Alexander W's event, so the rules separate rather than merge the
    # pair on its identical fixture list (issue #273): only the entry under test
    # could fold them.
    _titled_snapshot(snapshots / "20260101T000000Z", [
        ("d1", "LunarAzureViper", "01st Alexander W - Lands - E1", "E1", 1),
        ("d2", "FrostyIvoryStag", "02nd Alex W - Lands - E1", "E1", 2),
        ("d3", "RapidVioletHawk", "01st Connor E - Storm - E3", "E3", 1),
    ])
    monkeypatch.setattr("graph7ph.build.load_curation",
                        functools.partial(load_curation, tmp_path / "pilots.toml"))
    # A family name on the merge, a handle on the pin: the two shapes the
    # comparison takes (ADR 0005).
    (tmp_path / "pilots.toml").write_text(
        '[[merge]]\nids = ["LunarAzureViper", "FrostyIvoryStag"]\n'
        'canonical = "FrostyIvoryStag"\nas_of = "20260101T000000Z"\n'
        'names = { "LunarAzureViper" = "Lachie C", "FrostyIvoryStag" = "Alex W" }\n'
        '[[name]]\npilot = "RapidVioletHawk"\ndisplay_name = "Edward D"\n'
        'as_of = "20260101T000000Z"\nnames = { "RapidVioletHawk" = "EdwD7" }\n'
    )

    _build(argparse.Namespace(snapshots=snapshots, db=tmp_path / "graph"))

    out = capsys.readouterr().out
    assert "ENTRY REFUSED: 2 id(s)" in out
    assert "merge LunarAzureViper: written against 'Lachie C', now recovers 'Alexander W'" in out
    assert "name RapidVioletHawk: written against 'EdwD7', now recovers 'Connor E'" in out
    assert "pilots=3 " in out
    report = json.loads((tmp_path / "graph" / "reconciliation.json").read_text())
    assert [(r["kind"], r["key"]) for r in report["refused_entries"]] == [
        ("merge", "LunarAzureViper"), ("name", "RapidVioletHawk")]
