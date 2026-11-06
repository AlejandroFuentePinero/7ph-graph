"""Tests for the post-ingestion curation brief (issues #233, #273).

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
        "ruled_pairs": [], "rule_overrides": [], "refused_entries": [],
        "multi_name_ids": [], "held_names": [], "fired_holds": [], "curated": 0,
    } | overrides


def _pair(a, b, names, state, *, shape="nickname", overlap=0.0, archetype=None,
          pool=None, at_or_above=None, shared=(), hubs=None, canonical=None,
          overridden_by=None) -> dict:
    """A ruled pair as the build writes one."""
    return {"pilots": [a, b], "names": list(names), "shape": shape,
            "state": state, "overlap": overlap, "archetype": archetype, "pool": pool,
            "at_or_above": at_or_above, "shared_events": list(shared),
            "hubs": hubs or {}, "canonical": canonical, "overridden_by": overridden_by}


def _multi(pilot, display, *others) -> dict:
    return {"pilot": pilot, "display_name": display, "names": [display, *others]}


def _rows(report: str, heading: str) -> list[str]:
    """The bullet lines under one heading, or none where it is not printed."""
    if f"## {heading}" not in report:
        return []
    body = report.split(f"## {heading}", 1)[1].split("\n#", 1)[0]
    return [line for line in body.splitlines() if line.startswith("- ")]


def test_the_live_brief_states_exactly_what_the_live_report_holds():
    # The claim is about the real record, so it is graded against the real
    # record: every settled hold, every pair whose state changed, every override
    # and refusal, and every unexamined id the build wrote is a row in the
    # brief, in the one section its history puts it in, and the brief invents
    # none. Derived from `reconciliation.json` and the snapshot sequence each
    # run, never transcribed (issues #227, #271, #273).
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

    settled = [f for f in recon["fired_holds"] if f["trigger"] in SETTLING_TRIGGERS]
    rows = _rows(report, "Decisions available")
    assert len(rows) == len(settled)
    for hold in settled:
        assert any(hold["detail"] in row and all(pid in row for pid in hold["pilots"])
                   for row in rows)
    # Every pair whose state differs from what the previous sequence derived is
    # in the block of its state, and a pair whose state held is in none.
    before = {frozenset(p["pilots"]): p["state"]
              for p in (previous or {}).get("ruled_pairs", [])}
    for state, heading in [("merged", "Merged by rule"), ("separated", "Separated"),
                           ("watching", "Watching")]:
        rows = _rows(report, heading)
        for pair in recon["ruled_pairs"]:
            if pair["state"] != state:
                continue
            listed = any(all(pid in row for pid in pair["pilots"]) for row in rows)
            assert listed == (before.get(frozenset(pair["pilots"])) != state), pair
    for pair in recon["rule_overrides"]:
        assert any(all(pid in row for pid in pair["pilots"])
                   for row in _rows(report, "Rule overridden by the dictionary"))
    for entry in recon["refused_entries"]:
        assert any(entry["key"] in row for row in _rows(report, "Entries refused"))
    # Every unexamined id is in exactly one of the two queue sections, and
    # which one is whether the sequence before this snapshot already raised it.
    before_ids = {c["pilot"] for c in (previous or {}).get("multi_name_ids", [])}
    new = [c for c in recon["multi_name_ids"] if c["pilot"] not in before_ids]
    carried = [c for c in recon["multi_name_ids"] if c["pilot"] in before_ids]
    for candidates, heading in [(new, "New this ingestion"), (carried, "Carried over")]:
        rows = _rows(report, heading)
        for candidate in candidates:
            assert any(candidate["pilot"] in row for row in rows)
    assert f"- new: {len(new)}" in report
    assert f"- carried: {len(carried)}" in report
    # Every hold on file reaches the brief once: as a decision, or in the tail.
    assert len(_rows(report, "Unchanged holds")) == len(curation.holds) - len(settled)


def test_a_settled_hold_leads_the_brief_and_carries_the_fact_that_settled_it():
    # A fired settling trigger is a decision sitting unmade (issue #230), and is
    # why the issue is worth opening at all, so it leads.
    recon = _recon(
        fired_holds=[{"pilots": ["P"], "trigger": "event_split", "settles_on": "event_split",
                      "as_of": SNAPSHOT, "decks": ["d1", "d4"],
                      "detail": "registered at E1 under 'Tom H' with d1, 'Tom M' with d4"}],
        ruled_pairs=[_pair("A", "B", ("Toby M", "TobM7"), "merged", shape="handle",
                           overlap=1.0, archetype="lands", canonical="A")],
    )
    holds = {frozenset({"P"}): Hold("event_split", SNAPSHOT)}

    report = curation_report(recon, holds, SNAPSHOT)

    settled = report.index("Decisions available")
    assert settled < report.index("Merged by rule")
    assert "- `P` (event_split): registered at E1 under 'Tom H' with d1" in report


def test_the_three_blocks_list_only_pairs_whose_state_changed_since_the_previous_round():
    # The report is state changes only (ruling 9 on #266): a pair the rules
    # merged last round and this round is a settled fact, not news; a pair that
    # moved between states is listed under the state it now takes; a pair the
    # previous sequence never raised is listed under whichever state it took.
    toby = _pair("A", "B", ("Toby M", "TobM7"), "merged", shape="handle",
                 overlap=0.85, archetype="lands", pool=262, at_or_above=1, canonical="A")
    matt = _pair("C", "D", ("Matthew M", "Matt M"), "separated", overlap=0.31,
                 archetype="grixis", shared=["E1"])
    darcy = _pair("E", "F", ("Darcy E", "Darcy"), "watching", shape="first-name",
                  overlap=1.0, archetype="lands", hubs={"F": "bare first name"})
    recon = _recon(ruled_pairs=[toby, matt, darcy])
    previous = _recon(ruled_pairs=[
        toby, dict(matt, state="watching", shared_events=[]),
    ])

    report = curation_report(recon, {}, SNAPSHOT, previous=previous)

    assert _rows(report, "Merged by rule") == []
    assert _rows(report, "Separated (1)") == [
        "- `C` + `D`: Matthew M / Matt M (nickname), 0.31 on grixis, both at E1"]
    assert _rows(report, "Watching (1)") == [
        "- `E` + `F`: Darcy E / Darcy (first-name), 1.00 on lands, Darcy is a bare first name"]
    assert "- merged by rule: 1 (changed: 0)" in report
    assert "- separated: 1 (changed: 1)" in report
    assert "- watching: 1 (changed: 1)" in report
    # The instrument the merges are read against, re-measured by the build.
    rated = curation_report(_recon(ruled_pairs=[toby], stranger_base_rate={"0.4": 0.7731, "0.9": 0.01}),
                            {}, SNAPSHOT)
    assert "- stranger base rate: 77.3% at 0.4, 1.0% at 0.9" in rated

    # With no previous round everything is news, and a merged pair says what
    # the rules read and which node it kept.
    first = curation_report(recon, {}, SNAPSHOT)
    assert _rows(first, "Merged by rule (1)") == [
        "- `A` + `B`: Toby M = TobM7 (handle), 0.85 on lands (1 of 262 strangers as high), "
        "node `A`"]


def test_a_hub_in_several_pairs_is_one_row_listing_each_partner_with_its_overlap():
    # A bare first name against seven surnamed partners made seven rows on #262
    # (Ryan), though whose deck it is is one question (issue #271): the hub is
    # the row, listed once with every partner and each partner's best overlap
    # (ruling 9 on #266), and the heading still counts the pairs.
    recon = _recon(ruled_pairs=[
        _pair("RyanW", "Ryan", ("Ryan W", "Ryan"), "watching", shape="first-name",
              overlap=0.27, archetype="grixis", hubs={"Ryan": "bare first name"}),
        _pair("C", "D", ("Ann K", "Anne K"), "watching", shape="typo"),
        _pair("Ryan", "RyanLJ", ("Ryan", "Ryan L-J"), "watching", shape="first-name",
              hubs={"Ryan": "bare first name"}),
    ])

    rows = _rows(curation_report(recon, {}, SNAPSHOT), "Watching (3)")

    assert rows == [
        "- `Ryan` (Ryan) against 2: `RyanW` Ryan W 0.27 on grixis; "
        "`RyanLJ` Ryan L-J no deck on a shared archetype at different events",
        "- `C` + `D`: Ann K / Anne K (typo), no deck on a shared archetype at different events",
    ]


def test_overrides_and_refused_entries_are_listed_every_round_unchanged_or_not():
    # A dictionary edit is invisible to a diff between two sequences read under
    # the same dictionary, so a rule the dictionary overrode and an entry the
    # build refused are listed every build, derived rather than diffed
    # (amendments on #266), each naming what the rule said and what overrode it.
    overrides = [
        _pair("A", "B", ("Toby M", "TobM7"), "merged", shape="handle", overlap=1.0,
              archetype="lands", canonical="A", overridden_by="reject"),
        _pair("C", "D", ("Zachary M", "Zachaery M"), "separated", shape="typo",
              overlap=0.2, archetype="lands", shared=["E3"], overridden_by="merge"),
    ]
    refused = [{"kind": "merge", "ids": ["FrostyIvoryStag", "LunarAzureViper"],
                "key": "LunarAzureViper", "written": "Lachie C", "recovered": "Alexander W"}]
    recon = _recon(rule_overrides=overrides, refused_entries=refused)

    report = curation_report(recon, {}, SNAPSHOT, previous=recon)

    assert report.startswith("Curation review")
    assert _rows(report, "Rule overridden by the dictionary (2)") == [
        "- `A` + `B`: Toby M = TobM7 (handle), 1.00 on lands, node `A`; the rule says "
        "merged, a [[reject]] keeps them apart",
        "- `C` + `D`: Zachary M / Zachaery M (typo), 0.20 on lands, both at E3; the rule "
        "says separated, a [[merge]] folds them",
    ]
    assert _rows(report, "Entries refused (1)") == [
        "- merge `LunarAzureViper`: written against 'Lachie C', now recovers 'Alexander W'"]
    assert "- overridden: 2" in report and "- refused: 1" in report
    # Either alone is work: a refusal is a decision that stopped applying.
    assert curation_report(_recon(refused_entries=refused), {}, SNAPSHOT).startswith(
        "Curation review")


def test_the_multi_name_queue_splits_into_new_and_carried_above_the_held_tail():
    # The multi-name queue is not this ticket's (ruling 7 on #266): an id nobody
    # has read still ranks above the tail that has been reasoned about, and
    # which of the two queue sections it lands in is whether the previous
    # sequence already raised it (issue #271).
    recon = _recon(
        multi_name_ids=[_multi("RichardO", "Richard O", "Richard Owen"),
                        _multi("EddieL", "Eddie L", "Eddie LM")],
        held_names=[{"pilot": "MaxDK", "display_name": "Max DK", "names": ["Max DK", "Max D"],
                     "settles_on": "event_split", "as_of": SNAPSHOT}],
    )
    holds = {frozenset({"MaxDK"}): Hold("event_split", SNAPSHOT),
             frozenset({"Drifted"}): Hold("event_split", SNAPSHOT)}
    previous = _recon(multi_name_ids=[_multi("EddieL", "Eddie L", "Eddie LM")])

    report = curation_report(recon, holds, SNAPSHOT, previous=previous)

    assert _rows(report, "New this ingestion (1)") == ["- `RichardO`: Richard O, also Richard Owen"]
    assert _rows(report, "Carried over (1)") == ["- `EddieL`: Eddie L, also Eddie LM"]
    assert report.index("New this ingestion") < report.index("Unchanged holds")
    tail = _rows(report, "Unchanged holds (2)")
    assert tail == [
        f"- `Drifted`: settles on `event_split`, as of `{SNAPSHOT}`",
        f"- `MaxDK`: Max DK, also Max D, settles on `event_split`, as of `{SNAPSHOT}`",
    ]
    assert "- new: 1" in report and "- carried: 1" in report and "- unchanged holds: 2" in report


def test_a_round_with_nothing_to_work_says_so_instead_of_printing_empty_sections():
    # A brief filed after every ingestion is only read if it means something when
    # it arrives. With nothing settled, no state changed, nothing overridden or
    # refused and no id new, there is one sentence to write.
    report = curation_report(_recon(), {}, SNAPSHOT)

    assert report.strip().splitlines() == [
        f"Nothing to review for snapshot `{SNAPSHOT}`: no pair changed state, no rule "
        "was overridden, no entry was refused, no hold settled, and no id is new or "
        "carried."
    ]


def test_the_opening_words_are_what_the_runbook_files_on():
    # The ingestion runbook in docs/development.md decides whether to open an
    # issue by matching the brief's first line against "Curation review", so those
    # two words are an interface and not a caption. Renaming them without touching
    # the runbook is silent in the direction that loses work: every round would
    # print its brief and file nothing, and nobody finds out by reading a report
    # that never arrives.
    loud = _recon(ruled_pairs=[_pair("A", "B", ("Ann B", "Annie B"), "watching")])

    assert curation_report(loud, {}, SNAPSHOT).startswith("Curation review")
    assert curation_report(_recon(), {}, SNAPSHOT).startswith("Nothing to review")


def test_a_quiet_round_with_holds_still_on_file_leads_with_the_quiet_line():
    # The tail is not work, so it does not turn a quiet round into a loud one; it
    # is still printed, because a hold nobody lists is a hold nobody retires. A
    # pair whose state held since the previous round is not work either.
    toby = _pair("A", "B", ("Toby M", "TobM7"), "merged", shape="handle", canonical="A")
    recon = _recon(ruled_pairs=[toby])

    report = curation_report(recon, {frozenset({"P"}): Hold("event_split", SNAPSHOT)},
                             SNAPSHOT, previous=recon)

    assert report.splitlines()[0].startswith("Nothing to review")
    assert "## Unchanged holds (1)" in report
    assert "## Merged by rule" not in report
    assert "- merged by rule: 1 (changed: 0)" in report
