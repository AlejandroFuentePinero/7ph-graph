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
    Entry,
    Hold,
    dead_entries,
    load_curation,
)


ROOT = Path(__file__).resolve().parents[1]


def _write(tmp_path, toml: str):
    path = tmp_path / "pilots.toml"
    path.write_text(toml)
    return path


def test_a_hold_on_a_pair_is_refused_at_load_now_that_the_build_decides_pairs(tmp_path):
    # The pair holds retired with issue #273: every candidate pair's state is
    # derived at build (merged by rule, separated by a shared event, watching),
    # so a hold on two ids parks nothing and would only hide a pair from the
    # three-state report. Refused with the remedy rather than ignored, since an
    # entry that loads and fires nothing is the quiet failure ADR 0005 names.
    path = _write(tmp_path, """
        [[hold]]
        ids = ["A", "B"]
        settles_on = "shared_event"
        as_of = "20260815T140746Z"
    """)

    with pytest.raises(CurationError, match="derives") as exc:
        load_curation(path)
    assert "'A'" in str(exc.value) and "'B'" in str(exc.value)
    assert "[[merge]]" in str(exc.value) and "[[reject]]" in str(exc.value)


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
    assert curation.hold("B") is None


def test_a_hold_that_settles_on_a_retired_trigger_is_refused_at_load(tmp_path):
    # The build evaluates one trigger for a single-id hold, the event split
    # (#273 retired the rest), so a hold naming another would sit unexamined
    # forever while reading as watched.
    path = _write(tmp_path, """
        [[hold]]
        ids = ["A"]
        settles_on = "new_decks"
        as_of = "20260815T140746Z"
    """)

    with pytest.raises(CurationError, match="settles_on.*'new_decks'.*event_split"):
        load_curation(path)


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


@pytest.mark.parametrize("field", ['settles_on = "event_split"',
                                   'as_of = "20260815T140746Z"'])
def test_hold_missing_either_field_raises(tmp_path, field):
    # A hold without `settles_on` records no way out of the queue, and one
    # without `as_of` cannot say whether it predates the evidence now on file.
    # Either way the entry parks the pair forever, which is the one thing a
    # watchlist must not do.
    path = _write(tmp_path, f"""
        [[hold]]
        ids = ["A"]
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
        ids = ["A"]
        settles_on = "event_split"
        as_of = "{stamp}"
    """)
    with pytest.raises(CurationError, match="as_of"):
        load_curation(path)


@pytest.mark.parametrize("unquoted", ["20260815", "2026-08-15T14:07:46Z"])
def test_hold_with_an_unquoted_as_of_is_refused_as_a_sentence_not_a_traceback(
    tmp_path, unquoted
):
    # Leaving the stamp unquoted is the slip a curator actually makes, and TOML
    # answers it with an int or a datetime rather than a string. That has to reach
    # the same CurationError as any other malformed stamp: it is the only
    # exception `graph7ph build` catches to abort with a sentence, and the only
    # one `curation-report` catches before it prints, so anything else lands a
    # traceback on stdout and the runbook files it as the round's review brief.
    path = _write(tmp_path, f"""
        [[hold]]
        ids = ["A"]
        settles_on = "event_split"
        as_of = {unquoted}
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
        ids = ["A"]
        settles_on = "event_split"
        as_of = "19990101T000000Z"
    """)

    assert load_curation(path).hold("A").as_of == "19990101T000000Z"


def test_two_holds_on_one_id_raise_rather_than_last_wins(tmp_path):
    # A hold exists to be revisited, so appending a refreshed entry over an older
    # one is the natural edit -- and it would be last-wins, making the condition
    # the report carries a fact about block order in the file rather than about
    # either judgement, with the discarded one reported nowhere. Refused for the
    # same reason `_names` refuses a second pin on one id (issue #204).
    path = _write(tmp_path, """
        [[hold]]
        ids = ["A"]
        settles_on = "event_split"
        as_of = "20260713T232944Z"

        [[hold]]
        ids = ["A"]
        settles_on = "fresh_deck"
        as_of = "20260815T140746Z"
    """)
    with pytest.raises(CurationError, match="event_split"):
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


def test_a_hold_beside_an_unrelated_decision_loads(tmp_path):
    # A dictionary that holds one id and decides a pair elsewhere is the normal
    # state of a working watchlist, and must load.
    path = _write(tmp_path, """
        [[reject]]
        ids = ["A", "B"]
        as_of = "20260815T140746Z"
        names = { "A" = "Alex W", "B" = "Ben C" }

        [[hold]]
        ids = ["C"]
        settles_on = "event_split"
        as_of = "20260815T140746Z"
    """)

    curation = load_curation(path)

    assert curation.is_rejected("A", "B")
    assert curation.hold("C") == Hold("event_split", "20260815T140746Z")


def test_reject_with_three_ids_suppresses_every_pair(tmp_path):
    # A 3-id reject means the three are mutually distinct people, so every pair
    # among them must be suppressed -- not just one, and not none (the F10 bug).
    path = _write(tmp_path, """
        [[reject]]
        ids = ["A", "B", "C"]
        as_of = "20260815T140746Z"
        names = { "A" = "Alex W", "B" = "Ben C", "C" = "Cal T" }
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
        as_of = "20260815T140746Z"
        names = { "A" = "Alex W", "B" = "Alex W", "C" = "Alex W" }
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
        as_of = "20260815T140746Z"
        names = { "A" = "Alex W", "B" = "Ben C" }

        [[reject]]
        ids = ["C", "D", "E"]
        as_of = "20260815T140746Z"
        names = { "C" = "Cal T", "D" = "Dan S", "E" = "Ed D" }

        [[split]]
        ids = ["F", "G"]
        as_of = "20260815T140746Z"
        names = { "F" = "James L", "G" = "James L" }

        [[split]]
        ids = ["H", "I", "J", "K"]
        as_of = "20260815T140746Z"
        names = { "H" = "Sam L", "I" = "Sam L", "J" = "Sam L", "K" = "Sam L" }
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
        as_of = "20260815T140746Z"
        names = { "orphan" = "Peter K" }
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
            frozenset({"deadH"}): Hold("event_split", "20260815T140746Z"),
            frozenset({"liveB"}): Hold("event_split", "20260815T140746Z"),
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
    # A hold on a vanished id is the worst dead entry of the eight: it keeps an
    # id off the review list forever while naming a person who is gone.
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


def test_a_hold_dated_against_a_snapshot_no_build_read_is_reported():
    # `as_of` is graded on shape at load time, because `snapshots/` is gitignored
    # and every clone holds different stamps (issue #228). Whether it names a
    # snapshot a build actually read is the real question, and it is answerable
    # only where the ingested set exists (issue #230). Reported and never fatal:
    # a pruned or re-fetched snapshot must not turn a valid hold into a build
    # abort, and a hold is the one kind that applies nothing to the graph.
    curation = Curation(
        merges={}, rejected=frozenset(), names={}, deck_pilots={},
        holds={frozenset({"liveB"}): Hold("event_split", "20260815T140746Z")},
    )
    ids = {"liveB"}

    # The build read the snapshot the reasoning was written against.
    assert dead_entries(curation, ids, set(),
                        {"20260713T232944Z", "20260815T140746Z"}) == []

    # It did not: the entry dates itself against a corpus nobody can reproduce,
    # so the stamp is what the report names.
    pruned = dead_entries(curation, ids, set(), {"20260713T232944Z"})

    assert [(d.kind, d.key) for d in pruned] == [("hold", "20260815T140746Z")]
    assert "liveB" in pruned[0].detail


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
        as_of = "20260815T140746Z"
        names = { "AlexJ" = "Alex J", "AlexanderJ" = "Alexander J" }

        [[merge]]
        ids = ["AlexadnerJ", "AlexanderJ"]
        canonical = "AlexanderJ"
        as_of = "20260815T140746Z"
        names = { "AlexadnerJ" = "Alexadner J", "AlexanderJ" = "Alexander J" }
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


# --- Provenance on every entry that applies to the graph (issue #283) ---

STAMP = "20260713T232944Z"


@pytest.mark.parametrize("kind, body", [
    ("merge", 'ids = ["A", "B"]\ncanonical = "A"'),
    ("reject", 'ids = ["A", "B"]'),
    ("split", 'ids = ["A", "B"]'),
    ("name", 'pilot = "A"\ndisplay_name = "Alex W"'),
    ("deck_pilot", 'deck = "d1"\npilot = "A"'),
    ("deck_archetype", 'deck = "d1"\ndeck_name = "N"\nengine = "engine:e"\nengine_label = "L"'),
    ("deck_event", 'deck = "d1"\nevent = "CBR3"'),
])
@pytest.mark.parametrize("missing", ["as_of", "names"])
def test_an_entry_that_applies_to_the_graph_needs_its_provenance(tmp_path, kind, body, missing):
    # Upstream reuses a pilot id for a different person (issue #261), so an
    # entry keyed on an id is only safe while the id still names the person it
    # was written against. Every kind that applies to the graph therefore carries
    # the snapshot it was written on and the name each of its ids recovered then,
    # and the loader refuses one without them, where the author is still looking
    # at the entry (issue #283).
    provenance = {
        "as_of": f'as_of = "{STAMP}"',
        "names": 'names = { "A" = "Alex W", "B" = "Ben C", "d1" = "Alex W" }',
    }
    del provenance[missing]
    path = _write(tmp_path, f"[[{kind}]]\n{body}\n" + "\n".join(provenance.values()))

    with pytest.raises(CurationError, match=rf"\[\[{kind}\]\].*{missing}"):
        load_curation(path)


def test_a_well_formed_entry_loads_with_its_provenance(tmp_path):
    # What the build compares against the snapshot: the kind and position of the
    # table, the stamp, and the name under each id. A hold applies nothing to the
    # graph and so carries no names (issue #283).
    path = _write(tmp_path, f"""
        [[merge]]
        ids = ["A", "B"]
        canonical = "A"
        as_of = "{STAMP}"
        names = {{ "A" = "Alexander W", "B" = "Alex W" }}

        [[deck_pilot]]
        deck = "d1"
        pilot = "C"
        as_of = "{STAMP}"
        names = {{ "d1" = "Chris T", "C" = "Christopher T" }}

        [[hold]]
        ids = ["D"]
        settles_on = "event_split"
        as_of = "{STAMP}"
    """)

    curation = load_curation(path)

    assert curation.entries == (
        Entry("merge", 0, STAMP, {"A": "Alexander W", "B": "Alex W"}),
        Entry("deck_pilot", 0, STAMP, {"d1": "Chris T", "C": "Christopher T"}),
    )
    assert curation.canonical("B") == "A" and curation.deck_pilots == {"d1": "C"}


@pytest.mark.parametrize("names", [
    '{ "A" = "Alex W" }',                       # one id unnamed
    '{ "A" = "Alex W", "B" = "Ben C", "Z" = "Zed" }',  # a name under an id the entry does not name
    '{ "A" = "Alex W", "B" = "" }',             # an empty name guards nothing
])
def test_names_must_cover_exactly_the_ids_the_entry_names(tmp_path, names):
    path = _write(tmp_path, f"""
        [[reject]]
        ids = ["A", "B"]
        as_of = "{STAMP}"
        names = {names}
    """)
    with pytest.raises(CurationError, match=r"\[\[reject\]\].*names"):
        load_curation(path)
