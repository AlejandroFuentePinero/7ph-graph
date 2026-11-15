"""What a pilot's or a card's page states in numbers (issue #329).

The page head's tiles and the tables under the graphs, as the pure functions the app
builds them with. Each is read off the same query result the page's graph or chart
draws, so the two cannot disagree; the tests here hold each figure to the fixture's
own files, and on the real record to a second, independent reading.
"""

import html
import re
from datetime import datetime

import pytest

from graph7ph import numfmt
from graph7ph.app import (
    _archetype_card,
    _card_tiles,
    _pilot_descriptor,
    _pilot_tiles,
    _played_with_card,
    _record_card,
    _record_table,
    build_app,
)
from graph7ph.build import build_graph
from graph7ph.db import rows
from graph7ph.models import load_snapshot
from graph7ph.query import (
    Edge,
    Node,
    PilotDeck,
    Subgraph,
    card_cooccurrence_subgraph,
    card_deck_counts,
    card_usage_subgraph,
    pilot_affinity_subgraph,
    pilot_record,
    pilot_subgraph,
)
from graph7ph.trends import AdoptionCell, Series, card_adoption_over_time


def _facts(tiles) -> list[tuple]:
    return [(tile.label, tile.value, tile.note) for tile in tiles]


def test_a_pilots_tiles_are_four_neutral_facts(tmp_path, snapshot_dir, built_graph):
    # The fixture's Jordan C: Grixis at two events, 12th at one and 5th at the other.
    # Four facts and no judgement: no tile states an average or a mean finish, which
    # stays in the performance chart's caption.
    conn = built_graph(tmp_path, snapshot_dir)

    tiles = _pilot_tiles(pilot_subgraph(conn, "Jordan C"),
                         pilot_affinity_subgraph(conn, "Jordan C"),
                         pilot_record(conn, "Jordan C"))

    assert _facts(tiles) == [
        ("Events played", "2", None),
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
            _neighbourhood(len(record)), _affinity(("Grixis", 1)), record))[3]

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
        [_deck()],
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


def _assert_the_page_agrees(conn, pilot: str, played: dict) -> None:
    """The tiles, the record table's rows and the two graphs, for one pilot."""
    neighbourhood = pilot_subgraph(conn, pilot)
    affinity = pilot_affinity_subgraph(conn, pilot)
    record = pilot_record(conn, pilot)
    events, archetypes, most, best = _pilot_tiles(neighbourhood, affinity, record)

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
    assert events.value == f"{len({r.event for r in record}):,}", pilot
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
    for pilot in _every_pilot(conn):
        _assert_the_page_agrees(conn, pilot, played.get(pilot, {}))


def test_every_pilots_tiles_table_and_graphs_agree_on_the_real_record(live_graph):
    # Every pilot on the record, not a sample: the claim is about the page, and any
    # pilot's page can be opened.
    pilots = _every_pilot(live_graph)
    assert len(pilots) > 1000
    played = _archetype_events(live_graph)
    for pilot in pilots:
        _assert_the_page_agrees(live_graph, pilot, played.get(pilot, {}))


def test_a_cards_tiles_are_its_adoption_its_change_its_decks_and_its_archetypes(
    tmp_path, snapshot_dir, built_graph
):
    # The fixture's Daze: in both Grixis decks and not the Storm one, so 2 of the 3
    # decks of 2025, the record's only year.
    conn = built_graph(tmp_path, snapshot_dir)

    tiles = _card_tiles(card_usage_subgraph(conn, "daze"),
                        card_adoption_over_time(conn, "daze"))

    assert _facts(tiles) == [
        ("Adoption in 2025", "66.67%", "2 / 3 decks"),
        ("Change from last year", "n/a", "No earlier year on record"),
        ("Decks", "2", "of 3 on record"),
        ("Archetypes running it", "1", "in 1 broad class"),
    ]


def _adoption(*year_count_total) -> Series:
    return Series(cells=[AdoptionCell(year, count, count / total, total)
                         for year, count, total in year_count_total])


_A_CARD = Subgraph(nodes=[Node("card:x", "X (5% of meta)", "Card", decks=50, total_decks=1000)],
                   edges=[])


def test_a_cards_change_is_the_latest_years_share_less_the_year_befores():
    tiles = _card_tiles(_A_CARD, _adoption((2024, 1, 100), (2025, 3181, 10000),
                                           (2026, 3412, 10000)))

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


def test_the_cards_a_card_is_played_with_are_ranked_with_the_rates_the_edges_carry():
    card = _played_with_card(_package(("Ponder", 1203), ("Daze", 780)))

    header = re.findall(r"<th(?: [^>]*)?>(.*?)</th>", card.split("<tbody>")[0])
    assert header == ["#", "Card", "Decks together", "Rate"]
    assert _table_rows(card) == [
        ["1", "Ponder", "1,203 / 1,560", "77.12%"],
        ["2", "Daze", "780 / 1,560", "50%"],
    ]
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

    card = _played_with_card(both)

    assert _table_rows(card) == [["1", "C", "281 / 312", "90.06%"]]
    assert "the <span class='figure'>312</span> decks running both A and B" in card


def test_a_card_with_nothing_beside_it_gets_a_line_not_an_empty_table():
    alone = _played_with_card(_package())
    assert "<table" not in alone and "No card to list" in alone

    # Two cards no deck runs together: two seeds and no hub.
    apart = _played_with_card(Subgraph(
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
    cards = _real_cards(conn)
    assert len(cards) > 100

    for canon in cards:
        for board in (None, "Main", "Side"):
            usage = card_usage_subgraph(conn, canon, board)
            adoption = card_adoption_over_time(conn, canon, board)
            _, _, decks, archetypes = _card_tiles(usage, adoption)
            where = (canon, board)

            assert decks.value == f"{sum(cell.count for cell in adoption.cells):,}", where

            running = list(rows(conn.execute(
                "MATCH (a:Archetype)<-[:HAS_ARCHETYPE {isPrimary: true}]-(d:Deck)"
                "-[c:CONTAINS]->(:Card {canon: $canon}) "
                + ("WHERE c.board = $board " if board else "")
                + "RETURN a.tag, a.name, count(DISTINCT d)",
                {"canon": canon, "board": board} if board else {"canon": canon},
            )))
            assert archetypes.value == f"{len(running):,}", where
            assert sorted(_archetype_rows(_archetype_card(usage))) == sorted(
                (name, numfmt.count_of(n, totals[tag]), numfmt.share(n / totals[tag]))
                for tag, name, n in running
            ), where

        # One card's view has no board: a rate is a share of every deck running it.
        played_with = _played_with_card(card_cooccurrence_subgraph(conn, canon))
        either = _card_tiles(card_usage_subgraph(conn, canon),
                             card_adoption_over_time(conn, canon))[2]
        assert f"<span class='figure'>{either.value}</span> decks" in played_with, canon


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
    return re.findall(r"<div class='tile-label'>(.*?)</div>", markup)


def test_a_drawn_pilot_page_opens_with_its_head_and_closes_with_its_record(demo):
    head, *_, record = _drawn(demo, "draw_pilots", "pilot_overview", "Jordan C")

    # The name, the descriptor, then the four tiles, none of them an average.
    assert (head.index("page-name") < head.index("Jordan C")
            < head.index("Pilot, on record in 2025.") < head.index("tile-label"))
    assert _tile_labels(head) == [
        "Events played", "Archetypes played", "Most-played archetype", "Best finish"]
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
    # Daze is in both Grixis decks, which are the fixture's whole tempo class.
    assert _table_rows(archetypes) == [
        ["Tempo", "2 / 2", "100%"],
        ["Grixis", "2 / 2", "100%"],
    ]
    # And the tiles are the same reading, under the same board: Daze is in the main
    # board of 2 of the 3 decks of 2025, both of the one archetype in the table.
    assert re.findall(r"<div class='tile-value'>(.*?)</div>", head) == [
        "66.67%", "n/a", "2", "1"]


def test_a_cards_played_with_list_is_on_the_view_that_draws_those_cards(demo):
    outputs = _drawn(demo, "draw_cards", "card_cooccurrence", "daze", "", None, 5, False)
    head, played_with = outputs[7], outputs[-1]

    # The view draws no usage graph and has no board control, so its head is the
    # card over either board: the tiles Card overview shows on "Main or side".
    either = _drawn(demo, "draw_cards", "card_overview", "daze", "", None, 15, False)[0]
    tiles = r"<div class='tiles'>.*</div>"
    assert "Daze" in head
    assert re.search(tiles, head).group() == re.search(tiles, either).group()
    rows = _table_rows(played_with)
    # Five asked for, five listed, ranked, each in both of Daze's two decks at most.
    assert [row[0] for row in rows] == ["1", "2", "3", "4", "5"]
    assert all(row[2] in {"2 / 2", "1 / 2"} for row in rows)
    assert rows[0][2:] == ["2 / 2", "100%"]
    # A rate is a share of the decks the Decks tile counts.
    assert "Daze's <span class='figure'>2</span> decks" in played_with


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
    assert said(pair[9]) == [
        "The 5 cards most often in the decks that run it with Daze, lands left out."]
