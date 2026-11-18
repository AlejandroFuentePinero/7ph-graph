"""What a pilot's or a card's page states in numbers (issue #329).

The page head's tiles and the tables under the graphs, as the pure functions the app
builds them with. Each is read off the same query result the page's graph or chart
draws, so the two cannot disagree; the tests here hold each figure to the fixture's
own files, and on the real record to a second, independent reading.
"""

import html
import json
import re
from datetime import datetime, timedelta

import pytest

from graph7ph import numfmt
from graph7ph.app import (
    Pages,
    _archetype_card,
    _archetype_pair_tiles,
    _example_archetypes,
    _archetype_tiles,
    _archetype_timeline_figure,
    _landscape_figure,
    _landscape_top,
    _card_tiles,
    _drawn_card,
    _graph_list,
    _node_facts,
    _pair_tiles,
    _pair_years_card,
    _pilot_descriptor,
    _pilot_tiles,
    _record_card,
    _record_table,
    _share_list,
    _trend_figure,
    build_app,
)
from graph7ph.build import build_graph
from graph7ph.db import rows
from graph7ph.models import load_snapshot
from graph7ph.query import (
    DeckRecord,
    Edge,
    Node,
    PilotDeck,
    Subgraph,
    archetypes_on_record,
    card_cooccurrence_subgraph,
    card_deck_counts,
    card_usage_subgraph,
    coverage,
    deck_records,
    pilot_affinity_subgraph,
    pilot_record,
    pilot_subgraph,
)
from graph7ph.trends import (
    AdoptionCell,
    ArchetypeTimelinePoint,
    LandscapeCell,
    NotEnoughHistory,
    Series,
    SeriesCell,
    archetype_events_played,
    archetype_landscape,
    archetype_timeline,
    archetypes_with_history,
    card_adoption_over_time,
    latest_deck_year,
    meta_share_over_time,
    most_played,
    pair_adoption_over_time,
)


def _facts(tiles) -> list[tuple]:
    return [(tile.label, tile.value, tile.note) for tile in tiles]


def test_a_pilots_tiles_are_four_neutral_facts(tmp_path, snapshot_dir, built_graph):
    # The fixture's Jordan C: Grixis at two events, 12th at one and 5th at the other.
    # Four facts and no judgement: no tile states an average or a mean finish, which
    # stays in the performance chart's caption.
    conn = built_graph(tmp_path, snapshot_dir)

    tiles = _pilot_tiles(pilot_subgraph(conn, "Jordan C"),
                         pilot_affinity_subgraph(conn, "Jordan C"),
                         pilot_record(conn, "Jordan C"), coverage(conn).events)

    # The fixture holds two events, PogNov25 and CFWAT25, and Jordan C played both.
    assert _facts(tiles) == [
        ("Events played", "2 (100%)", "of 2 on record"),
        ("Archetypes played", "1", "in 1 broad class"),
        ("Most-played archetype", "Grixis", "at 2 events"),
        ("Best finish", "5th", "once"),
    ]


def _deck(**changed) -> PilotDeck:
    base = dict(event="SSWam", date=datetime(2024, 3, 12), archetype="Storm",
                placement=3, placement_imputed=None, field_size=24,
                field_imputed=None, deck_id="d1")
    return PilotDeck(**{**base, **changed})


def _neighbourhood(events: int) -> Subgraph:
    """A pilot's neighbourhood holding ``events`` events."""
    return Subgraph(
        nodes=[Node("pilot:p", "P", "Pilot")]
        + [Node(f"event:e{i}", f"E{i}", "Event") for i in range(events)],
        edges=[],
    )


def _affinity(*archetypes: tuple[str, int], macros: int = 1) -> Subgraph:
    """A pilot's affinity graph: ``(archetype, events)`` pairs, most events first."""
    nodes = [Node("pilot:p", "P", "Pilot")]
    nodes += [Node(f"macro:m{i}", f"m{i}", "Macro", weight=1) for i in range(macros)]
    nodes += [Node(f"arch:{name}", name, "Archetype", weight=events)
              for name, events in archetypes]
    return Subgraph(nodes=nodes, edges=[])


def test_the_best_finish_is_the_lowest_place_and_says_how_often():
    mark = numfmt.IMPUTED_MARK

    def best(*finishes: tuple[int | None, str | None]) -> tuple:
        record = [_deck(placement=place, placement_imputed=rule, deck_id=f"d{i}")
                  for i, (place, rule) in enumerate(finishes)]
        return _facts(_pilot_tiles(
            _neighbourhood(len(record)), _affinity(("Grixis", 1)), record, 10))[3]

    # 2nd beats 11th and 21st: places compare as numbers, not as text.
    assert best((11, None), (2, None), (21, None), (2, None)) == (
        "Best finish", "2nd", "2 times")
    # A place this project worked out carries the app's one mark, unless the source
    # also recorded that place for the pilot somewhere else.
    assert best((3, "title"), (9, None)) == ("Best finish", f"3rd{mark}", "once")
    assert best((3, "title"), (3, None)) == ("Best finish", "3rd", "2 times")
    # 24 decks on the record have no finish at all.
    assert best((None, "none"), (None, "none")) == (
        "Best finish", "None", "No finish on record")


def test_the_most_played_archetype_says_when_others_are_level_with_it():
    tiles = _pilot_tiles(
        _neighbourhood(3),
        _affinity(("Grixis", 1), ("Storm", 1), ("Lands", 1), macros=2),
        [_deck()], 10,
    )

    assert _facts(tiles)[1:3] == [
        ("Archetypes played", "3", "in 2 broad classes"),
        ("Most-played archetype", "Grixis", "at 1 event, level with 2 others"),
    ]


def _table_rows(markup: str) -> list[list[str]]:
    """A table's body rows, each as its cells' markup."""
    body = markup.split("<tbody>")[1]
    return [re.findall(r"<t[dh](?: [^>]*)?>(.*?)</t[dh]>", row)
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body)]


def test_a_pilots_record_is_a_row_per_deck(tmp_path, snapshot_dir, built_graph):
    # Read off the fixture's decks.json: Jordan C was 5th of 19 at CFWAT25, registered
    # 6 December 2025, and 12th of 31 at PogNov25, whose first deck was registered on
    # the 1st. Grixis both times. Newest first.
    record = pilot_record(built_graph(tmp_path, snapshot_dir), "Jordan C")

    table = _record_table(record)

    header = re.findall(r"<th(?: [^>]*)?>(.*?)</th>", table.split("<tbody>")[0])
    assert header == ["Event", "Registered", "Archetype", "Finish", "Decklist"]
    newest, oldest = _table_rows(table)
    assert newest[:4] == ["CFWAT25<span class='sub'>6 Dec 2025</span>",
                          "6 Dec 2025", "Grixis", "5 / 19"]
    assert oldest[:4] == ["PogNov25<span class='sub'>1 Dec 2025</span>",
                          "1 Dec 2025", "Grixis", "12 / 31"]
    assert "href='https://moxfield.com/decks/BsegXnsDsEWxh-vNbUrn0w'" in newest[4]
    assert "href='https://moxfield.com/decks/pkUbzmgN3UeqaWdYQYRgRg'" in oldest[4]


def test_a_record_row_marks_what_the_project_decided_and_leaves_blank_what_nobody_knows():
    mark = numfmt.IMPUTED_MARK
    finish = lambda **changed: _table_rows(_record_table([_deck(**changed)]))[0][3]  # noqa: E731

    assert finish() == "3 / 24"
    # Each number is marked on its own rule, as on the head-to-head's hover (#166).
    assert finish(placement_imputed="title") == f"3{mark} / 24"
    assert finish(field_imputed="floor") == f"3 / 24{mark}"
    # No finish on record, or no archetype: an empty cell, not a guess.
    assert finish(placement=None, placement_imputed="none") == ""
    assert _table_rows(_record_table([_deck(archetype=None)]))[0][2] == ""


def test_the_record_card_says_how_many_decks_and_explains_a_mark_only_when_one_shows():
    plain = _record_card([_deck(), _deck(event="CFWAT", deck_id="d2")])
    assert "Record" in plain
    assert "<span class='figure'>2</span> decks, newest first by when" in plain
    assert numfmt.IMPUTED_MARK not in re.sub(r"<[^>]+>", "", plain.split("<table")[0])

    marked = _record_card([_deck(field_imputed="floor")])
    assert "<span class='figure'>1</span> deck, newest first by when" in marked
    assert "this project worked out" in marked.split("<table")[0]

    # A pilot the record holds no deck for gets a line, not an empty table.
    assert "<table" not in _record_card([]) and "No decks on record." in _record_card([])


def test_a_pilots_descriptor_says_the_years_they_are_on_record():
    assert _pilot_descriptor([_deck()]) == "Pilot, on record in 2024."
    assert _pilot_descriptor(
        [_deck(date=datetime(2026, 1, 9)), _deck(date=datetime(2023, 5, 1))]
    ) == "Pilot, on record from 2023 to 2026."


def _every_pilot(conn):
    return [key for (key,) in rows(conn.execute("MATCH (p:Pilot) RETURN p.pilot"))]


def _archetype_events(conn) -> dict[str, dict[str, tuple[str, set[str]]]]:
    """Per pilot, each archetype they played as a deck's primary one: its name and
    the events they played it at. Off the graph's raw rows, not the affinity query."""
    played: dict = {}
    for pilot, tag, name, event in rows(conn.execute(
        """MATCH (p:Pilot)<-[:PILOTED_BY]-(d:Deck)-[:HAS_ARCHETYPE {isPrimary: true}]->(a:Archetype),
                 (d)-[:PLAYED_AT]->(e:Event)
           RETURN p.pilot, a.tag, a.name, e.event"""
    )):
        played.setdefault(pilot, {}).setdefault(tag, (name, set()))[1].add(event)
    return played


def _place(label: str) -> int:
    """A drawn placement's place: ``12th*`` -> 12."""
    return int(label.rstrip(numfmt.IMPUTED_MARK)[:-2])


def _events_on_record(conn) -> int:
    return next(rows(conn.execute("MATCH (e:Event) RETURN count(e)")))[0]


def _assert_the_page_agrees(conn, pilot: str, played: dict, on_record: int) -> None:
    """The tiles, the record table's rows and the two graphs, for one pilot."""
    neighbourhood = pilot_subgraph(conn, pilot)
    affinity = pilot_affinity_subgraph(conn, pilot)
    record = pilot_record(conn, pilot)
    events, archetypes, most, best = _pilot_tiles(neighbourhood, affinity, record,
                                                  on_record)

    # The table against the neighbourhood: the same events, the same decks, and the
    # same place on each deck's placement node.
    drawn = {node.id: node.label for node in neighbourhood.nodes}
    assert {f"event:{r.event}" for r in record} == {
        key for key in drawn if key.startswith("event:")}
    assert {f"deck:{r.deck_id}" for r in record} == {
        key for key in drawn if key.startswith("deck:")}
    for r in record:
        label = drawn.get(f"placement:{r.deck_id}")
        assert (label is None) == (r.placement is None), (pilot, r)
        if label is not None:
            assert _place(label) == r.placement, (pilot, r)
            assert label.endswith(numfmt.IMPUTED_MARK) == (r.placement_imputed is not None)

    # The tiles. Events against the table, a second query's reading of the record.
    attended = len({r.event for r in record})
    assert events.value == f"{attended:,} ({numfmt.share(attended / on_record)})", pilot
    assert events.note == f"of {on_record:,} on record", pilot
    # The two archetype tiles against the graph's raw rows.
    counts = sorted((len(at) for _, at in played.values()), reverse=True)
    assert archetypes.value == f"{len(counts):,}", pilot
    if counts:
        level = counts.count(counts[0]) - 1
        assert most.value in {name for name, at in played.values()
                              if len(at) == counts[0]}, pilot
        assert most.note == (
            f"at {counts[0]} event" + ("" if counts[0] == 1 else "s")
            + (f", level with {level} other" + ("" if level == 1 else "s") if level else "")
        ), pilot
    else:
        assert most.value == "None", pilot
    # The best finish against the placements the neighbourhood draws.
    finishes = [label for key, label in drawn.items() if key.startswith("placement:")]
    if finishes:
        lowest = min(map(_place, finishes))
        at_best = [label for label in finishes if _place(label) == lowest]
        assert best.value == min(at_best, key=len), pilot  # unmarked if any is
        assert best.note == ("once" if len(at_best) == 1 else f"{len(at_best)} times"), pilot
    else:
        assert best.value == "None", pilot


def test_a_pilots_tiles_table_and_graphs_agree(tmp_path, snapshot_dir, built_graph):
    conn = built_graph(tmp_path, snapshot_dir)
    played = _archetype_events(conn)
    on_record = _events_on_record(conn)
    for pilot in _every_pilot(conn):
        _assert_the_page_agrees(conn, pilot, played.get(pilot, {}), on_record)


def test_every_pilots_tiles_table_and_graphs_agree_on_the_real_record(live_graph):
    # Every pilot on the record, not a sample: the claim is about the page, and any
    # pilot's page can be opened.
    pilots = _every_pilot(live_graph)
    assert len(pilots) > 1000
    played = _archetype_events(live_graph)
    on_record = _events_on_record(live_graph)
    for pilot in pilots:
        _assert_the_page_agrees(live_graph, pilot, played.get(pilot, {}), on_record)


def test_a_cards_tiles_are_its_adoption_its_change_its_decks_and_its_archetypes(
    tmp_path, snapshot_dir, built_graph
):
    # The fixture's Daze: in both Grixis decks and not the Storm one, so 2 of the 3
    # decks of 2025, the record's only year.
    conn = built_graph(tmp_path, snapshot_dir)

    # The record's archetypes are Grixis and Storm, each the primary tag of a deck.
    assert archetypes_on_record(conn) == 2

    tiles = _card_tiles(card_usage_subgraph(conn, "daze"),
                        card_adoption_over_time(conn, "daze"), archetypes_on_record(conn))

    assert _facts(tiles) == [
        ("Adoption in 2025", "66.67%", "2 / 3 decks"),
        ("Change from last year", "n/a", "No earlier year on record"),
        ("Decks", "2 (66.67%)", "of 3 on record"),
        ("Archetypes running it", "1 (50%)", "of 2 on record, in 1 broad class"),
    ]


def _adoption(*year_count_total) -> Series:
    return Series(cells=[AdoptionCell(year, count, count / total, total)
                         for year, count, total in year_count_total])


_A_CARD = Subgraph(nodes=[Node("card:x", "X (5% of meta)", "Card", decks=50, total_decks=1000)],
                   edges=[])


def test_a_cards_change_is_the_latest_years_share_less_the_year_befores():
    tiles = _card_tiles(_A_CARD, _adoption((2024, 1, 100), (2025, 3181, 10000),
                                           (2026, 3412, 10000)), 40)

    assert _facts(tiles)[:2] == [
        ("Adoption in 2026", "34.12%", "3,412 / 10,000 decks"),
        ("Change from 2025", "+2.31%", "from 31.81%"),
    ]


def test_a_cards_archetypes_are_listed_by_share_under_their_broad_class(
    tmp_path, snapshot_dir, built_graph
):
    # The fixture's Brainstorm is in all three decks: the one Storm deck (combo) and
    # both Grixis decks (tempo). Each broad class heads its archetypes and states its
    # own share, as the usage graph's two tiers do.
    usage = card_usage_subgraph(built_graph(tmp_path, snapshot_dir), "brainstorm")

    card = _archetype_card(usage)

    header = re.findall(r"<th(?: [^>]*)?>(.*?)</th>", card.split("<tbody>")[0])
    assert header == ["Archetype", "Decks running it", "Share"]
    # Both classes run it in every deck, so the larger one leads: tempo's two decks
    # ahead of combo's one.
    assert _table_rows(card) == [
        ["Tempo", "2 / 2", "100%"],
        ["Grixis", "2 / 2", "100%"],
        ["Combo", "1 / 1", "100%"],
        ["Storm", "1 / 1", "100%"],
    ]
    assert card.count("class='group'") == 2  # the two broad classes head their rows
    assert "<span class='figure'>2</span> archetypes run it" in card


def test_an_archetype_row_is_the_usage_graphs_own_edge_written_as_a_share():
    # The two numbers an edge carries, and their share in the app's one form for a
    # share (§4): 9 of 120 is 7.5%, where the edge's label rounds it to 8%, and 1 of
    # 380 is 0.26%, where the label gives up and says "<1%".
    usage = Subgraph(
        nodes=[Node("card:x", "X (1% of meta)", "Card", decks=10, total_decks=1000),
               Node("macro:tempo", "tempo", "Macro"),
               Node("arch:grixis", "Grixis", "Archetype"),
               Node("arch:delver", "Delver <UR>", "Archetype")],
        edges=[Edge("card:x", "macro:tempo", "2%", decks=10, total_decks=500),
               Edge("macro:tempo", "arch:grixis", "8%", decks=9, total_decks=120),
               Edge("macro:tempo", "arch:delver", "<1%", decks=1, total_decks=380)],
    )

    assert _table_rows(_archetype_card(usage)) == [
        ["Tempo", "10 / 500", "2%"],
        ["Grixis", "9 / 120", "7.5%"],
        ["Delver &lt;UR&gt;", "1 / 380", "0.26%"],
    ]


def test_archetypes_are_listed_by_their_exact_share_not_the_graphs_rounded_order():
    # The usage graph orders on the whole percent and then on the larger archetype,
    # so it holds 16.9% (a 1,000-deck archetype) ahead of 17.2% (a 250-deck one):
    # both round to 17. Nothing in the picture shows that. A column of exact shares
    # would, so the table sorts on the share it prints.
    usage = Subgraph(
        nodes=[Node("card:x", "X", "Card", decks=212, total_decks=5000),
               Node("macro:tempo", "tempo", "Macro"),
               Node("arch:big", "Big", "Archetype"),
               Node("arch:small", "Small", "Archetype")],
        edges=[Edge("card:x", "macro:tempo", "17%", decks=212, total_decks=1250),
               Edge("macro:tempo", "arch:big", "17%", decks=169, total_decks=1000),
               Edge("macro:tempo", "arch:small", "17%", decks=43, total_decks=250)],
    )

    assert [row[0] + " " + row[2] for row in _table_rows(_archetype_card(usage))[1:]] == [
        "Small 17.2%", "Big 16.9%"]


def test_a_card_no_archetype_runs_gets_a_line_not_an_empty_table():
    # A card filtered to a board no deck runs it in: the usage graph is the card alone.
    card = _archetype_card(Subgraph(
        nodes=[Node("card:x", "X (0% of meta)", "Card", decks=0, total_decks=1000)],
        edges=[],
    ))

    assert "<table" not in card and "No archetype runs it" in card


def _package(*partners: tuple[str, int], decks: int = 1560) -> Subgraph:
    """One card's co-occurrence graph: ``(partner, shared decks)``, strongest first."""
    nodes = [Node("card:seed", "Seed", "Card", group="seed:seed")]
    edges = []
    for name, shared in partners:
        nodes.append(Node(f"card:{name}", name, "Card", group="cooccur"))
        edges.append(Edge("card:seed", f"card:{name}", f"{round(100 * shared / decks)}%",
                          decks=shared, total_decks=decks))
    return Subgraph(nodes=nodes, edges=edges)


def _played_with(cooccurrence: Subgraph) -> str:
    """The co-occurrence panel's list, whose title is always the same."""
    title, body = _graph_list("card_cooccurrence", cooccurrence, {})
    assert title == "Played with"
    return body


def _named_rows(markup: str) -> list[str]:
    """The node each body row of a table names, in row order."""
    return [html.unescape(node) for node in re.findall(
        r"<tr data-node='([^']*)'>", markup.split("<tbody>")[1])]


def test_the_cards_a_card_is_played_with_are_ranked_with_the_rates_the_edges_carry():
    # The list sat in a card of its own under the plots, a screen away from the graph
    # it restates. It is the graph's panel now (issue #330), every card the graph
    # draws and not a first ten, in the graph's own order, so the row's place is its
    # rank and it needs no column for one.
    partners = [(f"Card {i}", 1500 - 100 * i) for i in range(12)]
    card = _played_with(_package(*partners))

    header = re.findall(r"<th(?: [^>]*)?>(.*?)</th>", card.split("<tbody>")[0])
    assert header == ["Card", "Decks together", "Rate"]
    rows = _table_rows(card)
    assert len(rows) == 12
    assert rows[:2] == [["Card 0", "1,500 / 1,560", "96.15%"],
                        ["Card 1", "1,400 / 1,560", "89.74%"]]
    # Each row names the node it stands for, so a click on it selects that card.
    assert _named_rows(card)[:2] == ["card:Card 0", "card:Card 1"]
    # What a rate is a share of, said once over the table.
    assert "Seed's <span class='figure'>1,560</span> decks" in card
    assert "same board" in card


def test_two_cards_are_listed_with_what_the_decks_running_both_also_run():
    # The two-card view hangs every shared card off one hub, the decks running both,
    # and a rate is a share of those decks. The two cards' own edges to the hub are
    # not rows: they say how much of each card is in the pair, not what goes with it.
    hub = "both:a|b"
    both = Subgraph(
        nodes=[Node("card:a", "A", "Card", group="seed:a"),
               Node("card:b", "B", "Card", group="seed:b"),
               Node(hub, "Both · 312 decks", "Intersection", decks=312),
               Node("card:c", "C", "Card", group="cooccur")],
        edges=[Edge("card:a", hub, "20%", decks=312, total_decks=1560),
               Edge("card:b", hub, "39%", decks=312, total_decks=800),
               Edge(hub, "card:c", "90%", decks=281, total_decks=312)],
    )

    card = _played_with(both)

    assert _table_rows(card) == [["C", "281 / 312", "90.06%"]]
    assert "the <span class='figure'>312</span> decks running both A and B" in card


def test_a_card_with_nothing_beside_it_gets_a_line_not_an_empty_table():
    alone = _played_with(_package())
    assert "<table" not in alone and "No card to list" in alone

    # Two cards no deck runs together: two seeds and no hub.
    apart = _played_with(Subgraph(
        nodes=[Node("card:a", "A", "Card", group="seed:a"),
               Node("card:b", "B", "Card", group="seed:b")], edges=[]))
    assert "<table" not in apart and "No card to list" in apart


def _archetype_rows(card: str) -> list[tuple[str, ...]]:
    """The archetype table's archetype rows, past the lines that head each class."""
    if "<tbody>" not in card:
        return []
    return [tuple(html.unescape(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row))
            for row in re.findall(r"<tr>(.*?)</tr>", card.split("<tbody>")[1])]


def _real_cards(conn) -> list[str]:
    """A spread of the real record's cards: the thirty in the most decks, then every
    fortieth of the rest that any deck runs."""
    by_decks = sorted(card_deck_counts(conn).items(), key=lambda kv: (-kv[1], kv[0]))
    run = [canon for canon, decks in by_decks if decks]
    return run[:30] + run[30::40]


def test_a_cards_tiles_lists_and_pictures_agree_on_the_real_record(live_graph):
    # The card page's figures against readings they are not built from: the Decks
    # tile (the usage query's card node) against the adoption chart's own counts,
    # the archetype tile and every row of the archetype table against the graph's
    # raw rows, and the played-with list's denominator against the Decks tile. Under
    # each board filter, since a filter scopes every one of them.
    conn = live_graph
    totals = dict(rows(conn.execute(
        "MATCH (a:Archetype)<-[:HAS_ARCHETYPE {isPrimary: true}]-(d:Deck) "
        "RETURN a.tag, count(DISTINCT d)")))
    # The whole each count is a share of: every deck, and every archetype that is the
    # primary tag of one (the archetype nodes include a few that never are).
    deck_total = next(rows(conn.execute("MATCH (d:Deck) RETURN count(d)")))[0]
    assert archetypes_on_record(conn) == len(totals)
    cards = _real_cards(conn)
    assert len(cards) > 100

    for canon in cards:
        for board in (None, "Main", "Side"):
            usage = card_usage_subgraph(conn, canon, board)
            adoption = card_adoption_over_time(conn, canon, board)
            _, _, decks, archetypes = _card_tiles(usage, adoption, len(totals))
            where = (canon, board)

            counted = sum(cell.count for cell in adoption.cells)
            assert decks.value == f"{counted:,} ({numfmt.share(counted / deck_total)})", where

            running = list(rows(conn.execute(
                "MATCH (a:Archetype)<-[:HAS_ARCHETYPE {isPrimary: true}]-(d:Deck)"
                "-[c:CONTAINS]->(:Card {canon: $canon}) "
                + ("WHERE c.board = $board " if board else "")
                + "RETURN a.tag, a.name, count(DISTINCT d)",
                {"canon": canon, "board": board} if board else {"canon": canon},
            )))
            assert archetypes.value == (
                f"{len(running):,} ({numfmt.share(len(running) / len(totals))})"), where
            assert sorted(_archetype_rows(_archetype_card(usage))) == sorted(
                (name, numfmt.count_of(n, totals[tag]), numfmt.share(n / totals[tag]))
                for tag, name, n in running
            ), where

        # One card's view has no board: a rate is a share of every deck running it.
        played_with = _played_with(card_cooccurrence_subgraph(conn, canon))
        either = _card_tiles(card_usage_subgraph(conn, canon),
                             card_adoption_over_time(conn, canon), len(totals))[2]
        count = either.value.split(" (")[0]
        assert f"<span class='figure'>{count}</span> decks" in played_with, canon


# --- What a clicked node answers with, in the panel beside its graph (issue #330) ---


def _answers(view: str, subgraph: Subgraph, decks: dict) -> dict:
    """Each node's facts as the panel states them, by node id."""
    facts = _node_facts(view, subgraph, decks)
    assert [f.node for f in facts] == [n.id for n in subgraph.nodes]
    return {f.node: f for f in facts}


def _on_record(conn, subgraph: Subgraph) -> dict:
    """The record of every deck a graph draws, as the app reads it for the panel."""
    return deck_records(conn, [n.id.removeprefix("deck:") for n in subgraph.nodes
                               if n.kind == "Deck"])


def test_a_clicked_deck_answers_with_its_event_finish_date_and_list(
    tmp_path, snapshot_dir, built_graph
):
    # The details line read "Kind: Deck, Name: Grixis". A deck now says what a reader
    # asks next: who played it, where, how it finished and when, with its list one
    # click away. Read off the fixture's decks.json: Jordan C's Grixis, 12th of the
    # 31 at PogNov25, an event first registered on 1 December 2025.
    conn = built_graph(tmp_path, snapshot_dir)
    neighbourhood = pilot_subgraph(conn, "Jordan C")
    deck_id = "pkUbzmgN3UeqaWdYQYRgRg"

    said = _answers("pilot_neighbourhood", neighbourhood, _on_record(conn, neighbourhood))

    deck = said[f"deck:{deck_id}"]
    assert (deck.kind, deck.label) == ("Deck", "Grixis")
    assert list(deck.rows) == [
        ("Pilot", "Jordan C"), ("Event", "PogNov25"), ("Finish", "12 / 31"),
        ("Registered", "1 Dec 2025"), ("Archetype", "Grixis"),
    ]
    assert f"href='https://moxfield.com/decks/{deck_id}'" in deck.link
    # Its placement is a node of its own, and answers for the same deck.
    placement = said[f"placement:{deck_id}"]
    assert list(placement.rows) == [
        ("Deck", "Grixis"), ("Event", "PogNov25"), ("Finish", "12 / 31"),
        ("Registered", "1 Dec 2025"),
    ]
    assert placement.link == deck.link
    # An event says when and how big, and names what hangs off it.
    event = said["event:PogNov25"]
    assert list(event.rows) == [("Registered", "1 Dec 2025"), ("Field size", "31")]
    assert [(other.label, other.note) for other in event.linked] == [
        ("Jordan C", ""), ("Grixis", "12 / 31")]
    # And the pilot at the hub counts the events drawn round them.
    assert list(said["pilot:Jordan C"].rows) == [("Events played", "2")]


def _gem_graph() -> tuple[Subgraph, dict]:
    """One gem of one archetype and a deck running it, with that deck's record."""
    graph = Subgraph(
        nodes=[Node("arch:lands", "Lands", "Archetype", decks=100),
               Node("card:lands:port", "Rishadan Port", "Card", decks=21, total_decks=100,
                    top_decks=13, pilots=10, gem_luck=0.0039),
               Node("deck:d1", "Ada L · 3rd", "Deck", event="PogMay26")],
        edges=[Edge("arch:lands", "card:lands:port", "IN"),
               Edge("deck:d1", "card:lands:port", "CONTAINS:Main")],
    )
    record = DeckRecord("PogMay26", datetime(2026, 5, 2), "Lands", 3, None, 40,
                        "cohort", "d1", "Ada L")
    return graph, {"d1": record}


def test_no_node_of_any_view_answers_with_only_its_kind_and_name(
    tmp_path, snapshot_dir, built_graph
):
    # "Kind: Macro, Name: combo" was the whole answer for some nodes. Every node of
    # every view the app draws states at least one fact of its own, over all eight
    # kinds: the fixture's graphs, and a gem graph, which the fixture cannot draw.
    conn = built_graph(tmp_path, snapshot_dir)
    gems, gem_decks = _gem_graph()
    drawn = [
        ("pilot_neighbourhood", pilot_subgraph(conn, "Jordan C"), None),
        ("pilot_neighbourhood", pilot_subgraph(conn, "Jordan C", "AmberTealViper"), None),
        ("pilot_affinity", pilot_affinity_subgraph(conn, "Jordan C"), None),
        ("card_usage", card_usage_subgraph(conn, "brainstorm"), None),
        ("card_cooccurrence", card_cooccurrence_subgraph(conn, "brainstorm", top_n=5), None),
        ("card_cooccurrence",
         card_cooccurrence_subgraph(conn, "brainstorm", "daze", top_n=5), None),
        ("meta_gems", gems, gem_decks),
    ]

    kinds = set()
    for view, subgraph, decks in drawn:
        assert subgraph.nodes, view
        said = _answers(view, subgraph, decks or _on_record(conn, subgraph))
        for node in subgraph.nodes:
            facts = said[node.id]
            assert facts.rows, (view, node.kind, node.label)
            assert all(label and value for label, value in facts.rows), (view, facts)
            kinds.add(node.kind)
    assert kinds == {"Deck", "Pilot", "Card", "Archetype", "Macro", "Event",
                     "Placement", "Intersection"}


def _drawn_to_stand_out(card: str) -> set[str]:
    """The nodes a drawn card's graph draws larger than vis.js's own dot."""
    doc = html.unescape(re.search(r'<iframe srcdoc="([^"]*)"', card).group(1))
    nodes = json.loads(re.search(r"nodes = new vis\.DataSet\((\[.*?\])\)", doc, re.S).group(1))
    return {node["id"] for node in nodes if node.get("size", 25) > 25}


def test_each_view_marks_the_pilot_or_card_it_was_asked_about(
    tmp_path, snapshot_dir, built_graph
):
    # Issue #331: the card a usage graph was asked about was one dot among 106 of the
    # same size. Every view that is about a pilot or a card draws that node to stand
    # out: the pilot, or both of a head-to-head; the card, or both of a pair. The gem
    # view is about the whole format, so it marks nothing.
    conn = built_graph(tmp_path, snapshot_dir)
    gems, gem_decks = _gem_graph()
    lone = {"pilot": "Jordan C", "pilot2": None}
    pair = {"pilot": "Jordan C", "pilot2": "AmberTealViper"}
    lands = {"cooccur_drop_lands": False}
    drawn = [
        ("pilot_neighbourhood", lone, pilot_subgraph(conn, "Jordan C"), None,
         {"pilot:Jordan C"}),
        ("pilot_neighbourhood", pair, pilot_subgraph(conn, "Jordan C", "AmberTealViper"),
         None, {"pilot:Jordan C", "pilot:AmberTealViper"}),
        ("pilot_affinity", lone, pilot_affinity_subgraph(conn, "Jordan C"), None,
         {"pilot:Jordan C"}),
        ("card_usage", {}, card_usage_subgraph(conn, "brainstorm"), None,
         {"card:brainstorm"}),
        ("card_cooccurrence", lands, card_cooccurrence_subgraph(conn, "brainstorm", top_n=5),
         None, {"card:brainstorm"}),
        ("card_cooccurrence", lands,
         card_cooccurrence_subgraph(conn, "brainstorm", "daze", top_n=5), None,
         {"card:brainstorm", "card:daze"}),
        ("meta_gems", {}, gems, gem_decks, set()),
    ]

    for view, values, subgraph, decks, subject in drawn:
        card = _drawn_card(view, values, subgraph, decks or _on_record(conn, subgraph))
        assert _drawn_to_stand_out(card) == subject, view


def test_a_gem_and_its_archetype_state_the_claim_in_the_apps_one_count_form():
    # A gem's facts are its row of the gem table, said in full. A count against its
    # sample is written one way everywhere (§4): 13 of the card's 21 decks in the
    # archetype's best fifth is "13 / 21", as 21 of the archetype's 100 is "21 / 100".
    gems, decks = _gem_graph()

    said = _answers("meta_gems", gems, decks)

    assert list(said["card:lands:port"].rows) == [
        ("Archetype", "Lands"), ("Decks running it", "21 / 100"),
        ("In top 20%", "13 / 21"), ("Pilots", "10"), ("Odds it is luck", "0.39%"),
    ]
    assert list(said["arch:lands"].rows) == [("Scored decks", "100"), ("Gems", "1")]
    # The gem names the decks running it, each with the event that tells two decks
    # of one pilot apart, since their labels can be the same.
    assert [(other.label, other.note) for other in said["card:lands:port"].linked] == [
        ("Lands", ""), ("Ada L \u00b7 3rd", "PogMay26")]


def test_the_imputed_mark_is_explained_exactly_where_the_panel_draws_one():
    # A number this project worked out carries a mark, and the mark its legend
    # (issue #166). The panel prints a finish or a field for every deck drawn, most
    # of them the source's own, so the legend rides the facts that carry a mark and
    # not the whole card: a legend for an asterisk nobody can see is chrome (§14).
    legend = f"{numfmt.IMPUTED_MARK} marks a number this project worked out, " \
             "not one the source recorded."
    recorded, placed_by_us, unranked = (
        DeckRecord("Event 0", datetime(2025, 1, 1), "Grixis", 3, None, 40, None, "d0", "Ada L"),
        DeckRecord("Event 1", datetime(2025, 1, 8), "Grixis", 5, "title", 24, None, "d1", "Ada L"),
        DeckRecord("Event 2", datetime(2025, 1, 15), "Grixis", None, None, 30, "cohort", "d2",
                   "Ada L"),
    )
    decks = {deck.deck_id: deck for deck in (recorded, placed_by_us, unranked)}
    graph = _drawing(decks)

    said = _answers("pilot_neighbourhood", graph, decks)

    assert ("Finish", "3 / 40") in said["deck:d0"].rows and said["deck:d0"].note is None
    assert ("Finish", "5* / 24") in said["deck:d1"].rows and said["deck:d1"].note == legend
    # An event names its deck with that deck's finish, so it carries the mark too.
    assert said["event:Event 0"].note is None
    assert said["event:Event 1"].linked[1].note == "5* / 24"
    assert said["event:Event 1"].note == legend
    # A deck with no finish on record prints no field, so it has no mark to explain.
    # Its event does print the field, which here is one the project worked out.
    assert said["deck:d2"].note is None
    assert ("Field size", "30*") in said["event:Event 2"].rows
    assert said["event:Event 2"].note == legend

    # And the list says it once, when one of the finishes it lists is marked.
    _, marked = _graph_list("pilot_neighbourhood", graph, decks)
    _, clean = _graph_list("pilot_neighbourhood", _drawing({"d0": recorded}), {"d0": recorded})
    assert f"<div class='caption'>{legend}</div>" in marked
    assert legend not in clean


def test_one_card_explains_the_imputed_mark_once():
    # One wording for one mark, and one place for it on a card (§14). A placement
    # this project decided is marked on the graph's own label, so the card's head
    # carries the legend and the panel does not repeat it over its list or under a
    # node's facts. With no mark on the graph, the list carries it if it prints
    # one, and a node's facts only when nothing above them has.
    legend = f"{numfmt.IMPUTED_MARK} marks a number this project worked out"
    ours = DeckRecord("Event 1", datetime(2025, 1, 8), "Grixis", 5, "title", 24, None,
                      "d1", "Ada L")
    fielded = DeckRecord("Event 2", datetime(2025, 1, 15), "Grixis", None, None, 30,
                         "cohort", "d2", "Ada L")
    values = {"pilot": "ada", "pilot2": None}

    on_the_graph = _drawing({"d1": ours})
    on_the_graph.nodes.append(Node("placement:d1", "5th*", "Placement"))
    on_the_graph.edges.append(Edge("deck:d1", "placement:d1", "PLACED"))
    card = _drawn_card("pilot_neighbourhood", values, on_the_graph, {"d1": ours})
    assert card.count(legend) == 1 and card.index(legend) < card.index("graph-split")

    in_the_list = _drawn_card("pilot_neighbourhood", values, _drawing({"d1": ours}),
                              {"d1": ours})
    assert in_the_list.count(legend) == 1
    assert in_the_list.index("class='panel-list'") < in_the_list.index(legend)

    # A deck with no finish is listed with none, so only its event prints a mark.
    in_the_facts = _drawn_card("pilot_neighbourhood", values, _drawing({"d2": fielded}),
                               {"d2": fielded})
    assert in_the_facts.count(legend) == 1
    assert f"<div class='facts-note'>{legend}" in in_the_facts


# --- The list each graph's panel carries (issue #330) ---


def _results(count: int) -> dict:
    """``count`` decks of one pilot, a day apart, the newest numbered highest."""
    return {
        f"d{i}": DeckRecord(f"Event {i}", datetime(2025, 1, 1) + timedelta(days=i),
                            "Grixis", i + 1, None, 40, None, f"d{i}", "Ada L")
        for i in range(count)
    }


def _drawing(decks: dict) -> Subgraph:
    """A one-pilot neighbourhood drawing ``decks``, as `pilot_subgraph` shapes it."""
    nodes = [Node("pilot:ada", "Ada L", "Pilot")]
    edges = []
    for deck_id, deck in decks.items():
        nodes += [Node(f"event:{deck.event}", deck.event, "Event"),
                  Node(f"deck:{deck_id}", "Grixis", "Deck")]
        edges += [Edge("pilot:ada", f"event:{deck.event}", "PLAYED_AT"),
                  Edge(f"event:{deck.event}", f"deck:{deck_id}", "ENTERED")]
    return Subgraph(nodes=nodes, edges=edges)


def test_a_pilots_panel_lists_their_latest_ten_decks_and_points_at_the_rest():
    # The whole record is a table further down the page, so the panel holds only what
    # fits beside the graph: the ten newest, each naming its deck's node. Twelve
    # decks, numbered oldest to newest, so the ten are d11 down to d2.
    decks = _results(12)

    title, body = _graph_list("pilot_neighbourhood", _drawing(decks), decks)

    assert title == "Latest decks"
    assert _named_rows(body) == [f"deck:d{i}" for i in (11, 10, 9, 8, 7, 6, 5, 4, 3, 2)]
    # The event with its date under it, the archetype, and the finish over the field.
    assert _table_rows(body)[0] == [
        "Event 11<span class='sub'>12 Jan 2025</span>", "Grixis", "12 / 40"]
    assert "latest 10 of <span class='figure'>12</span> decks" in body

    # A record that fits is listed whole, with nothing to point at.
    _, short = _graph_list("pilot_neighbourhood", _drawing(_results(3)), _results(3))
    assert len(_table_rows(short)) == 3 and "class='caption'" not in short


def test_a_head_to_heads_panel_lists_the_shared_events_with_each_pilots_finish():
    ada, bob = "pilot:ada", "pilot:bob"
    nodes = [Node(ada, "Ada L", "Pilot", group=ada), Node(bob, "Bob C", "Pilot", group=bob)]
    edges, decks = [], {}
    for i, (when, a_place, b_place) in enumerate([(3, 1, 9), (20, 5, None)]):
        event = f"Event {i}"
        nodes.append(Node(f"event:{event}", event, "Event"))
        for who, name, place in ((ada, "Ada L", a_place), (bob, "Bob C", b_place)):
            deck_id = f"{who[-3:]}{i}"
            nodes.append(Node(f"deck:{deck_id}", "Grixis", "Deck", group=who))
            edges += [Edge(who, f"event:{event}", "PLAYED_AT"),
                      Edge(f"event:{event}", f"deck:{deck_id}", "ENTERED")]
            decks[deck_id] = DeckRecord(event, datetime(2025, 3, when), "Grixis", place,
                                        None, 24, None, deck_id, name)

    title, body = _graph_list("pilot_neighbourhood", Subgraph(nodes=nodes, edges=edges), decks)

    assert title == "Shared events"
    header = re.findall(r"<th(?: [^>]*)?>(.*?)</th>", body.split("<tbody>")[0])
    assert header == ["Event", "Ada L", "Bob C"]
    # Newest first, each pilot's finish under their own name, and none where no
    # finish is on record. A row names its event, the node the two chains meet at.
    assert _table_rows(body) == [
        ["Event 1<span class='sub'>20 Mar 2025</span>", "5 / 24", ""],
        ["Event 0<span class='sub'>3 Mar 2025</span>", "1 / 24", "9 / 24"],
    ]
    assert _named_rows(body) == ["event:Event 1", "event:Event 0"]


def test_an_affinity_panel_lists_every_archetype_played_by_events():
    # No table under the page restates this graph, so the list is not cut short.
    played = [(f"Archetype {i}", 20 - i) for i in range(14)]
    nodes = [Node("pilot:ada", "Ada L", "Pilot"), Node("macro:tempo", "tempo", "Macro", weight=30)]
    edges = [Edge("pilot:ada", "macro:tempo", "PLAYS:30", events=30)]
    for name, events in reversed(played):
        nodes.append(Node(f"arch:{name}", name, "Archetype", weight=events))
        edges.append(Edge("macro:tempo", f"arch:{name}", f"PLAYS:{events}", events=events))

    title, body = _graph_list("pilot_affinity", Subgraph(nodes=nodes, edges=edges), {})

    assert title == "Archetypes played"
    assert _table_rows(body) == [[name, "tempo", str(events)] for name, events in played]
    assert _named_rows(body) == [f"arch:{name}" for name, _ in played]


def test_a_cards_panel_lists_the_ten_archetypes_running_it_most_and_points_at_the_rest():
    # The archetype table under the plots holds every one, grouped by broad class.
    # The panel is the short answer to "where is it a staple": the ten by share.
    nodes = [Node("card:x", "X (12% of meta)", "Card", decks=120, total_decks=1000),
             Node("macro:tempo", "tempo", "Macro")]
    edges = [Edge("card:x", "macro:tempo", "12%", visible=True, decks=120, total_decks=1000)]
    for i in range(13):
        nodes.append(Node(f"arch:a{i}", f"Archetype {i}", "Archetype"))
        edges.append(Edge("macro:tempo", f"arch:a{i}", f"{i}%", visible=True,
                          decks=i + 1, total_decks=20))

    title, body = _graph_list("card_usage", Subgraph(nodes=nodes, edges=edges), {})

    assert title == "Archetypes by share"
    rows = _table_rows(body)
    assert rows[0] == ["Archetype 12", "13 / 20", "65%"] and len(rows) == 10
    assert _named_rows(body)[:2] == ["arch:a12", "arch:a11"]
    assert "top 10 of <span class='figure'>13</span> archetypes" in body

    # A card no archetype runs has a line and no empty table.
    _, none = _graph_list("card_usage", Subgraph(nodes=nodes[:1], edges=[]), {})
    assert "<table" not in none and "No archetype runs it" in none


def test_the_gems_panel_is_the_gem_table_with_each_row_naming_its_gem():
    gems, decks = _gem_graph()

    title, body = _graph_list("meta_gems", gems, decks)

    assert title == "The gems"
    header = re.findall(r"<th(?: [^>]*)?>(.*?)</th>", body.split("<tbody>")[0])
    # The odds column says what it is the odds of, for a reader new to the page.
    assert header == ["Card", "Decks running it", "In top 20%", "Pilots", "Odds it is luck"]
    assert _named_rows(body) == ["card:lands:port"]


# --- An archetype's page (issue #332) ---


def _shares(*rows: tuple[str, int, int, int]) -> Series:
    """A meta-share matrix from ``(tag, year, decks, decks that year)`` rows."""
    return Series(cells=[
        SeriesCell(tag=tag, archetype=tag.title(), year=year, n=n, share=n / total,
                   year_total=total)
        for tag, year, n, total in rows
    ])


def _landscape(year: int, total: int, *rows: tuple) -> Series:
    """One year's landscape from ``(tag, decks, mean norm, scored decks, events,
    better end, worse end)`` rows, the two ends being the mean's 90% interval."""
    return Series(cells=[
        LandscapeCell(tag=tag, archetype=tag.title(), year=year, n=n, share=n / total,
                      year_total=total, year_events=9, mean_norm=mean, scored=scored,
                      events=events, mean_low=low, mean_high=high)
        for tag, n, mean, scored, events, low, high in rows
    ])


_SHARES = (
    ("grixis", 2023, 5, 50), ("storm", 2023, 0, 50), ("lands", 2023, 0, 50),
    ("zoo", 2023, 0, 50), ("jund", 2023, 9, 50),
    ("grixis", 2024, 12, 200), ("storm", 2024, 30, 200), ("lands", 2024, 12, 200),
    ("zoo", 2024, 3, 200), ("jund", 2024, 0, 200),
)


def test_an_archetypes_tiles_are_its_latest_years_share_rank_finish_and_events():
    # Worked by hand. In 2024, the record's latest year, Grixis is 12 of 200 decks,
    # which is 6%. Storm has more (30) and Lands as many (12), so Grixis is 2nd of the
    # four archetypes played, level with one other. Its four scored decks, at two
    # events, average 0.3 from the top: a finish of 0.70. The landscape puts that
    # mean's 90% interval at 0.2 to 0.45, which flipped is 0.55 to 0.80, and no mean
    # is stated without it (ADR 0013). Grixis was played at three of the year's nine
    # events, one more than it was placed at (a bracket that cut no Grixis deck):
    # 33.33% of the season (issue #340).
    landscape = _landscape(
        2024, 200,
        ("grixis", 12, 0.3, 4, 2, 0.2, 0.45), ("storm", 30, 0.5, 20, 6, 0.4, 0.6),
        ("lands", 12, 0.6, 9, 4, 0.5, 0.7), ("zoo", 3, None, 0, 0, None, None),
    )

    assert _facts(_archetype_tiles("grixis", _shares(*_SHARES), landscape, 3, 9)) == [
        ("Share in 2024", "6%", "12 / 200 decks"),
        ("Rank in 2024", "2nd", "of 4 archetypes by share, level with 1 other"),
        ("Mean finish in 2024", "0.70", "90% range 0.55 to 0.80, 1 = first"),
        ("Events played in 2024", "3 (33.33%)", "of 9 events"),
    ]
    # Played that year and scored nowhere: a share, a rank and the events it was at,
    # and no finish.
    assert _facts(_archetype_tiles("zoo", _shares(*_SHARES), landscape, 1, 9))[1:] == [
        ("Rank in 2024", "4th", "of 4 archetypes by share"),
        ("Mean finish in 2024", "n/a", "No scored deck in 2024"),
        ("Events played in 2024", "1 (11.11%)", "of 9 events"),
    ]


def test_an_archetype_that_sat_the_latest_year_out_says_so_in_every_tile():
    # 6 of the 137 archetypes the picker offers have no deck in the record's latest
    # year, so the landscape holds no cell for them. The share is a real zero, and
    # there is no rank or finish to state.
    landscape = _landscape(2024, 200, ("grixis", 12, 0.3, 4, 2, 0.2, 0.45))
    sat_out = [
        ("Share in 2024", "0%", "0 / 200 decks"),
        ("Rank in 2024", "n/a", "No deck in 2024"),
        ("Mean finish in 2024", "n/a", "No scored deck in 2024"),
        ("Events played in 2024", "0 (0%)", "of 9 events"),
    ]

    assert _facts(_archetype_tiles("jund", _shares(*_SHARES), landscape, 0, 9)) == sat_out
    # A year too thin to place on a landscape at all has no finish to state either.
    assert _facts(_archetype_tiles("jund", _shares(*_SHARES), None, 0, 9)) == sat_out


def test_the_archetypes_to_try_are_the_most_played_the_picker_offers_five_at_most():
    # Issue #340. In 2024, the record's latest year, Storm has 30 decks and Grixis
    # and Lands 12 each, listed in that order as Metagame lists them (level
    # archetypes by tag). Zoo's 3 of 200 is over the 1% Metagame lists from, but
    # the picker does not offer it here, and a chip is a pick: it is no example.
    # Jund has no deck that year. Read off the shares, so a year that reorders the
    # meta reorders the chips.
    labels = {"grixis": "Grixis", "storm": "Storm", "lands": "Lands", "jund": "Jund"}

    assert _example_archetypes(_shares(*_SHARES), labels) == [
        ("Storm", "storm"), ("Grixis", "grixis"), ("Lands", "lands")]
    assert _example_archetypes(
        _shares(*_SHARES, ("jund", 2025, 40, 60), ("storm", 2025, 20, 60)),
        labels) == [("Jund", "jund"), ("Storm", "storm")]
    # Five at most, of however many are played.
    many = _shares(*[(f"deck{i}", 2024, 20 - i, 100) for i in range(8)])
    assert [tag for _, tag in _example_archetypes(
        many, {f"deck{i}": f"Deck {i}" for i in range(8)})] == [
        "deck0", "deck1", "deck2", "deck3", "deck4"]
    assert _example_archetypes(_shares(), labels) == []


def _meetings(*rows: tuple) -> Series:
    """A pair's timeline from ``(mean a, mean b)`` rows, one event each, a ``None``
    being a side the event did not score."""
    return Series(cells=[
        ArchetypeTimelinePoint(event=f"E{at}", date=datetime(2024, 1, 1 + at),
                               mean_norm_a=a, decks_a=int(a is not None),
                               mean_norm_b=b, decks_b=int(b is not None))
        for at, (a, b) in enumerate(rows)
    ])


_PAIR_LANDSCAPE = (
    ("grixis", 12, 0.3, 4, 2, 0.2, 0.45), ("storm", 30, 0.5, 20, 6, 0.4, 0.6),
    ("lands", 12, 0.6, 9, 4, 0.5, 0.7), ("zoo", 3, None, 0, 0, None, None),
)


def test_two_archetypes_tiles_keep_both_sides_of_each_figure_and_count_the_meetings():
    # Worked by hand (issue #340). In 2024, the record's latest year, Grixis is 12
    # of 200 decks (6%) and Storm 30 (15%). Grixis's four scored decks at two events
    # average 0.3 from the top, a finish of 0.70 with a 90% interval of 0.55 to
    # 0.80; Storm's twenty at six events average 0.5, a finish of 0.50 between 0.40
    # and 0.60. They were both scored at five events: Grixis finished nearer the top
    # at three (a lower norm is a better finish), Storm at one, and they were level
    # at one. A sixth event scored Grixis alone and bounds nothing, so it is no
    # meeting. Grixis was played at three events and Storm at seven. Every value
    # reads Grixis first, the order the two were given in, and says only what it is
    # (the maintainer's ruling, 2026-11-18): no deck counts, no intervals. The
    # meetings name who finished better more often: Grixis, 3 of 5, which a fair
    # coin produces, so the note says so (issue #175).
    landscape = _landscape(2024, 200, *_PAIR_LANDSCAPE)
    played = {"grixis": 3, "storm": 7}
    finishes = _meetings((0.1, 0.4), (0.2, 0.6), (0.3, 0.3), (0.5, 0.2), (0.25, 0.9),
                         (0.4, None))

    tiles = _archetype_pair_tiles([("grixis", "Grixis"), ("storm", "Storm")],
                                  _shares(*_SHARES), landscape, played, finishes)

    assert _facts(tiles) == [
        ("Share in 2024", "6% vs 15%", None),
        ("Mean finish in 2024", "0.70 vs 0.50", "First place = 1"),
        ("Events played in 2024", "3 vs 7", None),
        ("Finished better", "Grixis",
         "Ahead of Storm at 60% of shared events (3 of 5), a split a coin could produce"),
    ]
    # Two means that are level are level, whatever their last bit: on the real
    # record one deck 38th of 77 met two at 37th and 39th, whose mean came out 5e-17
    # under it, and that is no win for either side.
    level = _archetype_pair_tiles(
        [("grixis", "Grixis"), ("storm", "Storm")], _shares(*_SHARES), landscape, played,
        _meetings((38 / 77, (37 / 77 + 39 / 77) / 2), (0.1, 0.4)))
    assert 38 / 77 != (37 / 77 + 39 / 77) / 2
    assert _facts(level)[3] == (
        "Finished better", "Grixis",
        "Ahead of Storm at 50% of shared events (1 of 2), a split a coin could produce")
    # The other way round is the same comparison read from the other side: the
    # values swap, and the one ahead is still Grixis.
    swapped = _archetype_pair_tiles(
        [("storm", "Storm"), ("grixis", "Grixis")], _shares(*_SHARES), landscape, played,
        _meetings((0.4, 0.1), (0.6, 0.2), (0.3, 0.3), (0.2, 0.5), (0.9, 0.25)))
    assert [tile.value for tile in swapped] == [
        "15% vs 6%", "0.50 vs 0.70", "7 vs 3", "Grixis"]
    assert swapped[3].note == _facts(tiles)[3][2]
    # Twelve meetings all to Storm is no split a coin produces, so no hedge; and
    # one each is no lead at all.
    swept = _archetype_pair_tiles(
        [("grixis", "Grixis"), ("storm", "Storm")], _shares(*_SHARES), landscape, played,
        _meetings(*[(0.6, 0.1)] * 12))
    assert _facts(swept)[3] == (
        "Finished better", "Storm", "Ahead of Grixis at 100% of shared events (12 of 12)")
    even = _archetype_pair_tiles(
        [("grixis", "Grixis"), ("storm", "Storm")], _shares(*_SHARES), landscape, played,
        _meetings((0.1, 0.4), (0.4, 0.1)))
    assert _facts(even)[3] == (
        "Finished better", "Level", "Each ahead at 1 of 2 shared events")


def test_two_archetypes_tiles_say_what_one_side_or_the_pair_has_no_figure_for():
    # Zoo was played in 2024, at one event, and scored nowhere: it has a share and
    # events, and no finish to set beside Grixis's. Jund sat the year out, so it has
    # a real zero share, no events and nothing else. And a pair the timeline
    # refused has no meetings to count: it says how many events the two share,
    # which is why (one, or none at all).
    landscape = _landscape(2024, 200, *_PAIR_LANDSCAPE)
    shares = _shares(*_SHARES)
    played = {"grixis": 3, "storm": 7, "zoo": 1}

    with_zoo = _archetype_pair_tiles([("grixis", "Grixis"), ("zoo", "Zoo")],
                                     shares, landscape, played, None, met=1)
    assert _facts(with_zoo) == [
        ("Share in 2024", "6% vs 1.5%", None),
        ("Mean finish in 2024", "0.70 vs n/a", "First place = 1"),
        ("Events played in 2024", "3 vs 1", None),
        ("Finished better", "n/a", "Both placed at only 1 event"),
    ]
    neither = _archetype_pair_tiles([("zoo", "Zoo"), ("jund", "Jund")],
                                    shares, landscape, played, None)
    assert _facts(neither) == [
        ("Share in 2024", "1.5% vs 0%", None),
        ("Mean finish in 2024", "n/a vs n/a", "No scored deck in 2024"),
        ("Events played in 2024", "1 vs 0", None),
        ("Finished better", "n/a", "Never both placed at one event"),
    ]
    # A year too thin to place on a landscape has no finish for either side.
    assert _facts(_archetype_pair_tiles(
        [("grixis", "Grixis"), ("storm", "Storm")], shares, None, played, None))[1:3] == [
        ("Mean finish in 2024", "n/a vs n/a", "No scored deck in 2024"),
        ("Events played in 2024", "3 vs 7", None),
    ]


def test_two_archetypes_tiles_agree_with_each_ones_own_and_with_the_chart(live_graph):
    # Issue #340, over the real record's ten most played archetypes, every pair of
    # them in both orders. The first three tiles are each side's own tile's figure,
    # the two side by side. The fourth is read a second way, off the chart drawn
    # under the head: the events at which one line is over the other, a higher
    # point being a better finish, of the events both lines have a point at.
    shares = meta_share_over_time(live_graph)
    landscape = archetype_landscape(live_graph, latest_deck_year(shares))
    played, year_events = archetype_events_played(live_graph, latest_deck_year(shares))
    names = {tag: name for name, tag, _ in archetypes_with_history(live_graph)}
    top = [tag for tag in most_played(shares) if tag in names][:10]
    own = {tag: _archetype_tiles(tag, shares, landscape, played.get(tag, 0), year_events)
           for tag in top}

    drew = refused = level = 0
    for a in top:
        for b in top:
            if a == b:
                continue
            try:
                finishes, met = archetype_timeline(live_graph, a, b), 0
            except NotEnoughHistory as refusal:
                finishes, met = None, refusal.found
                refused += 1
            share, finish, events, meetings = _archetype_pair_tiles(
                [(a, names[a]), (b, names[b])], shares, landscape, played, finishes, met)
            assert share.value == f"{own[a][0].value} vs {own[b][0].value}", (a, b)
            assert finish.value == f"{own[a][2].value} vs {own[b][2].value}", (a, b)
            assert finish.note == "First place = 1", (a, b)
            assert events.value == " vs ".join(
                own[side][3].value.split()[0] for side in (a, b)), (a, b)
            assert share.note is None and events.note is None, (a, b)
            if finishes is None:
                assert meetings.value == "n/a", (a, b)
                continue
            drew += 1
            figure = _archetype_timeline_figure(names[a], names[b], finishes)
            line = {trace.name: dict(zip(trace.x, trace.y)) for trace in figure.data
                    if trace.name in (names[a], names[b]) and trace.mode
                    and "markers" in trace.mode}
            both = [at for at, y in line[names[a]].items()
                    if y is not None and line[names[b]].get(at) is not None]
            gaps = [line[names[a]][at] - line[names[b]][at] for at in both]
            ahead = sum(gap > 1e-9 for gap in gaps)
            behind = sum(gap < -1e-9 for gap in gaps)
            level += len(both) - ahead - behind
            if ahead == behind:
                assert meetings.value == "Level", (a, b)
                continue
            leader, other, led = ((names[a], names[b], ahead) if ahead > behind
                                  else (names[b], names[a], behind))
            assert meetings.value == leader, (a, b)
            assert meetings.note.startswith(
                f"Ahead of {other} at {numfmt.share(led / len(both))} of shared events "
                f"({led} of {len(both)})"), (a, b)

    # Both branches graded, and the level meetings that keep the two counts from
    # adding up to the total are in what was graded.
    assert drew >= 60 and len(top) == 10
    assert drew + refused == 90


def test_every_archetypes_tiles_agree_with_the_landscape_and_its_timeline(live_graph):
    # AC (#332): each tile agrees with the landscape and the timeline for the same
    # archetype and year. Three readings of the record's latest year, each by a query
    # of its own, for every archetype the picker offers. The tiles state the
    # landscape's cell. The timeline is the second reading of the finish: that
    # year's points, the events counted and the mean weighted by the decks each
    # rests on. And where the landscape draws the archetype's dot, the tiles are the
    # strings its hover prints and the ends of the bar it draws.
    shares = meta_share_over_time(live_graph)
    year = latest_deck_year(shares)
    landscape = archetype_landscape(live_graph, year)
    cells = {cell.tag: cell for cell in landscape.cells}
    by_decks = sorted((cell.n for cell in cells.values()), reverse=True)
    drawn = _landscape_top(landscape, 25)
    dots = _landscape_figure(drawn).data[0]
    printed = {cell.tag: (hover, score + up, score - down) for cell, hover, score, up, down
               in zip(drawn, dots.customdata, dots.y, dots.error_y.array,
                      dots.error_y.arrayminus)}
    offered = archetypes_with_history(live_graph)
    # The events played, read a second way (issue #340): every deck of the year
    # with its primary archetype and its event, grouped here rather than counted
    # by the query, over the year's events counted off the Event nodes.
    played, year_events = archetype_events_played(live_graph, year)
    regrouped: dict[str, set] = {}
    for tag, event in rows(live_graph.execute(
        "MATCH (d:Deck)-[r:HAS_ARCHETYPE]->(a:Archetype), "
        "(d)-[:PLAYED_AT]->(e:Event)-[:IN_YEAR]->(y:Year) "
        "WHERE r.isPrimary AND y.year = $year RETURN a.tag, e.event", {"year": year},
    )):
        regrouped.setdefault(tag, set()).add(event)
    ((held,),) = rows(live_graph.execute(
        "MATCH (e:Event)-[:IN_YEAR]->(:Year {year: $year}) RETURN count(e)", {"year": year}))
    assert year_events == held

    absent = 0
    for _, tag, _ in offered:
        share, rank, finish, events = _archetype_tiles(
            tag, shares, landscape, played.get(tag, 0), year_events)
        points = [point for point in archetype_timeline(live_graph, tag).cells
                  if point.date.year == year and point.mean_norm_a is not None]
        scored = sum(point.decks_a for point in points)
        at = len(regrouped.get(tag, ()))
        assert events.value == f"{at:,} ({numfmt.share(at / held)})", tag
        assert events.note == f"of {held:,} events", tag
        # Every event a finish is read from is one it was played at.
        assert len(points) <= at, tag
        if tag not in cells:
            # No deck that year, so the landscape holds no cell and the timeline no
            # point: there is nothing to agree with but the zero.
            absent += 1
            assert not points, tag
            assert [share.value, rank.value, finish.value] == ["0%", "n/a", "n/a"], tag
            continue
        cell = cells[tag]
        assert share.value == numfmt.share(cell.share), tag
        assert share.note == f"{cell.n:,} / {cell.year_total:,} decks", tag
        # Its place in the year's deck counts, most first: the first archetype on as
        # many decks takes the rank for all of them.
        assert rank.value.rstrip("stndrh") == str(by_decks.index(cell.n) + 1), tag
        assert rank.note.startswith(f"of {len(cells)} archetypes by share"), tag
        mean = sum(point.mean_norm_a * point.decks_a for point in points) / scored
        assert finish.value == f"{1 - mean:.2f}", tag
        if tag in printed:
            (_, hover_share, hover_decks, hover_finish, hover_scored, hover_events, _), \
                best, worst = printed[tag]
            assert (share.value, share.note, finish.value) == (
                hover_share, hover_decks, hover_finish), tag
            assert (hover_events, hover_scored) == (len(points), scored), tag
            assert finish.note == f"90% range {worst:.2f} to {best:.2f}, 1 = first", tag

    # Every branch graded: some archetypes sat the year out, every offered archetype
    # with a deck that year was scored there, and every drawn dot of the landscape
    # is an archetype the picker offers.
    assert len(offered) > 100 and absent
    assert set(printed) <= {tag for _, tag, _ in offered} and len(printed) == 25


# --- The pages as the app draws them: each tab's own callback, over the fixture ---


@pytest.fixture
def demo(tmp_path, snapshot_dir):
    artifact = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), artifact)
    return build_app(artifact)


def _drawn(demo, name: str, *picked) -> list[str]:
    """What the callback called ``name`` hands the page for ``picked``: each output's
    markup, and ``""`` for an output that is not markup (a plot, a visibility)."""
    draw = next(dep.fn for dep in demo.fns.values()
                if getattr(dep.fn, "__name__", "") == name)
    return [out.get("value") if isinstance(out.get("value"), str) else ""
            for out in draw(*picked)]


def _tile_labels(markup: str) -> list[str]:
    return [html.unescape(label)
            for label in re.findall(r"<div class='tile-label'>(.*?)</div>", markup)]


def test_a_drawn_pilot_page_opens_with_its_head_and_closes_with_its_record(demo):
    head, *_, record = _drawn(demo, "draw_pilots", "pilot_overview", "Jordan C")

    # The name, the descriptor, then the four tiles, none of them an average.
    assert (head.index("page-name") < head.index("Jordan C")
            < head.index("Pilot, on record in 2025.") < head.index("tile-label"))
    assert _tile_labels(head) == [
        "Events played", "Archetypes played", "Most-played archetype", "Best finish"]
    # Both of the fixture's two events, read against the record's whole (#340).
    assert "2 (100%)" in head and "of 2 on record" in head
    assert not re.search(r"average|mean", head, re.IGNORECASE)
    # The record is the last thing on the page: every result, in a table.
    assert "Record" in record and "CFWAT25" in record and "PogNov25" in record
    assert "5 / 19" in record and "12 / 31" in record


def test_a_drawn_card_page_opens_with_its_head_and_lists_its_archetypes(demo):
    outputs = _drawn(demo, "draw_cards", "card_overview", "daze", "Main", None, 15, False)
    head, archetypes = outputs[0], outputs[6]

    assert (head.index("page-name") < head.index("Daze")
            < head.index("Card, counted on the main board.") < head.index("tile-label"))
    assert _tile_labels(head) == [
        "Adoption in 2025", "Change from last year", "Decks", "Archetypes running it"]
    # Each count against the record's whole (#340): of its two archetypes, Grixis.
    assert "of 2 on record, in 1 broad class" in head
    # Daze is in both Grixis decks, which are the fixture's whole tempo class.
    assert _table_rows(archetypes) == [
        ["Tempo", "2 / 2", "100%"],
        ["Grixis", "2 / 2", "100%"],
    ]
    # And the tiles are the same reading, under the same board: Daze is in the main
    # board of 2 of the 3 decks of 2025, both of the one archetype in the table,
    # which is 1 of the record's 2 archetypes.
    assert re.findall(r"<div class='tile-value'>(.*?)</div>", head) == [
        "66.67%", "n/a", "2 (66.67%)", "1 (50%)"]


def test_a_cards_played_with_list_is_on_the_view_that_draws_those_cards(demo):
    outputs = _drawn(demo, "draw_cards", "card_cooccurrence", "daze", "", None, 5, False)
    head, graph = outputs[7], outputs[9]

    # The view draws no usage graph and has no board control, so its head is the
    # card over either board: the tiles Card overview shows on "Main or side".
    either = _drawn(demo, "draw_cards", "card_overview", "daze", "", None, 15, False)[0]
    tiles = r"<div class='tiles'>.*</div>"
    assert "Daze" in head
    assert re.search(tiles, head).group() == re.search(tiles, either).group()
    # The list is in the panel beside the graph that draws those cards (issue #330),
    # and the card that held it under the plots is gone: after the adoption chart's
    # note the view has only the two cards' year by year card (#351), which one card
    # does not draw.
    assert "class='graph-panel'" in graph and "Played with" in graph
    assert len(outputs) == 15 and outputs[13:] == ["", ""]
    rows = _table_rows(graph)
    # Five asked for, five listed, each in both of Daze's two decks at most.
    assert len(rows) == 5
    assert all(row[1] in {"2 / 2", "1 / 2"} for row in rows)
    assert rows[0][1:] == ["2 / 2", "100%"]
    # A rate is a share of the decks the Decks tile counts.
    assert "Daze's <span class='figure'>2</span> decks" in graph


def _tile_values(markup: str) -> list[str]:
    return re.findall(r"<div class='tile-value'>(.*?)</div>", markup)


def _tile_notes(markup: str) -> list[str]:
    return re.findall(r"<div class='tile-note'>(.*?)</div>", markup)


def test_two_cards_get_a_head_about_the_pair_not_about_the_first_card(demo):
    # After the maintainer's look (#329): with a second card the view is about the
    # two together, and its head described the first card alone. It names both, says
    # how much of the latest year's decks run both and how that moved, and then the
    # co-occurrence itself, once from each side: how much of each card's decks also
    # run the other. Off the fixture: Brainstorm is in all three decks of 2025 and
    # Daze in the two Grixis ones, so both are in two.
    outputs = _drawn(demo, "draw_cards", "card_cooccurrence", "brainstorm", "", "daze", 5, False)
    head, graph = outputs[7], outputs[9]

    assert (head.index("page-name") < head.index("Brainstorm and Daze")
            < head.index("Two cards, and what the decks running both also run.")
            < head.index("tile-label"))
    assert _tile_labels(head) == [
        "Together in 2025", "Change from last year",
        "Of Brainstorm's decks", "Of Daze's decks"]
    assert _tile_values(head) == ["66.67%", "n/a", "66.67%", "100%"]
    assert _tile_notes(head)[2:] == ["2 / 3 run both", "2 / 2 run both"]
    # The decks running both are the hub the graph hangs its cards off, and the base
    # the played-with list's rates are a share of.
    assert "Both \\u00b7 2 decks" in html.unescape(graph)  # as the graph's own data writes it
    assert "the <span class='figure'>2</span> decks running both" in graph


def _adoption_lines(demo, *picked) -> dict[str, list[float]]:
    """The adoption chart the co-occurrence view draws: each line's shares, by name."""
    draw = next(dep.fn for dep in demo.fns.values()
                if getattr(dep.fn, "__name__", "") == "draw_cards")
    figure = draw("card_cooccurrence", *picked)[11]["value"]
    return {trace.name: list(trace.y) for trace in figure.data}


def test_two_cards_adoption_chart_draws_them_together_as_a_third_line(demo):
    # After the maintainer's look (#329): the head states how much of the latest
    # year's decks run both, and the chart under it drew only each card's own line,
    # so that figure had no picture and no trend. The decks running both are a line
    # of their own, last, and its last point is the tile's figure.
    lines = _adoption_lines(demo, "brainstorm", "", "daze", 5, False)
    head = _drawn(demo, "draw_cards", "card_cooccurrence", "brainstorm", "", "daze", 5, False)[7]

    assert list(lines) == ["Brainstorm", "Daze", "Together"]
    assert lines["Together"] == [pytest.approx(2 / 3)]  # two of the three decks of 2025
    assert numfmt.share(lines["Together"][-1]) == _tile_values(head)[0]
    # One card has nothing to be together with.
    assert list(_adoption_lines(demo, "brainstorm", "", None, 5, False)) == ["Brainstorm"]


def test_one_card_or_the_same_card_twice_keeps_the_cards_own_head(demo):
    alone = _drawn(demo, "draw_cards", "card_cooccurrence", "brainstorm", "", None, 5, False)[7]
    twice = _drawn(demo, "draw_cards", "card_cooccurrence", "brainstorm", "", "brainstorm", 5, False)[7]

    assert _tile_labels(alone)[0] == "Adoption in 2025" and "Brainstorm and" not in alone
    assert twice == alone


def test_two_cards_no_deck_runs_together_say_so_in_every_tile(demo):
    head = _drawn(demo, "draw_cards", "card_cooccurrence", "dark ritual", "", "daze", 5, False)[7]

    assert "Dark Ritual and Daze" in head
    assert _tile_values(head) == ["0%", "n/a", "0%", "0%"]
    assert _tile_notes(head)[2:] == ["0 / 1 run both", "0 / 2 run both"]


def _table_headers(markup: str) -> list[str]:
    head = markup.split("<thead>")[1].split("</thead>")[0]
    return [html.unescape(header) for header in re.findall(r"<th[^>]*>(.*?)</th>", head)]


def test_two_cards_get_their_co_occurrence_year_by_year_under_the_chart(demo):
    # Issue #351: the one figure the page stated by year was the decks running both
    # as a share of all decks, which falls when either card is played less whatever
    # the two have to do with each other. The co-occurrence itself, each card's share
    # of its own decks that also run the other, was stated for the whole record only.
    # It is a row per year, last on the page. Off the fixture: Brainstorm is in all
    # three decks of 2025, the record's only year, and Daze in two of them.
    outputs = _drawn(demo, "draw_cards", "card_cooccurrence", "brainstorm", "", "daze", 5, False)
    by_year = outputs[-1]

    assert len(outputs) == 15
    assert "<div class='t-result-title'>Co-occurrence by year</div>" in by_year
    # The caption says what a figure is and names no direction: nothing here tests
    # a trend (ADR 0013), and the counts beside each share carry the evidence.
    assert re.findall(r"<div class='caption'>(.*?)</div>", by_year) == [
        "Each figure is the share of that card's decks in the year that also run the other."]
    assert _table_headers(by_year) == ["Year", "Of Brainstorm's decks", "Of Daze's decks"]
    assert _table_rows(by_year) == [[
        "2025",
        "66.67% <span class='base'>(2 / 3)</span>",
        "100% <span class='base'>(2 / 2)</span>",
    ]]


def test_one_card_has_no_pair_so_the_year_by_year_card_is_taken_down(demo):
    # The card is hidden itself, not left standing empty: one card, or the same card
    # picked twice, has no second card for a share to be of.
    draw = next(dep.fn for dep in demo.fns.values()
                if getattr(dep.fn, "__name__", "") == "draw_cards")

    def year_card(*picked):
        return draw("card_cooccurrence", *picked)[-2]["visible"]

    assert year_card("brainstorm", "", "daze", 5, False) is True
    assert year_card("brainstorm", "", None, 5, False) is False
    assert year_card("brainstorm", "", "brainstorm", 5, False) is False
    assert year_card("", "", None, 5, False) is False


def test_a_year_a_card_has_no_deck_in_states_no_share_and_says_so():
    # Issue #351: a share of a count is stated whatever its size (ADR 0013), so the
    # one deck of a thin year is a row like any other. Only a year the card has no
    # deck in has nothing to be a share of. The newer card here was not played in
    # 2023, and the older one's 40 decks that year ran none of it.
    together = _adoption((2023, 0, 192), (2024, 48, 941), (2026, 146, 3368))
    older = _adoption((2023, 40, 192), (2024, 248, 941), (2026, 207, 3368))
    newer = _adoption((2023, 0, 192), (2024, 58, 941), (2026, 357, 3368))

    table = _pair_years_card(together, [("Reanimate", older), ("Bitter Triumph", newer)])

    assert _table_rows(table) == [
        ["2023", "0% <span class='base'>(0 / 40)</span>", "No decks"],
        ["2024", "19.35% <span class='base'>(48 / 248)</span>",
         "82.76% <span class='base'>(48 / 58)</span>"],
        ["2026", "70.53% <span class='base'>(146 / 207)</span>",
         "40.9% <span class='base'>(146 / 357)</span>"],
    ]


def _real_pairs(conn) -> list[tuple[str, str]]:
    """Pairs of the real record's cards: each of the twelve in the most decks with
    the next, then a staple against a spread of the rest."""
    run = _real_cards(conn)
    return list(zip(run[:12], run[1:13])) + [(run[0], other) for other in run[30::10]]


def test_a_pairs_tiles_agree_with_its_graph_on_the_real_record(live_graph):
    # The two-card head against readings it is not built from. Each card's share of
    # the pair is its tile's figure, the two cards' own deck sets counted off raw
    # rows, and the two numbers on that card's line to the hub in the graph.
    conn = live_graph

    def decks_running(canon: str) -> set[str]:
        return {deck for (deck,) in rows(conn.execute(
            "MATCH (d:Deck)-[:CONTAINS]->(:Card {canon: $canon}) RETURN DISTINCT d.deckId",
            {"canon": canon}))}

    pairs = _real_pairs(conn)
    assert len(pairs) > 20
    for a, b in pairs:
        cards = [(canon, card_adoption_over_time(conn, canon)) for canon in (a, b)]
        tiles = _pair_tiles(pair_adoption_over_time(conn, a, b), cards)
        both = decks_running(a) & decks_running(b)
        lines = {edge.source: edge for edge in card_cooccurrence_subgraph(conn, a, b).edges
                 if edge.target.startswith("both:")}
        for canon, tile in zip((a, b), tiles[2:]):
            own = len(decks_running(canon))
            assert tile.value == numfmt.share(len(both) / own), (a, b)
            assert tile.note == f"{numfmt.count_of(len(both), own)} run both", (a, b)
            if both:  # no hub is drawn for two cards no deck runs together
                line = lines[f"card:{canon}"]
                assert (line.decks, line.total_decks) == (len(both), own), (a, b)


def _year_by_year(conn, a: str, b: str) -> tuple[list[list[str]], list]:
    """A pair's year-by-year rows and its head's tiles, off one reading of the three
    series, as the page builds both."""
    cards = [(canon, card_adoption_over_time(conn, canon)) for canon in (a, b)]
    together = pair_adoption_over_time(conn, a, b)
    return _table_rows(_pair_years_card(together, cards)), _pair_tiles(together, cards)


def test_a_pairs_year_by_year_shares_agree_with_the_raw_deck_lists_on_the_real_record(
    live_graph
):
    # Issue #351: every cell against a reading it is not built from. Each card's
    # decks off the raw rows, split by the year each deck's event falls in, and the
    # two sets intersected here.
    conn = live_graph
    year_of = dict(rows(conn.execute(
        "MATCH (d:Deck)-[:PLAYED_AT]->(:Event)-[:IN_YEAR]->(y:Year) RETURN d.deckId, y.year")))
    years = sorted(set(year_of.values()))

    def decks_running(canon: str) -> set[str]:
        return {deck for (deck,) in rows(conn.execute(
            "MATCH (d:Deck)-[:CONTAINS]->(:Card {canon: $canon}) RETURN DISTINCT d.deckId",
            {"canon": canon}))}

    def in_year(decks: set[str], year: int) -> int:
        return sum(year_of[deck] == year for deck in decks)

    pairs = _real_pairs(conn)
    assert len(pairs) > 20 and len(years) >= 4
    stated = unstated = 0
    for a, b in pairs:
        table, _ = _year_by_year(conn, a, b)
        running = {canon: decks_running(canon) for canon in (a, b)}
        both = running[a] & running[b]
        assert [row[0] for row in table] == [str(year) for year in years], (a, b)
        for row, year in zip(table, years):
            for cell, canon in zip(row[1:], (a, b)):
                own, shared = in_year(running[canon], year), in_year(both, year)
                if own:
                    stated += 1
                    assert cell == (f"{numfmt.share(shared / own)} <span class='base'>"
                                    f"({shared:,} / {own:,})</span>"), (a, b, year)
                else:
                    unstated += 1
                    assert cell == "No decks", (a, b, year)
    # Both kinds of cell were graded: a share, and a year a card sat out.
    assert stated > 100 and unstated > 0


def test_a_cards_year_by_year_counts_sum_to_its_tile_on_the_real_record(live_graph):
    # Issue #351: the table is the head's last two tiles split by year, so a card's
    # rows add up to its tile, both the decks running both and the decks running it.
    conn = live_graph
    for a, b in _real_pairs(conn):
        table, tiles = _year_by_year(conn, a, b)
        for column, tile in zip((1, 2), tiles[2:]):
            counts = [re.search(r"\(([\d,]+) / ([\d,]+)\)", row[column]) for row in table]
            shared, own = (
                sum(int(found.group(part).replace(",", "")) for found in counts if found)
                for part in (1, 2))
            assert tile.note == f"{numfmt.count_of(shared, own)} run both", (a, b)


def _captions(markup: str) -> list[str]:
    return re.findall(r"<div class='caption'>(.*?)</div>", markup)


def test_every_caption_on_the_pilot_and_card_pages_follows_the_caption_rule(demo):
    # The caption rule (#329): a sentence in the text colour, at most one highlighted
    # figure, no node count, no fragments strung on middle dots. Every caption the two
    # tabs' own callbacks hand the page, over both views of each.
    pages = "".join(
        _drawn(demo, "draw_pilots", "pilot_overview", "Jordan C")
        + _drawn(demo, "draw_head_to_head_view", "Jordan C", "AmberTealViper")
        + _drawn(demo, "draw_cards", "card_overview", "brainstorm", "", None, 15, False)
        + _drawn(demo, "draw_cards", "card_cooccurrence", "brainstorm", "", "daze", 5, True)
        + _drawn(demo, "draw_cards", "card_cooccurrence", "brainstorm", "", None, 5, False)
    )

    captions = _captions(pages)
    assert len(captions) >= 10
    for caption in captions:
        said = re.sub(r"<[^>]+>", "", caption)
        assert caption.count("class='figure'") <= 1, said
        assert "·" not in said and not re.search(r"\bnodes?\b", said), said
        assert said.endswith("."), said
    # And none is left in either of the roles a card sets in the accent.
    assert "t-caption" not in pages and "t-fieldstat" not in pages


def test_a_graph_card_says_what_it_draws_in_a_sentence(demo):
    _, _, neighbourhood, affinity, *_ = _drawn(
        demo, "draw_pilots", "pilot_overview", "Jordan C")
    said = lambda markup: [re.sub(r"<[^>]+>", "", c) for c in _captions(markup)]  # noqa: E731

    assert said(neighbourhood) == [
        "2 events, each with the deck played and where it finished."]
    assert said(affinity) == [
        "1 archetype, grouped by broad class and sized by the events played."]

    usage = _drawn(demo, "draw_cards", "card_overview", "daze", "Main", None, 15, False)[2]
    assert said(usage) == ["How much of each broad class and archetype runs it."]

    pair = _drawn(demo, "draw_cards", "card_cooccurrence", "brainstorm", "", "daze", 5, True)
    # The card's own sentence, then the one its panel's list carries (issue #330).
    assert said(pair[9]) == [
        "The 5 cards most often in the decks that run it with Daze, lands left out.",
        "Each rate is a share of the 2 decks running both Brainstorm and Daze."]


# --- A name opens its page (issue #333) ---


def _opened(markup: str) -> list[tuple[str, str, str]]:
    """Every name in ``markup`` that opens a page: its kind, its key and the name."""
    return [(kind, html.unescape(key), html.unescape(name)) for kind, key, name in re.findall(
        r"<a href='#' data-opens='([^']*)' data-key='([^']*)'>([^<]*)</a>", markup)]


def test_a_records_archetype_is_the_way_to_its_page_where_it_has_one():
    # A pilot's record names an archetype per deck, by its display name. The name is
    # a link where an archetype of that name has a page, and the text it was where
    # none does: six archetypes of the real record have no finishes line to draw, so
    # the picker does not offer them and a link would open nothing.
    record = [_deck(archetype="Grixis"), _deck(archetype="Soul Sisters", deck_id="d2"),
              _deck(archetype=None, deck_id="d3")]
    pages = Pages(archetypes={"grixis": "Grixis"})

    table = _record_table(record, pages)

    assert _opened(table) == [("archetype", "grixis", "Grixis")]
    # The one with no page is set in the muted ink, told apart from a link (#340);
    # a deck with no archetype has an empty cell, with nothing to set apart.
    assert [row[2] for row in _table_rows(table)][1:] == [
        "<span class='no-page'>Soul Sisters</span>", ""]
    # The card under the same table, and the panel's short copy of it beside the graph.
    assert _opened(_record_card(record, pages)) == [("archetype", "grixis", "Grixis")]
    decks = _results(2)
    _, panel = _graph_list("pilot_neighbourhood", _drawing(decks), decks, pages=pages)
    assert _opened(panel) == [("archetype", "grixis", "Grixis")] * 2


def test_an_archetype_listed_on_a_pilots_or_a_cards_page_is_the_way_to_its_page():
    # The lists that hold an archetype by its node, which carries the tag its picker
    # draws it from: a pilot's archetypes beside the affinity graph, a card's ten
    # beside the usage graph, and all of a card's in the table under the plots. One
    # archetype of each has no page, and stays text.
    pages = Pages(archetypes={"grixis": "Grixis", "storm": "Storm"})
    affinity = Subgraph(
        nodes=[Node("pilot:ada", "Ada L", "Pilot"), Node("macro:tempo", "tempo", "Macro", weight=5),
               Node("arch:grixis", "Grixis", "Archetype", weight=3),
               Node("arch:soul_sisters", "Soul Sisters", "Archetype", weight=2)],
        edges=[Edge("pilot:ada", "macro:tempo", "PLAYS:5", events=5),
               Edge("macro:tempo", "arch:grixis", "PLAYS:3", events=3),
               Edge("macro:tempo", "arch:soul_sisters", "PLAYS:2", events=2)],
    )
    usage = Subgraph(
        nodes=[Node("card:x", "X (12% of meta)", "Card", decks=120, total_decks=1000),
               Node("macro:combo", "combo", "Macro"),
               Node("arch:storm", "Storm", "Archetype"),
               Node("arch:soul_sisters", "Soul Sisters", "Archetype")],
        edges=[Edge("card:x", "macro:combo", "12%", visible=True, decks=120, total_decks=1000),
               Edge("macro:combo", "arch:storm", "50%", visible=True, decks=10, total_decks=20),
               Edge("macro:combo", "arch:soul_sisters", "10%", visible=True, decks=1,
                    total_decks=10)],
    )

    _, played = _graph_list("pilot_affinity", affinity, {}, pages=pages)
    _, by_share = _graph_list("card_usage", usage, {}, pages=pages)
    under_the_plots = _archetype_card(usage, pages)

    assert _opened(played) == [("archetype", "grixis", "Grixis")]
    assert _opened(by_share) == _opened(under_the_plots) == [("archetype", "storm", "Storm")]
    for listed in (played, by_share, under_the_plots):
        assert "<span class='no-page'>Soul Sisters</span>" in listed
    # A broad class heads its archetypes and is no subject: it has no page to open,
    # and is not set as a name missing one, so the heading and the thin archetype
    # under it do not read alike (#340).
    assert ">Combo<" in under_the_plots
    assert under_the_plots.count("no-page") == 1


def test_a_clicked_node_names_the_subjects_it_can_open():
    # A node's details are where a reader meets a subject they did not ask about: the
    # card a gem is, the archetype it was found in, who played the deck that ran it.
    # Each is the way to its own page. The neighbours listed under the facts stay a
    # way round the graph, as they were (#330): a click there selects the node.
    gems, decks = _gem_graph()
    gems.edges.append(Edge("arch:lands", "deck:d1", "IN"))  # two neighbours, so listed
    pages = Pages(pilots={"ada": "Ada L"}, cards={"port": "Rishadan Port"},
                  archetypes={"lands": "Lands"})

    said = {facts.node: facts for facts in _node_facts("meta_gems", gems, decks, pages=pages)}

    gem, archetype, deck = said["card:lands:port"], said["arch:lands"], said["deck:d1"]
    assert _opened(gem.label) == [("card", "port", "Rishadan Port")]
    assert _opened(dict(gem.rows)["Archetype"]) == [("archetype", "lands", "Lands")]
    assert _opened(archetype.label) == [("archetype", "lands", "Lands")]
    assert _opened(dict(deck.rows)["Pilot"]) == [("pilot", "ada", "Ada L")]
    assert _opened(dict(deck.rows)["Archetype"]) == [("archetype", "lands", "Lands")]
    # A deck is no subject, and neither is anything else its facts state.
    assert deck.label == "Ada L · 3rd"
    assert [_opened(value) for label, value in deck.rows
            if label not in ("Pilot", "Archetype")] == [[], [], []]
    assert archetype.linked and all(not _opened(other.label) for other in archetype.linked)


def test_a_page_never_links_to_itself(tmp_path, snapshot_dir, built_graph):
    # A graph names its own subject over and over: the pilot at the hub and on every
    # deck's facts, the card a usage graph is about. On the subject's own page, its
    # tab's first view, a link there would open what is already open, so the subject
    # is text and every other subject drawn beside it is a link.
    #
    # The other view of each tab is not the subject's page. A head-to-head names two
    # pilots and a co-occurrence graph starts from a card, and there each is a link
    # like any other name: the click opens that pilot's or that card's overview.
    conn = built_graph(tmp_path, snapshot_dir)
    pages = Pages(pilots={"Jordan C": "Jordan C", "AmberTealViper": "Tom S"},
                  cards={"brainstorm": "Brainstorm", "daze": "Daze", "ponder": "Ponder"},
                  archetypes={"grixis": "Grixis", "storm": "Storm"})

    def opened(view, values, subgraph):
        return set(_opened(_drawn_card(view, values, subgraph, _on_record(conn, subgraph),
                                       pages)))

    alone = opened("pilot_neighbourhood", {"pilot": "Jordan C", "pilot2": None},
                   pilot_subgraph(conn, "Jordan C"))
    played = opened("pilot_affinity", {"pilot": "Jordan C", "pilot2": None},
                    pilot_affinity_subgraph(conn, "Jordan C"))
    usage = opened("card_usage", {"card": "brainstorm", "card_board": ""},
                   card_usage_subgraph(conn, "brainstorm"))
    rivals = opened("pilot_neighbourhood", {"pilot": "Jordan C", "pilot2": "AmberTealViper"},
                    pilot_subgraph(conn, "Jordan C", "AmberTealViper"))
    beside = opened("card_cooccurrence",
                    {"card": "brainstorm", "cooccur_card2": None, "cooccur_top_n": 5,
                     "cooccur_drop_lands": False},
                    card_cooccurrence_subgraph(conn, "brainstorm", top_n=120))

    grixis, storm = ("archetype", "grixis", "Grixis"), ("archetype", "storm", "Storm")
    jordan, tom = ("pilot", "Jordan C", "Jordan C"), ("pilot", "AmberTealViper", "Tom S")
    # Jordan C's decks are Grixis; the pair's shared event has Tom S's Storm as well.
    assert alone == played == {grixis}
    assert usage == {grixis, storm}
    assert rivals == {grixis, storm, jordan, tom}
    assert beside == {("card", "brainstorm", "Brainstorm"), ("card", "daze", "Daze"),
                      ("card", "ponder", "Ponder")}
    # The subject is not a link, but it has a page, the one open: it is not set in
    # the muted ink that marks a name with no page (#340).
    own = _drawn_card("pilot_neighbourhood", {"pilot": "Jordan C", "pilot2": None},
                      pilot_subgraph(conn, "Jordan C"),
                      _on_record(conn, pilot_subgraph(conn, "Jordan C")), pages)
    assert "Jordan C" in own and "no-page" not in own


def test_the_built_page_answers_a_clicked_name_on_the_controls_that_draw_its_page(demo):
    # One hidden block answers every name. Its click sets six controls, in the order
    # its answer is written in: the pilot and the Pilots view, the card and the Cards
    # view, the archetype and the one it is compared with. The suite's snapshot can
    # only click a card in a browser, so the other two kinds are held here.
    import gradio as gr

    answer = next(dep for dep in demo.fns.values()
                  if getattr(dep.fn, "__name__", "") == "open_page")

    def control(block) -> str:
        return block.elem_id or block.choices[0][1]

    assert [control(block) for block in answer.outputs] == [
        "pilot-search", "pilot_overview", "card-search", "card_overview",
        "timeline-archetype", "timeline-second-archetype",
    ]
    (source,) = [block for block in demo.blocks.values()
                 if isinstance(block, gr.HTML) and "data-opens" in (block.js_on_load or "")]
    assert source.visible == "hidden" and answer.targets == [(source._id, "click")]

    same = gr.update()
    assert answer.fn(gr.EventData(source, {"kind": "pilot", "key": "Jordan C"})) == [
        "Jordan C", "pilot_overview", same, same, same, same]
    assert answer.fn(gr.EventData(source, {"kind": "card", "key": "brainstorm"})) == [
        same, same, "brainstorm", "card_overview", same, same]
    # Neither archetype of the snapshot has a page, so a click naming one sets nothing.
    assert answer.fn(gr.EventData(source, {"kind": "archetype", "key": "grixis"})) == [same] * 6


# --- Metagame's list of the most played (issue #334) ---


def _listed(markup: str) -> list[dict]:
    """Each row of the most-played list as what it states, read off its markup."""
    def text(cell: str) -> str:
        return html.unescape(re.sub(r"<[^>]+>", "", cell))

    rows = []
    for row in re.findall(r"<tr>(.*?)</tr>", markup.split("<tbody>")[1]):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row)
        rank, name, share, decks = cells[:4]
        rows.append({
            "rank": text(rank), "name": text(name),
            "opens": re.findall(r"data-key='(.*?)'", name),
            "share": text(re.search(r"<span class='share-now'>(.*?)</span>", share)[1]),
            "decks": text(decks),
            "change": text(cells[4].split("<span")[0]) if len(cells) == 6 else None,
            "from": (text(re.search(r"<span class='share-from'>(.*?)</span>", cells[4])[1])
                     if len(cells) == 6 else None),
            "years": re.findall(r"title='(.*?)'", cells[-1]),
        })
    return rows


def test_the_list_states_each_archetypes_share_its_change_and_its_years():
    # Worked by hand off the rows above. 2024 is the latest year, of 200 decks: Storm
    # 30 (15%), Grixis and Lands 12 each (6%), Zoo 3 (1.5%), Jund none. In 2023, of 50
    # decks, Grixis had 5 (10%) and the other three listed had none. So Storm is up 15
    # from nothing and Grixis is down 4 from 10. Jund is not listed: it has no deck
    # in 2024, whatever it had before. Grixis and Lands are level on 12 decks, so both
    # are 2nd, in tag order, and Zoo after them is 4th: the rank an archetype's own
    # page states (#332), which never places one of several equals ahead.
    markup = _share_list(_shares(*_SHARES), Pages(archetypes={"grixis": "Grixis"}))

    assert [(row["rank"], row["name"], row["share"], row["decks"], row["change"],
             row["from"]) for row in _listed(markup)] == [
        ("1", "Storm", "15%", "30 / 200", "+15%", "from 0%"),
        ("2", "Grixis", "6%", "12 / 200", "\u22124%", "from 10%"),
        ("2", "Lands", "6%", "12 / 200", "+6%", "from 0%"),
        ("4", "Zoo", "1.5%", "3 / 200", "+1.5%", "from 0%"),
    ]
    # Every year on record, each with the decks it rests on.
    assert _listed(markup)[1]["years"] == ["2023: 10%, 5 / 50 decks",
                                           "2024: 6%, 12 / 200 decks"]
    # A name is the way to its page where the archetype has one (#333).
    assert [row["opens"] for row in _listed(markup)] == [[], ["grixis"], [], []]
    # The columns say which years they compare, and the caption how many are listed
    # and by what rule.
    assert "Share of 2024 decks" in markup and "Change from 2023" in markup
    assert ("<div class='caption'>The <span class='figure'>4</span> archetypes with at "
            "least 1% of 2024's decks, most played first.") in markup


def test_an_archetype_under_one_percent_of_the_latest_year_is_not_listed():
    # 1 of 200 decks is half a percent; 2 of 200 is 1% exactly, and is listed.
    shares = _shares(("grixis", 2024, 197, 200), ("zoo", 2024, 2, 200),
                     ("jund", 2024, 1, 200))

    assert [row["name"] for row in _listed(_share_list(shares, Pages()))] == [
        "Grixis", "Zoo"]


def test_a_record_of_one_year_lists_shares_with_no_change_to_state():
    # A change is against the year before, and a record of one year has none: the
    # column is left out, where a dash in every row would be a column of nothing.
    markup = _share_list(_shares(("grixis", 2024, 6, 10), ("zoo", 2024, 4, 10)), Pages())

    assert "Change" not in markup
    assert [(row["name"], row["share"], row["change"]) for row in _listed(markup)] == [
        ("Grixis", "60%", None), ("Zoo", "40%", None)]


def test_a_record_with_no_archetype_deck_says_so_where_the_list_would_be():
    markup = _share_list(Series(cells=[]), Pages())

    assert "<table" not in markup
    assert "No archetypes to list yet." in markup


def test_every_listed_share_and_change_is_the_one_the_share_chart_draws(live_graph):
    # AC (#334): every share and change in the list agrees with the yearly shares
    # the share chart draws. The second reading is the chart on each archetype's own
    # page, point by point: what it prints over a year's point is what the list's
    # column for that year states, the latest year's is the row's share, and the
    # change is the gap between the last two as written (design doc, section 4). And
    # a row's rank is the one the rank tile of that archetype's page states.
    shares = meta_share_over_time(live_graph)
    listed = _listed(_share_list(shares, Pages()))
    tags = most_played(shares)
    # Who is listed, counted a second way: straight off the graph.
    year = latest_deck_year(shares)
    ((total,),) = rows(live_graph.execute(
        "MATCH (d:Deck)-[:PLAYED_AT]->(:Event)-[:IN_YEAR]->(y:Year {year: $year}) "
        "RETURN count(DISTINCT d)", {"year": year}))
    counted = [tag for tag, decks in rows(live_graph.execute(
        "MATCH (d:Deck)-[:HAS_ARCHETYPE {isPrimary: true}]->(a:Archetype), "
        "(d)-[:PLAYED_AT]->(:Event)-[:IN_YEAR]->(y:Year {year: $year}) "
        "RETURN a.tag, count(DISTINCT d)", {"year": year})) if decks * 100 >= total]
    assert sorted(tags) == sorted(counted) and len(listed) == len(tags) > 1

    for row, tag in zip(listed, tags, strict=True):
        (line,) = _trend_figure(shares, [tag]).data
        printed = [f"{at}: {share}, {decks}" for at, (share, decks)
                   in zip(line.x, line.customdata)]
        assert row["years"] == printed, tag
        assert line.name == row["name"], tag
        _, rank, _, _ = _archetype_tiles(tag, shares, None, 0, 1)
        assert re.match(r"\d+", rank.value).group() == row["rank"], tag
        now, before = line.customdata[-1][0], line.customdata[-2][0]
        assert row["share"] == now and row["from"] == f"from {before}", tag
        moved = round(float(now.rstrip("%")) - float(before.rstrip("%")), 2)
        sign = "+" if moved > 0 else "\u2212" if moved < 0 else ""
        assert row["change"] == f"{sign}{abs(moved):.2f}".rstrip("0").rstrip(".") + "%", tag


def test_the_change_is_against_the_year_before_not_the_first_on_record():
    # Three years: 40%, then 10%, then 25%. The row is up 15 from 10, not down 15
    # from 40, and each year keeps its own column.
    shares = _shares(("grixis", 2022, 40, 100), ("grixis", 2023, 10, 100),
                     ("grixis", 2024, 25, 100))

    (row,) = _listed(_share_list(shares, Pages()))

    assert (row["share"], row["change"], row["from"]) == ("25%", "+15%", "from 10%")
    assert row["years"] == ["2022: 40%, 40 / 100 decks", "2023: 10%, 10 / 100 decks",
                            "2024: 25%, 25 / 100 decks"]
