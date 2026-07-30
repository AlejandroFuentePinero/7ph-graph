import json
from collections import Counter, defaultdict

import pytest

from graph7ph import numfmt
from graph7ph.build import build_graph
from graph7ph.db import open_for_writing, rows
from graph7ph.models import load_snapshot
from graph7ph.query import (
    MAX_GEM_LUCK,
    MAX_GEM_NODES,
    MAX_GEM_SHARE,
    MIN_GEM_DECKS,
    MIN_GEM_SLICE,
    CardCooccurrence,
    HiddenGems,
    PilotNeighbourhood,
    _gem_tails,
    _pilot_deflated,
    card_cooccurrence_subgraph,
    card_usage_subgraph,
    coverage,
    deck_points,
    gem_archetypes,
    hidden_gems_subgraph,
    pilot_affinity_subgraph,
    pilot_subgraph,
    run_query,
)

# Every hand-authored fixture declares a field size no cohort it builds can
# contradict, so the build's field-size correction (issue #140) leaves it alone
# and the placementNorms these tests craft stand as written.
_FIELD_SIZE = 500

# The fixture's three decks, by pilot display name and archetype (see conftest).
JORDAN_DECKS = {"BsegXnsDsEWxh-vNbUrn0w", "pkUbzmgN3UeqaWdYQYRgRg"}  # Jordan C, Grixis


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


def _archetype_fields(tag):
    """The four source fields carrying a deck's archetype.

    A ``tag`` of ``None`` writes a deck the classifier gave no engine tag, which
    is what an unclassified deck looks like upstream: it still has a macro, so it
    reaches the affinity query's archetype hop with nothing to bind there.

    A ``tag`` may also be a list, for a deck the classifier gave several engine
    tags at once. That is the modal shape upstream (2332 of 4591 real decks carry
    more than one), so a fixture of single-tag decks alone leaves the common case
    untested. The first tag is the primary and the weights split evenly, which is
    all any query under test reads them for.
    """
    if tag is None:
        return {
            "engineTags": [], "engineTagLabels": {},
            "primaryTag": "", "primaryTagWeights": {},
        }
    tags = [tag] if isinstance(tag, str) else list(tag)
    return {
        "engineTags": [f"engine:{t}" for t in tags],
        "engineTagLabels": {f"engine:{t}": t.title() for t in tags},
        "primaryTag": f"engine:{tags[0]}",
        "primaryTagWeights": {f"engine:{t}": 100 // len(tags) for t in tags},
    }


def _write_snapshot(tmp_path, decks, canons, lands=frozenset(), points=None):
    """Write a minimal hand-authored snapshot for a focused test.

    ``decks`` is a list of dicts: ``id``, ``tag`` (archetype, ``None`` for a deck
    the classifier left unclassified), ``norm``
    (placementNorm), and ``m`` / ``s`` (main / side canon lists). Optional
    ``macro`` and ``event`` keys override the defaults (``control`` / ``E``), and
    an optional ``name`` overrides the deck title, which is how a test plants a
    placement only the title records (the recovery is anchored to the title's start,
    so the default "<pilot> - <id>" shape can never carry one).
    ``canons`` is the card catalogue by name; ``lands`` names those typed as
    ``Lands`` (the rest default to ``Instants``). ``points`` prices the cards that
    cost anything, as ``{canon: (deck cost, companion cost)}``; everything else is
    free in both contexts, as most of the format is.
    """
    points = points or {}
    idx = {c: i for i, c in enumerate(canons)}
    (tmp_path / "decks.json").write_text(json.dumps([
        {
            # Title reads "<pilot> - <deck>" so the recovered display name is the
            # deck's ``pilot`` key, keeping hand-authored pilots distinct.
            "deckId": d["id"], "name": d.get("name", f"{d.get('pilot', 'p')} - {d['id']}"),
            "deckName": d["id"],
            "pilot": d.get("pilot", "p"), "event": d.get("event", "E"),
            "eventId": f"evt_{d['id']}", "eventType": "Tournament",
            # A ``norm`` of ``None`` is a deck the source scored nothing for, so it
            # carries no placement either: the build mints a norm from any placement
            # it can see, so a placement beside a null norm is no longer an unranked
            # deck anywhere in the record (issue #162).
            "placement": 1 if d["norm"] is not None else None,
            "eventSize": _FIELD_SIZE,
            "placementNorm": d["norm"], "createdAt": "2025-06-01T00:00:00+00:00",
            "colour": "colour:U",
            "macro": f"macro:{d.get('macro', 'control')}",
            **_archetype_fields(d["tag"]),
        }
        for d in decks
    ]))
    (tmp_path / "cards_index.json").write_text(json.dumps({
        "v": 2,
        "cards": [
            {"canon": c, "name": c.title(),
             "type": "Lands" if c in lands else "Instants", "manaCost": "{U}",
             "manaValue": 1.0, "reserved": False,
             "points": points.get(c, (0, 0))[0],
             "pointsCompanion": points.get(c, (0, 0))[1]}
            for c in canons
        ],
        "decks": {
            d["id"]: {"m": [idx[c] for c in d.get("m", [])],
                      "s": [idx[c] for c in d.get("s", [])]}
            for d in decks
        },
    }))


def test_pilot_subgraph_chains_events_decks_and_placements_not_cards(tmp_path, snapshot_dir, built_graph):
    conn = built_graph(tmp_path, snapshot_dir)

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


def test_pilot_subgraph_edges_form_the_pilot_event_deck_placement_chain(tmp_path, snapshot_dir, built_graph):
    conn = built_graph(tmp_path, snapshot_dir)

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


def test_pilot_subgraph_labels_the_pilot_by_display_name(tmp_path, snapshot_dir, built_graph):
    conn = built_graph(tmp_path, snapshot_dir)

    # Keyed on the upstream id (AmberTealViper), the pilot node reads as the
    # recovered display name (Tom S), never the pseudonym.
    sub = pilot_subgraph(conn, "AmberTealViper")

    assert [n.label for n in sub.nodes if n.kind == "Pilot"] == ["Tom S"]
    assert [n.id for n in sub.nodes if n.kind == "Pilot"] == ["pilot:AmberTealViper"]


def test_a_placement_the_project_decided_is_marked_apart_from_one_it_was_given(
    tmp_path, built_graph
):
    # Issue #199: the neighbourhood printed every rank the same way, so a placement
    # read off a deck title looked exactly like one the source counted. d1 is scored
    # by the source; d2 is not, and its title is the only witness to 3rd, which is
    # the `title-single` recovery. Only the decided one takes the mark.
    _write_snapshot(
        tmp_path,
        [
            {"id": "d1", "tag": "x", "norm": 0.5, "pilot": "p", "m": ["a"]},
            # A second event, since two decks of one pilot at one event is the
            # collision that mints a second career (ADR 0010) and would split "p".
            {"id": "d2", "tag": "x", "norm": None, "pilot": "p", "event": "F",
             "m": ["a"], "name": "3rd p - Grixis - F"},
        ],
        ["a"],
    )
    conn = built_graph(tmp_path, tmp_path)

    sub = pilot_subgraph(conn, "p")

    placements = {n.label for n in sub.nodes if n.kind == "Placement"}
    assert placements == {"1st", f"3rd{numfmt.IMPUTED_MARK}"}


def test_unknown_pilot_yields_empty_subgraph(tmp_path, snapshot_dir, built_graph):
    conn = built_graph(tmp_path, snapshot_dir)

    sub = pilot_subgraph(conn, "Nobody At All")

    assert sub.nodes == []
    assert sub.edges == []


def test_pilot_head_to_head_shares_the_event_both_played(tmp_path, built_graph):
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
    conn = built_graph(tmp_path, tmp_path)

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


def test_pilot_head_to_head_tags_each_node_with_its_player(tmp_path, built_graph):
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
    conn = built_graph(tmp_path, tmp_path)

    sub = pilot_subgraph(conn, "p1", "p2")

    group = {n.id: n.group for n in sub.nodes}
    assert group["pilot:p1"] == "pilot:p1"
    assert group["pilot:p2"] == "pilot:p2"
    assert group["deck:d1"] == "pilot:p1"
    assert group["deck:d2"] == "pilot:p2"
    assert group["placement:d1"] == "pilot:p1"
    assert group["event:E"] is None  # both played it, so it belongs to neither


def test_pilot_single_neighbourhood_has_no_player_groups(tmp_path, snapshot_dir, built_graph):
    # One pilot has no second player to contrast, so every node stays ungrouped
    # (coloured by kind, not by player).
    conn = built_graph(tmp_path, snapshot_dir)

    sub = pilot_subgraph(conn, "Jordan C")

    assert all(n.group is None for n in sub.nodes)


def test_pilot_head_to_head_drops_events_only_one_pilot_played(tmp_path, built_graph):
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
    conn = built_graph(tmp_path, tmp_path)

    sub = pilot_subgraph(conn, "p1", "p2")

    assert {n.id for n in sub.nodes if n.kind == "Event"} == {"event:shared"}
    decks = {n.id for n in sub.nodes if n.kind == "Deck"}
    assert decks == {"deck:d2", "deck:d4"}  # the solo-event decks are gone
    # Both pilots still reach the one shared event.
    played = {(e.source, e.target) for e in sub.edges if e.label == "PLAYED_AT"}
    assert played == {("pilot:p1", "event:shared"), ("pilot:p2", "event:shared")}


def test_pilot_head_to_head_falls_back_to_one_pilot_when_second_is_empty(tmp_path, snapshot_dir, built_graph):
    # An empty second pilot is the ordinary neighbourhood: the two-arg call with
    # no second pilot matches the plain single-pilot call exactly.
    conn = built_graph(tmp_path, snapshot_dir)

    assert pilot_subgraph(conn, "Jordan C", "") == pilot_subgraph(conn, "Jordan C")


def test_card_usage_reads_adoption_rate_at_each_tier(tmp_path, snapshot_dir, built_graph):
    conn = built_graph(tmp_path, snapshot_dir)
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


def test_card_usage_carries_each_tier_adoption_as_numbers(tmp_path, built_graph):
    # A percent is a rounded display of a ratio, and the ratio's base is what
    # says whether to trust it: 1 of 1 and 1 of 3 are both real, and only the
    # counts tell them apart. Every tier here has its own base, so a swapped
    # numerator and denominator, or one tier borrowing another's base, fails.
    #
    # `x` runs in 1 of Rakdos's 3 decks and Boros's only deck. Both archetypes
    # are aggro, so aggro is 2 of 4; the meta is 10 decks, so the card is 2 of 10.
    _write_snapshot(
        tmp_path,
        [
            {"id": "r1", "tag": "rakdos", "norm": 0.0, "macro": "aggro", "m": ["x"]},
            {"id": "r2", "tag": "rakdos", "norm": 0.0, "macro": "aggro", "m": ["pad1"]},
            {"id": "r3", "tag": "rakdos", "norm": 0.0, "macro": "aggro", "m": ["pad2"]},
            # Its own pilot: a distinct registration that also runs x, not a
            # duplicate of r1 (same card list) the build would collapse.
            {"id": "b1", "tag": "boros", "norm": 0.0, "macro": "aggro", "pilot": "p2", "m": ["x"]},
        ]
        + [
            {"id": f"c{i}", "tag": "azorius", "norm": 0.0, "macro": "control", "m": [f"pad{i}"]}
            for i in range(3, 9)
        ],
        ["x", "pad1", "pad2", *(f"pad{i}" for i in range(3, 9))],
    )
    conn = built_graph(tmp_path, tmp_path)

    sub = card_usage_subgraph(conn, "x")

    # The card's meta play-rate: 2 of the 10 decks, no longer only readable out
    # of the "(20% of meta)" label it is welded into.
    card = next(n for n in sub.nodes if n.kind == "Card")
    assert (card.decks, card.total_decks) == (2, 10)
    assert card.label == "X (20% of meta)"

    counts = {(e.source, e.target): (e.decks, e.total_decks) for e in sub.edges}
    labels = {(e.source, e.target): e.label for e in sub.edges}
    # Aggro: 2 of its 4 decks run the card. Each archetype keeps its own base.
    assert counts[("card:x", "macro:aggro")] == (2, 4)
    assert counts[("macro:aggro", "arch:rakdos")] == (1, 3)
    assert counts[("macro:aggro", "arch:boros")] == (1, 1)
    # The labels are untouched, and 33% is a rounding the counts survive.
    assert labels[("card:x", "macro:aggro")] == "50%"
    assert labels[("macro:aggro", "arch:rakdos")] == "33%"
    assert labels[("macro:aggro", "arch:boros")] == "100%"


def test_card_usage_counts_only_the_decks_an_archetype_is_primary_of(tmp_path, built_graph):
    # An adoption rate names one archetype, so both its terms count that
    # archetype's own decks and not the decks that merely carry its tag beside a
    # stronger one (ADR 0023). Both terms are wrong under the wider reading, and
    # in opposite directions, so a fixture that moved only one of them would let
    # the other stay wide.
    #
    # Storm is the primary of `d1` alone. `d2` and `d3` are Grixis decks carrying
    # Storm as a secondary at weight 50, well inside the real tail. Storm is
    # therefore 1 of 1 decks, not 2 of 3: `d2` inflates the numerator by running
    # the card, `d3` inflates only the denominator by not.
    _write_snapshot(
        tmp_path,
        [
            {"id": "d1", "tag": "storm", "norm": 0.0, "m": ["x"]},
            {"id": "d2", "tag": ["grixis", "storm"], "norm": 0.0, "m": ["x", "pad1"]},
            {"id": "d3", "tag": ["grixis", "storm"], "norm": 0.0, "m": ["pad2"]},
            {"id": "d4", "tag": "grixis", "norm": 0.0, "m": ["pad3"]},
        ],
        ["x", "pad1", "pad2", "pad3"],
    )
    conn = built_graph(tmp_path, tmp_path)

    sub = card_usage_subgraph(conn, "x")

    counts = {(e.source, e.target): (e.decks, e.total_decks) for e in sub.edges}
    labels = {(e.source, e.target): e.label for e in sub.edges}
    # Storm's own single deck runs the card: 1 of 1, not 2 of 3.
    assert counts[("macro:control", "arch:storm")] == (1, 1)
    assert labels[("macro:control", "arch:storm")] == "100%"
    # Grixis is the control: it is the primary of three decks either way, so a
    # rate that was already reading its own decks does not move.
    assert counts[("macro:control", "arch:grixis")] == (1, 3)
    # The macro and meta tiers count decks rather than tags, so neither moves.
    card = next(n for n in sub.nodes if n.kind == "Card")
    assert (card.decks, card.total_decks) == (2, 4)
    assert counts[("card:x", "macro:control")] == (2, 4)


def test_card_usage_hangs_an_archetype_under_the_macro_of_its_own_decks(tmp_path, built_graph):
    # The grouping macro is chosen from the archetype's card-running decks, so it
    # has to read the same decks the rate above it does (ADR 0023). Leaving this
    # one query wide while the two rates narrow splits the population inside a
    # single function, which is the defect rather than a smaller version of it.
    #
    # Storm is the primary of `d1` alone, under control. `d2` and `d3` are aggro
    # Grixis decks carrying Storm as a secondary, and both run the card, so the
    # wider reading outvotes `d1` two to one and hangs Storm under aggro: a
    # grouping decided entirely by decks whose engine is Grixis.
    _write_snapshot(
        tmp_path,
        [
            {"id": "d1", "tag": "storm", "norm": 0.0, "macro": "control", "m": ["x"]},
            {"id": "d2", "tag": ["grixis", "storm"], "norm": 0.0, "macro": "aggro", "m": ["x", "pad1"]},
            {"id": "d3", "tag": ["grixis", "storm"], "norm": 0.0, "macro": "aggro", "m": ["x", "pad2"]},
        ],
        ["x", "pad1", "pad2"],
    )
    conn = built_graph(tmp_path, tmp_path)

    sub = card_usage_subgraph(conn, "x")

    parents = {e.target: e.source for e in sub.edges if e.target.startswith("arch:")}
    assert parents == {"arch:storm": "macro:control", "arch:grixis": "macro:aggro"}
    # Both macros are therefore drawn. Under the wider reading control has no
    # archetype to hold and never reaches the graph at all.
    assert {n.id for n in sub.nodes if n.kind == "Macro"} == {"macro:control", "macro:aggro"}


def test_card_usage_adoption_falls_when_a_card_is_only_in_some_decks(tmp_path, snapshot_dir, built_graph):
    conn = built_graph(tmp_path, snapshot_dir)
    # A card in both of Jordan's Grixis lists (tempo) but not Tom's Storm: 100%
    # adopted in Grixis, absent from Storm, so Storm/combo never appear.
    decks_by_card = _decks_by_card(snapshot_dir)
    canon = next(c for c, ds in decks_by_card.items() if ds == JORDAN_DECKS)

    sub = card_usage_subgraph(conn, canon)

    # Only Grixis/tempo appear; each is a named node, the percent rides its edge.
    assert {n.label for n in sub.nodes if n.kind in ("Macro", "Archetype")} == {"tempo", "Grixis"}
    assert {e.label for e in sub.edges} == {"100%"}


def test_card_usage_board_scopes_which_decks_count(tmp_path, snapshot_dir, built_graph):
    conn = built_graph(tmp_path, snapshot_dir)
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


def test_card_usage_of_unknown_card_is_empty(tmp_path, snapshot_dir, built_graph):
    conn = built_graph(tmp_path, snapshot_dir)

    sub = card_usage_subgraph(conn, "not a real card")

    assert sub.nodes == []
    assert sub.edges == []


def test_cooccurrence_counts_only_same_board_pairings(tmp_path, snapshot_dir, built_graph):
    conn = built_graph(tmp_path, snapshot_dir)
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


def test_cooccurrence_ignores_main_versus_side_pairings(tmp_path, built_graph):
    # One deck: A and B in the main, C in the side. A pairs with B (same board)
    # but not C, whose only overlap with A is main-versus-side.
    _write_snapshot(
        tmp_path,
        [{"id": "d1", "tag": "x", "norm": 0.0, "m": ["a", "b"], "s": ["c"]}],
        ["a", "b", "c"],
    )
    conn = built_graph(tmp_path, tmp_path)

    sub = card_cooccurrence_subgraph(conn, "a")

    neighbours = {n.label for n in sub.nodes if n.kind == "Card"} - {"A"}
    assert neighbours == {"B"}


def test_cooccurrence_keeps_the_top_n_by_rate_and_labels_the_percent(tmp_path, built_graph):
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
    conn = built_graph(tmp_path, tmp_path)

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

    # The percent is a rounded display of a ratio, so both of its terms survive
    # as numbers: a consumer reads the count and the base it is a share of
    # rather than parsing "75%" back into an approximation of them.
    counts = {
        e.target.removeprefix("card:"): (e.decks, e.total_decks)
        for e in full.edges
    }
    assert counts == {"b": (4, 4), "c": (3, 4), "d": (2, 4), "e": (1, 4)}


def test_cooccurrence_two_seeds_carry_the_intersection_counts_as_numbers(tmp_path, built_graph):
    conn = _cooccur_fixture(tmp_path, built_graph)

    sub = card_cooccurrence_subgraph(conn, "a", "b", top_n=50)
    hub = next(n for n in sub.nodes if n.kind == "Intersection")
    counts = {(e.source, e.target): (e.decks, e.total_decks) for e in sub.edges}

    # The hub's deck count is the denominator every percent below it is read
    # against, so it is a number and not only the "Both · 1 decks" label.
    assert hub.decks == 1
    # Each seed's share of its own decks that fall in the intersection, and the
    # shared card's count out of the both-decks base.
    assert counts[("card:a", hub.id)] == (1, 2)
    assert counts[("card:b", hub.id)] == (1, 2)
    assert counts[(hub.id, "card:x")] == (1, 1)


def _cooccur_fixture(tmp_path, built_graph):
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
    return built_graph(tmp_path, tmp_path)


def test_cooccurrence_two_seeds_hang_shared_cards_off_an_intersection_hub(tmp_path, built_graph):
    conn = _cooccur_fixture(tmp_path, built_graph)

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


def test_cooccurrence_second_seed_is_a_target_not_a_partner_node(tmp_path, built_graph):
    conn = _cooccur_fixture(tmp_path, built_graph)

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


def test_cooccurrence_two_seeds_line_up_shared_cards_right_of_the_hub(tmp_path, built_graph):
    conn = _cooccur_fixture(tmp_path, built_graph)

    sub = card_cooccurrence_subgraph(conn, "a", "b", top_n=50)
    pin = {n.id: n.pin for n in sub.nodes}
    hub = next(n for n in sub.nodes if n.kind == "Intersection")

    # Everything is pinned. The seeds sit left of the hub, which sits left of the
    # shared-card column, so the graph reads left-to-right into a lined-up list.
    assert all(p is not None for p in pin.values())
    assert pin["card:a"][0] == pin["card:b"][0] < pin[hub.id][0] < pin["card:x"][0]


def test_cooccurrence_drop_lands_excludes_land_cards_in_both_views(tmp_path, built_graph):
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
    conn = built_graph(tmp_path, tmp_path)

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


def test_cooccurrence_two_seeds_that_never_share_a_deck_show_no_hub(tmp_path, built_graph):
    # a and b never appear in the same deck, so the intersection is empty.
    _write_snapshot(
        tmp_path,
        [
            {"id": "d1", "tag": "x", "norm": 0.0, "m": ["a", "x"]},
            {"id": "d2", "tag": "x", "norm": 0.0, "m": ["b", "y"]},
        ],
        ["a", "b", "x", "y"],
    )
    conn = built_graph(tmp_path, tmp_path)

    sub = card_cooccurrence_subgraph(conn, "a", "b")

    # Two disconnected seeds, no empty "Both · 0 decks" hub and no edges.
    assert {n.id for n in sub.nodes} == {"card:a", "card:b"}
    assert not any(n.kind == "Intersection" for n in sub.nodes)
    assert sub.edges == []


def test_cooccurrence_single_seed_is_not_pinned(tmp_path, built_graph):
    conn = _cooccur_fixture(tmp_path, built_graph)

    # One seed keeps the physics layout (no fixed positions); pinning is only for
    # separating the two-seed hubs.
    sub = card_cooccurrence_subgraph(conn, "a", top_n=50)
    assert all(n.pin is None for n in sub.nodes)


def test_cooccurrence_two_seeds_rank_shared_cards_by_the_double_rate(tmp_path, built_graph):
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
    conn = built_graph(tmp_path, tmp_path)

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


def _slice(tag, n, cards=(), start=0):
    """``n`` ranked decks of one archetype, finishing evenly from first to last.

    ``cards`` maps a card name to the deck positions that run it, where 0 is the
    archetype's best finish and ``n - 1`` its worst, so a fixture states where a
    card lands in its own archetype rather than what it averages. Every deck also
    carries a unique pad, which keeps it non-empty without ever reaching the trust
    floor, and its own pilot, so a card's decks are as many pilots.

    Every deck sits at the default event, which is what makes these fixtures state a
    rule rather than an accident of where the decks were played: the null is asked
    inside the event (``query._gem_tails``), and one event is one stratum, so the odds
    here are the plain hypergeometric ones a reader can check by hand.
    ``test_a_card_carried_by_the_events_it_was_played_at_is_not_a_gem`` is the fixture
    that spreads them out, and it is the only one that needs to.
    """
    decks = [
        {"id": f"{tag}{i}", "tag": tag, "norm": round(i / (n - 1), 4),
         "pilot": f"{tag}p{i}", "m": [f"pad_{tag}{i}"]}
        for i in range(start, start + n)
    ]
    for card, positions in dict(cards).items():
        for i in positions:
            decks[i]["m"].append(card)
    return decks


def test_a_gem_is_a_rare_card_that_crowds_its_own_archetype_s_best_decks(
    tmp_path, built_graph
):
    # The rule is entirely within one archetype: rank the archetype's ranked decks,
    # take its best fifth, and ask how unlikely it is that a card this rare landed
    # this much of itself in there. `tech` and `spread` are equally rare (6 of 60
    # decks, inside the 15% ceiling and on the 6-deck floor) and finish differently:
    # tech's six decks are the archetype's six best, spread's six are scattered down
    # the field. Only concentration in the top cut is evidence.
    decks = _slice("x", 60, {"tech": range(6), "spread": [0, 1, 25, 35, 45, 55]})
    _write_snapshot(tmp_path, decks, _canons(decks))
    conn = built_graph(tmp_path, tmp_path)

    sub = hidden_gems_subgraph(conn)

    assert "card:x:tech" in _gem_cards(sub)
    assert "card:x:spread" not in _gem_cards(sub)


def test_a_card_carried_by_the_events_it_was_played_at_is_not_a_gem(
    tmp_path, built_graph
):
    # 27 of the corpus's 107 events publish only a top cut rather than a field (SSWam
    # holds 7 decks against 88 players), so every deck the graph has from one is a good
    # finish by construction and the entrants who missed the bracket leave no trace. A
    # deck from such an event lands in its archetype's cut 64% of the time against 18%
    # for a deck from a full field, so a card that merely turned up at them collects
    # top-cut decks without beating anybody. The archetype-wide tail books that as a
    # finding; asked inside the event, it is not one.
    #
    # `bracket` is such an event: eight of the archetype's decks, and they are its eight
    # best. `tourist` sits in six of them and nowhere else, so it holds six of the
    # archetype's twelve best decks and has beaten nothing, every deck of its archetype
    # at that event being in the cut. `local` is the control: as rare and as
    # concentrated, at an event that recorded winners and losers alike.
    decks = _slice("x", 60, {"tourist": range(6), "local": [8, 9, 10, 11, 20, 30]})
    for position, deck in enumerate(decks):
        deck["event"] = "bracket" if position < 8 else "field"
    _write_snapshot(tmp_path, decks, _canons(decks))
    conn = built_graph(tmp_path, tmp_path)

    cards = _gem_cards(hidden_gems_subgraph(conn))

    assert "card:x:local" in cards
    assert "card:x:tourist" not in cards
    # And it is the stratification that refuses it, not the bounds or the bar. Read
    # across the archetype, six of six in a cut of twelve clears the bar by three orders
    # of magnitude and the tourist is the strongest card in the fixture. Read inside
    # `bracket`, where all eight of the archetype's decks are in the cut, the same six
    # decks are the only six it could have been in and the tail is 1.
    assert _gem_tails(((60, 12, 6),))[6] <= MAX_GEM_LUCK
    assert _gem_tails(((8, 8, 6),))[6] == pytest.approx(1.0)


def test_a_card_the_same_few_pilots_keep_running_is_not_a_gem(tmp_path, built_graph):
    # The null counts decks; the claim needs people. "This card is in five of the
    # archetype's twelve best decks" is evidence when five players arrived at it
    # separately, and is one player's habit counted five times when they did not: a
    # strong pilot finishes high repeatedly and carries every card in the 60 up with
    # them, including the ones doing nothing. Stratifying by event cannot see this,
    # since ADR 0004 puts one deck per pilot per event and the correlation lives
    # across events.
    #
    # The sixty decks are spread over twelve events of five, so the cut of twelve is
    # exactly one deck per event and every stratum is the same shape. Both cards below
    # sit in seven decks at seven distinct events with five of them in the cut, so both
    # reach the same tail of 0.0047 and both would be gems on the decks alone. They
    # differ only in who was holding them: `crowd` is seven players, `habit` is two.
    # The repeat has to span events because ADR 0004 gives a pilot one deck per event,
    # which is also exactly why stratifying by event cannot catch this.
    decks = _slice("x", 60, {
        "crowd": [0, 1, 2, 3, 4, 17, 18],
        "habit": [5, 6, 7, 8, 9, 22, 23],
    })
    for position, deck in enumerate(decks):
        deck["event"] = f"e{position % 12}"
    for position in (5, 6, 7, 8, 9, 22, 23):
        decks[position]["pilot"] = "regular" if position % 2 else "friend"
    _write_snapshot(tmp_path, decks, _canons(decks))
    conn = built_graph(tmp_path, tmp_path)

    cards = _gem_cards(hidden_gems_subgraph(conn))

    assert "card:x:crowd" in cards
    assert "card:x:habit" not in cards
    # A card whose decks are all different players is charged nothing, which is the
    # point: only repetition costs. The same tail on two pilots crosses the bar.
    assert _gem_tails(((5, 1, 1),) * 7)[5] == pytest.approx(0.004672, rel=1e-3)
    assert _pilot_deflated(0.004672, 7, 7) == 0.004672
    assert _pilot_deflated(0.004672, 7, 2) > MAX_GEM_LUCK


def test_a_gem_carries_the_whole_claim_as_numbers(tmp_path, built_graph):
    # What the rule cut on rides out on the node rather than only as the filter that
    # produced it (issue #12): how rare the card is, how much of it is in the cut, how
    # big the archetype it is rare in is, how many pilots stand behind it, and the
    # exact odds. The fixture is chosen so the odds can be checked against a worked
    # example: 6 of the 60 decks, all 6 in a cut of 12, is C(12,6)/C(60,6) =
    # 924/50063860, a number nothing in the query recomputes the way the query does.
    decks = _slice("x", 60, {"tech": range(6)})
    _write_snapshot(tmp_path, decks, _canons(decks))
    conn = built_graph(tmp_path, tmp_path)

    gem = next(n for n in hidden_gems_subgraph(conn).nodes if n.id == "card:x:tech")

    assert (gem.decks, gem.top_decks, gem.total_decks) == (6, 6, 60)
    assert gem.pilots == 6  # a pilot each, so six decks really are six opinions
    assert gem.gem_luck == pytest.approx(1.84564e-05, rel=1e-4)
    # The label is untouched: the numbers ride alongside it, not inside it.
    assert gem.label == "Tech"


def test_a_gem_is_drawn_between_its_archetype_and_its_best_decks(tmp_path, built_graph):
    # `Archetype -> Card <- Deck`: the archetype the claim is scoped to on one side,
    # and on the other the decks that back it, which are the ones in the cut. The
    # decks outside the cut are counted (they are the card's rarity) and not drawn:
    # they are not what the claim rests on, and drawing them would put the reader's
    # eye on the decks the rule discounted.
    decks = _slice("x", 60, {"tech": [*range(7), 40]})
    _write_snapshot(tmp_path, decks, _canons(decks))
    conn = built_graph(tmp_path, tmp_path)

    sub = hidden_gems_subgraph(conn)

    gem = next(n for n in sub.nodes if n.id == "card:x:tech")
    assert (gem.decks, gem.top_decks) == (8, 7)
    assert [n.id for n in sub.nodes if n.kind == "Archetype"] == ["arch:x"]
    assert ("arch:x", "card:x:tech") in [(e.source, e.target) for e in sub.edges]
    # All seven of those, never the eighth: the cut is the claim, and a deck outside
    # it is not evidence for the card however early it ran one.
    assert {e.source for e in sub.edges if e.target == "card:x:tech"} == {
        "arch:x", *(f"deck:x{i}" for i in range(7))
    }
    assert {n.id for n in sub.nodes if n.kind == "Deck"} == {
        f"deck:x{i}" for i in range(7)
    }


def test_a_gem_draws_every_deck_it_counts(tmp_path, built_graph):
    # The picture and the table state one number. `top_decks` is what the card did and
    # every one of those decks is a node, so a reader who counts the decks hanging off
    # a card gets the column back. A cap here was carried while the list was long
    # enough to threaten the node budget; the budget cuts whole gems instead, so the
    # evidence behind a drawn one is never a sample the reader cannot see the edge of.
    decks = _slice("x", 60, {"tech": range(9)})
    _write_snapshot(tmp_path, decks, _canons(decks))
    conn = built_graph(tmp_path, tmp_path)

    sub = hidden_gems_subgraph(conn)

    gem = next(n for n in sub.nodes if n.id == "card:x:tech")
    assert (gem.decks, gem.top_decks) == (9, 9)
    assert {n.id for n in sub.nodes if n.kind == "Deck"} == {
        f"deck:x{i}" for i in range(9)
    }


def test_a_drawn_deck_is_wired_to_every_gem_of_its_archetype_it_runs(
    tmp_path, built_graph
):
    # A deck node is one deck, wired to every gem of its archetype it holds. Leaving out
    # an edge would draw a deck that visibly does not run a card it runs, and the
    # overlaps between gems are the one thing the picture says that the table cannot.
    #
    # `lead` sits in the 6th to 11th best decks and `trail` in the best 6, so they share
    # the 6th. That deck is one node with an edge to each, not two nodes.
    decks = _slice("x", 60, {"lead": range(5, 11), "trail": range(6)})
    _write_snapshot(tmp_path, decks, _canons(decks))
    conn = built_graph(tmp_path, tmp_path)

    sub = hidden_gems_subgraph(conn)

    drawn = {n.id for n in sub.nodes if n.kind == "Deck"}
    assert drawn == {f"deck:x{i}" for i in range(11)}  # the union, counted once
    assert {e.source for e in sub.edges if e.target == "card:x:lead"} == {
        "arch:x", *(f"deck:x{i}" for i in range(5, 11))
    }
    assert {e.source for e in sub.edges if e.target == "card:x:trail"} == {
        "arch:x", *(f"deck:x{i}" for i in range(6))
    }
    # And the shared deck is the overlap the picture exists to show.
    assert {e.target for e in sub.edges if e.source == "deck:x5"} == {
        "card:x:lead", "card:x:trail"
    }


def test_one_card_can_be_a_gem_in_two_archetypes_and_is_then_two_findings(
    tmp_path, built_graph
):
    # Everything is measured inside the archetype, so the same card is screened
    # separately in each one it appears in and the two answers stand or fall on their
    # own decks. `tech` crowds Alpha's cut and is scattered through Beta's; the meta
    # around it is irrelevant to both. Two nodes rather than one shared card node,
    # because a merged node could carry only one archetype's numbers.
    decks = (_slice("alpha", 60, {"tech": range(6)})
             + _slice("beta", 60, {"tech": [0, 1, 25, 35, 45, 55]}))
    _write_snapshot(tmp_path, decks, _canons(decks))
    conn = built_graph(tmp_path, tmp_path)

    cards = _gem_cards(hidden_gems_subgraph(conn))

    assert "card:alpha:tech" in cards
    assert "card:beta:tech" not in cards


def test_a_gem_must_be_rare_in_its_archetype_and_attested_by_enough_of_it(
    tmp_path, built_graph
):
    # The two bounds answer different questions, which is why only one is a share
    # (ADR 0012). `thin` is one deck under the floor and `staple` one deck over the
    # ceiling (15% of 60 is 9); both put every one of their decks in the cut, so the odds
    # alone would admit them well inside the bar. Neither is a gem: one is not
    # attested, the other is not rare. Both are counted off the constants, so a swept
    # floor moves the fixture with it rather than turning the bound it tests into a
    # coincidence.
    ceiling = round(MAX_GEM_SHARE * 60)
    decks = _slice("x", 60, {
        "thin": range(MIN_GEM_DECKS - 1),
        "keeper": range(MIN_GEM_DECKS),
        "staple": range(ceiling + 1),
    })
    _write_snapshot(tmp_path, decks, _canons(decks))
    conn = built_graph(tmp_path, tmp_path)

    cards = _gem_cards(hidden_gems_subgraph(conn))

    assert "card:x:keeper" in cards  # 6 decks: on the floor, under the ceiling
    assert "card:x:thin" not in cards
    assert "card:x:staple" not in cards


def test_an_archetype_too_small_to_ask_is_skipped_rather_than_answered_for(
    tmp_path, built_graph
):
    # Below MIN_GEM_SLICE the ceiling has fallen under the floor, so the rule asks for
    # a card in at least 6 decks and at most 5 and nothing can satisfy it. `tech` wins
    # every deck it is in, in both archetypes; only the size of the archetype around it
    # differs, by one deck across the crossover. There is no dropdown to refuse from
    # any more, so the small slice is simply not hunted in.
    decks = (_slice("small", 39, {"tech": range(6)})
             + _slice("big", 40, {"tech": range(6)}))
    _write_snapshot(tmp_path, decks, _canons(decks))
    conn = built_graph(tmp_path, tmp_path)

    sub = hidden_gems_subgraph(conn)

    assert _gem_cards(sub) == {"card:big:tech"}
    assert [n.id for n in sub.nodes if n.kind == "Archetype"] == ["arch:big"]


def test_the_drawn_list_says_how_many_of_it_chance_alone_would_have_put_there(
    tmp_path, built_graph
):
    # A list admitted on a probability threshold has a false-positive count whether or
    # not it is printed, and it is summed over every card the rule *looked at*, not
    # over the ones it kept: the rejects are most of the evidence about how often this
    # bar is cleared by accident. Two cards are screened here, identical in shape (6 of
    # 60 decks, a cut of 12) and opposite in result; a card of that shape has seven
    # possible outcomes and the best it can do without clearing the bar is five of six,
    # so each clears by chance 0.00078 of the time, not the 0.01 the bar names. The
    # list of one therefore expects 0.0016 of itself to be luck.
    decks = _slice("x", 60, {"tech": range(6), "spread": [0, 1, 25, 35, 45, 55]})
    _write_snapshot(tmp_path, decks, _canons(decks))
    conn = built_graph(tmp_path, tmp_path)

    sub = hidden_gems_subgraph(conn)

    assert _gem_cards(sub) == {"card:x:tech"}
    assert sub.expected_by_luck == pytest.approx(2 * 0.000777807, rel=1e-4)


def test_the_drawn_list_is_cut_to_the_node_budget_from_its_weakest_end(
    tmp_path, built_graph
):
    # The tab has no control to narrow with, so a threshold that admits more cards on
    # every ingest would eventually outgrow the canvas. 240 cards each sit in 6 of one
    # archetype's 20 best decks: every one clears the bar, and drawing them all would
    # be 1 archetype + 20 decks + 240 cards. The list is cut to the strongest that fit,
    # never packed with whichever happen to be cheap, so it stays a list a reader can
    # name: here the odds are identical, so the order is the tie-break (the card name)
    # and the survivors are its prefix.
    top = [f"c{j:03d}" for j in range(240)]
    decks = _slice("x", 60, {c: [(j + k) % 12 for k in range(6)] for j, c in enumerate(top)})
    _write_snapshot(tmp_path, decks, _canons(decks))
    conn = built_graph(tmp_path, tmp_path)

    sub = hidden_gems_subgraph(conn)

    drawn = sorted(n.id.removeprefix("card:x:") for n in sub.nodes if n.kind == "Card")
    assert len(sub.nodes) <= MAX_GEM_NODES
    assert len(drawn) < len(top), "nothing was cut, so the budget was never tested"
    assert drawn == sorted(top)[:len(drawn)]


def test_the_gem_node_budget_is_the_render_threshold():
    # The budget exists so the gem view cannot hand the renderer a picture it will
    # refuse to draw. It is written in `query` rather than imported, because `explore`
    # reads `query` and the import would close a cycle, so this is what keeps the two
    # numbers one number.
    from graph7ph.explore import RENDER_THRESHOLD

    assert MAX_GEM_NODES == RENDER_THRESHOLD


def test_min_gem_slice_is_the_smallest_slice_whose_bounds_are_not_inverted():
    # The skip's whole job is to drop archetypes where the ceiling has fallen under
    # the floor. It only does that if the smallest slice it admits still has room for
    # a gem, which is a property of the constants, not of any data. Pinned because the
    # constants are expected to be tuned (ADR 0012, ADR 0020) and rounding to nearest
    # here would silently re-admit an inverted rule.
    assert MAX_GEM_SHARE * MIN_GEM_SLICE >= MIN_GEM_DECKS
    # And it is the *smallest* such slice: one deck fewer must be inverted, or the
    # skip is dropping archetypes that could in fact have answered.
    assert MAX_GEM_SHARE * (MIN_GEM_SLICE - 1) < MIN_GEM_DECKS


def test_gems_ignore_decks_with_unknown_placement(tmp_path, built_graph):
    # A deck with no recorded finish cannot say whether a card over- or
    # under-performed, so it counts toward neither bound, neither the cut, nor the
    # archetype it is a share of. `tech` clears the floor on its ranked decks alone;
    # `short` sits one under it and does not reach it by padding with three unranked
    # ones.
    decks = _slice("x", 60, {
        "tech": range(MIN_GEM_DECKS),
        "short": range(MIN_GEM_DECKS, 2 * MIN_GEM_DECKS - 1),
    }) + [
        {"id": f"u{i}", "tag": "x", "norm": None, "pilot": f"up{i}", "event": f"uE{i}",
         "m": ["tech", "short", f"pad_u{i}"]}
        for i in range(3)
    ]
    _write_snapshot(tmp_path, decks, _canons(decks))
    conn = built_graph(tmp_path, tmp_path)

    sub = hidden_gems_subgraph(conn)

    assert _gem_cards(sub) == {"card:x:tech"}
    gem = next(n for n in sub.nodes if n.id == "card:x:tech")
    # The three unranked decks pad neither the card's count nor the archetype it is a
    # share of, though they run the card and carry the tag.
    assert (gem.decks, gem.total_decks) == (MIN_GEM_DECKS, 60)
    assert not [n for n in sub.nodes if n.id.startswith("deck:u")]


def test_gems_only_come_from_the_archetypes_big_enough_to_ask(tmp_path, built_graph):
    # `gem_archetypes` is the stated population the hunt draws from, so the two must
    # agree: an archetype the list leaves out can never appear in the answer. Four
    # qualify and `fringe` does not.
    #
    # Four rather than two, to hold the ORDER BY (issue #56). That query aggregates,
    # and an aggregation's rows come back in hash order, which is unrelated to the
    # order the fixture wrote its decks in. So no fixture ordering can make a missing
    # ORDER BY fail here; only the improbability of hash order landing on label order
    # can, and two archetypes leaves that far too likely.
    decks = ([d for tag in ("wide", "grindy", "combo", "midrange")
              for d in _slice(tag, MIN_GEM_SLICE + 10, {"tech": range(MIN_GEM_DECKS)})]
             + _slice("fringe", MIN_GEM_SLICE - 1, {"tech": range(MIN_GEM_DECKS)}))
    _write_snapshot(tmp_path, decks, _canons(decks))
    conn = built_graph(tmp_path, tmp_path)

    assert gem_archetypes(conn) == [
        ("Combo", "combo"), ("Grindy", "grindy"),
        ("Midrange", "midrange"), ("Wide", "wide"),
    ]

    offered = {f"arch:{tag}" for _, tag in gem_archetypes(conn)}
    drawn = {n.id for n in hidden_gems_subgraph(conn).nodes if n.kind == "Archetype"}
    assert drawn and drawn <= offered


def test_pilot_affinity_tiers_pilot_macro_archetype_by_event_count(tmp_path, snapshot_dir, built_graph):
    conn = built_graph(tmp_path, snapshot_dir)

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


def test_pilot_affinity_counts_distinct_events_not_decks(tmp_path, built_graph):
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
    conn = built_graph(tmp_path, tmp_path)

    sub = pilot_affinity_subgraph(conn, "p")

    assert [n.weight for n in sub.nodes if n.kind == "Macro"] == [1]
    assert [n.weight for n in sub.nodes if n.kind == "Archetype"] == [1]
    assert [e.label for e in sub.edges] == ["PLAYS:1", "PLAYS:1"]


def test_pilot_affinity_shares_one_archetype_node_across_macros(tmp_path, built_graph):
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
    conn = built_graph(tmp_path, tmp_path)

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


def test_pilot_affinity_reads_a_multi_tag_deck_as_its_primary_only(tmp_path, built_graph):
    # One deck carrying two archetypes under one macro, the modal shape in the
    # real data (2332 of 4591 decks) and one no other affinity fixture has: every
    # other one gives a deck a single tag, so nothing else pins which of a deck's
    # tags the affinity view is willing to credit the pilot with.
    #
    # `d1` is Storm primary and Grixis secondary at E1, `d2` is Storm alone at E2.
    # Grixis is drawn nowhere: an archetype the pilot was partly committed to on
    # one deck is not an archetype they play (ADR 0023, ADR 0020). Storm is both
    # events, so the macro's children sum to the macro rather than over-covering
    # it, which is what one primary per deck buys.
    _write_snapshot(
        tmp_path,
        [
            {"id": "d1", "tag": ["storm", "grixis"], "norm": 0.0, "event": "E1", "m": ["a"]},
            {"id": "d2", "tag": "storm", "norm": 0.0, "event": "E2", "m": ["a"]},
        ],
        ["a"],
    )
    conn = built_graph(tmp_path, tmp_path)

    sub = pilot_affinity_subgraph(conn, "p")

    macro = next(n for n in sub.nodes if n.kind == "Macro")
    assert (macro.id, macro.weight) == ("macro:control", 2)
    children = {
        e.target: e.events for e in sub.edges if e.source == "macro:control"
    }
    assert children == {"arch:storm": 2}
    # The secondary tag reaches neither the edges nor the nodes, so a reader
    # cannot find Grixis on this pilot at all.
    assert "arch:grixis" not in {n.id for n in sub.nodes}
    # The all-tag reading let a macro's children total 1.85x their parent over
    # 1144 of 1904 real rows (ADR 0014); with one primary per deck that is 0 of
    # 1907. Not a structural guarantee, since two decks a pilot entered at one
    # event under one macro can still carry different primaries, but nothing on
    # this artifact does. The tree drawing invites the reader to add the children
    # up, so the sum is asserted rather than left implicit.
    assert sum(children.values()) <= macro.weight


def test_pilot_affinity_uses_display_name_and_counts_one_deck(tmp_path, snapshot_dir, built_graph):
    conn = built_graph(tmp_path, snapshot_dir)

    # Keyed on the upstream id, labelled by display name (Tom S).
    sub = pilot_affinity_subgraph(conn, "AmberTealViper")

    assert [n.label for n in sub.nodes if n.kind == "Pilot"] == ["Tom S"]
    assert [(e.source, e.target, e.label) for e in sub.edges] == [
        ("pilot:AmberTealViper", "macro:combo", "PLAYS:1"),
        ("macro:combo", "arch:storm", "PLAYS:1"),
    ]


def test_pilot_affinity_carries_its_event_counts_as_numbers(tmp_path, built_graph):
    # "PLAYS:1" is display text. The count behind it survives as a number, so
    # reading a pilot's affinity never means parsing a label. The generalist
    # fixture distinguishes the three tiers: aggro's 2 events, Rakdos's 2 across
    # both macros, and 1 on each macro->archetype edge.
    _write_snapshot(
        tmp_path,
        [
            {"id": "d1", "tag": "rakdos", "norm": 0.0, "macro": "aggro", "event": "E1", "m": ["a"]},
            {"id": "d2", "tag": "rakdos", "norm": 0.0, "macro": "midrange", "event": "E2", "m": ["a"]},
            {"id": "d3", "tag": "boros", "norm": 0.0, "macro": "aggro", "event": "E3", "m": ["a"]},
        ],
        ["a"],
    )
    conn = built_graph(tmp_path, tmp_path)

    sub = pilot_affinity_subgraph(conn, "p")

    events = {(e.source, e.target): e.events for e in sub.edges}
    assert events[("pilot:p", "macro:aggro")] == 2
    assert events[("pilot:p", "macro:midrange")] == 1
    assert events[("macro:aggro", "arch:rakdos")] == 1
    # The node weight is already a number, so the whole tier is readable without
    # touching a label.
    assert {n.label: n.weight for n in sub.nodes if n.kind == "Archetype"} == {
        "Rakdos": 2,
        "Boros": 1,
    }


def test_pilot_affinity_keeps_a_pilot_whose_decks_carry_no_archetype(tmp_path, built_graph):
    # The query chains OPTIONAL MATCHes, so a deck with a macro but no archetype
    # comes back as a bound pilot and macro with a null archetype column. That
    # null row must read as "this pilot plays control, unclassified" rather than
    # as no play at all: the macro tier is still an answer even where the
    # archetype tier is empty.
    _write_snapshot(
        tmp_path,
        [{"id": "d1", "tag": None, "norm": 0.0, "macro": "control", "event": "E1", "m": ["a"]}],
        ["a"],
    )
    conn = built_graph(tmp_path, tmp_path)

    sub = pilot_affinity_subgraph(conn, "p")

    assert [(n.id, n.kind, n.weight) for n in sub.nodes] == [
        ("pilot:p", "Pilot", None),
        ("macro:control", "Macro", 1),
    ]
    assert [(e.source, e.target, e.events) for e in sub.edges] == [
        ("pilot:p", "macro:control", 1),
    ]


def test_pilot_affinity_of_a_pilot_with_no_decks_is_the_pilot_alone(tmp_path, snapshot_dir):
    # The far end of the same chain: every OPTIONAL MATCH binds null, so only the
    # opening MATCH has anything. The pilot is known and answers with an empty
    # affinity, which is a different claim from the unknown pilot below (who
    # yields nothing at all) and must not collapse into it.
    #
    # The lone test that plants a node rather than building one, so it opens its
    # own writer instead of taking the read-only `built_graph`.
    artifact = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), artifact)

    with open_for_writing(artifact) as conn:
        conn.execute(
            "CREATE (:Pilot {pilot: 'ghost', displayName: 'Ghost', lowConfidence: false})"
        )

        sub = pilot_affinity_subgraph(conn, "ghost")

    assert [(n.id, n.label, n.kind) for n in sub.nodes] == [("pilot:ghost", "Ghost", "Pilot")]
    assert sub.edges == []



def test_the_luck_count_is_the_same_number_on_every_call(live_graph):
    # The FAQ promises that a rebuild of the same graph reports the same numbers, so a
    # number that moved means the evidence moved. This one is a sum over ~1700 floats,
    # float addition is not associative, and Ladybug hands the same rows back in a
    # different order between calls, so the total drifted in its last digits until the
    # terms were put in an order of their own. Needs the live graph: the sum has to be
    # long enough for the order to matter, which no fixture of a few decks is.
    #
    # A weak guard, and deliberately kept anyway. Under pytest Ladybug happened to
    # return these rows in a stable order, so this passed while the bug was live and
    # only a run outside the harness (six distinct totals in six calls) exposed it. It
    # holds the promise where the suite can see it; what actually fixes it is summing
    # in sorted order, which does not depend on the runtime being kind.
    counts = {hidden_gems_subgraph(live_graph).expected_by_luck for _ in range(4)}

    assert len(counts) == 1


def test_pilot_affinity_draws_the_deck_rows_own_record_and_nests_parts_in_wholes(live_graph):
    # Two claims over every pilot on the live artifact, checked in one pass
    # because each needs the same 1086 subgraphs.
    #
    # First, the drawing is the record: the parent macros of every archetype node
    # equal the (pilot, primary archetype, macro) triples read straight off the
    # deck rows (ADR 0023), with no pilot_affinity_subgraph in the derivation.
    # The expectation comes from the rows rather than from a count pinned off a
    # previous run, because a pinned count has to be regraded whenever the
    # behaviour intentionally changes, and regrading it by running the changed
    # code turns the guard into a transcript of whatever the code now does. A
    # query that re-widened to every tag, or emptied, now disagrees with the
    # graph's own rows rather than with a number nobody can re-derive.
    #
    # Second, nesting: an archetype under a single macro is never drawn larger
    # than it. Both tiers count distinct events, and the archetype's events under
    # its only parent are a subset of that parent's, so a violation means one
    # tier changed unit. A later change that sized either tier by decks rather
    # than events would break it silently: an archetype counted by decks against
    # a macro counted by events inverts the moment a pilot enters two variants on
    # one day, and nothing would raise. On this artifact the nesting case is 2349
    # of the 2543 drawn archetype nodes; the rest sit under two or more macros
    # and are legitimately larger than any one of them.
    expected = defaultdict(set)
    for pilot, tag, macro in rows(live_graph.execute(
        """MATCH (p:Pilot)<-[:PILOTED_BY]-(d:Deck)-[:HAS_MACRO]->(m:`Macro`),
                 (d)-[:HAS_ARCHETYPE {isPrimary: true}]->(a:Archetype)
           RETURN p.pilot, a.tag, m.name"""
    )):
        expected[(pilot, tag)].add(macro)
    # The nesting case has to exist for the invariant to be checked over
    # anything: an expectation this derivation left empty would let both claims
    # pass vacuously.
    assert any(len(macros) == 1 for macros in expected.values())

    drawn = {}
    inversions = []
    for row in rows(live_graph.execute("MATCH (p:Pilot) RETURN p.pilot")):
        sub = pilot_affinity_subgraph(live_graph, row[0])
        weights = {n.id: n.weight for n in sub.nodes}
        parents = defaultdict(set)
        for edge in sub.edges:
            if edge.source.startswith("macro:"):
                parents[edge.target].add(edge.source)
        for archetype, macros in parents.items():
            drawn[(row[0], archetype.removeprefix("arch:"))] = {
                m.removeprefix("macro:") for m in macros
            }
            if len(macros) == 1:
                (macro,) = macros
                if weights[archetype] > weights[macro]:
                    inversions.append((row[0], archetype, macro))

    assert drawn == expected
    assert inversions == []


def test_pilot_affinity_of_unknown_pilot_is_empty(tmp_path, snapshot_dir, built_graph):
    conn = built_graph(tmp_path, snapshot_dir)

    sub = pilot_affinity_subgraph(conn, "Nobody At All")

    assert sub.nodes == []
    assert sub.edges == []


def test_run_query_dispatches_a_spec_to_its_query(tmp_path, snapshot_dir, built_graph):
    conn = built_graph(tmp_path, snapshot_dir)

    # The single spec -> subgraph seam routes to the right query function; the
    # oracle is the function called directly with the spec's parameters.
    assert run_query(conn, PilotNeighbourhood("Jordan C")) == pilot_subgraph(
        conn, "Jordan C"
    )
    # The optional second pilot rides the same spec through to the query.
    assert run_query(
        conn, PilotNeighbourhood("Jordan C", "AmberTealViper")
    ) == pilot_subgraph(conn, "Jordan C", "AmberTealViper")


def test_run_query_passes_spec_parameters_through(tmp_path, snapshot_dir, built_graph):
    conn = built_graph(tmp_path, snapshot_dir)
    decks_by_card = _decks_by_card(snapshot_dir)
    canon = next(c for c, ds in decks_by_card.items() if ds == JORDAN_DECKS)

    assert run_query(conn, CardCooccurrence(canon, top_n=25)) == (
        card_cooccurrence_subgraph(conn, canon, top_n=25)
    )
    # The parameterless gem spec reaches its query function too.
    assert run_query(conn, HiddenGems()) == hidden_gems_subgraph(conn)


# Five macros that adopt the seed card at exactly the same rate, so nothing but
# the tie-break decides their order. Named in alphabetical order.
_TIED_MACROS = ["aggro", "combo", "control", "midrange", "ramp"]


def _tied_macro_snapshot(path):
    """A snapshot where five macros each run the seed card in 1 of their 2 decks."""
    path.mkdir(parents=True)
    decks = []
    for macro in _TIED_MACROS:
        for i in (0, 1):
            decks.append({
                "deckId": f"{macro}{i}", "name": f"{macro}{i}", "deckName": "n",
                "pilot": f"p{macro}{i}", "event": "NYE", "eventId": "evt_1",
                "eventType": "Tournament", "eventSize": _FIELD_SIZE, "placement": 1, "placementNorm": 0.5,
                "createdAt": "2026-01-01T00:00:00+00:00", "colour": "colour:U",
                "macro": f"macro:{macro}", "engineTags": [f"engine:{macro}_arch"],
                "engineTagLabels": {f"engine:{macro}_arch": macro.title()},
                "primaryTag": f"engine:{macro}_arch",
                "primaryTagWeights": {f"engine:{macro}_arch": 1},
            })
    (path / "decks.json").write_text(json.dumps(decks))
    (path / "cards_index.json").write_text(json.dumps({
        "v": 2,
        "cards": [
            {"canon": "seed", "name": "Seed", "type": "Instants", "manaCost": None,
             "manaValue": 1.0, "reserved": False, "points": 0,
             "pointsCompanion": 0},
            {"canon": "filler", "name": "Filler", "type": "Lands", "manaCost": None,
             "manaValue": 0.0, "reserved": False, "points": 0,
             "pointsCompanion": 0},
        ],
        # Only the first deck of each macro runs the seed, so every macro adopts
        # it at 1 of 2 decks and all five percentages are identical.
        "decks": {
            f"{macro}{i}": {"m": [0, 1] if i == 0 else [1], "s": []}
            for macro in _TIED_MACROS
            for i in (0, 1)
        },
    }))


def _tied_archetype_snapshot(path):
    """Three archetype tags sharing one display name, one deck each running the seed.

    Registered in reverse tag order, so a sort that fails to break the tie keeps
    the order the rows arrived in rather than the order the tags demand.
    """
    path.mkdir(parents=True)
    tags = ["c_tag", "b_tag", "a_tag"]
    decks = []
    for tag in tags:
        for i in (0, 1):
            decks.append({
                "deckId": f"{tag}{i}", "name": f"{tag}{i}", "deckName": "n",
                "pilot": f"p{tag}{i}", "event": "NYE", "eventId": "evt_1",
                "eventType": "Tournament", "eventSize": _FIELD_SIZE, "placement": 1, "placementNorm": 0.5,
                "createdAt": "2026-01-01T00:00:00+00:00", "colour": "colour:U",
                "macro": "macro:aggro", "engineTags": [f"engine:{tag}"],
                # One shared display name, so the name tie-break cannot separate them.
                "engineTagLabels": {f"engine:{tag}": "Shared"},
                "primaryTag": f"engine:{tag}",
                "primaryTagWeights": {f"engine:{tag}": 1},
            })
    (path / "decks.json").write_text(json.dumps(decks))
    (path / "cards_index.json").write_text(json.dumps({
        "v": 2,
        "cards": [
            {"canon": "seed", "name": "Seed", "type": "Instants", "manaCost": None,
             "manaValue": 1.0, "reserved": False, "points": 0,
             "pointsCompanion": 0},
            {"canon": "filler", "name": "Filler", "type": "Lands", "manaCost": None,
             "manaValue": 0.0, "reserved": False, "points": 0,
             "pointsCompanion": 0},
        ],
        "decks": {
            f"{tag}{i}": {"m": [0, 1] if i == 0 else [1], "s": []}
            for tag in tags for i in (0, 1)
        },
    }))


def test_macros_tied_on_adoption_are_ordered_by_name(tmp_path, built_graph):
    # Two strategies can adopt a card at rates that round to the same percent, and
    # roughly a quarter of the real card catalogue has such a tie. Without a
    # tie-break the order falls out of an unordered set, so the same query on the
    # same graph answers differently between runs and the view reshuffles for no
    # reason. Ties resolve on name, as they already do for archetypes just below.
    _tied_macro_snapshot(tmp_path / "snap")
    conn = built_graph(tmp_path, tmp_path / "snap")

    subgraph = card_usage_subgraph(conn, "seed")

    macros = [n.label for n in subgraph.nodes if n.kind == "Macro"]
    assert macros == _TIED_MACROS


def test_archetypes_tied_on_adoption_and_name_are_ordered_by_tag(tmp_path, built_graph):
    # Two tags can carry the same display name (which is why the app suffixes
    # duplicate labels at all). Tied on adoption, size and name, only the tag is
    # left to separate them, and without it the order falls out of a Cypher query
    # with no ORDER BY, so it would move with the engine rather than the data.
    _tied_archetype_snapshot(tmp_path / "snap")
    conn = built_graph(tmp_path, tmp_path / "snap")

    subgraph = card_usage_subgraph(conn, "seed")

    archetypes = [n.id for n in subgraph.nodes if n.kind == "Archetype"]
    assert archetypes == ["arch:a_tag", "arch:b_tag", "arch:c_tag"]


def test_coverage_counts_the_graph_the_snapshot_built(tmp_path, snapshot_dir, built_graph):
    conn = built_graph(tmp_path, snapshot_dir)

    cov = coverage(conn)

    # Independent oracle: the counts read off the raw snapshot files, not the
    # graph the query walks. Pilots is the resolved count (two people across the
    # three registrations), which conftest documents for this fixture.
    decks = json.loads((snapshot_dir / "decks.json").read_text())
    index = json.loads((snapshot_dir / "cards_index.json").read_text())
    years = {int(d["createdAt"][:4]) for d in decks}
    assert cov.events == len({d["event"] for d in decks})  # 2
    assert cov.decks == len(decks)                          # 3
    assert cov.cards == len(index["cards"])                 # 121
    assert cov.pilots == 2
    assert (cov.first_year, cov.last_year) == (min(years), max(years))  # 2025, 2025


# The two companions and the source's own prices for them, plus an ordinary
# pointed card to show the surcharge is not a second price list (issue #143).
_COMPANION_POINTS = {
    "lurrus of the dream-den": (0, 3),
    "lutri, the spellchaser": (0, 3),
    "force of will": (1, 0),
    "sol ring": (3, 0),
}


def test_deck_points_charge_a_companion_by_the_board_it_sits_in(tmp_path, built_graph):
    # The 7PH cost of a card depends on where it is played: a companion in the main
    # board is an ordinary creature at its default 0, and the same card in the
    # sideboard is a companion at 3 (issue #143). Everything else costs what it
    # costs on either board, since sideboard cards count toward the total.
    _write_snapshot(
        tmp_path,
        [
            # Lurrus main-board: an ordinary creature, so only the Force of Will
            # is paid for.
            {"id": "maindeck", "tag": "a", "norm": 0.1,
             "m": ["lurrus of the dream-den", "force of will"], "s": []},
            # The same 75 with Lurrus moved to the sideboard: 1 + the 3 point
            # companion charge.
            {"id": "companion", "tag": "a", "norm": 0.1,
             "m": ["force of will"], "s": ["lurrus of the dream-den"]},
            # A pointed card in the sideboard is charged its deck cost, not the
            # companion cost every card carries a zero of.
            {"id": "sideboarded", "tag": "a", "norm": 0.1,
             "m": ["force of will"], "s": ["sol ring"]},
            # Nothing pointed at all: the format's ordinary deck.
            {"id": "free", "tag": "a", "norm": 0.1, "m": ["island"], "s": []},
        ],
        ["lurrus of the dream-den", "lutri, the spellchaser", "force of will",
         "sol ring", "island"],
        points=_COMPANION_POINTS,
    )
    conn = built_graph(tmp_path, tmp_path)

    assert deck_points(conn) == {
        "maindeck": 1, "companion": 4, "sideboarded": 4, "free": 0,
    }


def test_a_deck_holding_both_companions_is_charged_one_surcharge(tmp_path, built_graph):
    # A deck names one companion, so the charge is per deck and not per card. Two
    # decks in the record sideboard both cards, and the source charges 3 for one of
    # them and 0 for the other, never 6 (issue #143).
    _write_snapshot(
        tmp_path,
        [{"id": "both", "tag": "a", "norm": 0.1, "m": ["force of will"],
          "s": ["lurrus of the dream-den", "lutri, the spellchaser"]}],
        ["lurrus of the dream-den", "lutri, the spellchaser", "force of will"],
        points=_COMPANION_POINTS,
    )
    conn = built_graph(tmp_path, tmp_path)

    assert deck_points(conn) == {"both": 4}


def test_a_deck_with_no_cards_costs_nothing_rather_than_vanishing(tmp_path, built_graph):
    # The source's two files can disagree: a deck in `decks.json` that
    # `cards_index.json` lists no boards for reaches the graph with no CONTAINS
    # edges at all. It costs 0 and is still counted, because a reader taking the
    # share of decks over the points limit divides by this population, and a deck
    # missing from the mapping would quietly shrink the denominator.
    _write_snapshot(
        tmp_path,
        [{"id": "empty", "tag": "a", "norm": 0.1, "m": [], "s": []},
         {"id": "priced", "tag": "a", "norm": 0.1, "m": ["force of will"], "s": []}],
        ["force of will"],
        points=_COMPANION_POINTS,
    )
    conn = built_graph(tmp_path, tmp_path)

    assert deck_points(conn) == {"empty": 0, "priced": 1}
    # Ints, not the Decimals the engine sums to: these totals are reported and
    # serialised, and a Decimal compares equal to an int right up until it is
    # written to JSON.
    assert {type(v) for v in deck_points(conn).values()} == {int}


def test_a_sideboarded_companion_pays_the_companion_cost_not_both(tmp_path, built_graph):
    # `pointsCompanion` is what the card costs *as* a companion, so it replaces the
    # deck cost rather than stacking on it. No card is priced in both contexts today
    # (both companions are free in the deck, which is why the two readings agree on
    # every deck in the record and the 98.54% measurement cannot tell them apart),
    # so this fixes the reading a points revision would otherwise decide silently:
    # a companion priced at 1 in the deck pays 3 as a companion, not 4 (issue #143).
    _write_snapshot(
        tmp_path,
        [{"id": "companion", "tag": "a", "norm": 0.1, "m": ["force of will"],
          "s": ["lurrus of the dream-den"]},
         # The same card in the main board is an ordinary creature and pays the
         # deck cost the revision gave it.
         {"id": "maindeck", "tag": "a", "norm": 0.1,
          "m": ["force of will", "lurrus of the dream-den"], "s": []}],
        ["lurrus of the dream-den", "force of will"],
        points={"lurrus of the dream-den": (1, 3), "force of will": (1, 0)},
    )
    conn = built_graph(tmp_path, tmp_path)

    assert deck_points(conn) == {"companion": 4, "maindeck": 2}


def test_a_card_listed_on_both_boards_is_charged_once(tmp_path, built_graph):
    # The format is singleton, so a card the source lists in both the main board and
    # the sideboard is one card listed twice, not two copies. 8 such listings sit
    # across 7 decks in the record (`forest`, `island`, `get out`, `dismember`,
    # `brazen borrower // petty theft`), all of them free, so a per-edge sum reads
    # right today and would read a pointed one 1 to 5 points high (issue #143).
    _write_snapshot(
        tmp_path,
        [{"id": "listed twice", "tag": "a", "norm": 0.1, "m": ["sol ring"],
          "s": ["sol ring"]}],
        ["sol ring"],
        points=_COMPANION_POINTS,
    )
    conn = built_graph(tmp_path, tmp_path)

    assert deck_points(conn) == {"listed twice": 3}
