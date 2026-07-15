import json
from collections import Counter, defaultdict

import kuzu

from graph7ph.build import build_graph
from graph7ph.models import load_snapshot
from graph7ph.query import (
    ArchetypeUniqueCards,
    CardCooccurrence,
    HiddenGems,
    PilotNeighbourhood,
    archetype_unique_cards_subgraph,
    card_cooccurrence_subgraph,
    card_usage_subgraph,
    hidden_gems_subgraph,
    pilot_affinity_subgraph,
    pilot_subgraph,
    run_query,
)

# The fixture's three decks, by pilot display name and archetype (see conftest).
JORDAN_DECKS = {"BsegXnsDsEWxh-vNbUrn0w", "pkUbzmgN3UeqaWdYQYRgRg"}  # Jordan C, Grixis
STORM_DECK = "bLaqow87tE2TCnphLvH1lg"  # Tom S, Storm


def _connect(tmp_path, snapshot_dir):
    db_path = tmp_path / "graph.kuzu"
    build_graph(load_snapshot(snapshot_dir), db_path)
    return kuzu.Connection(kuzu.Database(str(db_path)))


def _expected_cards_for(snapshot_dir, deck_ids):
    """Independent oracle: distinct canons across the given decks, straight
    from the raw index rather than through the graph."""
    index = json.loads((snapshot_dir / "cards_index.json").read_text())
    canon = [c["canon"] for c in index["cards"]]
    cards = set()
    for did in deck_ids:
        for board in ("m", "s"):
            cards.update(canon[i] for i in index["decks"][did][board])
    return cards


def _decks_by_card(snapshot_dir):
    """Independent oracle: for each canon, the set of deck ids running it."""
    index = json.loads((snapshot_dir / "cards_index.json").read_text())
    canon = [c["canon"] for c in index["cards"]]
    decks = defaultdict(set)
    for deck_id, boards in index["decks"].items():
        for board in ("m", "s"):
            for i in boards[board]:
                decks[canon[i]].add(deck_id)
    return decks


def _grixis_only(snapshot_dir):
    """Cards in both of Jordan's Grixis lists and not the Storm list."""
    return _expected_cards_for(snapshot_dir, JORDAN_DECKS) - _expected_cards_for(
        snapshot_dir, {STORM_DECK}
    )


def _same_board_cooccurrence(snapshot_dir, seed):
    """Independent oracle: for each other canon, the count of decks where it sits
    in the same board (Main or Side) as ``seed``. Cross-board pairs don't count."""
    index = json.loads((snapshot_dir / "cards_index.json").read_text())
    canon = [c["canon"] for c in index["cards"]]
    counts = Counter()
    for boards in index["decks"].values():
        partners = set()
        for key in ("m", "s"):
            cards = {canon[i] for i in boards[key]}
            if seed in cards:
                partners |= cards - {seed}
        for other in partners:
            counts[other] += 1
    return counts


def _write_snapshot(tmp_path, decks, canons):
    """Write a minimal hand-authored snapshot for a focused test.

    ``decks`` is a list of dicts: ``id``, ``tag`` (archetype), ``norm``
    (placementNorm), and ``m`` / ``s`` (main / side canon lists). ``canons`` is
    the card catalogue by name.
    """
    idx = {c: i for i, c in enumerate(canons)}
    (tmp_path / "decks.json").write_text(json.dumps([
        {
            "deckId": d["id"], "name": d["id"], "deckName": d["id"],
            "pilot": "p", "event": "E",
            "eventId": f"evt_{d['id']}", "eventType": "Tournament", "placement": 1,
            "placementNorm": d["norm"], "colour": "colour:U", "macro": "macro:control",
            "engineTags": [f"engine:{d['tag']}"],
            "engineTagLabels": {f"engine:{d['tag']}": d["tag"].title()},
            "primaryTag": f"engine:{d['tag']}", "primaryTagWeights": {f"engine:{d['tag']}": 100},
        }
        for d in decks
    ]))
    (tmp_path / "cards_index.json").write_text(json.dumps({
        "v": 2,
        "cards": [
            {"canon": c, "name": c.title(), "type": "Instants", "manaCost": "{U}",
             "manaValue": 1.0, "reserved": False, "priceUsd": 0.0, "points": 0}
            for c in canons
        ],
        "decks": {
            d["id"]: {"m": [idx[c] for c in d.get("m", [])],
                      "s": [idx[c] for c in d.get("s", [])]}
            for d in decks
        },
    }))


def test_pilot_subgraph_chains_events_decks_and_placements_not_cards(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)

    sub = pilot_subgraph(conn, "Jordan C")

    kinds = {n.kind: [] for n in sub.nodes}
    for n in sub.nodes:
        kinds[n.kind].append(n)

    # pilot -> event -> deck -> placement, no cards. The deck reads as its own
    # name ("Grixis"), free of the placement and pilot in the full title.
    assert [n.label for n in kinds["Pilot"]] == ["Jordan C"]
    assert {n.label for n in kinds["Event"]} == {"CFWAT25", "PogNov25"}
    assert [n.label for n in kinds["Deck"]] == ["Grixis", "Grixis"]
    assert {n.label for n in kinds["Placement"]} == {"5th", "12th"}
    assert "Card" not in kinds


def test_pilot_subgraph_edges_form_the_pilot_event_deck_placement_chain(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)

    sub = pilot_subgraph(conn, "Jordan C")

    by_label = {lbl: [e for e in sub.edges if e.label == lbl]
                for lbl in ("PLAYED_AT", "ENTERED", "PLACED")}
    kind = {n.id: n.kind for n in sub.nodes}

    # One link at each hop of the chain per deck, and each hop joins the right kinds.
    assert len(by_label["PLAYED_AT"]) == 2  # pilot -> event
    assert len(by_label["ENTERED"]) == 2    # event -> deck
    assert len(by_label["PLACED"]) == 2     # deck -> placement
    for e in by_label["PLAYED_AT"]:
        assert kind[e.source] == "Pilot" and kind[e.target] == "Event"
    for e in by_label["ENTERED"]:
        assert kind[e.source] == "Event" and kind[e.target] == "Deck"
    for e in by_label["PLACED"]:
        assert kind[e.source] == "Deck" and kind[e.target] == "Placement"
    assert not any(e.label.startswith("CONTAINS") for e in sub.edges)


def test_pilot_subgraph_labels_the_pilot_by_display_name(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)

    # Keyed on the upstream id (AmberTealViper), the pilot node reads as the
    # recovered display name (Tom S), never the pseudonym.
    sub = pilot_subgraph(conn, "AmberTealViper")

    assert [n.label for n in sub.nodes if n.kind == "Pilot"] == ["Tom S"]
    assert [n.id for n in sub.nodes if n.kind == "Pilot"] == ["pilot:AmberTealViper"]


def test_unknown_pilot_yields_empty_subgraph(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)

    sub = pilot_subgraph(conn, "Nobody At All")

    assert sub.nodes == []
    assert sub.edges == []


def test_card_usage_lists_every_deck_and_pilot_running_the_card(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)
    decks_by_card = _decks_by_card(snapshot_dir)
    # A card played in all three decks, i.e. across both pilots.
    canon = next(c for c, ds in decks_by_card.items() if len(ds) == 3)

    sub = card_usage_subgraph(conn, canon)

    by_kind = defaultdict(list)
    for n in sub.nodes:
        by_kind[n.kind].append(n)

    assert {n.id for n in by_kind["Card"]} == {f"card:{canon}"}
    assert {n.id for n in by_kind["Deck"]} == {f"deck:{d}" for d in decks_by_card[canon]}
    # Pilots are labelled by display name, not the upstream key (AmberTealViper).
    assert {n.label for n in by_kind["Pilot"]} == {"Jordan C", "Tom S"}

    # Every deck edges to the card it runs and up to its pilot.
    contains = [e for e in sub.edges if e.label.startswith("CONTAINS")]
    piloted = [e for e in sub.edges if e.label == "PILOTED_BY"]
    assert len(contains) == 3
    assert len(piloted) == 3
    node_ids = {n.id for n in sub.nodes}
    for e in sub.edges:
        assert e.source in node_ids and e.target in node_ids


def test_card_usage_of_a_grixis_only_card_reaches_one_pilot(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)
    decks_by_card = _decks_by_card(snapshot_dir)
    # A card only in Jordan's two identical Grixis lists.
    canon = next(c for c, ds in decks_by_card.items() if ds == JORDAN_DECKS)

    sub = card_usage_subgraph(conn, canon)

    decks = {n.id for n in sub.nodes if n.kind == "Deck"}
    pilots = {n.label for n in sub.nodes if n.kind == "Pilot"}
    assert decks == {f"deck:{d}" for d in JORDAN_DECKS}
    assert pilots == {"Jordan C"}


def test_card_usage_of_unknown_card_is_empty(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)

    sub = card_usage_subgraph(conn, "not a real card")

    assert sub.nodes == []
    assert sub.edges == []


def test_cooccurrence_counts_only_same_board_pairings(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)
    decks_by_card = _decks_by_card(snapshot_dir)
    # A card in Jordan's two (identical) Grixis lists and no storm list.
    canon = next(c for c, ds in decks_by_card.items() if ds == JORDAN_DECKS)

    sub = card_cooccurrence_subgraph(conn, canon, min_shared=2)

    # Neighbours and their counts match the same-board oracle: a card in the
    # seed's board across both Jordan lists, never a cross-board partner.
    oracle = _same_board_cooccurrence(snapshot_dir, canon)
    expected = {c for c, n in oracle.items() if n >= 2}
    assert expected  # the seed does have same-board partners in the fixture
    neighbours = {n.id for n in sub.nodes if n.kind == "Card"} - {f"card:{canon}"}
    assert neighbours == {f"card:{c}" for c in expected}
    assert all(e.source == f"card:{canon}" for e in sub.edges)
    for e in sub.edges:
        assert e.label == f"COOCCURS:{oracle[e.target.removeprefix('card:')]}"


def test_cooccurrence_ignores_main_versus_side_pairings(tmp_path):
    # One deck: A and B in the main, C in the side. A pairs with B (same board)
    # but not C, whose only overlap with A is main-versus-side.
    _write_snapshot(
        tmp_path,
        [{"id": "d1", "tag": "x", "norm": 0.0, "m": ["a", "b"], "s": ["c"]}],
        ["a", "b", "c"],
    )
    conn = _connect(tmp_path, tmp_path)

    sub = card_cooccurrence_subgraph(conn, "a", min_shared=1)

    neighbours = {n.label for n in sub.nodes if n.kind == "Card"} - {"A"}
    assert neighbours == {"B"}


def test_cooccurrence_threshold_above_max_shared_yields_only_the_card(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)
    decks_by_card = _decks_by_card(snapshot_dir)
    canon = next(c for c, ds in decks_by_card.items() if ds == JORDAN_DECKS)

    # The card is in only 2 decks, so nothing shares 3 with it.
    sub = card_cooccurrence_subgraph(conn, canon, min_shared=3)

    assert {n.id for n in sub.nodes} == {f"card:{canon}"}
    assert sub.edges == []


def test_unique_cards_are_only_found_in_that_archetype(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)

    # min_decks=1 because the fixture only has two decks per archetype.
    sub = archetype_unique_cards_subgraph(conn, "grixis", min_decks=1)

    # Unique to Grixis = in both Grixis lists and nowhere else. The 29 cards also
    # in the Storm list appear outside Grixis, so they are excluded.
    grixis_only = _grixis_only(snapshot_dir)
    cards = {n.id for n in sub.nodes if n.kind == "Card"}
    assert cards == {f"card:{c}" for c in grixis_only}

    archetypes = [n for n in sub.nodes if n.kind == "Archetype"]
    assert [n.label for n in archetypes] == ["Grixis"]
    # Every edge runs from the archetype to a card unique to it.
    assert all(e.source == "arch:grixis" for e in sub.edges)
    assert {e.target for e in sub.edges} == cards


def test_unique_cards_exclude_cards_shared_with_other_archetypes(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)

    sub = archetype_unique_cards_subgraph(conn, "storm", min_decks=1)

    # Storm has one list; the cards unique to it are those not also in Grixis.
    storm_only = _expected_cards_for(snapshot_dir, {STORM_DECK}) - _expected_cards_for(
        snapshot_dir, JORDAN_DECKS
    )
    cards = {n.id for n in sub.nodes if n.kind == "Card"}
    assert cards == {f"card:{c}" for c in storm_only}


def test_unique_cards_need_support_from_enough_decks(tmp_path):
    # Archetype x runs `core` in three decks and `fringe` in one; `shared` is in
    # an x deck and a y deck. A support floor keeps core, drops the one-off
    # fringe as noise, and shared is never unique because it appears outside x.
    _write_snapshot(
        tmp_path,
        [
            {"id": "x1", "tag": "x", "norm": 0.0, "m": ["core", "fringe", "shared"]},
            {"id": "x2", "tag": "x", "norm": 0.0, "m": ["core", "filler1"]},
            {"id": "x3", "tag": "x", "norm": 0.0, "m": ["core", "filler2"]},
            {"id": "y1", "tag": "y", "norm": 0.0, "m": ["shared", "other"]},
        ],
        ["core", "fringe", "shared", "filler1", "filler2", "other"],
    )
    conn = _connect(tmp_path, tmp_path)

    strict = archetype_unique_cards_subgraph(conn, "x", min_decks=3)
    assert {n.id for n in strict.nodes if n.kind == "Card"} == {"card:core"}

    loose = archetype_unique_cards_subgraph(conn, "x", min_decks=1)
    loose_cards = {n.id for n in loose.nodes if n.kind == "Card"}
    assert "card:fringe" in loose_cards  # admitted once the floor drops
    assert "card:shared" not in loose_cards  # still not unique: also in a y deck


def test_hidden_gems_are_rare_cards_that_place_highly(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)

    # In at most 2 decks (min_decks=1 for this small fixture); high-placing =
    # mean placementNorm <= 0.35.
    sub = hidden_gems_subgraph(conn, min_decks=1, max_decks=2, max_norm=0.35)

    # Only Jordan's Grixis-only cards qualify: in 2 decks placing 5th and 12th
    # (mean norm 0.29). The 29 cards in all 3 decks are not rare; the Storm-only
    # cards are rare but placed 21st (norm 0.67), so neither survives.
    grixis_only = _grixis_only(snapshot_dir)
    cards = {n.id for n in sub.nodes if n.kind == "Card"}
    decks = {n.id for n in sub.nodes if n.kind == "Deck"}
    assert cards == {f"card:{c}" for c in grixis_only}
    assert decks == {f"deck:{d}" for d in JORDAN_DECKS}
    # Each gem edges from the decks that run it.
    node_ids = {n.id for n in sub.nodes}
    assert all(e.label.startswith("CONTAINS") for e in sub.edges)
    for e in sub.edges:
        assert e.source in node_ids and e.target in node_ids


def test_hidden_gems_colour_filter_narrows_to_that_colour(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)

    # Green appears only in the Storm deck (UBRG); the Grixis-only gems are in
    # UBR decks, so none of them survive a Green filter.
    sub = hidden_gems_subgraph(conn, min_decks=1, max_decks=2, max_norm=0.35, colour="G")

    assert sub.nodes == []
    assert sub.edges == []


def test_hidden_gems_archetype_filter_scopes_rarity_to_that_archetype(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)

    # Rarity is measured within the slice. Unfiltered, the 29 cards in all three
    # decks are too common (3 decks) to be gems; but within the two Grixis decks
    # they appear in only 2, so scoping to Grixis admits every Grixis card, not
    # just the 46 Grixis-only ones. That widening is the proof rarity is scoped.
    grixis = hidden_gems_subgraph(
        conn, min_decks=1, max_decks=2, max_norm=0.35, archetype="grixis"
    )
    storm = hidden_gems_subgraph(
        conn, min_decks=1, max_decks=2, max_norm=0.35, archetype="storm"
    )

    grixis_cards = _expected_cards_for(snapshot_dir, JORDAN_DECKS)
    assert {n.id for n in grixis.nodes if n.kind == "Card"} == {
        f"card:{c}" for c in grixis_cards
    }
    # Storm's one deck placed 21st (norm 0.67), above the 0.35 bar: no gems.
    assert storm.nodes == []


def test_hidden_gems_surface_a_common_card_that_is_rare_within_an_archetype(tmp_path):
    # `common` is in both Storm decks and one Lands deck; the Lands deck that
    # runs it placed first, so its role there differs from its Storm staple role.
    _write_snapshot(
        tmp_path,
        [
            {"id": "s1", "tag": "storm", "norm": 0.0, "m": ["common", "storm_only"]},
            {"id": "s2", "tag": "storm", "norm": 0.1, "m": ["common", "storm_only"]},
            {"id": "l1", "tag": "lands", "norm": 0.0, "m": ["common", "fringe"]},
            {"id": "l2", "tag": "lands", "norm": 0.9, "m": ["fringe", "chaff"]},
        ],
        ["common", "storm_only", "fringe", "chaff"],
    )
    conn = _connect(tmp_path, tmp_path)

    # `common` is in 3 decks overall, so globally it is never rare.
    globally = hidden_gems_subgraph(conn, min_decks=1, max_decks=1, max_norm=0.2)
    assert "card:common" not in {n.id for n in globally.nodes}

    # But within Lands it is in just one deck, which placed first: a gem there.
    within_lands = hidden_gems_subgraph(
        conn, min_decks=1, max_decks=1, max_norm=0.2, archetype="lands"
    )
    assert {n.id for n in within_lands.nodes if n.kind == "Card"} == {"card:common"}


def test_hidden_gems_floor_separates_lucky_draws_from_real_gems(tmp_path):
    # `steady` overperforms across five decks; `lucky` only across two. A floor
    # of three keeps steady and rejects lucky; dropping the floor admits both.
    decks = [
        {"id": f"s{i}", "tag": "x", "norm": 0.1, "m": ["steady", f"pad{i}"]}
        for i in range(5)
    ] + [
        {"id": f"l{i}", "tag": "x", "norm": 0.1, "m": ["lucky", f"pod{i}"]}
        for i in range(2)
    ]
    canons = ["steady", "lucky"] + [f"pad{i}" for i in range(5)] + [f"pod{i}" for i in range(2)]
    _write_snapshot(tmp_path, decks, canons)
    conn = _connect(tmp_path, tmp_path)

    trusted = hidden_gems_subgraph(conn, min_decks=3, max_decks=100, max_norm=0.2)
    assert {n.id for n in trusted.nodes if n.kind == "Card"} == {"card:steady"}

    loose = hidden_gems_subgraph(conn, min_decks=1, max_decks=100, max_norm=0.2)
    loose_cards = {n.id for n in loose.nodes if n.kind == "Card"}
    assert {"card:steady", "card:lucky"} <= loose_cards

    # Once steady spreads past the rarity ceiling it stops being a hidden gem.
    common = hidden_gems_subgraph(conn, min_decks=3, max_decks=4, max_norm=0.2)
    assert "card:steady" not in {n.id for n in common.nodes}


def test_hidden_gems_ignore_decks_with_unknown_placement(tmp_path):
    # `gem` is in two ranked decks (placed well) and three with no placement.
    # Unranked decks can't confirm overperformance, so they count for neither
    # bound: gem is a two-deck gem, not a five-deck one.
    decks = [
        {"id": "r1", "tag": "x", "norm": 0.1, "m": ["gem", "p1"]},
        {"id": "r2", "tag": "x", "norm": 0.1, "m": ["gem", "p2"]},
        {"id": "u1", "tag": "x", "norm": None, "m": ["gem", "p3"]},
        {"id": "u2", "tag": "x", "norm": None, "m": ["gem", "p4"]},
        {"id": "u3", "tag": "x", "norm": None, "m": ["gem", "p5"]},
    ]
    _write_snapshot(tmp_path, decks, ["gem", "p1", "p2", "p3", "p4", "p5"])
    conn = _connect(tmp_path, tmp_path)

    # Counted over ranked decks only, gem sits in a [1, 2] band, and only its
    # two ranked decks appear (the three unranked ones are not shown).
    band = hidden_gems_subgraph(conn, min_decks=1, max_decks=2, max_norm=0.2)
    assert "card:gem" in {n.id for n in band.nodes if n.kind == "Card"}
    gem_decks = {e.source for e in band.edges if e.target == "card:gem"}
    assert gem_decks == {"deck:r1", "deck:r2"}

    # A floor of three is not satisfied by the three unranked decks.
    trusted = hidden_gems_subgraph(conn, min_decks=3, max_decks=10, max_norm=0.2)
    assert "card:gem" not in {n.id for n in trusted.nodes}


def test_pilot_affinity_weights_archetypes_by_event_count(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)

    # Jordan is a Grixis specialist: both his decks carry the one archetype, and
    # they were played at two different events (CFWAT25, PogNov25).
    sub = pilot_affinity_subgraph(conn, "Jordan C")

    pilots = [n for n in sub.nodes if n.kind == "Pilot"]
    archetypes = [n for n in sub.nodes if n.kind == "Archetype"]
    assert [n.label for n in pilots] == ["Jordan C"]
    assert [n.label for n in archetypes] == ["Grixis"]
    # The archetype node carries its event count as a weight, and the edge label
    # shows the same count.
    assert [n.weight for n in archetypes] == [2]
    assert [(e.source, e.target, e.label) for e in sub.edges] == [
        ("pilot:Jordan C", "arch:grixis", "PLAYS:2")
    ]


def test_pilot_affinity_counts_distinct_events_not_decks(tmp_path):
    # One pilot, three decks of one archetype, all registered at the same event.
    # The affinity is one event, not three decks: it measures showing up, not
    # how many variants were entered on the day.
    _write_snapshot(
        tmp_path,
        [
            {"id": "d1", "tag": "x", "norm": 0.0, "m": ["a"]},
            {"id": "d2", "tag": "x", "norm": 0.0, "m": ["a"]},
            {"id": "d3", "tag": "x", "norm": 0.0, "m": ["a"]},
        ],
        ["a"],
    )
    conn = _connect(tmp_path, tmp_path)

    sub = pilot_affinity_subgraph(conn, "p")

    arch = [n for n in sub.nodes if n.kind == "Archetype"]
    assert [n.weight for n in arch] == [1]
    assert [e.label for e in sub.edges] == ["PLAYS:1"]


def test_pilot_affinity_uses_display_name_and_counts_one_deck(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)

    # Keyed on the upstream id, labelled by display name (Tom S).
    sub = pilot_affinity_subgraph(conn, "AmberTealViper")

    assert [n.label for n in sub.nodes if n.kind == "Pilot"] == ["Tom S"]
    assert [(e.target, e.label) for e in sub.edges] == [("arch:storm", "PLAYS:1")]


def test_pilot_affinity_of_unknown_pilot_is_empty(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)

    sub = pilot_affinity_subgraph(conn, "Nobody At All")

    assert sub.nodes == []
    assert sub.edges == []


def test_run_query_dispatches_a_spec_to_its_query(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)

    # The single spec -> subgraph seam routes to the right query function; the
    # oracle is the function called directly with the spec's parameters.
    assert run_query(conn, PilotNeighbourhood("Jordan C")) == pilot_subgraph(
        conn, "Jordan C"
    )


def test_run_query_passes_spec_parameters_through(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)
    decks_by_card = _decks_by_card(snapshot_dir)
    canon = next(c for c, ds in decks_by_card.items() if ds == JORDAN_DECKS)

    assert run_query(conn, CardCooccurrence(canon, min_shared=3)) == (
        card_cooccurrence_subgraph(conn, canon, min_shared=3)
    )
    assert run_query(
        conn, HiddenGems(min_decks=1, max_decks=2, max_norm=0.35, colour="G")
    ) == hidden_gems_subgraph(conn, min_decks=1, max_decks=2, max_norm=0.35, colour="G")
    assert run_query(conn, ArchetypeUniqueCards("grixis", min_decks=1)) == (
        archetype_unique_cards_subgraph(conn, "grixis", min_decks=1)
    )
