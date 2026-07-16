"""Seam A tests for pilot identity resolution (issue #4, ADR 0004).

These exercise the pure resolution functions on crafted deck fixtures: title
parsing, majority vote, fuzzy variant consolidation, null re-keying, and the
reconciliation report.
"""

from types import SimpleNamespace

import pytest

from graph7ph.pilots import display_name_from_title, resolve_pilots


def _deck(deck_id, pilot, title, event=None, placement=1):
    """A minimal stand-in for a Deck: resolution reads these five fields.

    ``event`` defaults to the deck id so unrelated decks never collide on a
    shared event; collision tests pass an explicit shared event.
    """
    return SimpleNamespace(
        deck_id=deck_id, pilot=pilot, name=title,
        event=event or deck_id, placement=placement,
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
        # No title.
        (None, None),
    ],
)
def test_display_name_from_title_strips_placement_and_takes_name(title, expected):
    assert display_name_from_title(title) == expected


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


def test_shared_display_name_across_ids_flagged_as_under_merge():
    # One display name under two distinct upstream ids: a candidate under-merge
    # the data cannot resolve on its own (ADR 0004). Never merged automatically.
    decks = [
        _deck("d1", "BraveCyanWolf", "01st Tom M - Lands - CFWAT25"),
        _deck("d2", "BraveCyanWolf", "05th Tom M - Lands - PogNov25"),
        _deck("d3", "Tom M", "12th Tom M - Storm - ETEAE"),
    ]

    res = resolve_pilots(decks)

    # Both pilots survive as separate nodes.
    assert {p.pilot for p in res.pilots} == {"BraveCyanWolf", "Tom M"}
    under = res.report.under_merges
    assert len(under) == 1
    assert under[0].display_name == "Tom M"
    assert set(under[0].pilots) == {"BraveCyanWolf", "Tom M"}


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

    import kuzu

    from graph7ph.build import build_graph, reconciliation_path
    from graph7ph.models import load_snapshot

    _build_snapshot(tmp_path, [
        _raw_deck("d1", "SolarGreenPanda", "05th/08th Nick C - Izzet - CFWAT25"),
        _raw_deck("d2", "nan", "Darcy - Mono R - Area52IQ"),
    ])
    db_path = tmp_path / "graph.kuzu"

    counts = build_graph(load_snapshot(tmp_path), db_path)
    conn = kuzu.Connection(kuzu.Database(str(db_path)))

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
