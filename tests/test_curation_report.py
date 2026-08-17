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
from graph7ph.curation_report import curation_report
from graph7ph.db import artifact_path
from graph7ph.pilots import SETTLING_TRIGGERS
from graph7ph.provenance import staleness

SNAPSHOT = "20260815T140746Z"


def _recon(**overrides) -> dict:
    """A reconciliation report with nothing open, plus whatever a test opens."""
    return {
        "under_merges": [], "multi_name_ids": [], "held_merges": [],
        "held_names": [], "fired_holds": [], "curated": 0,
    } | overrides


def _rows(report: str, heading: str) -> list[str]:
    """The bullet lines under one heading, or none where it is not printed."""
    if f"## {heading}" not in report:
        return []
    body = report.split(f"## {heading}", 1)[1].split("\n#", 1)[0]
    return [line for line in body.splitlines() if line.startswith("- ")]


def test_the_live_brief_states_exactly_what_the_live_report_holds():
    # The claim is about the real record, so it is graded against the real
    # record: every fired trigger and every unexamined row the build wrote is a
    # row in the brief, and the brief invents none. Derived from
    # `reconciliation.json` each run, never transcribed, which is what keeps the
    # brief honest as the queues move (issue #227).
    artifact = artifact_path()
    recon_file = artifact / "reconciliation.json"
    if not recon_file.exists():
        pytest.skip(f"no graph artifact at {artifact}; build one with "
                    "`uv run graph7ph build`")
    if stale := staleness(artifact):
        pytest.skip(f"cannot report on the real record: {stale}")
    recon = json.loads(recon_file.read_text())
    holds = load_curation().holds

    report = curation_report(recon, holds, SNAPSHOT)

    settled = [f for f in recon["fired_holds"] if f["trigger"] in SETTLING_TRIGGERS]
    moved = [f for f in recon["fired_holds"] if f["trigger"] not in SETTLING_TRIGGERS]
    new = recon["under_merges"] + recon["multi_name_ids"]
    for fired, heading in [(settled, "Decisions available"), (moved, "Evidence moved")]:
        rows = _rows(report, heading)
        assert len(rows) == len(fired)
        for hold in fired:
            assert any(hold["detail"] in row and all(pid in row for pid in hold["pilots"])
                       for row in rows)
    rows = _rows(report, "New this ingestion")
    assert len(rows) == len(new)
    for candidate in new:
        ids = candidate.get("pilots") or [candidate["pilot"]]
        assert any(all(pid in row for pid in ids) for row in rows)
    assert f"- unexamined: {len(new)}" in report
    # Every hold on file that nothing fired reaches the tail, so the dictionary
    # and the brief cannot disagree about how many pairs are parked.
    assert len(_rows(report, "Unchanged holds")) == len(holds) - len(recon["fired_holds"])


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


def test_the_unexamined_queues_are_what_this_ingestion_introduced():
    # Both identity queues mean "nobody has read this" since the thorough passes
    # (issues #231, #232), so an unexamined row is a row this ingestion brought
    # in, and it ranks above the tail that has already been reasoned about.
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


def test_the_counts_close_the_brief_with_what_is_still_unexamined():
    # The figure that means "open this many questions" is the unexamined one, and
    # after the thorough passes it should only ever be what this ingestion
    # introduced (issues #231, #232). Held and curated are counted beside it
    # because they are what makes that figure small.
    recon = _recon(
        under_merges=[{"display_name": "Joe M", "relation": "nickname",
                       "pilots": ["A", "B"]}],
        multi_name_ids=[{"pilot": "R", "display_name": "Richard O",
                         "names": ["Richard O", "Richard Owen"]}],
        fired_holds=[{"pilots": ["C", "D"], "trigger": "shared_event",
                      "settles_on": "shared_event", "as_of": SNAPSHOT,
                      "decks": [], "detail": "both registered at E1"},
                     {"pilots": ["E", "F"], "trigger": "new_decks",
                      "settles_on": "shared_event", "as_of": SNAPSHOT,
                      "decks": [], "detail": "gained decks"}],
        curated=222,
    )
    holds = {frozenset(pair): Hold("shared_event", SNAPSHOT)
             for pair in [("C", "D"), ("E", "F"), ("G", "H")]}

    counts = curation_report(recon, holds, SNAPSHOT).split("## Counts")[1]

    assert "unexamined: 2" in counts
    assert "decisions available: 1" in counts
    assert "evidence moved: 1" in counts
    assert "unchanged holds: 1" in counts
    assert "curated: 222" in counts


def test_a_round_with_nothing_to_work_says_so_instead_of_printing_empty_sections():
    # A brief filed after every ingestion is only read if it means something when
    # it arrives. With nothing settled, nothing new and nothing moved, there is
    # one sentence to write, and no section can be worth a heading over nothing.
    report = curation_report(_recon(), {}, SNAPSHOT)

    assert report.strip().splitlines() == [
        f"Nothing to review for snapshot `{SNAPSHOT}`: no hold settled, "
        "no candidate is new, and no hold's evidence moved."
    ]


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
