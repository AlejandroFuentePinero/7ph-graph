"""Tests for the curation dictionary: loading, validation, and dead-entry
detection (issue #37, ADR 0005).

Load-time tests write a tiny TOML to ``tmp_path`` and read it back through
:func:`load_curation`, the public seam a maintainer's edits pass through.
Dead-entry tests exercise :func:`dead_entries` directly against hand-built
id-sets, the seam the build calls with the unioned snapshot ids. One test reads
neither: it loads the checked-in ``curation/pilots.toml``, because the guards are
only worth what the real dictionary can pass (issue #204).
"""

from pathlib import Path

import pytest

from graph7ph.curation import (
    CURATION_PATH,
    ArchetypeOverride,
    Curation,
    CurationError,
    DeadEntry,
    Hold,
    dead_entries,
    load_curation,
)


ROOT = Path(__file__).resolve().parents[1]


def _write(tmp_path, toml: str):
    path = tmp_path / "pilots.toml"
    path.write_text(toml)
    return path


def test_a_hold_records_the_pair_a_human_examined_and_left_undecided(tmp_path):
    # The eighth kind (issue #228). A hold is not a decision, it is the record
    # that one was attempted and could not be reached, so it carries the two
    # things a later pass needs: what would settle the pair, and the snapshot the
    # reasoning was written against.
    path = _write(tmp_path, """
        [[hold]]
        ids = ["A", "B"]
        settles_on = "shared_event"
        as_of = "20260815T140746Z"
    """)

    curation = load_curation(path)

    assert curation.hold("A", "B") == Hold(
        settles_on="shared_event", as_of="20260815T140746Z")
    # Keyed on the unordered pair, like every other all-pairs decision.
    assert curation.hold("B", "A") == curation.hold("A", "B")
    assert curation.hold("A", "C") is None


def test_a_hold_records_the_single_id_a_human_examined_and_left_undecided(tmp_path):
    # The other identity queue asks about one id rather than a pair: an id whose
    # decks recovered several surnames is one id that may be several people
    # (issues #39, #232). The same kind records it, carrying the same two fields
    # and keyed on the one id the question is about.
    path = _write(tmp_path, """
        [[hold]]
        ids = ["A"]
        settles_on = "event_split"
        as_of = "20260815T140746Z"
    """)

    curation = load_curation(path)

    assert curation.hold("A") == Hold(
        settles_on="event_split", as_of="20260815T140746Z")
    # It holds the id's own names open, and says nothing about any pair it is in.
    assert curation.hold("A", "B") is None


def test_name_pin_on_non_canonical_merge_member_raises(tmp_path):
    # `names` is looked up by the canonical bucket id, so a pin on a member that
    # merges away can never fire: an authoring contradiction, not a dead entry.
    path = _write(tmp_path, """
        [[merge]]
        ids = ["A", "B"]
        canonical = "A"

        [[name]]
        pilot = "B"
        display_name = "Real"
    """)
    with pytest.raises(CurationError):
        load_curation(path)


def test_reject_on_a_merged_pair_raises_and_names_the_remedy(tmp_path):
    # A merge says "same person" and a reject says "not the same person" about the
    # one pair. The appended inverse is not an override, it is inert: the merge has
    # already folded the two ids, so the reject moves no deck and leaves no dead
    # entry, and a reviewer's correction looks recorded while doing nothing. The
    # remedy is to edit the [[merge]], so the message has to say so (issue #204).
    path = _write(tmp_path, """
        [[merge]]
        ids = ["A", "B"]
        canonical = "A"

        [[reject]]
        ids = ["A", "B"]
    """)
    with pytest.raises(CurationError, match=r"edit the \[\[merge\]\]"):
        load_curation(path)


def test_split_on_a_transitively_merged_pair_raises(tmp_path):
    # The guard reads the folded group, not the entry it was written in. "B" and "C"
    # never share a [[merge]] block, but both merge onto "A", so the dictionary says
    # they are one person just as plainly, and a [[split]] against them is just as
    # inert -- the identical-name join it overrides never sees a pair the merges
    # already folded.
    path = _write(tmp_path, """
        [[merge]]
        ids = ["A", "B"]
        canonical = "A"

        [[merge]]
        ids = ["A", "C"]
        canonical = "A"

        [[split]]
        ids = ["B", "C"]
    """)
    with pytest.raises(CurationError, match=r"edit the \[\[merge\]\]"):
        load_curation(path)


def test_two_name_pins_on_one_pilot_raise(tmp_path):
    # `_names` is a dict, so last-wins: with two pins on one id the rendered name is
    # decided by which block sits lower in the file, not by either decision. Reversing
    # the blocks renders the other name and nothing anywhere says which was meant, so
    # the repeat is refused where the author can still see both (issue #204).
    path = _write(tmp_path, """
        [[name]]
        pilot = "A"
        display_name = "Tristian G"

        [[name]]
        pilot = "A"
        display_name = "Tristan G"
    """)
    with pytest.raises(CurationError, match="Tristian G"):
        load_curation(path)


def test_hold_with_three_ids_leaves_every_pair_open(tmp_path):
    # A hold is an all-pairs decision like reject and split: three ids nobody
    # could separate leave three open questions, not one three-way one, so each
    # pair carries the entry's condition.
    path = _write(tmp_path, """
        [[hold]]
        ids = ["A", "B", "C"]
        settles_on = "shared_event"
        as_of = "20260815T140746Z"
    """)

    curation = load_curation(path)

    assert len(curation.holds) == 3
    assert all(len(pair) == 2 for pair in curation.holds)
    assert curation.hold("B", "C") == Hold("shared_event", "20260815T140746Z")


@pytest.mark.parametrize("field", ['settles_on = "shared_event"',
                                   'as_of = "20260815T140746Z"'])
def test_hold_missing_either_field_raises(tmp_path, field):
    # A hold without `settles_on` records no way out of the queue, and one
    # without `as_of` cannot say whether it predates the evidence now on file.
    # Either way the entry parks the pair forever, which is the one thing a
    # watchlist must not do.
    path = _write(tmp_path, f"""
        [[hold]]
        ids = ["A", "B"]
        {field}
    """)
    with pytest.raises(CurationError):
        load_curation(path)


@pytest.mark.parametrize("stamp", ["2026-08-15", "20260815", "yesterday",
                                   "20260815T140746"])
def test_hold_with_an_as_of_that_is_not_a_snapshot_stamp_raises(tmp_path, stamp):
    # `as_of` dates the reasoning against the corpus it was written over, so it
    # has to be a snapshot's name, which `fetch` mints as "%Y%m%dT%H%M%SZ". The
    # shape is all this can check: `snapshots/` is gitignored and each directory
    # is named for the wall clock of the machine that fetched it, so no two
    # clones hold the same stamps and existence is not a portable question
    # (issue #228). Whether the stamp names a snapshot *this build read* is
    # answerable only at build time, which is #230.
    path = _write(tmp_path, f"""
        [[hold]]
        ids = ["A", "B"]
        settles_on = "shared_event"
        as_of = "{stamp}"
    """)
    with pytest.raises(CurationError, match="as_of"):
        load_curation(path)


def test_a_well_formed_as_of_loads_whether_or_not_that_snapshot_is_on_this_disk(
    tmp_path, monkeypatch
):
    # The portability property, and the reason existence is not checked here.
    # `snapshots/` is gitignored and `fetch` names each directory for the wall
    # clock of the machine that made it, so the stamps differ in every clone and
    # CI has none at all. A dictionary that only loads on the machine that wrote
    # it would make the one entry kind that applies nothing to the graph the only
    # one that can stop the graph being built.
    monkeypatch.chdir(tmp_path)  # no snapshots/ anywhere in sight
    path = _write(tmp_path, """
        [[hold]]
        ids = ["A", "B"]
        settles_on = "shared_event"
        as_of = "19990101T000000Z"
    """)

    assert load_curation(path).hold("A", "B").as_of == "19990101T000000Z"


def test_two_holds_on_one_pair_raise_rather_than_last_wins(tmp_path):
    # A hold exists to be revisited, so appending a refreshed entry over an older
    # one is the natural edit -- and it would be last-wins, making the condition
    # the report carries a fact about block order in the file rather than about
    # either judgement, with the discarded one reported nowhere. Refused for the
    # same reason `_names` refuses a second pin on one id (issue #204).
    path = _write(tmp_path, """
        [[hold]]
        ids = ["A", "B"]
        settles_on = "shared_event"
        as_of = "20260713T232944Z"

        [[hold]]
        ids = ["A", "B", "C"]
        settles_on = "fresh_deck"
        as_of = "20260815T140746Z"
    """)
    with pytest.raises(CurationError, match="shared_event"):
        load_curation(path)


@pytest.mark.parametrize("decision", [
    '[[merge]]\nids = ["A", "B"]\ncanonical = "A"',
    '[[reject]]\nids = ["A", "B"]',
    '[[split]]\nids = ["A", "B"]',
])
def test_a_pair_both_held_and_decided_raises(tmp_path, decision):
    # A hold says "looked, could not settle"; a merge, reject or split each say
    # "settled". Both cannot be true of one pair, and there is no precedence to
    # apply: whichever wins, the other entry is a recorded judgement firing
    # nothing, which is the shape ADR 0005 already refuses for reject-on-merge.
    path = _write(tmp_path, f"""
        {decision}

        [[hold]]
        ids = ["A", "B"]
        settles_on = "shared_event"
        as_of = "20260815T140746Z"
    """)
    with pytest.raises(CurationError, match="A"):
        load_curation(path)


def test_a_hold_on_one_id_a_merge_folds_away_raises(tmp_path):
    # The multi-name queue is read off the canonical id a merge leaves standing,
    # so a hold naming a folded-away member matches nothing there and leaves the
    # canonical reported as unexamined -- while the hold's own triggers, which do
    # resolve the merge, can still announce a decision on it. The queue and the
    # banner would then disagree about the same id. Refused the way a [[name]]
    # pinned on a merged id is, and with the same fix (issue #232).
    path = _write(tmp_path, """
        [[merge]]
        ids = ["A", "C"]
        canonical = "C"

        [[hold]]
        ids = ["A"]
        settles_on = "event_split"
        as_of = "20260815T140746Z"
    """)
    with pytest.raises(CurationError, match="hold the canonical id instead"):
        load_curation(path)


def test_a_hold_on_a_pair_two_merges_chained_together_raises(tmp_path):
    # The folded group is what counts, not the entry: A and C reach one canonical
    # through separate [[merge]] blocks, so they are as decided as two ids named
    # in the same one, and a hold across them is the same contradiction.
    path = _write(tmp_path, """
        [[merge]]
        ids = ["A", "B"]
        canonical = "B"

        [[merge]]
        ids = ["B", "C"]
        canonical = "B"

        [[hold]]
        ids = ["A", "C"]
        settles_on = "shared_event"
        as_of = "20260815T140746Z"
    """)
    with pytest.raises(CurationError):
        load_curation(path)


def test_a_hold_beside_an_unrelated_decision_loads(tmp_path):
    # The guard has to bite on the pair, not on the presence of both kinds: a
    # dictionary that holds one pair and decides a different one is the normal
    # state of a working watchlist, and must load.
    path = _write(tmp_path, """
        [[reject]]
        ids = ["A", "B"]

        [[hold]]
        ids = ["A", "C"]
        settles_on = "shared_event"
        as_of = "20260815T140746Z"
    """)

    curation = load_curation(path)

    assert curation.is_rejected("A", "B")
    assert curation.hold("A", "C") == Hold("shared_event", "20260815T140746Z")


def test_reject_with_three_ids_suppresses_every_pair(tmp_path):
    # A 3-id reject means the three are mutually distinct people, so every pair
    # among them must be suppressed -- not just one, and not none (the F10 bug).
    path = _write(tmp_path, """
        [[reject]]
        ids = ["A", "B", "C"]
    """)
    curation = load_curation(path)
    assert curation.is_rejected("A", "B")
    assert curation.is_rejected("A", "C")
    assert curation.is_rejected("B", "C")


def test_split_with_three_ids_keeps_every_pair_apart(tmp_path):
    # A 3-id split means the three are mutually distinct people who share a
    # display name, so every pair among them must be kept apart at the join --
    # not just one, and not none (the reject-shape F10 bug applied to splits).
    path = _write(tmp_path, """
        [[split]]
        ids = ["A", "B", "C"]
    """)
    curation = load_curation(path)
    assert curation.is_split("A", "B")
    assert curation.is_split("A", "C")
    assert curation.is_split("B", "C")
    assert not curation.is_split("A", "D")


def test_merge_repeating_one_id_raises(tmp_path):
    # A merge of an id into itself is a typo that folds nobody: before the guard
    # counted distinct ids it loaded clean and emitted no merge at all, so the
    # build printed the same counts as if the entry had never been written.
    path = _write(tmp_path, """
        [[merge]]
        ids = ["A", "A"]
        canonical = "A"
    """)
    with pytest.raises(CurationError):
        load_curation(path)


def test_reject_repeating_one_id_raises(tmp_path):
    # Same typo on a reject stores a size-1 frozenset, which `is_rejected` can
    # never match because it always builds a 2-element one: a decision that is
    # recorded, reported nowhere, and permanently unmatchable.
    path = _write(tmp_path, """
        [[reject]]
        ids = ["A", "A"]
    """)
    with pytest.raises(CurationError):
        load_curation(path)


def test_reject_repeating_an_id_alongside_a_second_raises(tmp_path):
    # The half-live shape: ["A", "A", "B"] would store the live A/B pair plus a
    # dead size-1 set, so the entry works and is partly discarded at once. The
    # guard counts distinct ids, so this is refused rather than half-applied.
    path = _write(tmp_path, """
        [[reject]]
        ids = ["A", "A", "B"]
    """)
    with pytest.raises(CurationError):
        load_curation(path)


def test_every_stored_pair_holds_exactly_two_ids(tmp_path):
    # The postcondition `_pairs` states in its own docstring, and the one ADR
    # 0009 repeats: an all-pairs decision is stored as size-2 frozensets, whether
    # it was authored with two ids or expanded from more.
    path = _write(tmp_path, """
        [[reject]]
        ids = ["A", "B"]

        [[reject]]
        ids = ["C", "D", "E"]

        [[split]]
        ids = ["F", "G"]

        [[split]]
        ids = ["H", "I", "J", "K"]
    """)
    curation = load_curation(path)
    assert len(curation.rejected) == 4 and len(curation.splits) == 7
    for pair in curation.rejected | curation.splits:
        assert len(pair) == 2


def test_deck_event_entry_records_the_event_a_deck_was_really_played_at(tmp_path):
    # A malformed source event (a stringified NaN) strands a deck at an event
    # that never happened. The decision names the event it really belongs to,
    # keyed on the deck id (issue #167).
    path = _write(tmp_path, """
        [[deck_event]]
        deck = "orphan"
        event = "CBR3"
    """)
    assert load_curation(path).deck_events == {"orphan": "CBR3"}


@pytest.mark.parametrize("entry", ['deck = "orphan"', 'event = "CBR3"'])
def test_deck_event_entry_missing_either_half_raises(tmp_path, entry):
    # Half an entry decides nothing: without a deck there is nothing to move,
    # and without an event there is nowhere to move it.
    path = _write(tmp_path, f"""
        [[deck_event]]
        {entry}
    """)
    with pytest.raises(CurationError):
        load_curation(path)


def test_the_checked_in_dictionary_holds_no_contradiction():
    # The guards above are worth nothing if the dictionary they grade cannot pass
    # them, and the record is the only place the contradictions were real: three
    # pairs carried a [[reject]] a later [[merge]] had overturned, and one id carried
    # two [[name]] pins (issue #204). Graded on the checked-in file rather than a
    # fixture because that is the copy every build reads. Two whole-graph builds
    # would redden on a contradiction too (`build_graph` loads this file whenever no
    # curation is passed), but they would report it as a broken build; this says
    # which file is wrong, in milliseconds.
    #
    # Anchored on __file__, not on `load_curation`'s cwd-relative default: that
    # default returns an *empty* dictionary for a path it cannot see, so run from
    # anywhere but the repo root it would pass vacuously (the ROOT idiom of
    # `test_deploy.py` and `test_entrypoint.py`).
    load_curation(ROOT / CURATION_PATH)


def _mixed_curation() -> Curation:
    """A dictionary with one live and one dead entry of every type."""
    return Curation(
        merges={"deadMember": "canon", "liveMember": "canon"},
        rejected=frozenset({
            frozenset({"deadA", "liveB"}),   # deadA absent -> pair can't fire
            frozenset({"liveB", "liveC"}),   # both present -> live
        }),
        names={"deadName": "X", "liveName": "Y"},
        deck_pilots={"deadDeck": "p1", "liveDeck": "p2"},
        deck_archetypes={"deadDeck2": ArchetypeOverride("N", "engine:e", "L")},
        deck_events={"deadDeck3": "E", "liveDeck": "E"},
        splits=frozenset({
            frozenset({"deadS", "liveB"}),   # deadS absent -> pair can't fire
            frozenset({"liveB", "liveC"}),   # both present -> live
        }),
        holds={
            frozenset({"deadH", "liveB"}): Hold("shared_event", "20260815T140746Z"),
            frozenset({"liveB", "liveC"}): Hold("shared_event", "20260815T140746Z"),
        },
    )


def test_dead_entries_flags_every_absent_keyed_entry():
    pilot_ids = {"canon", "liveMember", "liveB", "liveC", "liveName"}
    deck_ids = {"liveDeck"}
    dead = dead_entries(_mixed_curation(), pilot_ids, deck_ids)

    flagged = {(d.kind, d.key) for d in dead}
    assert ("merge", "deadMember") in flagged
    assert ("reject", "deadA") in flagged
    assert ("split", "deadS") in flagged
    # A hold on a vanished id is the worst dead entry of the eight: it keeps a
    # pair off the review list forever while naming a person who is gone.
    assert ("hold", "deadH") in flagged
    assert ("name", "deadName") in flagged
    assert ("deck_pilot", "deadDeck") in flagged
    assert ("deck_archetype", "deadDeck2") in flagged
    assert ("deck_event", "deadDeck3") in flagged
    # Live entries never appear.
    assert not any(key in {"liveMember", "canon", "liveB", "liveC", "liveName"}
                   for _, key in flagged)


def test_a_hold_on_one_absent_id_is_reported_without_a_partner():
    # A hold on a single id keeps that id's own names off the multi-name queue,
    # so an id the source has since dropped parks a question about nobody. It is
    # the same dead entry as a held pair's, and it names what it was holding
    # open rather than an empty partner list (issue #232).
    curation = Curation(
        merges={}, rejected=frozenset(), names={}, deck_pilots={},
        holds={frozenset({"goneH"}): Hold("event_split", "20260815T140746Z")},
    )

    assert dead_entries(curation, {"liveB"}, set()) == [
        DeadEntry("hold", "goneH", "held on its own several names")]
    assert dead_entries(curation, {"goneH"}, set()) == []


def test_an_id_held_both_alone_and_in_a_pair_is_reported_for_each():
    # Two entries asking two questions of one id: whether its own names are one
    # person, and whether it is the same person as another id. Reporting them on
    # one row would let a maintainer retire the pair the row names and leave the
    # other entry on file, dead and now flagged by nothing (issue #232).
    curation = Curation(
        merges={}, rejected=frozenset(), names={}, deck_pilots={},
        holds={
            frozenset({"goneH"}): Hold("event_split", "20260815T140746Z"),
            frozenset({"goneH", "other"}): Hold("shared_event", "20260815T140746Z"),
        },
    )

    assert dead_entries(curation, {"other"}, set()) == [
        DeadEntry("hold", "goneH", "held on its own several names"),
        DeadEntry("hold", "goneH", "held against ['other']"),
    ]


def test_a_hold_dated_against_a_snapshot_no_build_read_is_reported():
    # `as_of` is graded on shape at load time, because `snapshots/` is gitignored
    # and every clone holds different stamps (issue #228). Whether it names a
    # snapshot a build actually read is the real question, and it is answerable
    # only where the ingested set exists (issue #230). Reported and never fatal:
    # a pruned or re-fetched snapshot must not turn a valid hold into a build
    # abort, and a hold is the one kind that applies nothing to the graph.
    curation = Curation(
        merges={}, rejected=frozenset(), names={}, deck_pilots={},
        holds={frozenset({"liveB", "liveC"}):
               Hold("shared_event", "20260815T140746Z")},
    )
    ids = {"liveB", "liveC"}

    # The build read the snapshot the reasoning was written against.
    assert dead_entries(curation, ids, set(),
                        {"20260713T232944Z", "20260815T140746Z"}) == []

    # It did not: the entry dates itself against a corpus nobody can reproduce,
    # so the stamp is what the report names.
    pruned = dead_entries(curation, ids, set(), {"20260713T232944Z"})

    assert [(d.kind, d.key) for d in pruned] == [("hold", "20260815T140746Z")]
    assert "liveB" in pruned[0].detail and "liveC" in pruned[0].detail


def test_dead_entries_empty_when_all_ids_present():
    cur = _mixed_curation()
    pilot_ids = {"canon", "deadMember", "liveMember", "deadA", "deadS", "deadH",
                 "liveB", "liveC", "deadName", "liveName"}
    deck_ids = {"deadDeck", "liveDeck", "deadDeck2", "deadDeck3"}
    assert dead_entries(cur, pilot_ids, deck_ids) == []


def test_merges_flatten_transitively_across_entries(tmp_path):
    # "Alexadner J" merges into "Alex J", which merges into "Alexander J": all
    # three must land on the one canonical in a single lookup.
    path = _write(tmp_path, """
        [[merge]]
        ids = ["AlexJ", "AlexanderJ"]
        canonical = "AlexanderJ"

        [[merge]]
        ids = ["AlexadnerJ", "AlexanderJ"]
        canonical = "AlexanderJ"
    """)
    curation = load_curation(path)
    assert curation.canonical("AlexJ") == "AlexanderJ"
    assert curation.canonical("AlexadnerJ") == "AlexanderJ"
    assert curation.canonical("AlexanderJ") == "AlexanderJ"


def test_double_canonical_in_one_group_raises(tmp_path):
    # Two entries chain into one group (shared "B") but name different canonical
    # ids: they cannot both win, so this is a contradiction, not a preference.
    path = _write(tmp_path, """
        [[merge]]
        ids = ["A", "B"]
        canonical = "A"

        [[merge]]
        ids = ["B", "C"]
        canonical = "C"
    """)
    with pytest.raises(CurationError):
        load_curation(path)
