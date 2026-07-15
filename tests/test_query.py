import json
from collections import Counter, defaultdict

import kuzu
import pytest

from graph7ph.build import build_graph
from graph7ph.models import load_snapshot
from graph7ph.query import (
    MAX_GEM_SHARE,
    MIN_GEM_DECKS,
    MIN_GEM_SLICE,
    CardCooccurrence,
    HiddenGems,
    PilotNeighbourhood,
    SliceTooSmall,
    card_cooccurrence_subgraph,
    card_usage_subgraph,
    gem_archetypes,
    hidden_gems_subgraph,
    pilot_affinity_subgraph,
    pilot_subgraph,
    run_query,
)

# The fixture's three decks, by pilot display name and archetype (see conftest).
JORDAN_DECKS = {"BsegXnsDsEWxh-vNbUrn0w", "pkUbzmgN3UeqaWdYQYRgRg"}  # Jordan C, Grixis


def _connect(tmp_path, snapshot_dir):
    db_path = tmp_path / "graph.kuzu"
    build_graph(load_snapshot(snapshot_dir), db_path)
    return kuzu.Connection(kuzu.Database(str(db_path)))


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


def _write_snapshot(tmp_path, decks, canons, lands=frozenset()):
    """Write a minimal hand-authored snapshot for a focused test.

    ``decks`` is a list of dicts: ``id``, ``tag`` (archetype), ``norm``
    (placementNorm), and ``m`` / ``s`` (main / side canon lists). Optional
    ``macro`` and ``event`` keys override the defaults (``control`` / ``E``).
    ``canons`` is the card catalogue by name; ``lands`` names those typed as
    ``Lands`` (the rest default to ``Instants``).
    """
    idx = {c: i for i, c in enumerate(canons)}
    (tmp_path / "decks.json").write_text(json.dumps([
        {
            # Title reads "<pilot> - <deck>" so the recovered display name is the
            # deck's ``pilot`` key, keeping hand-authored pilots distinct.
            "deckId": d["id"], "name": f"{d.get('pilot', 'p')} - {d['id']}",
            "deckName": d["id"],
            "pilot": d.get("pilot", "p"), "event": d.get("event", "E"),
            "eventId": f"evt_{d['id']}", "eventType": "Tournament", "placement": 1,
            "placementNorm": d["norm"], "colour": "colour:U",
            "macro": f"macro:{d.get('macro', 'control')}",
            "engineTags": [f"engine:{d['tag']}"],
            "engineTagLabels": {f"engine:{d['tag']}": d["tag"].title()},
            "primaryTag": f"engine:{d['tag']}", "primaryTagWeights": {f"engine:{d['tag']}": 100},
        }
        for d in decks
    ]))
    (tmp_path / "cards_index.json").write_text(json.dumps({
        "v": 2,
        "cards": [
            {"canon": c, "name": c.title(),
             "type": "Lands" if c in lands else "Instants", "manaCost": "{U}",
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


def test_pilot_head_to_head_shares_the_event_both_played(tmp_path):
    # Two pilots met at one event (E): each ran their own deck there. The event
    # is the head-to-head hinge, one shared node both pilots reach, with each
    # pilot's own deck hanging under it.
    _write_snapshot(
        tmp_path,
        [
            {"id": "d1", "tag": "x", "norm": 0.0, "pilot": "p1", "event": "E", "m": ["a"]},
            {"id": "d2", "tag": "y", "norm": 0.0, "pilot": "p2", "event": "E", "m": ["a"]},
        ],
        ["a"],
    )
    conn = _connect(tmp_path, tmp_path)

    sub = pilot_subgraph(conn, "p1", "p2")

    events = [n for n in sub.nodes if n.kind == "Event"]
    assert [n.id for n in events] == ["event:E"]  # one shared event, not two
    # Both pilots reach it, and both their decks hang off it.
    assert ("pilot:p1", "event:E", "PLAYED_AT") in [
        (e.source, e.target, e.label) for e in sub.edges
    ]
    assert ("pilot:p2", "event:E", "PLAYED_AT") in [
        (e.source, e.target, e.label) for e in sub.edges
    ]
    assert {n.id for n in sub.nodes if n.kind == "Deck"} == {"deck:d1", "deck:d2"}
    assert ("event:E", "deck:d1", "ENTERED") in [
        (e.source, e.target, e.label) for e in sub.edges
    ]
    assert ("event:E", "deck:d2", "ENTERED") in [
        (e.source, e.target, e.label) for e in sub.edges
    ]


def test_pilot_head_to_head_tags_each_node_with_its_player(tmp_path):
    # So the render can colour each player's chain: every node a player owns
    # carries that player's id as its group, and the shared event they both
    # played stays ungrouped (neutral).
    _write_snapshot(
        tmp_path,
        [
            {"id": "d1", "tag": "x", "norm": 0.0, "pilot": "p1", "event": "E", "m": ["a"]},
            {"id": "d2", "tag": "y", "norm": 0.0, "pilot": "p2", "event": "E", "m": ["a"]},
        ],
        ["a"],
    )
    conn = _connect(tmp_path, tmp_path)

    sub = pilot_subgraph(conn, "p1", "p2")

    group = {n.id: n.group for n in sub.nodes}
    assert group["pilot:p1"] == "pilot:p1"
    assert group["pilot:p2"] == "pilot:p2"
    assert group["deck:d1"] == "pilot:p1"
    assert group["deck:d2"] == "pilot:p2"
    assert group["placement:d1"] == "pilot:p1"
    assert group["event:E"] is None  # both played it, so it belongs to neither


def test_pilot_single_neighbourhood_has_no_player_groups(tmp_path, snapshot_dir):
    # One pilot has no second player to contrast, so every node stays ungrouped
    # (coloured by kind, not by player).
    conn = _connect(tmp_path, snapshot_dir)

    sub = pilot_subgraph(conn, "Jordan C")

    assert all(n.group is None for n in sub.nodes)


def test_pilot_head_to_head_drops_events_only_one_pilot_played(tmp_path):
    # The head-to-head is the overlap: an event only one pilot played is not a
    # meeting, so it and its deck are dropped. Only the shared event survives.
    _write_snapshot(
        tmp_path,
        [
            {"id": "d1", "tag": "x", "norm": 0.0, "pilot": "p1", "event": "solo1", "m": ["a"]},
            {"id": "d2", "tag": "x", "norm": 0.0, "pilot": "p1", "event": "shared", "m": ["a"]},
            {"id": "d3", "tag": "y", "norm": 0.0, "pilot": "p2", "event": "solo2", "m": ["a"]},
            {"id": "d4", "tag": "y", "norm": 0.0, "pilot": "p2", "event": "shared", "m": ["a"]},
        ],
        ["a"],
    )
    conn = _connect(tmp_path, tmp_path)

    sub = pilot_subgraph(conn, "p1", "p2")

    assert {n.id for n in sub.nodes if n.kind == "Event"} == {"event:shared"}
    decks = {n.id for n in sub.nodes if n.kind == "Deck"}
    assert decks == {"deck:d2", "deck:d4"}  # the solo-event decks are gone
    # Both pilots still reach the one shared event.
    played = {(e.source, e.target) for e in sub.edges if e.label == "PLAYED_AT"}
    assert played == {("pilot:p1", "event:shared"), ("pilot:p2", "event:shared")}


def test_pilot_head_to_head_falls_back_to_one_pilot_when_second_is_empty(tmp_path, snapshot_dir):
    # An empty second pilot is the ordinary neighbourhood: the two-arg call with
    # no second pilot matches the plain single-pilot call exactly.
    conn = _connect(tmp_path, snapshot_dir)

    assert pilot_subgraph(conn, "Jordan C", "") == pilot_subgraph(conn, "Jordan C")


def test_card_usage_reads_adoption_rate_at_each_tier(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)
    decks_by_card = _decks_by_card(snapshot_dir)
    # A card in all three decks: it is 100% adopted everywhere it can be, so every
    # tier reads 100% and the card headlines 100% of the meta.
    canon = next(c for c, ds in decks_by_card.items() if len(ds) == 3)

    sub = card_usage_subgraph(conn, canon)

    # Each tier is a named node; the adoption percent rides the edge that reaches
    # it (card -%-> macro -%-> archetype).
    card = next(n for n in sub.nodes if n.kind == "Card")
    assert card.label.endswith("(100% of meta)")
    assert {n.label for n in sub.nodes if n.kind in ("Macro", "Archetype")} == {"tempo", "combo", "Grixis", "Storm"}
    # Everything here is 100% adopted, so every edge reads "100%".
    assert {e.label for e in sub.edges} == {"100%"}
    assert all(e.visible for e in sub.edges)
    # Every node is a default dot: a uniform size with its name beside it, never a
    # circle sized to fit the text.
    assert all(n.shape is None for n in sub.nodes)
    node_ids = {n.id for n in sub.nodes}
    for e in sub.edges:
        assert e.source in node_ids and e.target in node_ids


def test_card_usage_adoption_falls_when_a_card_is_only_in_some_decks(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)
    # A card in both of Jordan's Grixis lists (tempo) but not Tom's Storm: 100%
    # adopted in Grixis, absent from Storm, so Storm/combo never appear.
    decks_by_card = _decks_by_card(snapshot_dir)
    canon = next(c for c, ds in decks_by_card.items() if ds == JORDAN_DECKS)

    sub = card_usage_subgraph(conn, canon)

    # Only Grixis/tempo appear; each is a named node, the percent rides its edge.
    assert {n.label for n in sub.nodes if n.kind in ("Macro", "Archetype")} == {"tempo", "Grixis"}
    assert {e.label for e in sub.edges} == {"100%"}


def test_card_usage_board_scopes_which_decks_count(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)
    # cori-steel cutter sits in the main of Jordan's two tempo Grixis decks and
    # the side of Tom's combo Storm deck, so the board selects which archetype
    # shows: main -> Grixis only, side -> Storm only, unset -> both.
    canon = "cori-steel cutter"

    def archetypes(board):
        sub = card_usage_subgraph(conn, canon, board)
        return {n.label for n in sub.nodes if n.kind == "Archetype"}

    # Adoption is 100% wherever it appears; the board decides where that is.
    assert archetypes(None) == {"Grixis", "Storm"}
    assert archetypes("Main") == {"Grixis"}
    assert archetypes("Side") == {"Storm"}


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

    sub = card_cooccurrence_subgraph(conn, canon, top_n=200)

    # Neighbours match the same-board oracle: every card in the seed's board
    # across a Jordan list, never a cross-board partner. Each edge is labelled
    # with the co-occurrence rate: the percent of the seed's decks that run it.
    oracle = _same_board_cooccurrence(snapshot_dir, canon)
    seed_decks = len(decks_by_card[canon])
    assert oracle  # the seed does have same-board partners in the fixture
    neighbours = {n.id for n in sub.nodes if n.kind == "Card"} - {f"card:{canon}"}
    assert neighbours == {f"card:{c}" for c in oracle}
    assert all(e.source == f"card:{canon}" for e in sub.edges)
    for e in sub.edges:
        shared = oracle[e.target.removeprefix("card:")]
        assert e.label == f"{round(100 * shared / seed_decks)}%"


def test_cooccurrence_ignores_main_versus_side_pairings(tmp_path):
    # One deck: A and B in the main, C in the side. A pairs with B (same board)
    # but not C, whose only overlap with A is main-versus-side.
    _write_snapshot(
        tmp_path,
        [{"id": "d1", "tag": "x", "norm": 0.0, "m": ["a", "b"], "s": ["c"]}],
        ["a", "b", "c"],
    )
    conn = _connect(tmp_path, tmp_path)

    sub = card_cooccurrence_subgraph(conn, "a")

    neighbours = {n.label for n in sub.nodes if n.kind == "Card"} - {"A"}
    assert neighbours == {"B"}


def test_cooccurrence_keeps_the_top_n_by_rate_and_labels_the_percent(tmp_path):
    # Seed "a" runs in four decks; partners co-occur at descending rates.
    _write_snapshot(
        tmp_path,
        [
            {"id": "d1", "tag": "x", "norm": 0.0, "m": ["a", "b", "c", "d", "e"]},
            {"id": "d2", "tag": "x", "norm": 0.0, "m": ["a", "b", "c", "d"]},
            {"id": "d3", "tag": "x", "norm": 0.0, "m": ["a", "b", "c"]},
            {"id": "d4", "tag": "x", "norm": 0.0, "m": ["a", "b"]},
        ],
        ["a", "b", "c", "d", "e"],
    )
    conn = _connect(tmp_path, tmp_path)

    # Top 2 by rate keeps only the two strongest partners (b at 100%, c at 75%),
    # each labelled with the percent of the seed's four decks that run it.
    sub = card_cooccurrence_subgraph(conn, "a", top_n=2)
    labels = {e.target.removeprefix("card:"): e.label for e in sub.edges}
    assert labels == {"b": "100%", "c": "75%"}

    # A wider limit keeps the rest, still labelled by rate; a present-but-tiny
    # partner reads its true low rate rather than being dropped.
    full = card_cooccurrence_subgraph(conn, "a", top_n=50)
    labels = {e.target.removeprefix("card:"): e.label for e in full.edges}
    assert labels == {"b": "100%", "c": "75%", "d": "50%", "e": "25%"}


def _cooccur_fixture(tmp_path):
    # Only d1 runs both a and b, and x with them; y sits in an a-only deck, z in a
    # b-only deck, so neither is shared by both. Every card is in the main board.
    _write_snapshot(
        tmp_path,
        [
            {"id": "d1", "tag": "x", "norm": 0.0, "m": ["a", "b", "x"]},
            {"id": "d2", "tag": "x", "norm": 0.0, "m": ["a", "y"]},
            {"id": "d3", "tag": "x", "norm": 0.0, "m": ["b", "z"]},
        ],
        ["a", "b", "x", "y", "z"],
    )
    return _connect(tmp_path, tmp_path)


def test_cooccurrence_two_seeds_hang_shared_cards_off_an_intersection_hub(tmp_path):
    conn = _cooccur_fixture(tmp_path)

    sub = card_cooccurrence_subgraph(conn, "a", "b", top_n=50)
    hub = next(n for n in sub.nodes if n.kind == "Intersection")
    edges = {(e.source, e.target): e.label for e in sub.edges}

    # Only x lives in a deck running both a and b, so only x is kept, hung off the
    # intersection hub with its double rate (100% of the one shared deck). Each
    # seed links to the hub with the fraction of its decks in the intersection
    # (50% of a's two decks, 50% of b's). y and z are dropped.
    assert edges == {
        ("card:a", hub.id): "50%",
        ("card:b", hub.id): "50%",
        (hub.id, "card:x"): "100%",
    }
    assert hub.label == "Both · 1 decks"


def test_cooccurrence_second_seed_is_a_target_not_a_partner_node(tmp_path):
    conn = _cooccur_fixture(tmp_path)

    sub = card_cooccurrence_subgraph(conn, "a", "b", top_n=50)
    groups = {n.id: n.group for n in sub.nodes}

    # The two seeds carry distinct colour groups; every shared card shares one
    # group, so targets and shared cards read apart. The hub is its own kind, not
    # a card. y and z are not shared by both seeds, so they are not drawn at all.
    assert groups["card:a"] != groups["card:b"]
    assert groups["card:a"] != "cooccur" and groups["card:b"] != "cooccur"
    assert groups["card:x"] == "cooccur"
    assert next(n for n in sub.nodes if n.kind == "Intersection").group is None
    assert "card:y" not in groups and "card:z" not in groups


def test_cooccurrence_two_seeds_line_up_shared_cards_right_of_the_hub(tmp_path):
    conn = _cooccur_fixture(tmp_path)

    sub = card_cooccurrence_subgraph(conn, "a", "b", top_n=50)
    pin = {n.id: n.pin for n in sub.nodes}
    hub = next(n for n in sub.nodes if n.kind == "Intersection")

    # Everything is pinned. The seeds sit left of the hub, which sits left of the
    # shared-card column, so the graph reads left-to-right into a lined-up list.
    assert all(p is not None for p in pin.values())
    assert pin["card:a"][0] == pin["card:b"][0] < pin[hub.id][0] < pin["card:x"][0]


def test_cooccurrence_drop_lands_excludes_land_cards_in_both_views(tmp_path):
    # fetch is a land; bolt is not. Both share every both-deck with the seeds.
    _write_snapshot(
        tmp_path,
        [
            {"id": "d1", "tag": "x", "norm": 0.0, "m": ["a", "b", "fetch", "bolt"]},
            {"id": "d2", "tag": "x", "norm": 0.0, "m": ["a", "b", "fetch"]},
            {"id": "d3", "tag": "x", "norm": 0.0, "m": ["a", "b", "bolt"]},
        ],
        ["a", "b", "fetch", "bolt"],
        lands={"fetch"},
    )
    conn = _connect(tmp_path, tmp_path)

    # Two-seed: the land is dropped, leaving the non-land package.
    kept = {n.id for n in card_cooccurrence_subgraph(conn, "a", "b", drop_lands=True).nodes
            if n.group == "cooccur"}
    assert kept == {"card:bolt"}
    # Unfiltered, the land is kept.
    unfiltered = {n.id for n in card_cooccurrence_subgraph(conn, "a", "b").nodes
                  if n.group == "cooccur"}
    assert unfiltered == {"card:bolt", "card:fetch"}
    # The single-seed path honours the filter too.
    solo = card_cooccurrence_subgraph(conn, "a", drop_lands=True)
    assert all(n.id != "card:fetch" for n in solo.nodes)


def test_cooccurrence_two_seeds_that_never_share_a_deck_show_no_hub(tmp_path):
    # a and b never appear in the same deck, so the intersection is empty.
    _write_snapshot(
        tmp_path,
        [
            {"id": "d1", "tag": "x", "norm": 0.0, "m": ["a", "x"]},
            {"id": "d2", "tag": "x", "norm": 0.0, "m": ["b", "y"]},
        ],
        ["a", "b", "x", "y"],
    )
    conn = _connect(tmp_path, tmp_path)

    sub = card_cooccurrence_subgraph(conn, "a", "b")

    # Two disconnected seeds, no empty "Both · 0 decks" hub and no edges.
    assert {n.id for n in sub.nodes} == {"card:a", "card:b"}
    assert not any(n.kind == "Intersection" for n in sub.nodes)
    assert sub.edges == []


def test_cooccurrence_single_seed_is_not_pinned(tmp_path):
    conn = _cooccur_fixture(tmp_path)

    # One seed keeps the physics layout (no fixed positions); pinning is only for
    # separating the two-seed hubs.
    sub = card_cooccurrence_subgraph(conn, "a", top_n=50)
    assert all(n.pin is None for n in sub.nodes)


def test_cooccurrence_two_seeds_rank_shared_cards_by_the_double_rate(tmp_path):
    # Decks d1-d3 run both a and b; d4/d5 run only one. Among the both-decks, x
    # appears in two, y and z in one each; q and w never share a both-deck.
    _write_snapshot(
        tmp_path,
        [
            {"id": "d1", "tag": "x", "norm": 0.0, "m": ["a", "b", "x", "y"]},
            {"id": "d2", "tag": "x", "norm": 0.0, "m": ["a", "b", "x"]},
            {"id": "d3", "tag": "x", "norm": 0.0, "m": ["a", "b", "z"]},
            {"id": "d4", "tag": "x", "norm": 0.0, "m": ["a", "q"]},
            {"id": "d5", "tag": "x", "norm": 0.0, "m": ["b", "w"]},
        ],
        ["a", "b", "x", "y", "z", "q", "w"],
    )
    conn = _connect(tmp_path, tmp_path)

    # Three decks run both seeds. Ranked by that double rate, x (2/3) leads, then
    # y and z tie (1/3, y first by name); top_n=2 keeps x and y. z is cut, and q
    # and w never appear (they share no both-deck).
    sub = card_cooccurrence_subgraph(conn, "a", "b", top_n=2)
    partners = {n.id for n in sub.nodes if n.group == "cooccur"}
    assert partners == {"card:x", "card:y"}

    hub = next(n for n in sub.nodes if n.kind == "Intersection")
    labels = {(e.source, e.target): e.label for e in sub.edges}
    assert labels[(hub.id, "card:x")] == "67%"  # 2 of the 3 both-decks
    assert labels[(hub.id, "card:y")] == "33%"  # 1 of the 3

    # The stronger card sits higher up the column (smaller y) than the weaker one.
    pin = {n.id: n.pin for n in sub.nodes}
    assert pin["card:x"][0] == pin["card:y"][0]
    assert pin["card:x"][1] < pin["card:y"][1]


def _filler(tag, n, norm, carrying=(), start=0):
    """``n`` decks of one archetype, each carrying ``carrying`` plus a unique pad.

    The pad keeps every deck non-empty without polluting the result: a card in
    one deck is under the trust floor, so pads can never surface as gems.
    """
    return [
        {"id": f"{tag}{i}", "tag": tag, "norm": norm, "m": [*carrying, f"pad_{tag}{i}"]}
        for i in range(start, start + n)
    ]


def _canons(decks):
    """Every card name the decks mention, in first-seen order."""
    return list(dict.fromkeys(c for d in decks for c in d.get("m", [])))


def _gem_cards(sub):
    return {n.id for n in sub.nodes if n.kind == "Card"}


def test_hidden_gems_are_rare_cards_that_place_highly(tmp_path):
    # 100 ranked decks, so the ceiling is 10. `gem` is in 6 of them, all placing
    # in the top tenth; `dud` is equally rare but places mid-field.
    decks = (
        _filler("x", 6, 0.1, carrying=["gem"])
        + _filler("x", 6, 0.5, carrying=["dud"], start=6)
        + _filler("x", 88, 0.5, start=12)
    )
    _write_snapshot(tmp_path, decks, _canons(decks))
    conn = _connect(tmp_path, tmp_path)

    sub = hidden_gems_subgraph(conn)

    # Rare and overperforming survives; rare but mid-field does not.
    assert "card:gem" in _gem_cards(sub)
    assert "card:dud" not in _gem_cards(sub)
    # The gem edges from exactly the ranked decks that run it, so the placement
    # behind the mean is visible.
    assert {e.source for e in sub.edges if e.target == "card:gem"} == {
        f"deck:x{i}" for i in range(6)
    }
    assert all(e.label.startswith("CONTAINS") for e in sub.edges)


def test_hidden_gems_ceiling_is_a_share_so_rarity_means_the_same_in_any_slice(tmp_path):
    # `edge` is in 8 decks, all of them Small. Small has 50 ranked decks, the
    # meta has 200. The same 8 decks read as rare against the meta (ceiling 20)
    # but as a staple within Small (ceiling 5). That flip is the point of a
    # share: an absolute ceiling could not tell those two slices apart.
    decks = (
        _filler("small", 8, 0.1, carrying=["edge"])
        + _filler("small", 42, 0.5, start=8)
        + _filler("big", 150, 0.5)
    )
    _write_snapshot(tmp_path, decks, _canons(decks))
    conn = _connect(tmp_path, tmp_path)

    assert "card:edge" in _gem_cards(hidden_gems_subgraph(conn))
    assert "card:edge" not in _gem_cards(hidden_gems_subgraph(conn, archetype="small"))


def test_hidden_gems_floor_is_absolute_so_trust_does_not_scale_with_the_meta(tmp_path):
    # `four` and `five` both place perfectly; only their deck counts differ, and
    # only across the floor. The floor is evidence, not a share, so the verdict
    # must not move when the meta around them grows.
    def build(meta_size):
        decks = (
            _filler("x", 4, 0.0, carrying=["four"])
            + _filler("x", 5, 0.0, carrying=["five"], start=4)
            + _filler("x", meta_size - 9, 0.5, start=9)
        )
        d = tmp_path / f"meta{meta_size}"
        d.mkdir()
        _write_snapshot(d, decks, _canons(decks))
        return _connect(d, d)

    for meta_size in (100, 400):
        cards = _gem_cards(hidden_gems_subgraph(build(meta_size)))
        assert "card:five" in cards, f"five-deck card lost at meta {meta_size}"
        assert "card:four" not in cards, f"four-deck card admitted at meta {meta_size}"


def test_hidden_gems_scope_rarity_to_the_archetype_slice(tmp_path):
    # `common` is a Storm staple (in 30 of 50 Storm decks) but fringe tech in
    # Lands (5 of 50), where the decks running it place first. Scoping to Lands
    # surfaces it; against the whole meta its Storm ubiquity buries it.
    decks = (
        _filler("storm", 30, 0.5, carrying=["common"])
        + _filler("storm", 20, 0.5, start=30)
        + _filler("lands", 5, 0.0, carrying=["common"])
        + _filler("lands", 45, 0.5, start=5)
    )
    _write_snapshot(tmp_path, decks, _canons(decks))
    conn = _connect(tmp_path, tmp_path)

    # 35 of 100 decks overall: far past the meta ceiling of 10.
    assert "card:common" not in _gem_cards(hidden_gems_subgraph(conn))
    # Within Lands it is 5 of 50: on the floor, on the ceiling, and winning.
    within_lands = hidden_gems_subgraph(conn, archetype="lands")
    assert "card:common" in _gem_cards(within_lands)
    # Scoping also restricts the decks drawn to the slice.
    assert {e.source for e in within_lands.edges if e.target == "card:common"} == {
        f"deck:lands{i}" for i in range(5)
    }


def test_min_gem_slice_is_the_smallest_slice_whose_band_is_not_inverted():
    # The guard's whole job is to reject slices where the ceiling has fallen
    # under the floor. It only does that if the smallest slice it admits still
    # has room for a gem, which is a property of the three constants, not of any
    # data. Pinned because the constants are expected to be tuned (ADR 0005) and
    # rounding to nearest here would silently re-admit an inverted band.
    assert MAX_GEM_SHARE * MIN_GEM_SLICE >= MIN_GEM_DECKS
    # And it is the *smallest* such slice: one deck fewer must be inverted, or
    # the guard is refusing slices that could in fact have answered.
    assert MAX_GEM_SHARE * (MIN_GEM_SLICE - 1) < MIN_GEM_DECKS


def test_hidden_gems_refuse_a_slice_too_small_to_support_the_claim(tmp_path):
    # Below MIN_GEM_SLICE the ceiling falls under the floor, so the band is empty
    # by construction. `tech` is in 5 of Fringe's 20 decks and wins every time:
    # under a naive read a gem, but 5 of 20 is a quarter of that archetype, which
    # is a staple, not a hidden gem. Refuse rather than answer "none", which would
    # read as "no gems here" instead of "not enough decks to tell".
    decks = (
        _filler("fringe", 5, 0.0, carrying=["tech"])
        + _filler("fringe", 15, 0.5, start=5)
        + _filler("wide", 80, 0.5)
    )
    _write_snapshot(tmp_path, decks, _canons(decks))
    conn = _connect(tmp_path, tmp_path)

    with pytest.raises(SliceTooSmall, match="20 ranked decks"):
        hidden_gems_subgraph(conn, archetype="fringe")

    # An archetype nothing is filed under is refused on the same grounds.
    with pytest.raises(SliceTooSmall, match="0 ranked decks"):
        hidden_gems_subgraph(conn, archetype="nonexistent")

    # The 100-deck meta around it still answers, so the refusal is about the
    # slice's size and not a query that has stopped working.
    assert "card:tech" in _gem_cards(hidden_gems_subgraph(conn))


def test_gem_archetypes_offer_only_the_slices_that_can_answer(tmp_path):
    # `wide` clears MIN_GEM_SLICE; `fringe` does not. Only the answerable one is
    # offered, so a slice too small is never put to the user as though it might.
    decks = _filler("wide", 60, 0.5) + _filler("fringe", 40, 0.5)
    _write_snapshot(tmp_path, decks, _canons(decks))
    conn = _connect(tmp_path, tmp_path)

    assert gem_archetypes(conn) == [("Wide", "wide")]

    # And every tag offered is one the gem query will actually accept.
    for _, tag in gem_archetypes(conn):
        hidden_gems_subgraph(conn, archetype=tag)


def test_hidden_gems_ignore_decks_with_unknown_placement(tmp_path):
    # `gem` is in 5 ranked decks (placed well) and 3 with no placement. Unranked
    # decks cannot confirm overperformance, so they count for neither bound: gem
    # clears the floor of 5 on its ranked decks alone, and `short` does not
    # reach it by padding with unranked ones.
    decks = (
        _filler("x", 5, 0.1, carrying=["gem"])
        + _filler("x", 4, 0.1, carrying=["short"], start=5)
        + [
            {"id": f"u{i}", "tag": "x", "norm": None, "m": ["gem", "short", f"pad_u{i}"]}
            for i in range(3)
        ]
        + _filler("x", 91, 0.5, start=9)
    )
    _write_snapshot(tmp_path, decks, _canons(decks))
    conn = _connect(tmp_path, tmp_path)

    sub = hidden_gems_subgraph(conn)

    assert "card:gem" in _gem_cards(sub)
    # Only the ranked decks appear; the unranked three are not drawn.
    assert {e.source for e in sub.edges if e.target == "card:gem"} == {
        f"deck:x{i}" for i in range(5)
    }
    # Three unranked decks do not lift a four-deck card over the floor.
    assert "card:short" not in _gem_cards(sub)


def test_pilot_affinity_tiers_pilot_macro_archetype_by_event_count(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)

    # Jordan is a Grixis specialist: both decks are tempo/Grixis, played at two
    # different events (CFWAT25, PogNov25). The macro sits between the pilot and
    # the archetype, and both tiers count the same two events.
    sub = pilot_affinity_subgraph(conn, "Jordan C")

    assert [n.label for n in sub.nodes if n.kind == "Pilot"] == ["Jordan C"]
    assert [(n.label, n.weight) for n in sub.nodes if n.kind == "Macro"] == [("tempo", 2)]
    assert [(n.label, n.weight) for n in sub.nodes if n.kind == "Archetype"] == [("Grixis", 2)]
    # pilot -> macro -> archetype, every edge labelled by the event count.
    assert [(e.source, e.target, e.label) for e in sub.edges] == [
        ("pilot:Jordan C", "macro:tempo", "PLAYS:2"),
        ("macro:tempo", "arch:grixis", "PLAYS:2"),
    ]


def test_pilot_affinity_counts_distinct_events_not_decks(tmp_path):
    # One pilot, three decks of one macro/archetype, all at the same event. The
    # affinity is one event, not three decks: it measures showing up, not how
    # many variants were entered on the day. Both tiers see one event.
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

    assert [n.weight for n in sub.nodes if n.kind == "Macro"] == [1]
    assert [n.weight for n in sub.nodes if n.kind == "Archetype"] == [1]
    assert [e.label for e in sub.edges] == ["PLAYS:1", "PLAYS:1"]


def test_pilot_affinity_shares_one_archetype_node_across_macros(tmp_path):
    # A generalist: Rakdos played as both aggro (event E1) and midrange (E2),
    # plus a Boros aggro list (E3). The archetype an already-seen name reappears
    # under is one shared node with an edge from each macro, sized by its events
    # across both; each macro edge carries only that macro's events.
    _write_snapshot(
        tmp_path,
        [
            {"id": "d1", "tag": "rakdos", "norm": 0.0, "macro": "aggro", "event": "E1", "m": ["a"]},
            {"id": "d2", "tag": "rakdos", "norm": 0.0, "macro": "midrange", "event": "E2", "m": ["a"]},
            {"id": "d3", "tag": "boros", "norm": 0.0, "macro": "aggro", "event": "E3", "m": ["a"]},
        ],
        ["a"],
    )
    conn = _connect(tmp_path, tmp_path)

    sub = pilot_affinity_subgraph(conn, "p")

    # Rakdos is one node (not one per macro), weighted by its two events.
    rakdos = [n for n in sub.nodes if n.kind == "Archetype" and n.label == "Rakdos"]
    assert len(rakdos) == 1 and rakdos[0].weight == 2
    macros = {n.label: n.weight for n in sub.nodes if n.kind == "Macro"}
    assert macros == {"aggro": 2, "midrange": 1}
    edges = {(e.source, e.target): e.label for e in sub.edges}
    assert edges[("macro:aggro", "arch:rakdos")] == "PLAYS:1"
    assert edges[("macro:midrange", "arch:rakdos")] == "PLAYS:1"
    assert edges[("macro:aggro", "arch:boros")] == "PLAYS:1"


def test_pilot_affinity_uses_display_name_and_counts_one_deck(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)

    # Keyed on the upstream id, labelled by display name (Tom S).
    sub = pilot_affinity_subgraph(conn, "AmberTealViper")

    assert [n.label for n in sub.nodes if n.kind == "Pilot"] == ["Tom S"]
    assert [(e.source, e.target, e.label) for e in sub.edges] == [
        ("pilot:AmberTealViper", "macro:combo", "PLAYS:1"),
        ("macro:combo", "arch:storm", "PLAYS:1"),
    ]


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
    # The optional second pilot rides the same spec through to the query.
    assert run_query(
        conn, PilotNeighbourhood("Jordan C", "AmberTealViper")
    ) == pilot_subgraph(conn, "Jordan C", "AmberTealViper")


def test_run_query_passes_spec_parameters_through(tmp_path, snapshot_dir):
    conn = _connect(tmp_path, snapshot_dir)
    decks_by_card = _decks_by_card(snapshot_dir)
    canon = next(c for c, ds in decks_by_card.items() if ds == JORDAN_DECKS)

    assert run_query(conn, CardCooccurrence(canon, top_n=25)) == (
        card_cooccurrence_subgraph(conn, canon, top_n=25)
    )
    # The gem spec's archetype reaches the query function: the fixture's slice is
    # far under MIN_GEM_SLICE, and the refusal names the archetype it was handed.
    with pytest.raises(SliceTooSmall, match="grixis"):
        run_query(conn, HiddenGems(archetype="grixis"))
