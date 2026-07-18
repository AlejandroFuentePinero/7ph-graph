"""Tests for the curation dictionary: loading, validation, and dead-entry
detection (issue #37, ADR 0005).

Load-time tests write a tiny TOML to ``tmp_path`` and read it back through
:func:`load_curation`, the public seam a maintainer's edits pass through.
Dead-entry tests exercise :func:`dead_entries` directly against hand-built
id-sets, the seam the build calls with the unioned snapshot ids.
"""

import pytest

from graph7ph.curation import (
    ArchetypeOverride,
    Curation,
    CurationError,
    dead_entries,
    load_curation,
)


def _write(tmp_path, toml: str):
    path = tmp_path / "pilots.toml"
    path.write_text(toml)
    return path


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
    )


def test_dead_entries_flags_every_absent_keyed_entry():
    pilot_ids = {"canon", "liveMember", "liveB", "liveC", "liveName"}
    deck_ids = {"liveDeck"}
    dead = dead_entries(_mixed_curation(), pilot_ids, deck_ids)

    flagged = {(d.kind, d.key) for d in dead}
    assert ("merge", "deadMember") in flagged
    assert ("reject", "deadA") in flagged
    assert ("name", "deadName") in flagged
    assert ("deck_pilot", "deadDeck") in flagged
    assert ("deck_archetype", "deadDeck2") in flagged
    # Live entries never appear.
    assert not any(key in {"liveMember", "canon", "liveB", "liveC", "liveName"}
                   for _, key in flagged)


def test_dead_entries_empty_when_all_ids_present():
    cur = _mixed_curation()
    pilot_ids = {"canon", "deadMember", "liveMember", "deadA", "liveB", "liveC",
                 "deadName", "liveName"}
    deck_ids = {"deadDeck", "liveDeck", "deadDeck2"}
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
