"""Seam A tests for pilot identity resolution (issue #4, ADR 0004).

These exercise the pure resolution functions on crafted deck fixtures: title
parsing, majority vote, fuzzy variant consolidation, null re-keying, and the
reconciliation report.
"""

from types import SimpleNamespace

import pytest

from graph7ph.curation import Curation, CurationError
from graph7ph.pilots import (
    display_name_from_title,
    name_relation,
    resolve_pilots,
)


def _deck(deck_id, pilot, title, event=None, placement=1, deck_name=None):
    """A minimal stand-in for a Deck: resolution reads these six fields.

    ``event`` defaults to the deck id so unrelated decks never collide on a
    shared event; collision tests pass an explicit shared event. ``deck_name``
    is the source's own parse of the deck, which name recovery subtracts from
    separator-less titles.
    """
    return SimpleNamespace(
        deck_id=deck_id, pilot=pilot, name=title,
        event=event or deck_id, placement=placement, deck_name=deck_name,
    )


def _pilot(resolution, pilot_id):
    return next(p for p in resolution.pilots if p.pilot == pilot_id)


@pytest.mark.parametrize(
    "title, expected",
    [
        # Placement token + name + deck + event: recover just the name.
        ("05th/08th Jordan C - Grixis - CFWAT25", "Jordan C"),
        ("13th Michael A - 8PT Izzet Wizards - CFWAT25", "Michael A"),
        # Unknown-placement token (??st) is still a placement token.
        ("??st Andrew V - Mox Jund - CFWAT25", "Andrew V"),
        # Hyphen-range and zero-padded tokens.
        ("05th-8th Graham K - 4C Nadu Walks - PoGTeams2024", "Graham K"),
        # Null-pilot decks carry no placement token at all.
        ("Darcy - Mono R - Area52IQ", "Darcy"),
        # En-dash separator between name and deck.
        ("James M – BUG Flash Hulk", "James M"),
        # No space around the separator.
        ("Graham K- 4C Nadu Walks", "Graham K"),
        # Placement then a separator before the name: "<placement> - <name> - ...".
        ("005th-8th - Kyle G - 8pt UB Tempo - TMC25", "Kyle G"),
        ("1st - Robert L - Pats Birthday Brawl", "Robert L"),
        # Alphabetic placement placeholder (unknown placement).
        ("XXth Jayden G - Storm - PogNov25", "Jayden G"),
        # Hyphenated names: an intra-name hyphen has no surrounding spaces, so it
        # must not be mistaken for the name/deck separator.
        ("032nd John-Paul K - Storm - ETEAE", "John-Paul K"),
        ("019th Chris K-H - Jund - CB5", "Chris K-H"),
        ("25th Xian-Zhi L - Storm - PogAug25", "Xian-Zhi L"),
        # A leading "*" still marks a placement, not a name.
        ("*12th Ciaran C - 8pt Boros Legends - WAC", "Ciaran C"),
        # A cut ("Top 8") is a placement too, however it is spaced.
        ("Top 8 Ben H - Jeskai Tempo - CanBrawl2", "Ben H"),
        ("Top 8  Benjamin S - Jund - CanBrawl2", "Benjamin S"),
        # No title.
        (None, None),
    ],
)
def test_display_name_from_title_strips_placement_and_takes_name(title, expected):
    assert display_name_from_title(title) == expected


@pytest.mark.parametrize(
    "title, deck_name, event, expected",
    [
        # Separator-less titles: subtract the source's own event and deck name
        # from the tail, and the pilot is what is left.
        ("1st Ben N Lurrus Breach PoGTeams2024", "Lurrus Breach", "PoGTeams2024", "Ben N"),
        ("5th-8th Jack D Grixis Oracle PoGTeams2024", "Grixis Oracle", "PoGTeams2024", "Jack D"),
        # The event's own name carries a hyphen, which is not a separator.
        ("1st Luke M Goblins PoGWinaDual07-01", "Goblins", "PoGWinaDual07-01", "Luke M"),
        # An empty pilot field leaves the deck sitting where the name belongs.
        # Subtracting the deck name exposes a points marker, which is never a
        # person, so the pilot is unknown rather than "8pt Blue Moon".
        ("121st  - 8pt Blue Moon - HighlanderWorlds26", "Blue Moon", "HighlanderWorlds26", None),
        # The source parsed the deck as the person's name (the title carries no
        # deck at all), so subtracting it would erase the name. Keep the name.
        ("5th-8th - Liam B - Pats Birthday Brawl", "Liam B", "Pats Birthday Brawl", "Liam B"),
        # A normal title is unaffected by having the extra context.
        ("05th/08th Jordan C - Grixis - CFWAT25", "Grixis", "CFWAT25", "Jordan C"),
        # A genuinely long name survives; it is not deck noise.
        ("24th Israel van der R - Rakdos Midrange - WAC", "Rakdos Midrange", "WAC",
         "Israel van der R"),
        # A separator already isolates the name segment, so a surname that
        # happens to equal the deck archetype ("Storm") must not be subtracted:
        # the name is "John Storm", not the bare "John" that would false-join to
        # any other John (F11).
        ("5th John Storm - Storm - E", "Storm", "E", "John Storm"),
    ],
)
def test_display_name_subtracts_source_deck_and_event(title, deck_name, event, expected):
    assert display_name_from_title(title, deck_name=deck_name, event=event) == expected


@pytest.mark.parametrize(
    "a, b, expected",
    [
        # Same name, different upstream ids: only the case differs.
        ("Nathan S", "Nathan s", "exact"),
        ("Michael B", "Michael B", "exact"),
        # A bare first name against the regular form.
        ("Noelle", "Noelle T", "first-name"),
        ("Angus", "Angus M", "first-name"),
        # A handle the title carried instead of a name: first three letters,
        # surname initial, and an optional per-registration number.
        ("OdeB", "Oden B", "handle"),
        ("CalT13", "Calum T", "handle"),
        ("JonB48", "Jonah B", "handle"),
        # A nickname or abbreviation of the same first name.
        ("Alex J", "Alexander J", "nickname"),
        ("Chris T", "Christopher T", "nickname"),
        ("Matt B", "Matthew B", "nickname"),
        # One slip of the fingers: a transposition, or a letter added/dropped.
        ("Alexadner J", "Alexander J", "typo"),
        ("Jodran M", "Jordan M", "typo"),
        ("Brnadon O", "Brandon O", "typo"),
        ("Tristian F", "Tristan F", "typo"),
        ("Daneil T", "Daniel T", "typo"),
        # A different surname is a different person, however close the first name.
        ("Jordan C", "Jordan B", None),
        # A hyphenated double-barrel initial reads the same as its unspaced form,
        # so the surname matches and the shared first name proposes them.
        ("Chris K-H", "Chris KH", "nickname"),
        # But a plain initial is not the same surname as a double-barrel one.
        ("Chris K", "Chris K-H", None),
        # Two edits apart is two people, not a typo: these names are too short
        # for the shape to tell drift from difference.
        ("Jake M", "Jack M", None),
        ("Ramona B", "Damon B", None),
        ("Daniel M", "Annie M", None),
        # An opaque handle relates to nothing; only the dictionary can place it.
        ("alejandrofp", "Alejandro D", None),
    ],
)
def test_name_relation_classifies_how_two_recovered_names_relate(a, b, expected):
    assert name_relation(a, b) == expected
    assert name_relation(b, a) == expected  # the relation is symmetric


def test_name_candidates_are_reported_but_never_merged_by_heuristics():
    # The heuristics only ever propose. "Noelle"/"Noelle T" and every "Dan"
    # pairing are surfaced as candidates, each tagged with its relation, but the
    # ids stay separate until the dictionary says otherwise (issue #9, wave 1).
    decks = [
        _deck("d1", "HiddenGreenPanda", "01st Noelle - Storm - CFWAT25"),
        _deck("d2", "FrostyIndigoStag", "02nd Noelle T - Storm - PogNov25"),
        _deck("d3", "MistyAzureHawk", "03rd Dan - Grixis - ETEAE"),
        _deck("d4", "BraveGreenOrca", "04th Dan P - Grixis - Area52IQ"),
    ]

    res = resolve_pilots(decks)

    # Every id kept its own pilot node: nothing was merged.
    assert {p.pilot for p in res.pilots} == {
        "HiddenGreenPanda", "FrostyIndigoStag", "MistyAzureHawk", "BraveGreenOrca"
    }
    # Every relation, "first-name" included, is only ever surfaced as a
    # candidate for a human -- none is auto-applied by the heuristics (F12).
    by_name = {u.display_name: u for u in res.report.under_merges}
    assert by_name["Noelle"].relation == "first-name"
    assert res.report.curated == 0


def test_display_name_tiebreak_is_deterministic_on_name_not_source_order():
    # Two equally-voted spellings under one id: the winner must be fixed by the
    # name string, so a re-export that reorders the decks cannot flip which
    # identity the id resolves to (F7).
    d1 = _deck("d1", "P", "1st Aaron B - Storm - E1", event="E1")
    d2 = _deck("d2", "P", "2nd Zoe B - Storm - E2", event="E2")

    forward = _pilot(resolve_pilots([d1, d2]), "P").display_name
    reverse = _pilot(resolve_pilots([d2, d1]), "P").display_name

    assert forward == reverse == "Zoe B"  # max name string, not deck order


def test_pilot_is_keyed_on_upstream_id_with_majority_display_name():
    # The recovered name drifts across a pilot's decks; the majority wins and
    # the node stays keyed on the stable upstream id, never the name (ADR 0004).
    decks = [
        _deck("d1", "SolarGreenPanda", "05th/08th Nick C - Izzet - CFWAT25"),
        _deck("d2", "SolarGreenPanda", "12th Nick C - Izzet - PogNov25"),
        _deck("d3", "SolarGreenPanda", "42ndh Nick C - Izzet - ETEAE"),
        _deck("d4", "SolarGreenPanda", "5th-8th Nick C Izzet PoGTeams2024"),  # noisy tail
    ]

    res = resolve_pilots(decks)

    pilot = _pilot(res, "SolarGreenPanda")
    assert pilot.display_name == "Nick C"
    assert pilot.low_confidence is False
    assert res.deck_pilot == {d.deck_id: "SolarGreenPanda" for d in decks}


def test_multi_name_id_is_reported_when_decks_span_two_surname_families():
    # One upstream id whose decks recover two different surname initials (H, M)
    # is likely two people reusing one id. The majority name wins the node, but
    # the minority must not vanish silently: the id surfaces for a human (F7).
    decks = [
        _deck("d1", "P", "1st Tom H - Storm - E1", event="E1"),
        _deck("d2", "P", "2nd Tom H - Storm - E2", event="E2"),
        _deck("d3", "P", "3rd Tom M - Lands - E3", event="E3"),
    ]

    res = resolve_pilots(decks)

    [entry] = res.report.multi_name_ids
    assert entry.pilot == "P"
    assert entry.display_name == "Tom H"  # majority still wins the node
    assert set(entry.names) == {"Tom H", "Tom M"}


def test_two_surnames_at_one_event_are_a_split_not_a_multi_name_id():
    # Two surnames confined to a single event are a same-event collision the
    # event split already separates into numbered people; only surnames recurring
    # across disjoint events point to one id reused by two people (F7).
    decks = [
        _deck("d1", "P", "1st Tom H - Storm - E1", event="E1", placement=1),
        _deck("d2", "P", "2nd Tom M - Lands - E1", event="E1", placement=2),
    ]

    res = resolve_pilots(decks)

    assert res.report.multi_name_ids == []
    assert len(res.report.event_splits) == 1  # separated as a collision instead


def test_single_surname_family_is_not_a_multi_name_id():
    # First-name spelling drift under one surname ("Dan S"/"Daniel S") is a
    # variant cluster, not two people: it must not be reported as a multi-name id.
    decks = [
        _deck("d1", "Q", "1st Dan S - Storm - E1", event="E1"),
        _deck("d2", "Q", "2nd Daniel S - Storm - E2", event="E2"),
    ]

    res = resolve_pilots(decks)

    assert res.report.multi_name_ids == []


def test_null_pilot_decks_rekeyed_per_name_not_collapsed():
    # The 26 nan-pilot decks must not collapse into one bogus node; each distinct
    # recovered name becomes its own low-confidence pilot (ADR 0004).
    decks = [
        _deck("d1", "nan", "Darcy - Mono R - Area52IQ"),
        _deck("d2", "nan", "Jed - Oath Reanimator - Area52IQ"),
        _deck("d3", "nan", "Darcy - Burn - DeckaDiceIQ"),  # same name, same pilot
    ]

    res = resolve_pilots(decks)

    # Two synthetic pilots (Darcy, Jed), never one collapsed "nan" node.
    assert all(p.pilot != "nan" for p in res.pilots)
    assert {p.display_name for p in res.pilots} == {"Darcy", "Jed"}
    assert all(p.low_confidence for p in res.pilots)
    # Both Darcy decks land on the same synthetic pilot; Jed on its own.
    assert res.deck_pilot["d1"] == res.deck_pilot["d3"] != res.deck_pilot["d2"]


@pytest.mark.parametrize("sentinel", ["", "None", "N/A", "null", "NaN", "none"])
def test_null_sentinels_beyond_literal_nan_are_rekeyed_not_collapsed(sentinel):
    # The lost-pilot sentinel is a serializer artifact, not just literal "nan":
    # ''/'None'/'N/A'/'null' (any case) are pilotless too. Two such decks at
    # distinct events must re-key per recovered name, not collapse into one node
    # keyed on the sentinel (the ADR-0004 collapse; F8).
    decks = [
        _deck("d1", sentinel, "Darcy - Mono R - E1", event="E1"),
        _deck("d2", sentinel, "Jed - Oath - E2", event="E2"),
    ]

    res = resolve_pilots(decks)

    assert all(p.pilot != sentinel for p in res.pilots)
    assert {p.display_name for p in res.pilots} == {"Darcy", "Jed"}
    assert all(p.low_confidence for p in res.pilots)
    assert res.deck_pilot["d1"] != res.deck_pilot["d2"]


def test_untitled_null_decks_stay_separate_not_collapsed():
    # Null decks whose title yields no name must not collapse into one bogus
    # "unknown" node; each stays its own low-confidence pilot.
    decks = [_deck("d1", "nan", None), _deck("d2", "nan", None)]

    res = resolve_pilots(decks)

    assert res.deck_pilot["d1"] != res.deck_pilot["d2"]
    assert len(res.report.null_pilots) == 2


def test_fuzzy_spelling_variants_consolidate_and_are_reported():
    # "Dan S" and "Daniel S" are one person; they consolidate to the majority
    # spelling, and the merge is surfaced as a variant cluster for review.
    decks = [
        _deck("d1", "Daniel S", "01st Dan S - Storm - CFWAT25"),
        _deck("d2", "Daniel S", "05th Dan S - Storm - PogNov25"),
        _deck("d3", "Daniel S", "12th Dan S - Storm - ETEAE"),
        _deck("d4", "Daniel S", "21st Daniel S - Storm - Area52IQ"),
    ]

    res = resolve_pilots(decks)

    assert _pilot(res, "Daniel S").display_name == "Dan S"
    cluster = next(c for c in res.report.variant_clusters if c.pilot == "Daniel S")
    assert cluster.display_name == "Dan S"
    assert cluster.variants == {"Dan S": 3, "Daniel S": 1}


def test_identical_display_names_join_into_one_person():
    # Display name is the primary player identity (ADR 0007): two ids that
    # recover the same name are the same person and are joined onto the busier
    # id, carrying every deck. Logged, and no longer an under-merge candidate.
    decks = [
        _deck("d1", "BraveCyanWolf", "01st Tom M - Lands - CFWAT25"),
        _deck("d2", "BraveCyanWolf", "05th Tom M - Lands - PogNov25"),
        _deck("d3", "Tom M", "12th Tom M - Storm - ETEAE"),
    ]

    res = resolve_pilots(decks)

    assert {p.pilot for p in res.pilots} == {"BraveCyanWolf"}
    assert {res.deck_pilot[d] for d in ("d1", "d2", "d3")} == {"BraveCyanWolf"}
    assert res.report.under_merges == []
    joined = res.report.joined_names
    assert len(joined) == 1
    assert joined[0].canonical == "BraveCyanWolf"
    assert joined[0].display_name == "Tom M"
    assert joined[0].merged == ["BraveCyanWolf", "Tom M"]


def test_null_bucket_is_reported_and_excluded_from_under_merges():
    # A null-derived name that collides with a real pilot must not inflate the
    # under-merge list (the null bucket is surfaced on its own instead).
    decks = [
        _deck("d1", "AmberRedGecko", "01st Kyle G - Jund - CFWAT25"),
        _deck("d2", "nan", "Kyle G - Burn - Area52IQ"),
    ]

    res = resolve_pilots(decks)

    assert res.report.under_merges == []
    assert [p.display_name for p in res.report.null_pilots] == ["Kyle G"]
    assert all(p.low_confidence for p in res.report.null_pilots)


def test_null_orphan_joins_into_the_real_pilot_of_that_name():
    # A registration whose upstream id the source lost lands in the null bucket
    # under its recovered name. It shares that name with a real pilot, so it is
    # the same person: the orphan deck joins onto the real (canonical) id.
    decks = [
        _deck("d1", "AmberRedGecko", "01st Kyle G - Jund - CFWAT25"),
        _deck("d2", "nan", "05th Kyle G - Burn - Area52IQ"),
    ]

    res = resolve_pilots(decks)

    assert {p.pilot for p in res.pilots} == {"AmberRedGecko"}
    assert res.deck_pilot["d2"] == "AmberRedGecko"
    joined = next(j for j in res.report.joined_names if j.display_name == "Kyle G")
    assert joined.canonical == "AmberRedGecko"


def test_same_name_ids_colliding_at_one_event_are_reseparated():
    # Joining is unconditional on name, but the one-deck-per-event invariant is
    # not lost: two same-name ids that both entered one event join, then the
    # event split deals them back into numbered people for that event.
    decks = [
        _deck("d1", "BraveCyanWolf", "10th Tom M - Lands - NHC26", event="NHC26", placement=10),
        _deck("d2", "Tom M", "20th Tom M - Storm - NHC26", event="NHC26", placement=20),
    ]

    res = resolve_pilots(decks)

    assert res.deck_pilot["d1"] != res.deck_pilot["d2"]
    assert {p.display_name for p in res.pilots} == {"Tom M 1", "Tom M 2"}


def test_same_event_duplicates_split_into_numbered_people():
    # Three decks under one id at one event are three people sharing a name, not
    # one person with three lists. They split into numbered identities.
    decks = [
        _deck("d1", "Daniel S", "14th Dan S - Dimir - NHC26", event="NHC26", placement=14),
        _deck("d2", "Daniel S", "87th Dan S - Jeskai - NHC26", event="NHC26", placement=87),
        _deck("d3", "Daniel S", "154th Dan S - Breach - NHC26", event="NHC26", placement=154),
    ]

    res = resolve_pilots(decks)

    # Three distinct pilots, one deck each, numbered by placement order.
    assert {p.display_name for p in res.pilots} == {"Dan S 1", "Dan S 2", "Dan S 3"}
    assert len({res.deck_pilot[d.deck_id] for d in decks}) == 3
    assert all(p.low_confidence for p in res.pilots)
    split = next(s for s in res.report.event_splits if s.display_name == "Dan S")
    assert split.people == ["Dan S 1", "Dan S 2", "Dan S 3"]


def test_split_keeps_one_per_event_record_and_spins_off_only_extras():
    # A pilot with one deck at most events and a single two-deck collision keeps
    # a full one-per-event record on identity 1; only the extra deck spins off.
    decks = [
        _deck("a", "Dan S", "05th Dan S - Storm - E1", event="E1", placement=5),
        _deck("b", "Dan S", "10th Dan S - Storm - E2", event="E2", placement=10),
        _deck("c", "Dan S", "20th Dan S - Storm - E2", event="E2", placement=20),
    ]

    res = resolve_pilots(decks)

    # Identity 1 keeps E1 and the better-placed E2 deck; identity 2 gets the rest.
    p1, p2 = res.deck_pilot["a"], res.deck_pilot["c"]
    assert res.deck_pilot["a"] == res.deck_pilot["b"] != res.deck_pilot["c"]
    display = {p.pilot: p.display_name for p in res.pilots}
    assert display[p1] == "Dan S 1"
    assert display[p2] == "Dan S 2"


def test_no_collision_leaves_the_pilot_untouched():
    # One deck per event is the norm; nothing is split and no name is renumbered.
    decks = [
        _deck("d1", "Nick C", "05th Nick C - Izzet - CFWAT25", event="CFWAT25"),
        _deck("d2", "Nick C", "12th Nick C - Izzet - PogNov25", event="PogNov25"),
    ]

    res = resolve_pilots(decks)

    assert [p.display_name for p in res.pilots] == ["Nick C"]
    assert res.report.event_splits == []


# --- Append-stable threading of a split id's decks into careers (issue #34) ---


_STORM = frozenset({"grapeshot", "past in flames", "manamorphose"})
_LANDS = frozenset({"crop rotation", "gitrog monster", "valakut"})


def _lists(**by_deck):
    """Deck signatures in the build's (main, side) shape, side empty."""
    return {deck_id: (cards, frozenset()) for deck_id, cards in by_deck.items()}


def test_split_id_is_threaded_into_careers_by_deck_similarity():
    # One id fields two decks at each of two events: two people sharing an id.
    # Placement-rank dealing (ADR 0004) crosses the archetypes into incoherent
    # careers because the ranks invert between events; threading by card overlap
    # keeps each person's like decks on one thread (issue #34).
    decks = [
        _deck("d1", "P", "1st P - Storm - E1", event="E1", placement=1),
        _deck("d2", "P", "2nd P - Lands - E1", event="E1", placement=2),
        _deck("d3", "P", "2nd P - Storm - E2", event="E2", placement=2),
        _deck("d4", "P", "1st P - Lands - E2", event="E2", placement=1),
    ]
    decklists = _lists(d1=_STORM, d2=_LANDS, d3=_STORM, d4=_LANDS)

    res = resolve_pilots(decks, decklists=decklists)

    # The two Storm decks land on one career, the two Lands decks on the other.
    assert res.deck_pilot["d1"] == res.deck_pilot["d3"]
    assert res.deck_pilot["d2"] == res.deck_pilot["d4"]
    assert res.deck_pilot["d1"] != res.deck_pilot["d2"]
    assert {p.display_name for p in res.pilots} == {"P 1", "P 2"}


def test_threading_is_append_stable_and_order_independent():
    # A career is numbered by its earliest deck, so re-ingesting the same decks
    # plus a later one keeps every prior deck on its thread and files the
    # newcomer on the career it resembles, without renumbering (issue #34).
    lists = _lists(d1=_STORM, d2=_LANDS, d3=_STORM, d4=_LANDS)
    before = [
        _deck("d1", "P", "1st P - Storm - E1", event="E1"),
        _deck("d2", "P", "2nd P - Lands - E1", event="E1"),
        _deck("d3", "P", "1st P - Storm - E2", event="E2"),
        _deck("d4", "P", "2nd P - Lands - E2", event="E2"),
    ]
    first = resolve_pilots(before, decklists=lists)

    # A later Storm deck at a new event; deck ids grow with registration order.
    after_decks = before + [_deck("d5", "P", "3rd P - Storm - E3", event="E3")]
    after_lists = {**lists, "d5": (_STORM, frozenset())}
    after = resolve_pilots(after_decks, decklists=after_lists)

    prior = ("d1", "d2", "d3", "d4")
    assert {d: after.deck_pilot[d] for d in prior} == {d: first.deck_pilot[d] for d in prior}
    assert after.deck_pilot["d5"] == after.deck_pilot["d1"]  # joined the Storm career

    # The same decks in any order thread into the same careers.
    shuffled = resolve_pilots(list(reversed(after_decks)), decklists=after_lists)
    assert shuffled.deck_pilot == after.deck_pilot


def test_a_late_deck_at_an_existing_event_does_not_displace_an_incumbent():
    # A deck backfilled onto an event that already split must not bump a deck
    # already threaded there onto another career: the incumbent chose first, so
    # the newcomer takes what is left (issue #34, the drift threading must not
    # merely relocate from placement rank to card overlap).
    mixed = frozenset({"grapeshot", "past in flames", "crop rotation"})  # closer to Storm
    before = [
        _deck("d1", "P", "1st P - Storm - E1", event="E1"),
        _deck("d2", "P", "2nd P - Lands - E1", event="E1"),
        _deck("d3", "P", "1st P - Mixed - E2", event="E2"),  # sole E2 deck -> Storm career
    ]
    lists = _lists(d1=_STORM, d2=_LANDS, d3=mixed)
    first = resolve_pilots(before, decklists=lists)

    # A larger-id Storm deck lands on E2, overlapping the Storm career more than d3.
    after_decks = before + [_deck("d4", "P", "3rd P - Storm - E2", event="E2")]
    after = resolve_pilots(after_decks, decklists={**lists, "d4": (_STORM, frozenset())})

    assert after.deck_pilot["d3"] == first.deck_pilot["d3"]  # incumbent stayed put
    assert after.deck_pilot["d4"] != after.deck_pilot["d3"]  # newcomer took the free career


def test_seeding_collision_gives_the_accumulated_career_to_the_best_fitting_deck():
    # At the event that first splits an id, the colliding deck that matches the
    # pilot's existing history keeps that career even if it has the LARGER deck
    # id; the odd deck out seeds the new career. Oldest-first alone would hand
    # the accumulated career to whichever colliding deck sorts first by id,
    # stranding the real continuation on a fresh career (issue #34 audit).
    decks = [
        _deck("d1", "P", "1st P - Storm - E1", event="E1"),  # history, oldest id
        _deck("d2", "P", "2nd P - Storm - E2", event="E2"),  # more history
        # collision at E3: d3 (smaller id) is the odd deck; d4 (larger id) matches history
        _deck("d3", "P", "1st P - Lands - E3", event="E3"),
        _deck("d4", "P", "2nd P - Storm - E3", event="E3"),
    ]
    lists = _lists(d1=_STORM, d2=_STORM, d3=_LANDS, d4=_STORM)

    res = resolve_pilots(decks, decklists=lists)

    # d4 (Storm) joins the accumulated Storm career; d3 (Lands) is the new person.
    assert res.deck_pilot["d4"] == res.deck_pilot["d1"] == res.deck_pilot["d2"]
    assert res.deck_pilot["d3"] != res.deck_pilot["d1"]


# --- Curation: the human-decision dictionary the build applies (issue #9) ---


def test_curated_merge_collapses_ids_carrying_every_deck():
    # Two upstream ids a human confirmed are one person. They collapse onto the
    # chosen canonical id, and every deck of both follows, none dropped.
    decks = [
        _deck("d1", "ShinyCrimsonHeron", "01st Alexander J - Jeskai - E1", event="E1"),
        _deck("d2", "AmberTealOrca", "02nd Alexadner J - Jeskai - E2", event="E2"),
        _deck("d3", "AmberTealOrca", "03rd Alexadner J - Jeskai - E3", event="E3"),
    ]
    curation = Curation(
        merges={"AmberTealOrca": "ShinyCrimsonHeron"},
        rejected=frozenset(), names={}, deck_pilots={},
    )

    res = resolve_pilots(decks, curation)

    assert [p.pilot for p in res.pilots] == ["ShinyCrimsonHeron"]
    assert set(res.deck_pilot.values()) == {"ShinyCrimsonHeron"}  # all three decks
    # The merged pair no longer appears as a candidate to decide again.
    assert res.report.under_merges == []
    assert res.report.curated == 1  # the confirmed merge counts as a decision made


def test_split_keeps_two_same_name_ids_apart(tmp_path):
    # Two different people both recover "James L" at different events: the
    # identical-name join (ADR 0007) would fuse them into one node. A [[split]]
    # naming the two ids overrides the join, keeping them two people. Logged.
    decks = [
        _deck("gr1", "GrixisJamesId", "01st James L - Grixis - E1", event="E1"),
        _deck("wa1", "WalksJamesId", "02nd James L - Walks - E2", event="E2"),
    ]
    splits = frozenset({frozenset({"GrixisJamesId", "WalksJamesId"})})
    curation = Curation(
        merges={}, rejected=frozenset(), names={}, deck_pilots={}, splits=splits,
    )

    res = resolve_pilots(decks, curation)

    assert {p.pilot for p in res.pilots} == {"GrixisJamesId", "WalksJamesId"}
    assert res.deck_pilot["gr1"] != res.deck_pilot["wa1"]
    # Both kept their own real id, so neither is a low-confidence synthetic node.
    assert not any(p.low_confidence for p in res.pilots)
    assert res.report.joined_names == []  # nothing fused
    split = next(s for s in res.report.name_splits if s.display_name == "James L")
    assert sorted(split.people) == ["GrixisJamesId", "WalksJamesId"]
    # Without the split, the same two ids fuse into one node.
    fused = resolve_pilots(decks)
    assert len({p.pilot for p in fused.pilots}) == 1


def test_split_leaves_a_same_name_null_orphan_as_its_own_node():
    # Splitting two real "James L" ids while a null-bucket orphan recovers the
    # same name must not abort: the orphan's synthetic key cannot be named in a
    # split (ADR 0009), and it cannot be attributed to either real James L, so it
    # stays its own low-confidence node rather than transitively re-fusing them.
    decks = [
        _deck("gr1", "GrixisJamesId", "01st James L - Grixis - E1", event="E1"),
        _deck("wa1", "WalksJamesId", "02nd James L - Walks - E2", event="E2"),
        _deck("or1", "nan", "03rd James L - Storm - E3", event="E3"),  # id lost
    ]
    splits = frozenset({frozenset({"GrixisJamesId", "WalksJamesId"})})
    curation = Curation(
        merges={}, rejected=frozenset(), names={}, deck_pilots={}, splits=splits,
    )

    res = resolve_pilots(decks, curation)

    assert {"GrixisJamesId", "WalksJamesId"} <= {p.pilot for p in res.pilots}
    # The orphan attached to neither real James L and stayed low confidence.
    assert res.deck_pilot["or1"] not in ("GrixisJamesId", "WalksJamesId")
    orphan = _pilot(res, res.deck_pilot["or1"])
    assert orphan.low_confidence


def test_under_specified_split_in_a_trio_raises():
    # Three ids all recover "James L". Splitting only A from B leaves C unsplit,
    # so C rejoins both and transitively re-fuses A with B. That is an
    # under-specified split: raise it loudly rather than silently re-fusing.
    decks = [
        _deck("A", "A", "01st James L - Grixis - E1", event="E1"),
        _deck("B", "B", "02nd James L - Walks - E2", event="E2"),
        _deck("C", "C", "03rd James L - Storm - E3", event="E3"),
    ]
    splits = frozenset({frozenset({"A", "B"})})
    curation = Curation(
        merges={}, rejected=frozenset(), names={}, deck_pilots={}, splits=splits,
    )

    with pytest.raises(CurationError):
        resolve_pilots(decks, curation)


def test_split_a_trio_three_ways():
    # Splitting all three pairs keeps every "James L" apart as its own person.
    decks = [
        _deck("A", "A", "01st James L - Grixis - E1", event="E1"),
        _deck("B", "B", "02nd James L - Walks - E2", event="E2"),
        _deck("C", "C", "03rd James L - Storm - E3", event="E3"),
    ]
    splits = frozenset({
        frozenset({"A", "B"}), frozenset({"A", "C"}), frozenset({"B", "C"}),
    })
    curation = Curation(
        merges={}, rejected=frozenset(), names={}, deck_pilots={}, splits=splits,
    )

    res = resolve_pilots(decks, curation)

    assert {p.pilot for p in res.pilots} == {"A", "B", "C"}


def test_pinned_display_name_beats_the_majority_vote():
    # The vote would pick the opaque handle "alejandrofp"; the dictionary pins
    # the real name against it.
    decks = [_deck("d1", "CleverBlueWhale", "91st alejandrofp - Blue Moon - E")]
    curation = Curation(
        merges={}, rejected=frozenset(),
        names={"CleverBlueWhale": "Alejandro D"}, deck_pilots={},
    )

    res = resolve_pilots(decks, curation)

    assert _pilot(res, "CleverBlueWhale").display_name == "Alejandro D"


def test_rejected_candidate_stays_suppressed_across_a_rebuild():
    # "Joe M" and "Joel M" look like one person by shape but are two people. Once
    # rejected, the pair never returns to the review list.
    decks = [
        _deck("d1", "NimbleBlackEagle", "01st Joe M - Grixis - E1", event="E1"),
        _deck("d2", "FrostyBlueOtter", "02nd Joel M - Grixis - E2", event="E2"),
    ]
    rejected = frozenset({frozenset({"NimbleBlackEagle", "FrostyBlueOtter"})})
    curation = Curation(merges={}, rejected=rejected, names={}, deck_pilots={})

    res = resolve_pilots(decks, curation)

    assert res.report.under_merges == []       # suppressed
    assert res.report.curated == 1             # and counted, so the list shrinks
    # Without the rejection it would be a live candidate.
    assert resolve_pilots(decks).report.under_merges != []


def test_null_deck_resolved_to_a_real_pilot_via_override():
    # A null-pilot deck a human traced to its real owner is reassigned before the
    # name vote, so it strengthens that pilot rather than minting a lone node.
    decks = [
        _deck("d1", "LuckyTealLynx", "01st Brennan C - Lurrus Breach - E1", event="E1"),
        _deck("d2", "nan", "5th-8th - Brennan C - Pats Birthday Brawl",
              event="Pats", deck_name="Brennan C"),
    ]
    curation = Curation(
        merges={}, rejected=frozenset(), names={},
        deck_pilots={"d2": "LuckyTealLynx"},
    )

    res = resolve_pilots(decks, curation)

    assert res.deck_pilot["d2"] == "LuckyTealLynx"
    assert all(p.pilot != "nan" and not p.pilot.startswith("nan:") for p in res.pilots)
    assert res.report.null_pilots == []


def test_identical_registration_is_dropped_and_logged():
    # Same id, event, name, and card-for-card list: one entry submitted twice.
    # The best placement is kept; the drop is recorded, never silent.
    decks = [
        _deck("d1", "BravePurpleFalcon", "132nd Christopher K - Bots - NHC26",
              event="NHC26", placement=132),
        _deck("d2", "BravePurpleFalcon", "133rd Christopher K - Bots - NHC26",
              event="NHC26", placement=133),
    ]
    decklists = {"d1": ("bots",), "d2": ("bots",)}  # identical signatures

    res = resolve_pilots(decks, decklists=decklists)

    assert res.dropped_decks == frozenset({"d2"})     # worse placement dropped
    assert res.deck_pilot == {"d1": "BravePurpleFalcon"}
    [dup] = res.report.dropped_duplicates
    assert (dup.dropped_deck, dup.kept_deck, dup.display_name) == ("d2", "d1", "Christopher K")


def test_duplicate_that_unifies_only_after_a_merge_is_dropped_not_split():
    # Two ids a human merged are one person. Their decks at one event carry an
    # identical list: one registration entered under two ids, not two people.
    # Keyed on the merged (canonical) id, the copy is dropped, never split into
    # numbered phantoms on an un-curable "#2" key (F4).
    decks = [
        _deck("d1", "A", "10th Tom H - Storm - E", event="E", placement=10),
        _deck("d2", "B", "20th Tom H - Storm - E", event="E", placement=20),
    ]
    curation = Curation(merges={"B": "A"}, rejected=frozenset(), names={}, deck_pilots={})
    decklists = {"d1": ("storm",), "d2": ("storm",)}

    res = resolve_pilots(decks, curation, decklists)

    assert res.dropped_decks == frozenset({"d2"})     # the copy, worse placement
    assert {p.display_name for p in res.pilots} == {"Tom H"}  # not "Tom H 1"/"Tom H 2"
    assert res.report.event_splits == []
    [dup] = res.report.dropped_duplicates
    assert dup.dropped_deck == "d2" and dup.kept_deck == "d1"


def test_duplicate_that_unifies_only_after_the_null_join_is_dropped_not_split():
    # A lost-id copy sits in the null bucket, sharing a recovered name and an
    # identical list with a real pilot at one event. Dedup ran before the join,
    # so it survived; the event split must collapse the card-identical pair
    # rather than number them into two phantoms (F4).
    decks = [
        _deck("d1", "AmberRedGecko", "10th Kyle G - Burn - E", event="E", placement=10),
        _deck("d2", "nan", "20th Kyle G - Burn - E", event="E", placement=20),
    ]
    decklists = {"d1": ("burn",), "d2": ("burn",)}

    res = resolve_pilots(decks, decklists=decklists)

    assert res.dropped_decks == frozenset({"d2"})
    assert {p.display_name for p in res.pilots} == {"Kyle G"}  # one person, not two
    assert res.report.event_splits == []
    [dup] = res.report.dropped_duplicates
    assert dup.dropped_deck == "d2" and dup.kept_deck == "d1"


def test_two_people_under_one_id_sharing_a_list_at_one_event_are_kept_not_collapsed():
    # Two distinct people share one upstream id and an identical list at one event
    # (a shared account or a data error). The collapse is for copies unified from
    # DIFFERENT ids by a merge or the null-join, not for a same-id collision:
    # teammates share a list but never a name, so both survive and the event split
    # numbers them (F4 guard).
    decks = [
        _deck("d1", "R", "10th Alice A - Storm - E", event="E", placement=10),
        _deck("d2", "R", "20th Bob B - Storm - E", event="E", placement=20),
    ]
    decklists = {"d1": ("storm",), "d2": ("storm",)}

    res = resolve_pilots(decks, decklists=decklists)

    assert res.dropped_decks == frozenset()  # neither dropped
    assert len({res.deck_pilot["d1"], res.deck_pilot["d2"]}) == 2  # split into two people


def test_teammates_sharing_a_list_are_not_treated_as_duplicates():
    # Same event and identical decklist but different names: two teammates who
    # played the same list, not one duplicated entry. Both are kept.
    decks = [
        _deck("d1", "nan", "3rd/4th - Brennan C - Pats", event="Pats", deck_name="Brennan C"),
        _deck("d2", "nan", "5th-8th - Cody K - Pats", event="Pats", deck_name="Cody K"),
    ]
    decklists = {"d1": ("same",), "d2": ("same",)}

    res = resolve_pilots(decks, decklists=decklists)

    assert res.dropped_decks == frozenset()
    assert res.report.dropped_duplicates == []


def _build_snapshot(tmp_path, decks):
    """Write a minimal, buildable snapshot (one shared card) for the given decks."""
    import json

    (tmp_path / "decks.json").write_text(json.dumps(decks))
    (tmp_path / "cards_index.json").write_text(json.dumps({
        "v": 2,
        "cards": [{"canon": "island", "name": "Island", "type": "Lands",
                   "manaCost": None, "manaValue": 0.0, "reserved": False,
                   "priceUsd": 0.5, "points": 0}],
        "decks": {d["deckId"]: {"m": [0], "s": []} for d in decks},
    }))


def _raw_deck(deck_id, pilot, title):
    return {
        "deckId": deck_id, "name": title, "deckName": "Grixis",
        "pilot": pilot, "event": "E",
        "eventId": "evt_1", "eventType": "Tournament", "placement": 1,
        "placementNorm": 0.0, "createdAt": "2025-06-01T00:00:00+00:00",
        "colour": "colour:U", "macro": "macro:tempo",
        "engineTags": [], "engineTagLabels": {}, "primaryTag": "",
        "primaryTagWeights": {},
    }


def test_build_pilot_nodes_carry_display_name_and_rekey_nulls(tmp_path):
    import json

    import ladybug

    from graph7ph.build import build_graph, reconciliation_path
    from graph7ph.db import database_path
    from graph7ph.models import load_snapshot

    _build_snapshot(tmp_path, [
        _raw_deck("d1", "SolarGreenPanda", "05th/08th Nick C - Izzet - CFWAT25"),
        _raw_deck("d2", "nan", "Darcy - Mono R - Area52IQ"),
    ])
    db_path = tmp_path / "graph"

    counts = build_graph(load_snapshot(tmp_path), db_path)
    conn = ladybug.Connection(ladybug.Database(str(database_path(db_path))))

    # Real pilot keyed on the upstream id, carrying the recovered display name.
    assert counts.pilots == 2
    row = conn.execute(
        "MATCH (p:Pilot {pilot: 'SolarGreenPanda'}) RETURN p.displayName, p.lowConfidence"
    ).get_next()
    assert row == ["Nick C", False]

    # The null-pilot deck is re-keyed to its own low-confidence per-name pilot,
    # never a collapsed "nan" node.
    assert conn.execute("MATCH (p:Pilot {pilot: 'nan'}) RETURN count(p)").get_next()[0] == 0
    null = conn.execute(
        "MATCH (:Deck {deckId: 'd2'})-[:PILOTED_BY]->(p:Pilot) "
        "RETURN p.displayName, p.lowConfidence"
    ).get_next()
    assert null == ["Darcy", True]

    # The reconciliation report is emitted for human review, including the
    # re-keyed null bucket.
    report = json.loads(reconciliation_path(db_path).read_text())
    assert {"variant_clusters", "under_merges", "null_pilots"} <= report.keys()
    assert [p["display_name"] for p in report["null_pilots"]] == ["Darcy"]


def test_dead_curation_entry_is_reported_not_fatal():
    # A merge keyed on an id that appears in no deck (a typo, or an
    # upstream-reissued pseudonym) fires nothing. It must surface in the report's
    # dead_entries, never break the build (issue #37).
    decks = [_deck("d1", "P1", "1st Alice A - Storm - E1")]
    curation = Curation(
        merges={"ghost": "P1"}, rejected=frozenset(),
        names={}, deck_pilots={}, deck_archetypes={},
    )
    res = resolve_pilots(decks, curation)

    assert [p.display_name for p in res.pilots] == ["Alice A"]  # build still succeeds
    assert any(d.kind == "merge" and d.key == "ghost"
               for d in res.report.dead_entries)


def test_name_pin_on_live_canonical_not_reported_dead():
    # B merges into A, and only B carries decks, so A never appears as a raw
    # pilot id. The pin on A still fires (A is the resolved bucket), so it must
    # not be reported dead: dead-detection resolves through the merges (#37).
    decks = [_deck("d1", "B", "1st Alice A - Storm - E1")]
    curation = Curation(
        merges={"B": "A"}, rejected=frozenset(),
        names={"A": "Alice A"}, deck_pilots={}, deck_archetypes={},
    )
    res = resolve_pilots(decks, curation)

    assert next(p for p in res.pilots).display_name == "Alice A"  # pin fired
    assert res.report.dead_entries == []
