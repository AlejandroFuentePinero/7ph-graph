"""Tests for the post-ingestion curation brief (issue #233).

The renderer is exercised against hand-built reconciliation dicts and
dictionaries, the two things the command reads, since the point of the brief is
what it puts first and what it leaves out. One test reads the live bundle
instead, because "derive, never transcribe" is a claim about the real report and
a fixture would only restate the case it was written to show.
"""

import json

import pytest

from graph7ph.curation import Hold, load_curation
from graph7ph.__main__ import SNAPSHOTS_ROOT
from graph7ph.curation_report import curation_report, latest_snapshot
from graph7ph.db import artifact_path
from graph7ph.ingest import previous_reconciliation
from graph7ph.pilots import SETTLING_TRIGGERS
from graph7ph.provenance import staleness

SNAPSHOT = "20260815T140746Z"


def _recon(**overrides) -> dict:
    """A reconciliation report with nothing open, plus whatever a test opens."""
    return {
        "under_merges": [], "multi_name_ids": [], "held_merges": [],
        "held_names": [], "fired_holds": [], "curated": 0,
    } | overrides


def _new_decks(pilots: list[str], decks: list[str]) -> dict:
    """A `new_decks` firing as the build writes one: the hold, and what it gained."""
    return {"pilots": pilots, "trigger": "new_decks", "settles_on": "shared_event",
            "as_of": SNAPSHOT, "decks": decks,
            "detail": f"gained decks since {SNAPSHOT}: {pilots[0]} with {', '.join(decks)}"}


def _rows(report: str, heading: str) -> list[str]:
    """The bullet lines under one heading, or none where it is not printed."""
    if f"## {heading}" not in report:
        return []
    body = report.split(f"## {heading}", 1)[1].split("\n#", 1)[0]
    return [line for line in body.splitlines() if line.startswith("- ")]


def test_the_live_brief_states_exactly_what_the_live_report_holds():
    # The claim is about the real record, so it is graded against the real
    # record: every fired trigger and every unexamined row the build wrote is a
    # row in the brief, in the one section its history puts it in, and the brief
    # invents none. Derived from `reconciliation.json` and the snapshot sequence
    # each run, never transcribed, which is what keeps the brief honest as the
    # queues move (issues #227, #271).
    artifact = artifact_path()
    recon_file = artifact / "reconciliation.json"
    if not recon_file.exists():
        pytest.skip(f"no graph artifact at {artifact}; build one with "
                    "`uv run graph7ph build`")
    if stale := staleness(artifact):
        pytest.skip(f"cannot report on the real record: {stale}")
    snapshot = latest_snapshot(SNAPSHOTS_ROOT)
    if snapshot is None:
        pytest.skip(f"no snapshots in {SNAPSHOTS_ROOT}/")
    recon = json.loads(recon_file.read_text())
    curation = load_curation()
    previous = previous_reconciliation(SNAPSHOTS_ROOT, snapshot, curation)

    report = curation_report(recon, curation.holds, snapshot, previous=previous)

    key = lambda c: frozenset(c.get("pilots") or [c["pilot"]])  # noqa: E731
    settled = [f for f in recon["fired_holds"] if f["trigger"] in SETTLING_TRIGGERS]
    rows = _rows(report, "Decisions available")
    assert len(rows) == len(settled)
    for hold in settled:
        assert any(hold["detail"] in row and all(pid in row for pid in hold["pilots"])
                   for row in rows)
    # Every unexamined row is in exactly one of the two queue sections, and
    # which one is whether the sequence before this snapshot already raised it.
    before = {key(c) for c in (previous or {}).get("under_merges", [])
              + (previous or {}).get("multi_name_ids", [])}
    queue = recon["under_merges"] + recon["multi_name_ids"]
    new = [c for c in queue if key(c) not in before]
    carried = [c for c in queue if key(c) in before]
    for candidates, heading in [(new, "New this ingestion"), (carried, "Carried over")]:
        rows = _rows(report, heading)
        for candidate in candidates:
            assert any(all(pid in row for pid in key(candidate)) for row in rows)
    assert f"- new: {len(new)}" in report
    assert f"- carried: {len(carried)}" in report
    # Every hold on file reaches the brief once: as a decision, as evidence that
    # moved this round, or in the tail; so the dictionary and the brief cannot
    # disagree about how many pairs are parked.
    moved = _rows(report, "Evidence moved")
    for hold in recon["fired_holds"]:
        if hold["trigger"] not in SETTLING_TRIGGERS:
            assert any(hold["detail"] in row for row in moved) or any(
                all(pid in row for pid in hold["pilots"])
                for row in _rows(report, "Unchanged holds"))
    assert len(moved) + len(_rows(report, "Unchanged holds")) == len(curation.holds) - len(settled)


def test_a_settled_hold_leads_the_brief_and_carries_the_fact_that_settled_it():
    # A fired settling trigger is a decision sitting unmade (issue #230), and is
    # why the issue is worth opening at all, so it leads. A hold whose evidence
    # merely moved decides nothing and must not be filed beside it.
    recon = _recon(fired_holds=[
        {"pilots": ["FrostyBlueOtter", "NimbleBlackEagle"],
         "trigger": "shared_event", "settles_on": "shared_event",
         "as_of": SNAPSHOT, "decks": ["d1", "d4"],
         "detail": "both registered at E1: FrostyBlueOtter with d1, "
                   "NimbleBlackEagle with d4"},
        {"pilots": ["AmberAmberStag", "SwiftMagentaRaven"],
         "trigger": "new_decks", "settles_on": "new_decks", "as_of": SNAPSHOT,
         "decks": ["d9"], "detail": f"gained decks since {SNAPSHOT}: "
                                    "AmberAmberStag with d9"},
    ])
    holds = {
        frozenset({"FrostyBlueOtter", "NimbleBlackEagle"}): Hold("shared_event", SNAPSHOT),
        frozenset({"AmberAmberStag", "SwiftMagentaRaven"}): Hold("new_decks", SNAPSHOT),
    }

    report = curation_report(recon, holds, SNAPSHOT)

    settled = report.index("Decisions available")
    assert settled < report.index("Evidence moved")
    assert "both registered at E1: FrostyBlueOtter with d1" in report
    # The soft trigger's own line is below, not up here with the decisions.
    assert report.index("gained decks since") > settled


def test_both_unexamined_queues_rank_above_the_held_tail():
    # Both identity queues mean "nobody has read this" since the thorough passes
    # (issues #231, #232), so a row from either ranks above the tail that has
    # already been reasoned about; which of the two queue sections it lands in
    # is the previous round's business (issue #271), and with no previous round
    # both rows are new.
    recon = _recon(
        under_merges=[{"display_name": "Joe M", "relation": "nickname",
                       "pilots": ["NimbleBlackEagle", "FrostyBlueOtter"]}],
        multi_name_ids=[{"pilot": "RichardO", "display_name": "Richard O",
                         "names": ["Richard O", "Richard Owen"]}],
        held_merges=[{"display_name": "Ann B", "relation": "initial",
                      "pilots": ["A", "B"], "settles_on": "shared_event",
                      "as_of": SNAPSHOT}],
    )

    report = curation_report(recon, {frozenset({"A", "B"}): Hold("shared_event", SNAPSHOT)}, SNAPSHOT)

    new = report.index("New this ingestion")
    assert new < report.index("Unchanged holds")
    assert "`NimbleBlackEagle` + `FrostyBlueOtter`" in report
    assert "Joe M" in report and "nickname" in report
    # The multi-name queue asks the same question of one id, so it is new too.
    assert "`RichardO`" in report
    assert "Richard Owen" in report


def test_the_unchanged_tail_is_every_hold_the_dictionary_holds_and_nothing_fired():
    # The population is the dictionary, not the report's `held_merges` rows: a
    # pair whose recovered names have drifted apart reaches no such row while
    # still being held, and reading the tail off the review list would leave it
    # in the dictionary and in no brief (the hole issue #230 closes for triggers,
    # here for the tail). A hold the data has settled is a decision above, so it
    # is not restated down here.
    recon = _recon(
        held_merges=[{"display_name": "Ann B", "relation": "initial",
                      "pilots": ["A", "B"], "settles_on": "shared_event",
                      "as_of": SNAPSHOT}],
        fired_holds=[{"pilots": ["C", "D"], "trigger": "shared_event",
                      "settles_on": "shared_event", "as_of": SNAPSHOT,
                      "decks": ["d1"], "detail": "both registered at E1"}],
    )
    holds = {
        frozenset({"A", "B"}): Hold("shared_event", SNAPSHOT),
        frozenset({"C", "D"}): Hold("shared_event", SNAPSHOT),
        frozenset({"Drifted", "Apart"}): Hold("new_decks", SNAPSHOT),
    }

    tail = curation_report(recon, holds, SNAPSHOT).split("## Unchanged holds")[1]

    assert "## Unchanged holds (2)" in curation_report(recon, holds, SNAPSHOT)
    assert "`A` + `B`: Ann B (initial), settles on `shared_event`" in tail
    # No `held_merges` row to name it, so the ids are what the brief has.
    assert "`Apart` + `Drifted`: settles on `new_decks`" in tail
    assert "`C` + `D`" not in tail


def test_an_id_holding_several_pairs_is_called_out_as_one_decision_that_closes_rows():
    # One id pairing with several partners is where a single decision retires
    # several rows, which is the difference between a tail worth reading and 106
    # unranked lines.
    holds = {
        frozenset({"Hub", "B"}): Hold("shared_event", SNAPSHOT),
        frozenset({"Hub", "C"}): Hold("shared_event", SNAPSHOT),
        frozenset({"D", "E"}): Hold("shared_event", SNAPSHOT),
    }

    report = curation_report(_recon(), holds, SNAPSHOT)

    assert "- `Hub`: held against `B`, `C`" in report
    assert "`D`:" not in report


def test_the_counts_close_the_brief_with_new_carried_moved_and_unchanged_apart():
    # The figure that means "open this many questions" is what this ingestion
    # introduced; what the previous round already listed is counted beside it,
    # not inside it, as are the holds that moved this round and the ones that
    # did not (issue #271). Held and curated are what keep the first figure small.
    joe = {"display_name": "Joe M", "relation": "nickname", "pilots": ["A", "B"]}
    ann = {"display_name": "Ann K", "relation": "typo", "pilots": ["K", "L"]}
    previous = _recon(under_merges=[joe],
                      fired_holds=[_new_decks(["E", "F"], ["d8"]),
                                   _new_decks(["I", "J"], ["d7"])])
    recon = _recon(
        under_merges=[joe, ann],
        multi_name_ids=[{"pilot": "R", "display_name": "Richard O",
                         "names": ["Richard O", "Richard Owen"]}],
        fired_holds=[{"pilots": ["C", "D"], "trigger": "shared_event",
                      "settles_on": "shared_event", "as_of": SNAPSHOT,
                      "decks": [], "detail": "both registered at E1"},
                     _new_decks(["E", "F"], ["d8", "d9"]),
                     _new_decks(["I", "J"], ["d7"])],
        curated=222,
    )
    holds = {frozenset(pair): Hold("shared_event", SNAPSHOT)
             for pair in [("C", "D"), ("E", "F"), ("G", "H"), ("I", "J")]}

    counts = curation_report(recon, holds, SNAPSHOT, previous=previous).split("## Counts")[1]

    assert counts.strip().splitlines() == [
        "- new: 2", "- carried: 1", "- decisions available: 1",
        "- evidence moved: 1", "- unchanged holds: 2", "- curated: 222"]


def test_a_round_with_nothing_to_work_says_so_instead_of_printing_empty_sections():
    # A brief filed after every ingestion is only read if it means something when
    # it arrives. With nothing settled, nothing new and nothing moved, there is
    # one sentence to write, and no section can be worth a heading over nothing.
    report = curation_report(_recon(), {}, SNAPSHOT)

    assert report.strip().splitlines() == [
        f"Nothing to review for snapshot `{SNAPSHOT}`: no hold settled, "
        "no candidate is new or carried, and no hold's evidence moved."
    ]


def test_the_opening_words_are_what_the_runbook_files_on():
    # The ingestion runbook in docs/development.md decides whether to open an
    # issue by matching the brief's first line against "Curation review", so those
    # two words are an interface and not a caption. Renaming them without touching
    # the runbook is silent in the direction that loses work: every round would
    # print its brief and file nothing, and nobody finds out by reading a report
    # that never arrives.
    loud = _recon(under_merges=[{"display_name": "Ann B", "relation": "initial",
                                 "pilots": ["A", "B"]}])

    assert curation_report(loud, {}, SNAPSHOT).startswith("Curation review")
    assert curation_report(_recon(), {}, SNAPSHOT).startswith("Nothing to review")


def test_a_quiet_round_with_holds_still_on_file_leads_with_the_quiet_line():
    # The tail is not work, so it does not turn a quiet round into a loud one; it
    # is still printed, because a hold nobody lists is a hold nobody retires.
    recon = _recon(held_merges=[{"display_name": "Ann B", "relation": "initial",
                                 "pilots": ["A", "B"], "settles_on": "shared_event",
                                 "as_of": SNAPSHOT}])

    report = curation_report(recon, {frozenset({"A", "B"}): Hold("shared_event", SNAPSHOT)}, SNAPSHOT)

    assert report.splitlines()[0].startswith("Nothing to review")
    assert "## Unchanged holds (1)" in report
    assert "## Decisions available" not in report
    assert "## New this ingestion" not in report


def test_a_candidate_on_the_previous_round_is_carried_and_one_not_on_it_is_new():
    # "New this ingestion" counted every unexamined pair, and 41 of the 57 it
    # listed on #262 were on the previous round's issue. What this ingestion
    # introduced is the queue less the queue the sequence before it would have
    # produced (issue #271); without a previous round every candidate is new.
    joe = {"display_name": "Joe M", "relation": "nickname", "pilots": ["A", "B"]}
    ann = {"display_name": "Ann K", "relation": "typo", "pilots": ["C", "D"]}
    recon = _recon(under_merges=[joe, ann])

    report = curation_report(recon, {}, SNAPSHOT, previous=_recon(under_merges=[joe]))

    assert _rows(report, "New this ingestion (1)") == ["- `C` + `D`: Ann K (typo)"]
    assert _rows(report, "Carried over (1)") == ["- `A` + `B`: Joe M (nickname)"]
    first = curation_report(recon, {}, SNAPSHOT)
    assert len(_rows(first, "New this ingestion (2)")) == 2
    assert "Carried over" not in first


def test_a_hub_id_in_several_candidate_pairs_is_one_row_listing_its_partners():
    # A bare first name against seven surnamed partners made seven rows on #262
    # (Ryan), though whose deck it is is one question (issue #271). The row sits
    # where the hub's first pair did, and the heading still counts the pairs.
    recon = _recon(under_merges=[
        {"display_name": "Ryan W", "relation": "first-name", "pilots": ["RyanW", "Ryan"]},
        {"display_name": "Ann K", "relation": "typo", "pilots": ["C", "D"]},
        {"display_name": "Ryan L-J", "relation": "first-name", "pilots": ["RyanLJ", "Ryan"]},
    ])

    rows = _rows(curation_report(recon, {}, SNAPSHOT), "New this ingestion (3)")

    assert rows == [
        "- `Ryan` against 2: `RyanW` Ryan W (first-name); `RyanLJ` Ryan L-J (first-name)",
        "- `C` + `D`: Ann K (typo)",
    ]


def test_a_moved_hold_is_listed_in_the_round_its_evidence_moved_and_not_again():
    # `new_decks` grades against the hold's own `as_of`, so once any deck arrived
    # the hold was re-listed every round (60 of the 70 rows on #262 were on the
    # previous issue). A hold whose deck delta is what the previous round already
    # reported is in the tail; one whose delta grew is listed, naming the decks
    # that arrived this round; one firing for the first time needs no such note,
    # its detail being this round's news in full (issue #271).
    previous = _recon(fired_holds=[_new_decks(["A", "B"], ["d1"]),
                                   _new_decks(["C", "D"], ["d2"])])
    recon = _recon(fired_holds=[_new_decks(["A", "B"], ["d1"]),
                                _new_decks(["C", "D"], ["d2", "d3"]),
                                _new_decks(["E", "F"], ["d4"])])
    holds = {frozenset(pair): Hold("shared_event", SNAPSHOT)
             for pair in [("A", "B"), ("C", "D"), ("E", "F")]}

    report = curation_report(recon, holds, SNAPSHOT, previous=previous)

    moved = _rows(report, "Evidence moved (2)")
    assert any(row.startswith("- `C` + `D`") and row.endswith("; this round: d3")
               for row in moved)
    assert any(row.startswith("- `E` + `F`") and "this round" not in row for row in moved)
    assert _rows(report, "Unchanged holds (1)") == [
        f"- `A` + `B`: settles on `shared_event`, as of `{SNAPSHOT}`"]


def test_a_round_whose_only_work_is_carried_over_still_files():
    # The runbook closes the previous round's issue when it files the next, so
    # carried rows have no open issue but this round's: a round with nothing
    # new still opens with the words the runbook files on, and lists them.
    joe = {"display_name": "Joe M", "relation": "nickname", "pilots": ["A", "B"]}

    report = curation_report(_recon(under_merges=[joe]), {}, SNAPSHOT,
                             previous=_recon(under_merges=[joe]))

    assert report.startswith("Curation review")
    assert _rows(report, "Carried over (1)") == ["- `A` + `B`: Joe M (nickname)"]
