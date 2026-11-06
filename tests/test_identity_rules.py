"""Tests for the rules that decide the clear identity cases at build (issue #273).

Every build derives one of three states for every candidate pair the name
heuristics raise: merged by rule, separated by a shared event, or watching. The
rules read the resolved node and fold before the event split, so they are
exercised through :func:`pilots.resolve_pilots`, the seam the build uses, on
crafted decks that carry what the rules read: a title, an event, a primary
archetype, a colour, a registration time and a card list.
"""

from datetime import datetime, timedelta

import pytest
from types import SimpleNamespace

from graph7ph.curation import Curation
from graph7ph.pilots import resolve_pilots

T0 = datetime(2026, 1, 1)


def _deck(deck_id, pilot, title, event, cards, *, tag="lands", colour="colour:BG",
          days=0, placement=1):
    """A deck the rules can read, registered ``days`` after the first of 2026."""
    return SimpleNamespace(
        deck_id=deck_id, pilot=pilot, name=title, event=event, placement=placement,
        deck_name=None, created_at=T0 + timedelta(days=days), primary_tag=tag,
        colour=colour,
    )


def _cards(n, offset=0):
    """``n`` distinct card names starting at ``offset``, as a (main, side) signature."""
    return (frozenset(f"card{i}" for i in range(offset, offset + n)), frozenset())


def _resolve(decks, decklists, curation=None):
    return resolve_pilots(decks, curation or Curation.empty(), decklists)


def _state(res, a, b):
    [pair] = [p for p in res.report.ruled_pairs if set(p.pilots) == {a, b}]
    return pair


def test_a_handle_sharing_a_real_names_list_merges_onto_the_real_name():
    # Toby M / TobM7 on #262: the handle's one deck is the real name's own list
    # at another event, which clears the handle bar (0.9 raw). The merge is
    # derived, not written: the handle's deck moves onto the real-name id, which
    # keeps its display name, and the report says so.
    decks = [
        _deck("d1", "RealName", "1st Toby M - Lands - E1", "E1", _cards(70)),
        _deck("d2", "RealName", "3rd Toby M - Lands - E2", "E2", _cards(70), days=60),
        _deck("d3", "Handle", "5th TobM7 - Lands - E3", "E3", _cards(70), days=120),
    ]
    decklists = {"d1": _cards(70), "d2": _cards(70), "d3": _cards(70)}

    res = _resolve(decks, decklists)

    pair = _state(res, "RealName", "Handle")
    assert (pair.state, pair.shape, pair.canonical) == ("merged", "handle", "RealName")
    assert pair.overlap == 1.0
    assert res.deck_pilot["d3"] == "RealName"
    assert [p.pilot for p in res.pilots] == ["RealName"]
    assert res.pilots[0].display_name == "Toby M"

    # The handle busier than the real name still loses the node: busiest-then-id
    # would have named this person "TobM7".
    busier_handle = decks[1:] + [
        _deck("d4", "Handle", "5th TobM7 - Lands - E4", "E4", _cards(70), days=180)]
    res = _resolve(busier_handle, decklists | {"d4": _cards(70)})
    assert _state(res, "RealName", "Handle").canonical == "RealName"
    assert [(p.pilot, p.display_name) for p in res.pilots] == [("RealName", "Toby M")]


def test_a_shared_event_separates_a_pair_and_vetoes_a_merge_it_would_have_made():
    # Two ids both registered at one event with different decks are two people
    # (ADR 0005), whatever their lists say elsewhere: four rejected pairs on file
    # clear the bar on a list shared at other events and every one of them is
    # caught by this veto. Separated means no entry and no graph effect: the
    # pair leaves the watch list, and both stay the nodes they were.
    decks = [
        _deck("d1", "MattB", "1st Matthew B - Lands - E1", "E1", _cards(70)),
        _deck("d2", "MattB", "1st Matthew B - Lands - E2", "E2", _cards(70), days=40),
        _deck("d3", "MatB", "2nd Matt B - Lands - E3", "E3", _cards(70), days=80),
        _deck("d4", "MatB", "7th Matt B - Walks - E1", "E1", _cards(70, 100),
              tag="walks", placement=7),
    ]
    decklists = {d.deck_id: _cards(70, 100 if d.deck_id == "d4" else 0) for d in decks}

    res = _resolve(decks, decklists)

    pair = _state(res, "MattB", "MatB")
    assert (pair.state, pair.overlap, pair.shared_events) == ("separated", 1.0, ["E1"])
    assert pair.canonical is None
    assert sorted(p.pilot for p in res.pilots) == ["MatB", "MattB"]
    assert res.deck_pilot["d3"] == "MatB"


def test_deck_evidence_is_read_on_one_archetype_at_different_events_only():
    # Overlap counts between decks on the same primary archetype at different
    # events: the same list on two archetypes is the format's shared shell, and
    # two ids' decks at one event overlap about as little as strangers' (ADR
    # 0005), so a 1.00 there is no evidence. The "nan" event a stranded deck
    # carries is a hole, not an event two ids can share.
    across = [
        _deck("d1", "A", "1st Ben C - Lands - E1", "E1", None, tag="lands"),
        _deck("d2", "B", "1st Benjamin C - Walks - E2", "E2", None, tag="walks", days=40),
    ]
    at_one = [
        _deck("d3", "A", "1st Ben C - Lands - E1", "E1", None),
        _deck("d4", "B", "2nd Benjamin C - Lands - E1", "E1", None, placement=2),
        _deck("d5", "B", "1st Benjamin C - Lands - E2", "E2", None, days=40),
    ]
    stranded = [
        _deck("d6", "A", "1st Ben C - Lands - nan", "nan", None),
        _deck("d7", "B", "1st Benjamin C - Lands - nan", "nan", None, days=40),
    ]
    decklists = {f"d{i}": _cards(70, 100 if i == 5 else 0) for i in range(1, 8)}

    assert _state(_resolve(across, decklists), "A", "B").overlap == 0.0
    same_event = _state(_resolve(at_one, decklists), "A", "B")
    assert (same_event.state, same_event.overlap) == ("separated", 0.0)
    nan = _state(_resolve(stranded, decklists), "A", "B")
    assert (nan.state, nan.shared_events) == ("watching", [])


def test_a_bare_first_name_merges_only_as_a_single_deck_and_is_never_separated():
    # A bare first name can hold several people (seven Nathans on #262), so it
    # is a hub: with two decks it stays watching however well one of them
    # matches (Darcy / Darcy E on #262, 1.00 on the one deck), with one deck it
    # merges, and sharing an event with a surnamed partner separates nothing,
    # because the deck at that event may be another bearer of the name.
    darcy_e = [
        _deck("d1", "DarcyE", "1st Darcy E - Lands - E1", "E1", _cards(70)),
        _deck("d2", "DarcyE", "1st Darcy E - Lands - E2", "E2", _cards(70), days=40),
    ]
    bare_one = [_deck("d3", "Darcy", "3rd Darcy - Lands - E3", "E3", _cards(70), days=80)]
    bare_two = bare_one + [
        _deck("d4", "Darcy", "9th Darcy - Walks - E4", "E4", _cards(70, 100),
              tag="walks", days=90)]
    decklists = {f"d{i}": _cards(70, 100 if i == 4 else 0) for i in range(1, 6)}

    single = _state(_resolve(darcy_e + bare_one, decklists), "DarcyE", "Darcy")
    assert (single.state, single.shape, single.canonical) == ("merged", "first-name", "DarcyE")

    two = _state(_resolve(darcy_e + bare_two, decklists), "DarcyE", "Darcy")
    assert (two.state, two.overlap, two.hubs) == ("watching", 1.0, {"Darcy": "bare first name"})

    # The bare side at the partner's event: a hub shares an event with everyone.
    at_e1 = darcy_e + [
        _deck("d5", "Darcy", "9th Darcy - Walks - E1", "E1", _cards(70, 100), tag="walks",
              placement=9)]
    shared = _state(_resolve(at_e1, decklists), "DarcyE", "Darcy")
    assert (shared.state, shared.shared_events) == ("watching", ["E1"])


def test_a_numbered_family_merges_when_it_shares_no_event_and_is_never_separated():
    # An id the event split numbers into several people (two decks at one event)
    # is a hub too: sharing an event with it proves nothing, so the pair watches
    # rather than separates; and with no shared event a list that clears the bar
    # merges it like any other id (JamL9 / James L 1 on file at 1.00), the split
    # then renumbering the family on the merged node.
    family = [
        _deck("d1", "JamesL", "1st James L - Walks - E1", "E1", _cards(70), tag="walks"),
        _deck("d2", "JamesL", "4th James L - Lands - E1", "E1", _cards(70, 100),
              placement=4),
    ]
    handle_apart = [_deck("d3", "JamL9", "2nd JamL9 - Walks - E2", "E2", _cards(70),
                          tag="walks", days=30)]
    handle_at_e1 = [_deck("d4", "JamL9", "8th JamL9 - Lands - E1", "E1", _cards(70, 200),
                          placement=8)]
    decklists = {"d1": _cards(70), "d2": _cards(70, 100), "d3": _cards(70), "d4": _cards(70, 200)}

    apart = _resolve(family + handle_apart, decklists)
    pair = _state(apart, "JamesL", "JamL9")
    assert (pair.state, pair.hubs) == ("merged", {"JamesL": "several entries at one event"})
    assert apart.deck_pilot == {"d1": "JamesL", "d2": "JamesL#2", "d3": "JamesL"}

    together = _state(_resolve(family + handle_at_e1, decklists), "JamesL", "JamL9")
    assert (together.state, together.shared_events) == ("watching", ["E1"])


def _pair_at(overlap_cards, *, relation_names, tag="lands", colour="colour:BG"):
    """Two single-deck ids at different events whose 70-card lists share ``overlap_cards``.

    Two lists of 70 sharing k cards overlap k / (140 - k): 40 is 0.4, 67 is 0.92.
    """
    name_a, name_b = relation_names
    decks = [
        _deck("a1", "A", f"1st {name_a} - X - E1", "E1", None, tag=tag, colour=colour),
        _deck("b1", "B", f"1st {name_b} - X - E2", "E2", None, tag=tag, colour=colour,
              days=40),
    ]
    decklists = {"a1": _cards(70), "b1": _cards(70, 70 - overlap_cards)}
    return decks, decklists


@pytest.mark.parametrize("names, shape, overlap_cards, state", [
    # A non-name typo takes the lowered bar, 0.4 raw: an insertion or deletion
    # on five letters or more, or a transposition of any length (Mai F / Mia F).
    (("Gianni C", "Giani C"), "typo", 40, "merged"),       # 40/100 = 0.4
    (("Gianni C", "Giani C"), "typo", 39, "watching"),     # 39/101 = 0.39
    (("Mai F", "Mia F"), "typo", 40, "merged"),
    # A substitution reaches another real name in one slip (Andrea / Andrew),
    # and a short insertion or deletion does too (Joe / Jose): never by rule.
    (("Andrea C", "Andrew C"), "look-alike", 70, "watching"),
    (("Joe M", "Jose M"), "look-alike", 70, "watching"),
    # A nickname, a handle and a bare first name take 0.9 raw.
    (("Ben C", "Benjamin C"), "nickname", 67, "merged"),   # 67/73 = 0.92
    (("Ben C", "Benjamin C"), "nickname", 66, "watching"), # 66/74 = 0.89
    (("ChrJ18", "Chris J"), "handle", 67, "merged"),
    (("Dallas", "Dallas W"), "first-name", 67, "merged"),
])
def test_each_name_shape_has_its_own_deck_bar(names, shape, overlap_cards, state):
    decks, decklists = _pair_at(overlap_cards, relation_names=names)

    pair = _state(_resolve(decks, decklists), "A", "B")

    assert (pair.shape, pair.state) == (shape, state)


def _strangers(n, *, colour="colour:BG", top=False):
    """``n`` strangers on Lands in ``colour``, registered a month apart in pairs.

    Each stranger's list shares 20 of 70 cards with its neighbours, so every
    stranger pair scores well under the pair under test; with ``top`` the last
    two share everything, scoring 1.0. The pair's own two decks sit in the pool
    too, as two distinct pilots, so the pair itself is always one of the pool's
    pairs scoring at or above its own overlap.
    """
    decks, lists = [], {}
    for i in range(n):
        did = f"s{i}"
        decks.append(_deck(did, f"S{i}", f"1st Stranger {i} - X - F{i}", f"F{i}", None,
                           colour=colour, days=(i % 2) * 30))
        lists[did] = _cards(70, 1000 + i * 50) if not (top and i >= n - 2) else _cards(70, 5000)
    return decks, lists


def test_a_name_shape_also_merges_in_the_top_percentile_of_a_matched_pool():
    # Under the raw bar, a nickname still merges where its overlap sits at or
    # above the 99th percentile of the matched stranger pool: distinct pilots
    # on the same archetype and colour, a comparable gap apart (ADR 0005's
    # baseline rule). The pool must reach 100 pairs, or no percentile counts:
    # top 1% of a smaller pool is no pair at all.
    decks, decklists = _pair_at(40, relation_names=("Ben C", "Benjamin C"))  # 0.4

    # 15 strangers a month apart make 72 pairs in the gap window: under the floor.
    few, few_lists = _strangers(15)
    small = _state(_resolve(decks + few, decklists | few_lists), "A", "B")
    assert (small.state, small.pool) == ("watching", None)

    # 21 strangers make 132 pairs in the window, the pair itself the only one
    # scoring as high as the pair: 1 in 132 is inside the top 1%.
    many, many_lists = _strangers(21)
    big = _state(_resolve(decks + many, decklists | many_lists), "A", "B")
    assert (big.state, big.pool, big.at_or_above) == ("merged", 132, 1)

    # Two strangers sharing a list a month apart put a second pair above it:
    # 2 in 132 is outside the top 1%, so the pair no longer clears.
    peak, peak_lists = _strangers(21, top=True)
    topped = _state(_resolve(decks + peak, decklists | peak_lists), "A", "B")
    assert (topped.state, topped.at_or_above) == ("watching", 2)


def test_the_pool_takes_the_busier_sides_colour_where_the_two_decks_differ():
    # A pool is matched on colour, and a pair whose closest decks differ in
    # colour takes the busier side's: its deck is the one with a population
    # to match against, the lone deck being the slip.
    decks = [
        _deck("a1", "A", "1st Ben C - X - E1", "E1", None, colour="colour:BG"),
        _deck("a2", "A", "1st Ben C - X - E3", "E3", None, colour="colour:BG", days=30),
        _deck("b1", "B", "1st Benjamin C - X - E2", "E2", None, colour="colour:G", days=40),
    ]
    decklists = {"a1": _cards(70), "a2": _cards(70, 300), "b1": _cards(70, 30)}  # 0.4
    bg, bg_lists = _strangers(21, colour="colour:BG")

    # 110 stranger pairs plus the busier side's two decks against the strangers
    # a month from each; those two against each other are one pilot, not a pair.
    pair = _state(_resolve(decks + bg, decklists | bg_lists), "A", "B")
    assert (pair.state, pair.pool) == ("merged", 131)

    # The same strangers in the lone side's colour match nothing.
    g, g_lists = _strangers(21, colour="colour:G")
    pair = _state(_resolve(decks + g, decklists | g_lists), "A", "B")
    assert (pair.state, pair.pool) == ("watching", None)


def test_a_nickname_merge_keeps_the_busiest_id_and_a_tie_goes_to_the_lower_id():
    # The join copies the canonical's display name without a re-vote, so for a
    # nickname or bare-first-name pair the busiest id keeps the node, a tie
    # going to the id the identical-name join would pick (ruling 11 on #266).
    decks = [
        _deck("d1", "Zed", "1st Ben C - X - E1", "E1", None),
        _deck("d2", "Zed", "1st Ben C - X - E3", "E3", None, days=200),
        _deck("d3", "Abe", "1st Benjamin C - X - E2", "E2", None, days=40),
    ]
    decklists = {"d1": _cards(70), "d2": _cards(70, 300), "d3": _cards(70)}

    busiest = _resolve(decks, decklists)
    assert _state(busiest, "Zed", "Abe").canonical == "Zed"
    assert [(p.pilot, p.display_name) for p in busiest.pilots] == [("Zed", "Ben C")]

    tied = _resolve(decks[:1] + decks[2:], decklists)
    assert _state(tied, "Zed", "Abe").canonical == "Zed"
    assert [(p.pilot, p.display_name) for p in tied.pilots] == [("Zed", "Ben C")]


def test_a_reject_beats_a_rule_merge_and_a_merge_beats_the_separator_both_listed():
    # Pipeline order: the rules read the resolved node after the dictionary is
    # applied, so a [[reject]] or [[split]] keeps a pair apart that the bar
    # would merge, and a [[merge]] folds a pair the shared event would part.
    # Both are listed every build, derived rather than diffed, because an edit
    # to the dictionary is invisible to a diff between snapshot sequences.
    decks = [
        _deck("d1", "RealName", "1st Toby M - Lands - E1", "E1", None),
        _deck("d2", "Handle", "5th TobM7 - Lands - E2", "E2", None, days=60),
        _deck("d3", "ZacM", "1st Zachary M - Lands - E3", "E3", None, days=100),
        _deck("d4", "ZacM", "1st Zachary M - Lands - E4", "E4", None, days=140),
        _deck("d5", "ZaxM", "2nd Zachaery M - Walks - E3", "E3", None, tag="walks",
              days=100, placement=2),
    ]
    decklists = {"d1": _cards(70), "d2": _cards(70), "d3": _cards(70, 100),
                 "d4": _cards(70, 100), "d5": _cards(70, 200)}
    curation = Curation(
        merges={"ZaxM": "ZacM"}, rejected=frozenset({frozenset({"RealName", "Handle"})}),
        names={}, deck_pilots={},
    )

    res = _resolve(decks, decklists, curation)

    # The merged pair's shared event is a collision on the merged node, which
    # the event split numbers apart, as the dictionary merge accepted.
    assert sorted(p.pilot for p in res.pilots) == ["Handle", "RealName", "ZacM", "ZacM#2"]
    assert res.deck_pilot["d5"] == "ZacM#2"
    assert [p.pilots for p in res.report.ruled_pairs] == []
    assert [(p.pilots, p.state, p.overridden_by) for p in res.report.rule_overrides] == [
        (["Handle", "RealName"], "merged", "reject"),
        (["ZacM", "ZaxM"], "separated", "merge"),
    ]
    assert res.report.curated == 2


@pytest.mark.parametrize("kind", ["reject", "split"])
def test_a_ruling_on_a_raw_id_the_name_join_folded_still_keeps_the_pair_apart(kind):
    # The rules read the node after the identical-name join, and a [[reject]]
    # or [[split]] is recorded on raw ids: here on the one the join folded into
    # its namesake. The ruling travels with the raw id into the node, so the
    # pair is kept apart and listed as an override, never merged by rule.
    decks = [
        _deck("d1", "RealA", "1st Toby M - Lands - E1", "E1", None),
        _deck("d2", "RealB", "2nd Toby M - Lands - E2", "E2", None, days=30, placement=2),
        _deck("d3", "Handle", "5th TobM7 - Lands - E3", "E3", None, days=60),
    ]
    decklists = {"d1": _cards(70), "d2": _cards(70, 100), "d3": _cards(70)}
    apart = frozenset({frozenset({"RealA", "Handle"})})
    curation = Curation(
        merges={}, rejected=apart if kind == "reject" else frozenset(), names={}, deck_pilots={},
        splits=apart if kind == "split" else frozenset(),
    )

    res = _resolve(decks, decklists, curation)

    assert sorted(p.pilot for p in res.pilots) == ["Handle", "RealB"]
    assert [p.pilots for p in res.report.ruled_pairs] == []
    assert [(p.pilots, p.state, p.overridden_by) for p in res.report.rule_overrides] == [
        (["Handle", "RealB"], "merged", kind),
    ]
    assert res.report.curated == 1


def test_the_states_do_not_depend_on_the_order_decks_or_pairs_are_read_in():
    # Two builds on the same snapshots produce identical states (ruling 11 on
    # #266): the pool is exhaustive, so a pair's percentile cannot depend on
    # which pairs were scored before it, and nothing reads deck order.
    first, first_lists = _pair_at(40, relation_names=("Ben C", "Benjamin C"))
    second, second_lists = _pair_at(40, relation_names=("Dan T", "Daniel T"))
    for d in second:
        d.deck_id = d.deck_id.replace("1", "2")
        d.pilot += "2"
        d.event += "x"
    second_lists = {k.replace("1", "2"): v for k, v in second_lists.items()}
    pool, pool_lists = _strangers(21)
    decks, decklists = first + second + pool, first_lists | second_lists | pool_lists

    forward = _resolve(decks, decklists).report.ruled_pairs
    backward = _resolve(list(reversed(decks)), dict(reversed(decklists.items()))).report.ruled_pairs

    assert forward == backward
    assert [p.pilots for p in forward] == [["A", "B"], ["A2", "B2"]]
    assert all(p.pool for p in forward)


# --- The record: the 2026-11-04 sequence, which the rules were measured on ----------

LAST_SNAPSHOT = "20261104T091012Z"

# The eleven pairs issue #266 names, by id, with the state the ticket says each
# takes under the rules (its acceptance criteria), the Darcy pair excepted from
# the merges by the bare-name clause.
NAMED_ON_266 = {
    ("FrostyIvoryStag", "LunarAzureViper"): "merged",      # Alex W / Alexander W, 1.00
    ("FrostyPurpleGecko", "LuckyGreenRaven527"): "merged",  # Ben C / Benjamin C, 1.00
    ("ShadowTealHeron", "WildVioletDolphin"): "merged",     # Mai F / Mia F, 1.00
    ("LuckyGoldManta", "RapidPurpleGecko"): "merged",       # Gianni C / Giani C, 0.97
    ("MistyCrimsonBadger", "WanderingGreenFalcon"): "merged",  # Jospeh M / Joseph M, 0.95
    ("CleverSilverWhale", "FluffyCrimsonOrca"): "merged",   # Sebastiano C / Sebastian C, 0.94
    ("NimbleMagentaWolf751", "RapidMaroonDolphin"): "merged",  # Ben P / Benjamin P, 0.92
    ("LunarAzureGecko", "NimbleAmberOtter"): "merged",      # Toby M / TobM7, 0.85 at p1.00
    ("IvoryVioletDolphin", "LuckyMaroonPanda"): "watching",  # Darcy E / Darcy: bare name, two decks
    ("FlyingJadeFalcon", "FrostyGreenTiger"): "separated",  # Matthew M / Matt M
    ("FrostyCrimsonManta", "ShadowBlueOtter"): "separated",  # Josh W / Joshua W
}


@pytest.fixture(scope="module")
def record():
    """The 2026-11-04 union, as the build reads it, or a skip where it is not on disk.

    The sequence is cut at that snapshot so the claims below stay about the
    corpus the rules were measured on (issue #266) as later fetches arrive; a
    machine without those snapshots skips rather than grading nothing.
    """
    from graph7ph.__main__ import SNAPSHOTS_ROOT
    from graph7ph.ingest import _snapshot_dirs, gate_sequence, load_checked
    from graph7ph.repairs import apply_repairs, load_repairs

    dirs = [d for d in _snapshot_dirs(SNAPSHOTS_ROOT) if d.name <= LAST_SNAPSHOT]
    if not dirs or dirs[-1].name != LAST_SNAPSHOT:
        pytest.skip(f"the sequence to {LAST_SNAPSHOT} is not under {SNAPSHOTS_ROOT}/")
    repairs = load_repairs()
    return gate_sequence([apply_repairs(load_checked(d), repairs)[0] for d in dirs]).snapshot


def _ruled(snapshot, curation):
    from graph7ph.build import reconcile

    _, pilots = reconcile(snapshot, curation, None)
    return pilots


def test_the_composed_rule_reproduces_the_dictionarys_labels_on_the_record(record):
    # AC (#273): the replay. With dictionary merges off, the composed rule (bar,
    # shared-event veto, hub guard) run over every pair the heuristics raise
    # merges no pair a human rejected or split and separates no pair a human
    # merged. In-sample by construction, since the bars were set on these same
    # labels; what is out of sample is the stranger base rate the ADR records.
    from graph7ph.curation import load_curation
    from itertools import combinations

    on_file = load_curation()
    groups: dict[str, set[str]] = {}
    for member, canonical in on_file.merges.items():
        groups.setdefault(canonical, {canonical}).add(member)
    merged = {frozenset(pair) for ids in groups.values() for pair in combinations(sorted(ids), 2)}
    apart = on_file.rejected | on_file.splits

    states = {frozenset(p.pilots): p.state
              for p in _ruled(record, Curation.empty()).report.ruled_pairs}

    assert [sorted(pair) for pair in apart if states.get(pair) == "merged"] == []
    assert [sorted(pair) for pair in merged if states.get(pair) == "separated"] == []
    # And the rule does real work on the labelled set rather than passing by
    # abstaining: the merges it reproduces, the figure ADR 0028 records. Derived
    # independently from the review's row dump (the composed rule applied to
    # its rows gives 43) plus DylB25 / Dylan B, which the dump's sampled pool
    # kept under the top percentile and the exhaustive pool admits at 162 of
    # 16,207.
    assert sum(1 for pair in merged if states.get(pair) == "merged") == 44


def test_the_pairs_issue_266_names_land_as_the_ticket_states_on_the_record(record):
    # AC (#273): under the dictionary on file, the nine pairs #266 names come out
    # merged except Darcy / Darcy E (a bare first name with two decks, watching),
    # the Toby M / TobM7 node is named Toby M, and the two pairs the shared event
    # decides come out separated.
    from graph7ph.curation import load_curation

    pilots = _ruled(record, load_curation())
    states = {frozenset(p.pilots): p for p in pilots.report.ruled_pairs}

    assert {ids: states[frozenset(ids)].state for ids in NAMED_ON_266} == NAMED_ON_266
    toby = states[frozenset(("LunarAzureGecko", "NimbleAmberOtter"))]
    assert [p.display_name for p in pilots.pilots if p.pilot == toby.canonical] == ["Toby M"]
    folded = next(pid for pid in toby.pilots if pid != toby.canonical)
    assert folded not in set(pilots.deck_pilot.values())
    assert folded not in {p.pilot for p in pilots.pilots}


def test_two_builds_on_the_record_derive_identical_states(record):
    # AC (#273): determinism. The pool is exhaustive and nothing reads wall-clock
    # or deck order, so the same snapshots give the same states twice over.
    from graph7ph.curation import load_curation

    first = _ruled(record, load_curation()).report
    second = _ruled(record, load_curation()).report

    assert first.ruled_pairs == second.ruled_pairs
    assert first.rule_overrides == second.rule_overrides


def test_the_report_carries_the_stranger_base_rate_at_each_bar_every_build():
    # The out-of-sample instrument (ADR 0028): the share of every matched
    # stranger pair, all gaps, scoring at or above each bar, re-measured by the
    # build rather than quoted from the page that first recorded it. Twenty
    # strangers sharing 20 of 70 cards with each other score 20/120 = 0.17
    # pairwise, so none reaches 0.4; two more sharing a full list add one pair
    # at 1.0 among the 231.
    decks, decklists = _strangers(22, top=True)

    rate = _resolve(decks, decklists).report.stranger_base_rate

    assert rate == {"0.4": 1 / 231, "0.5": 1 / 231, "0.8": 1 / 231, "0.9": 1 / 231, "1.0": 1 / 231}
    assert _resolve([], {}).report.stranger_base_rate == {b: 0.0 for b in rate}
