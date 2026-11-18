import json
from collections import Counter
from dataclasses import replace
from datetime import datetime

import ladybug
import pytest

from graph7ph.build import (
    MIN_CUT_FIELD,
    RegistrationSpread,
    TwoWinners,
    _check_sole_winner,
    build_graph,
    corrected_field,
    graph_counts,
    reconciliation_path,
)
from graph7ph.curation import (
    ArchetypeOverride,
    Curation,
    CurationError,
    Hold,
    load_curation,
)
from graph7ph.db import (
    artifact_path,
    database_path,
    open_database,
    open_for_reading,
    rows,
)
from graph7ph.models import Deck, load_snapshot
from graph7ph.repairs import Refile, Repairs, apply_repairs, load_repairs


def _scalar(conn, query, params=None):
    return conn.execute(query, params or {}).get_next()[0]


# Every deck in the real record that carries no placement at all, and the title
# showing why nothing could recover one: the source's own explicit unknown
# ("??st", "XXth"), or a title with no placement in it whatsoever. These are the
# whole of what stays unranked after minting (issue #162), 24 decks across 4
# events under 24 distinct pilots. Enumerated rather than counted, so the next
# class of uncertainty fails a test the build it lands rather than surfacing as a
# gap somebody notices in a chart months later.
UNRANKABLE = {
    "3Xmk6tmp4EmdsjhAR4hsxg": "Jed - Oath Reanimator - Area52IQ",
    "H8Imnz5dakug-6FSqzKNZg": "Darcy - Mono R - Area52IQ",
    "L-L3SafKt0OI6BpxB8XIZQ": "Jett - 8pt UR Prowess - Area52IQ",
    "VQBHAQS_IkGWUYgfC5_2lQ": "Jake P - 4C Midrange - Area52IQ",
    "fDtwpjhL8Eyc-PTcTvNCiA": "Connor P - 4C Kiki Pod - Area52IQ",
    "yVyG8D8DQ0CCT1EvsfmF1g": "James L - Atraxa Walks - Area52IQ",
    "5heC6vkMyUKOq7zYO8Df3w": "??st Matt B - Food - CFWAT25",
    "UyIGbLg7Pk2itMPCiaVSRg": "??st Andrew V - Mox Jund - CFWAT25",
    "5EzR6_GoHkOTm82Z03CbNg": "Bennett - Omnath Walks - DeckaDiceIQ",
    "I4hp44SJ80i5DywYFPpi7g": "Clement - Orzhov Aristocrats - DeckaDiceIQ",
    "hLkyJInRh0Cg4KBowWNBdA": "Jake - BR Artifcacts - DeckaDiceIQ",
    "lrzNuDcV2kuAX277mTkeDg": "Reece - Jund - DeckaDiceIQ",
    "6LithT-HokGwFqVgONI8Yw": "XXth Jenny O - Goblins - GGWAD",
    "8BGY3Qy_LEewzJeeQGDKBg": "XXth Thomas S - 8pt Izzet Tempo - GGWAD",
    "9Bg-4iGBbEaxm--QNjPmHg": "XXth Cody W - Hardened Scales - GGWAD",
    "A-rTUVcaDUyPFNoo3ESIoA": "XXth Harry F - Goblins - GGWAD",
    "Chr2KinMQEutlvNfwxL4dg": "XXth Chris KH - Shops - GGWAD",
    "DAcvFcMfjEikQflSmLOePA": "XXth Jake S - Gorgeous Reanimator - GGWAD",
    "K7rXbSY7sECJVwNXBrQrDw": "XXth Jordan L - Nadu Walks - GGWAD",
    "OXXJoh8Pbk-YGiw_b46o1Q": "XXth Daniel T - 8pt Omnath/Nadu Midrange - GGWAD",
    "bo7EBYfa20evPxlesiNMog": "XXth Sophie P - Bus Driver Grixis - GGWAD",
    "eijogJHP0UOQna3oW25Glg": "XXth Jayden G - Breach Bond - GGWAD",
    "qFCberD4WUWYWZVUbKQx2g": "XXth Chris D - Jund Reanimator - GGWAD",
    "uWQwygs90UK_2Km7hEm0FA": "XXth Mark S - 8pt Boros Equipment - GGWAD",
}


def test_every_deck_in_the_record_is_ranked_or_says_it_cannot_be(live_graph):
    # The invariant minting exists to establish, over the whole record rather than a
    # fixture: a deck either carries a norm, and so reaches every metric that reads
    # one, or it says outright that no rule could give it one. Nothing sits between
    # the two, which is where the 28 decks of issue #162 sat, holding a placement the
    # project had already decided while falling out of every mean and every chart.
    #
    # Both provenance columns are read, because between them they say *why* a deck is
    # unranked, which one column cannot. A `normImputed` of "none" means no rule could
    # give it a norm; the `placementImputed` beside it says whether that is because
    # nothing could recover a placement (the whole of the record today) or because
    # a placement was recovered and the event's field could not normalise it.
    unranked = {
        deck_id: (name, placement_rule, norm_rule)
        for deck_id, name, placement_rule, norm_rule in rows(live_graph.execute(
            """MATCH (d:Deck) WHERE d.placementNorm IS NULL
               RETURN d.deckId, d.name, d.placementImputed, d.normImputed"""))
    }
    assert unranked == {
        deck_id: (name, "none", "none") for deck_id, name in UNRANKABLE.items()
    }

    # No deck is ranked without a placement to rank it: the inverse gap, which the
    # record has never held and which minting cannot introduce, since it mints from a
    # placement.
    assert _scalar(live_graph, """MATCH (d:Deck)
        WHERE d.placementNorm IS NOT NULL AND d.placement IS NULL
        RETURN count(d)""") == 0


def _deck(deck_id, event, created_at, **overrides):
    """A minimal deck record, for snapshots crafted to exercise one behaviour.

    The title carries the deck id so each recovers a distinct name: same-named
    decks join on identity (ADR 0007) and a card-identical pair then collapses,
    which would silently merge fixtures meant to stay separate registrations.

    ``overrides`` replaces any source field, for the fixtures that need a
    particular placement, field size or event type.
    """
    return {"deckId": deck_id, "name": deck_id, "deckName": "n", "pilot": deck_id,
            "event": event, "eventId": f"evt_{event}", "eventType": "Tournament",
            # A field no fixture cohort contradicts, so a deck built for some
            # other behaviour is not swept up by the field-size correction.
            "placement": 1, "placementNorm": 0.0, "eventSize": 32,
            "createdAt": created_at,
            "colour": "colour:U", "macro": "macro:control", "engineTags": [],
            "engineTagLabels": {}, "primaryTag": "", "primaryTagWeights": {}
            } | overrides


def _write_snapshot(path, decks):
    (path / "decks.json").write_text(json.dumps(decks))
    (path / "cards_index.json").write_text(json.dumps({
        "v": 2,
        "cards": [{"canon": "island", "name": "Island", "type": "Lands",
                   "manaCost": None, "manaValue": 0.0, "reserved": False,
                   "points": 0, "pointsCompanion": 0}],
        "decks": {d["deckId"]: {"m": [0], "s": []} for d in decks},
    }))


def test_build_loads_nodes_and_edges_with_source_counts(snapshot_dir, tmp_path):
    snap = load_snapshot(snapshot_dir)

    counts = build_graph(snap, tmp_path / "graph")

    # Counts are read back out of the built graph, so this asserts the graph
    # actually holds one node/edge per source record (issue-2 AC #6).
    assert counts.pilots == 2
    assert counts.decks == 3
    assert counts.cards == 121
    assert counts.piloted_by == 3
    assert counts.contains == 225

    # The full v1 model's new node types (issue-3 AC #1).
    assert counts.events == 2          # CFWAT25, PogNov25
    assert counts.archetypes == 2      # Grixis, Storm
    assert counts.macros == 2          # tempo, combo
    assert counts.colours == 5         # the five atoms, always
    assert counts.card_types == 7      # distinct card types in the fixture

    # The new fact edges (issue-3 AC #2, #3).
    assert counts.played_at == 3       # one per deck
    assert counts.has_archetype == 3   # one tag per fixture deck
    assert counts.has_macro == 3
    assert counts.deck_colour == 10    # UBR + UBR + UBRG
    assert counts.card_colour == 95    # colours derived from mana pips
    assert counts.has_type == 121      # one per card

    # The temporal dimension (issue-26 AC #3): both fixture events sit in 2025.
    assert counts.years == 1
    assert counts.in_year == 2         # one per event


def test_event_links_to_the_year_its_decks_were_created_in(tmp_path, snapshot_dir, built_graph):
    conn = built_graph(tmp_path, snapshot_dir)

    assert _scalar(
        conn, "MATCH (:Event {event: 'PogNov25'})-[:IN_YEAR]->(y:Year) RETURN y.year"
    ) == 2025

    # Every Event links to exactly one Year (issue-26 AC #1): min and max both,
    # since min alone would miss an Event that had picked up two.
    assert list(rows(conn.execute(
        "MATCH (e:Event) OPTIONAL MATCH (e)-[:IN_YEAR]->(y:Year) "
        "WITH e, count(y) AS n RETURN min(n), max(n)",
    ))) == [[1, 1]]


def test_event_spanning_several_days_resolves_to_one_year(tmp_path):
    # An event's decks trickle in over days, and often across a month boundary;
    # they still collapse to the single Year the event ran in (issue-26 AC #7).
    # Shaped after PogNov25, whose real decks span 2025-11-29 to 2025-12-01.
    _write_snapshot(tmp_path, [
        _deck("d1", "PogNov25", "2025-11-29T13:01:51+00:00"),
        _deck("d2", "PogNov25", "2025-11-30T12:00:00+00:00"),
        _deck("d3", "PogNov25", "2025-12-01T05:41:48+00:00"),
    ])

    counts = build_graph(load_snapshot(tmp_path), tmp_path / "graph")
    conn = open_for_reading(tmp_path / "graph")

    assert counts.years == 1
    assert counts.in_year == 1
    assert _scalar(
        conn, "MATCH (:Event {event: 'PogNov25'})-[:IN_YEAR]->(y:Year) RETURN y.year"
    ) == 2025


def test_the_build_writes_the_database_and_its_report_into_one_bundle(
    snapshot_dir, tmp_path
):
    # The artifact is a directory containing the database, not a directory that is
    # the database (issue #47). Everything the build produces lands inside it, so
    # a single rename of that one path promotes the graph and its reports together.
    artifact = tmp_path / "graph"

    build_graph(load_snapshot(snapshot_dir), artifact)

    assert database_path(artifact).exists()
    assert reconciliation_path(artifact).exists()
    # Nothing escapes the bundle: a sibling would not travel with the rename.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["graph"]


def test_the_schema_declares_nine_node_tables_and_nine_relationship_tables(
    snapshot_dir, tmp_path, built_graph
):
    # The schema DDL carries two names that read as odd out of context, the
    # backticked `Macro` label and `isPrimary`, both inherited from the previous
    # engine's reserved words and kept deliberately (see `build.py`, ADR 0011).
    # This holds the whole DDL to the shape it declares rather than trusting that
    # a build which did not raise created every table it asked for.
    conn = built_graph(tmp_path, snapshot_dir)

    kinds = Counter(row[0] for row in rows(conn.execute("CALL show_tables() RETURN type")))

    assert kinds == {"NODE": 9, "REL": 9}


def test_the_build_leaves_a_settled_database_with_no_write_ahead_log(
    snapshot_dir, tmp_path
):
    # Ladybug keeps a `.wal` beside the database while it is open and folds it in
    # when the last Connection to it goes, so the build closes both rather than
    # leaving it to interpreter exit (issue #48). Otherwise promotion can rename a
    # bundle whose database is still carrying unsettled writes, and what the app
    # later opens is not what the build read its counts out of.
    #
    # What this can and cannot catch: it passes however `build_graph` closes,
    # because nothing outlives the call holding the Connection and refcounting
    # settles the file on the way out regardless. It guards the invariant, not the
    # line that delivers it, so it fails on a refactor that parks the Connection in
    # a longer-lived scope (a module global, a returned handle, a cache) and stays
    # green if the closing is deleted outright. The ordering rule that makes the
    # closing real is pinned separately, below.
    artifact = tmp_path / "graph"

    counts = build_graph(load_snapshot(snapshot_dir), artifact)

    # Named through `database_path`, so the database stays named in one place
    # (ADR 0008) and a later engine swap moves this test with it.
    assert sorted(p.name for p in artifact.iterdir()) == sorted(
        [database_path(artifact).name, "figures.json", "reconciliation.json",
         "provenance.json"]
    )
    # A fresh open sees the same graph the build reported, with nothing left to
    # replay: the counts are readable off the settled file alone.
    with open_database(artifact, read_only=True) as db, \
            ladybug.Connection(db) as reopened:
        assert graph_counts(reopened) == counts


def test_the_write_ahead_log_settles_on_the_connection_closing_not_the_database(
    tmp_path
):
    # The rule `build_graph` is built on, pinned because getting it wrong is
    # invisible: it costs nothing at the time and shows up as a torn artifact only
    # once something holds a Connection open a little longer (issue #50).
    #
    # The migration recorded this the wrong way round. Issue #48 believed an
    # explicit `Database.close()` was what checkpointed, and wrote that into the
    # build, the deploy guard, and this file. It is not: with a Connection still
    # alive, closing the Database leaves the log exactly where it was.
    #
    # If the second half of this ever goes red, that is good news rather than a
    # regression: it means Ladybug learned to settle on the Database closing, and
    # `build_graph` no longer has to care about the order it unwinds in.
    def written(path):
        db = ladybug.Database(str(path))
        conn = ladybug.Connection(db)
        conn.execute("CREATE NODE TABLE T(id INT64, PRIMARY KEY(id))")
        conn.execute("CREATE (:T {id: 1})")
        return db, conn

    def logs(path):
        return [p for p in path.parent.iterdir() if p.name.endswith(".wal")]

    connection_first = tmp_path / "a" / "graph.ladybug"
    connection_first.parent.mkdir()
    db, conn = written(connection_first)
    conn.close()
    db.close()

    assert logs(connection_first) == [], "closing the Connection settles the file"

    database_only = tmp_path / "b" / "graph.ladybug"
    database_only.parent.mkdir()
    db, conn = written(database_only)
    db.close()

    assert logs(database_only) != [], "closing the Database alone does not settle it"


def test_events_in_different_years_get_distinct_year_nodes(tmp_path):
    _write_snapshot(tmp_path, [
        _deck("d1", "E2024", "2024-03-01T00:00:00+00:00"),
        _deck("d2", "E2026", "2026-02-01T00:00:00+00:00"),
    ])

    counts = build_graph(load_snapshot(tmp_path), tmp_path / "graph")
    conn = open_for_reading(tmp_path / "graph")

    assert counts.years == 2
    assert counts.in_year == 2
    assert dict(rows(conn.execute(
        "MATCH (e:Event)-[:IN_YEAR]->(y:Year) RETURN e.event, y.year"))) == {
        "E2024": 2024, "E2026": 2026}


def test_event_straddling_two_calendar_years_fails_the_build(tmp_path):
    # createdAt only dates an event while its decks share one calendar year, so
    # a straddle must fail loudly rather than silently take the earlier year
    # (issue-26 AC #4, ADR 0006).
    _write_snapshot(tmp_path, [
        _deck("d1", "NYE", "2025-12-31T00:00:00+00:00"),
        _deck("d2", "NYE", "2026-01-01T00:00:00+00:00"),
    ])

    with pytest.raises(ValueError, match="NYE"):
        build_graph(load_snapshot(tmp_path), tmp_path / "graph")


def test_an_event_whose_decks_were_registered_a_year_apart_fails_the_build(tmp_path):
    # The 2026-11 VFNIQ shape: upstream filed the 2026 running under the 2025
    # event, so one name holds decks registered 378 days apart, where no running
    # in the corpus spans more than 34. The guard reads the dates directly, so
    # the abort names what happened, and refuses before anything is written.
    _write_snapshot(tmp_path, [
        _deck("d1", "VFNIQ", "2025-10-04T23:00:04+00:00"),
        _deck("d2", "VFNIQ", "2026-10-18T11:11:05+00:00"),
    ])

    with pytest.raises(RegistrationSpread, match=r"VFNIQ .*2025-10-04 to 2026-10-18"):
        build_graph(load_snapshot(tmp_path), tmp_path / "graph")
    assert not (tmp_path / "graph").exists()


def _winner(deck_id, title, event="E", **overrides):
    """A parsed deck whose title claims (a tier at) rank 1, for the sole-winner guard."""
    return Deck.model_validate(
        _deck(deck_id, event, "2026-06-27T00:00:00+00:00", name=title, **overrides)
    )


def test_a_second_winner_fails_the_build(tmp_path):
    # The 2026-08 S&CWADJune shape: upstream folded a second event into one
    # name, so the merged record crowns two winners on exclusive titles. No
    # per-record guard can see the merge (the foreign decks arrive under fresh
    # ids), but a Tournament crowns one winner, so the build refuses to absorb
    # it rather than publish a doubled bracket.
    _write_snapshot(tmp_path, [
        _deck("d1", "WAD", "2026-06-27T00:00:00+00:00", name="1st A - Storm - WAD"),
        _deck("d2", "WAD", "2026-07-25T00:00:00+00:00", name="01st B - Grixis - WAD"),
    ])

    with pytest.raises(TwoWinners, match="WAD seats 2 decks at rank 1"):
        build_graph(load_snapshot(tmp_path), tmp_path / "graph")
    assert not (tmp_path / "graph").exists()  # aborted before anything was written


def test_a_split_final_seats_both_winners(tmp_path):
    # "1st/2nd" is one tier seating two, not two events: the normal shape of an
    # unplayed final (BBB, MazeWATrop), so it builds clean.
    _write_snapshot(tmp_path, [
        _deck("d1", "BBB", "2026-06-27T00:00:00+00:00", name="1st/2nd A - Lands - BBB"),
        _deck("d2", "BBB", "2026-06-27T00:00:00+00:00", name="01st/2nd B - Breach - BBB"),
    ])

    build_graph(load_snapshot(tmp_path), tmp_path / "graph")


def test_two_merged_split_finals_still_read_as_two_events():
    # Even a merge of two events that both split their finals is caught: four
    # decks cannot sit in a band their titles cap at two.
    decks = [_winner(f"d{i}", f"1st/2nd P{i} - Deck - E") for i in range(4)]

    with pytest.raises(TwoWinners):
        _check_sole_winner(decks)


def test_an_uncapped_title_disarms_the_winner_guard():
    # "Top 4" bounds a depth, not a tier: cohort-resolved cuts legitimately put
    # four decks at rank 1 (CanBrawl2), so a group holding any title that caps
    # nothing is left alone.
    decks = [_winner("d1", "1st A - Storm - E"),
             _winner("d2", "Top 4 B - Jund - E")]

    _check_sole_winner(decks)


def test_a_teams_event_crowns_a_whole_team():
    # Three teammates each titled "1st" is the normal Teams shape
    # (PoGTeams2024), so only Tournaments are in the guard's scope.
    decks = [_winner(f"d{i}", f"1st P{i} - Deck - T", event="T", eventType="Teams")
             for i in range(3)]

    _check_sole_winner(decks)


def _one_registration_entered_twice(event, kept_date, dropped_date):
    """A duplicate pair at ``event``: same pilot, name and list, two dates.

    ``_deck`` gives every fixture its own pilot key and its own title so that
    hand-authored decks stay separate registrations, which is the opposite of
    what a duplicate needs. Overriding both puts the pair on one resolved pilot
    and one recovered display name, and ``_write_snapshot`` already gives every
    deck the same one-card list, so the pair matches on the whole duplicate key
    (pilot, event, name, card signature) and the resolution folds it (ADR 0004).
    Both carry placement 1, so the survivor is decided by the deck id tie-break:
    ``d1`` is kept and ``d2`` is dropped whatever dates they are given.
    """
    pair = [_deck("d1", event, kept_date), _deck("d2", event, dropped_date)]
    for deck in pair:
        deck["pilot"] = "same"
        deck["name"] = f"1st Same Person - n - {event}"
    return pair


def test_a_dropped_duplicate_still_counts_toward_the_year_straddle(tmp_path):
    # The straddle guard has to read the source's own population rather than the
    # one left after duplicate registrations are removed, or a duplicate can
    # silence it: the deck carrying the out-of-year date is exactly the deck that
    # gets dropped, and the guard then sees a single-year event and derives a
    # confident year for it with nothing printed. Measured on the live snapshot,
    # rewriting the one real duplicate loser's date across a New Year makes the
    # pre-drop call raise and the post-drop call return 2026 (issue #103).
    #
    # `test_event_straddling_two_calendar_years_fails_the_build` builds a straddle
    # with no duplicates in it, so it holds the guard but not its position in the
    # build. This is the test that holds the ordering.
    #
    # The control build first, because the abort below proves nothing about the
    # ordering unless the pair really is a duplicate and `d2` really is the deck
    # the resolution removes. Dated inside one year, the same pair builds, and the
    # graph keeps one deck of the two.
    control = tmp_path / "control"
    control.mkdir()
    _write_snapshot(control, _one_registration_entered_twice(
        "NYE", "2025-12-30T00:00:00+00:00", "2025-12-31T00:00:00+00:00"))
    artifact = tmp_path / "control-graph"

    counts = build_graph(load_snapshot(control), artifact)

    assert counts.decks == 1
    report = json.loads(reconciliation_path(artifact).read_text())
    assert [(d["dropped_deck"], d["kept_deck"]) for d in report["dropped_duplicates"]] == [
        ("d2", "d1")
    ]

    # Now the same pair with the dropped deck alone on the far side of a New Year.
    # Post-drop the event reads as a clean 2025; the build must still abort.
    straddle = tmp_path / "straddle"
    straddle.mkdir()
    _write_snapshot(straddle, _one_registration_entered_twice(
        "NYE", "2025-12-31T00:00:00+00:00", "2026-01-01T00:00:00+00:00"))

    with pytest.raises(ValueError, match="NYE"):
        build_graph(load_snapshot(straddle), tmp_path / "straddle-graph")


def test_a_failed_build_leaves_the_bundle_it_was_pointed_at_untouched(
    tmp_path, snapshot_dir
):
    # Everything that can reject the data runs before the bundle is touched, so a
    # build aimed straight at a live artifact cannot damage it on the way to
    # failing. Without that ordering a straddle would clear the bundle first and
    # leave an empty directory where a good graph used to be.
    artifact = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), artifact)
    before = reconciliation_path(artifact).read_text()

    straddle = tmp_path / "straddle"
    straddle.mkdir()
    _write_snapshot(straddle, [
        _deck("d1", "NYE", "2025-12-31T00:00:00+00:00"),
        _deck("d2", "NYE", "2026-01-01T00:00:00+00:00"),
    ])

    with pytest.raises(ValueError, match="NYE"):
        build_graph(load_snapshot(straddle), artifact)

    assert database_path(artifact).exists()
    assert reconciliation_path(artifact).read_text() == before


def test_deck_carries_colour_identity_and_dimension_edges(tmp_path, snapshot_dir, built_graph):
    conn = built_graph(tmp_path, snapshot_dir)
    grixis = "BsegXnsDsEWxh-vNbUrn0w"

    # Colour identity is a Deck property (ADR 0002); the atoms are edges.
    assert _scalar(
        conn, "MATCH (d:Deck {deckId: $id}) RETURN d.colourIdentity", {"id": grixis}
    ) == "UBR"

    colours = {
        r[0]
        for r in rows(conn.execute(
            "MATCH (:Deck {deckId: $id})-[:DECK_COLOUR]->(c:Colour) RETURN c.colour",
            {"id": grixis}))
    }
    assert colours == {"U", "B", "R"}

    assert _scalar(
        conn,
        "MATCH (:Deck {deckId: $id})-[:HAS_MACRO]->(m:`Macro`) RETURN m.name",
        {"id": grixis},
    ) == "tempo"

    assert _scalar(
        conn,
        "MATCH (:Deck {deckId: $id})-[:PLAYED_AT]->(e:Event) RETURN e.event",
        {"id": grixis},
    ) == "CFWAT25"


def test_deck_persists_created_at_as_a_property(tmp_path):
    # The registration date is stored on the Deck node so the head-to-head timeline
    # can read it as a sub-year x-axis (ADR 0013's amendment to ADR 0006). Every
    # other trend still groups by the Year node; only this one reads the per-deck
    # date. createdAt is UTC throughout the source, so it round-trips as UTC.
    _write_snapshot(tmp_path, [_deck("d1", "E", "2025-06-01T09:30:00+00:00")])
    artifact = tmp_path / "graph"
    build_graph(load_snapshot(tmp_path), artifact)
    conn = open_for_reading(artifact)

    stored = _scalar(conn, "MATCH (d:Deck {deckId: 'd1'}) RETURN d.createdAt")
    assert stored == datetime(2025, 6, 1, 9, 30)


def test_deck_to_archetype_carries_weight_and_primary_flag(tmp_path, snapshot_dir, built_graph):
    conn = built_graph(tmp_path, snapshot_dir)
    grixis = "BsegXnsDsEWxh-vNbUrn0w"

    row = conn.execute(
        """MATCH (:Deck {deckId: $id})-[r:HAS_ARCHETYPE]->(a:Archetype)
           RETURN a.name, r.weight, r.isPrimary""",
        {"id": grixis},
    ).get_next()
    assert row == ["Grixis", 100, True]


def test_card_links_to_type_and_to_each_pip_colour(tmp_path, snapshot_dir, built_graph):
    conn = built_graph(tmp_path, snapshot_dir)

    # A two-colour card links to each of its colours (issue-3 AC #3).
    strix_colours = {
        r[0]
        for r in rows(conn.execute(
            "MATCH (:Card {canon: 'baleful strix'})-[:CARD_COLOUR]->(c:Colour) "
            "RETURN c.colour"))
    }
    assert strix_colours == {"U", "B"}

    # A land with no mana cost has no colour edges.
    assert _scalar(
        conn,
        "MATCH (:Card {canon: 'arid mesa'})-[:CARD_COLOUR]->(c:Colour) "
        "RETURN count(c)",
    ) == 0

    assert _scalar(
        conn,
        "MATCH (:Card {canon: 'arid mesa'})-[:HAS_TYPE]->(t:CardType) RETURN t.type",
    ) == "Lands"


def test_card_carries_the_companion_cost_beside_the_default_one(tmp_path):
    # A card's 7PH cost depends on the context it is played in, so both of the
    # source's values land on the node: nothing downstream can charge a companion
    # its real 3 points off `points` alone (issue #143). Lurrus's own source
    # values, and an island beside it, since a reader that returned the companion
    # cost for every card would price the whole format at 0.
    (tmp_path / "decks.json").write_text(json.dumps(
        [_deck("d1", "E", "2025-06-01T00:00:00+00:00")]
    ))
    (tmp_path / "cards_index.json").write_text(json.dumps({
        "v": 2,
        "cards": [{"canon": "island", "name": "Island", "type": "Lands",
                   "manaCost": None, "manaValue": 0.0, "reserved": False,
                   "points": 0, "pointsCompanion": 0},
                  {"canon": "lurrus of the dream-den",
                   "name": "Lurrus of the Dream-Den", "type": "Creatures",
                   "manaCost": "{1}{W/B}{W/B}", "manaValue": 3.0,
                   "reserved": False, "points": 0, "pointsCompanion": 3}],
        "decks": {"d1": {"m": [0], "s": [1]}},
    }))

    build_graph(load_snapshot(tmp_path), tmp_path / "graph")
    conn = open_for_reading(tmp_path / "graph")

    assert dict(rows(conn.execute(
        "MATCH (c:Card) RETURN c.canon, [c.points, c.pointsCompanion]"
    ))) == {"lurrus of the dream-den": [0, 3], "island": [0, 0]}


def test_multi_archetype_deck_weights_and_flags_each_edge(tmp_path):
    # A deck may carry several archetypes, each weighted, with one primary
    # (CONTEXT.md / issue-3 AC #2). The shared fixture only has single-archetype
    # decks, so this hand-authored snapshot exercises the multi-archetype path.
    (tmp_path / "decks.json").write_text(json.dumps([{
        "deckId": "d1", "name": "n", "deckName": "n", "pilot": "p", "event": "E",
        "eventId": "evt_1", "eventType": "Tournament", "placement": 1,
        "placementNorm": 0.0, "createdAt": "2025-06-01T00:00:00+00:00",
        "colour": "colour:UB", "macro": "macro:control",
        "engineTags": ["engine:grixis", "engine:control"],
        "engineTagLabels": {"engine:grixis": "Grixis", "engine:control": "Control"},
        "primaryTag": "engine:grixis",
        "primaryTagWeights": {"engine:grixis": 70, "engine:control": 30},
    }]))
    (tmp_path / "cards_index.json").write_text(json.dumps({
        "v": 2,
        "cards": [{"canon": "island", "name": "Island", "type": "Lands",
                   "manaCost": None, "manaValue": 0.0, "reserved": False,
                   "points": 0, "pointsCompanion": 0}],
        "decks": {"d1": {"m": [0], "s": []}},
    }))

    db_path = tmp_path / "graph"
    build_graph(load_snapshot(tmp_path), db_path)
    conn = open_for_reading(db_path)

    edges = {
        name: (weight, is_primary)
        for name, weight, is_primary in rows(conn.execute(
            """MATCH (:Deck {deckId: 'd1'})-[r:HAS_ARCHETYPE]->(a:Archetype)
               RETURN a.name, r.weight, r.isPrimary"""))
    }
    # Each archetype keeps its own weight; only the primary tag is flagged.
    assert edges == {"Grixis": (70, True), "Control": (30, False)}


def test_deck_archetype_override_reclassifies_a_mistitled_deck(tmp_path):
    # A deck the source mistitled ("Blue Moon", so engine:blue_moon won primary
    # over the izzet_prowess the cards show) is corrected by a [[deck_archetype]]
    # entry, which collapses it onto the single corrected engine (issue #9).
    (tmp_path / "decks.json").write_text(json.dumps([{
        "deckId": "d1", "name": "n", "deckName": "Blue Moon", "pilot": "p",
        "event": "E", "eventId": "evt_1", "eventType": "Tournament",
        "placement": 1, "placementNorm": 0.0,
        "createdAt": "2026-04-25T00:00:00+00:00",
        "colour": "colour:UR", "macro": "macro:tempo",
        "engineTags": ["engine:blue_moon", "engine:izzet_prowess"],
        "engineTagLabels": {"engine:blue_moon": "Blue Moon",
                            "engine:izzet_prowess": "Izzet Prowess"},
        "primaryTag": "engine:blue_moon",
        "primaryTagWeights": {"engine:blue_moon": 85, "engine:izzet_prowess": 15},
    }]))
    (tmp_path / "cards_index.json").write_text(json.dumps({
        "v": 2,
        "cards": [{"canon": "island", "name": "Island", "type": "Lands",
                   "manaCost": None, "manaValue": 0.0, "reserved": False,
                   "points": 0, "pointsCompanion": 0}],
        "decks": {"d1": {"m": [0], "s": []}},
    }))
    curation = Curation(
        merges={}, rejected=frozenset(), names={}, deck_pilots={},
        deck_archetypes={"d1": ArchetypeOverride(
            deck_name="UR Prowess", engine="engine:izzet_prowess",
            engine_label="Izzet Prowess", source="engine:blue_moon")},
    )

    db_path = tmp_path / "graph"
    build_graph(load_snapshot(tmp_path), db_path, curation)
    conn = open_for_reading(db_path)

    # The deck now carries the corrected name and a single Izzet Prowess
    # archetype at full weight; the mistitled Blue Moon tag is gone entirely.
    assert _scalar(conn, "MATCH (d:Deck {deckId: 'd1'}) RETURN d.deckName") == "UR Prowess"
    edges = {
        name: (weight, is_primary)
        for name, weight, is_primary in rows(conn.execute(
            """MATCH (:Deck {deckId: 'd1'})-[r:HAS_ARCHETYPE]->(a:Archetype)
               RETURN a.name, r.weight, r.isPrimary"""))
    }
    assert edges == {"Izzet Prowess": (100, True)}


def _storm(deck_id, placement, weights, labels):
    """A deck tagged with ``weights`` (code -> weight), its top weight primary."""
    return _deck(deck_id, "E", "2026-04-25T00:00:00+00:00", placement=placement,
                 engineTags=list(weights), primaryTagWeights=weights,
                 primaryTag=max(weights, key=weights.get),
                 engineTagLabels={c: labels[c] for c in weights})


_STORM_LABELS = {"engine:gf_storm": "Gf Storm",
                 "engine:gluten_free_storm": "Gluten Free Storm",
                 "engine:storm": "Storm"}
_GF_STORM = Curation(merges={}, rejected=frozenset(), names={}, deck_pilots={},
                     archetype_aliases={"engine:gf_storm": "engine:gluten_free_storm"})


def _archetype_edges(conn):
    """Every deck's archetypes as deck id -> {label: (weight, is primary)}."""
    edges: dict[str, dict] = {}
    for deck, name, weight, primary in rows(conn.execute(
            """MATCH (d:Deck)-[r:HAS_ARCHETYPE]->(a:Archetype)
               RETURN d.deckId, a.name, r.weight, r.isPrimary""")):
        edges.setdefault(deck, {})[name] = (weight, primary)
    return edges


def test_an_archetype_alias_folds_a_spelling_split_into_one_archetype(tmp_path):
    # "GF Storm" and "Gluten Free Storm" are one deck that upstream files under
    # two codes by title spelling (issue #374). Every deck carrying the alias
    # carries the archetype instead, under the archetype's own label; a deck
    # carrying both sums the two weights, and its primary follows the fold.
    # Tags the alias does not name are left as upstream gave them.
    _write_snapshot(tmp_path, [
        _storm("g1", 1, {"engine:gf_storm": 100}, _STORM_LABELS),
        _storm("g2", 2, {"engine:gf_storm": 60, "engine:gluten_free_storm": 35,
                         "engine:storm": 5}, _STORM_LABELS),
        _storm("t1", 3, {"engine:gluten_free_storm": 100}, _STORM_LABELS),
    ])

    build_graph(load_snapshot(tmp_path), tmp_path / "graph", _GF_STORM)
    conn = open_for_reading(tmp_path / "graph")

    assert _archetype_edges(conn) == {
        "g1": {"Gluten Free Storm": (100, True)},
        "g2": {"Gluten Free Storm": (95, True), "Storm": (5, False)},
        "t1": {"Gluten Free Storm": (100, True)},
    }
    assert {name for (name,) in rows(conn.execute(
        "MATCH (a:Archetype) RETURN a.name"))} == {"Gluten Free Storm", "Storm"}


@pytest.mark.parametrize("weights, gone, label", [
    # Upstream folded the alias itself: no deck carries it any more.
    ({"engine:gluten_free_storm": 100}, "engine:gf_storm", "Gluten Free Storm"),
    # Upstream folded the other way: the target is the code it retired, and
    # forcing decks onto it would overrule upstream's own ruling.
    ({"engine:gf_storm": 100}, "engine:gluten_free_storm", "Gf Storm"),
])
def test_an_alias_steps_aside_once_upstream_moves_under_it(tmp_path, weights, gone, label):
    # Upstream's later ruling is the long-term answer (issue #374): an alias
    # applies only while some deck carries each of its two codes. Otherwise the
    # decks stay as upstream tagged them, the build goes on, and the report
    # lists the alias so it can be retired.
    _write_snapshot(tmp_path, [_storm("d1", 1, weights, _STORM_LABELS)])

    build_graph(load_snapshot(tmp_path), tmp_path / "graph", _GF_STORM)
    conn = open_for_reading(tmp_path / "graph")

    assert _archetype_edges(conn) == {"d1": {label: (100, True)}}
    report = json.loads(reconciliation_path(tmp_path / "graph").read_text())
    (row,) = [d for d in report["dead_entries"] if d["kind"] == "archetype_alias"]
    assert row["key"] == "engine:gf_storm" and gone in row["detail"]


def test_a_deck_archetype_steps_aside_once_upstream_refiles_the_deck(tmp_path):
    # The override was written while upstream filed the deck under Blue Moon.
    # Upstream has since re-tagged it, and its re-filing is the long-term answer
    # (issue #374): the deck stays as upstream now tags it, title included, and
    # the report names the code upstream moved it to so the entry is retired.
    _write_snapshot(tmp_path, [_deck(
        "d1", "E", "2026-04-25T00:00:00+00:00", deckName="Blue Moon",
        engineTags=["engine:izzet_tempo"], primaryTag="engine:izzet_tempo",
        engineTagLabels={"engine:izzet_tempo": "Izzet Tempo"},
        primaryTagWeights={"engine:izzet_tempo": 100})])
    curation = Curation(
        merges={}, rejected=frozenset(), names={}, deck_pilots={},
        deck_archetypes={"d1": ArchetypeOverride(
            deck_name="UR Prowess", engine="engine:izzet_prowess",
            engine_label="Izzet Prowess", source="engine:blue_moon")},
    )

    build_graph(load_snapshot(tmp_path), tmp_path / "graph", curation)
    conn = open_for_reading(tmp_path / "graph")

    assert _scalar(conn, "MATCH (d:Deck {deckId: 'd1'}) RETURN d.deckName") == "Blue Moon"
    assert _archetype_edges(conn) == {"d1": {"Izzet Tempo": (100, True)}}
    report = json.loads(reconciliation_path(tmp_path / "graph").read_text())
    (row,) = [d for d in report["dead_entries"] if d["kind"] == "deck_archetype"]
    assert row["key"] == "d1"
    assert "engine:izzet_tempo" in row["detail"] and "engine:blue_moon" in row["detail"]


_FAMILY_LABELS = {
    "engine:grixis": "Grixis", "engine:bus_stop_grixis": "Bus Stop Grixis",
    "engine:breach": "Breach", "engine:jeskai": "Jeskai",
    "engine:jeskai_breach": "Jeskai Breach", "engine:breachbond": "Breachbond",
    "engine:dimir": "Dimir", "engine:flood_moon": "Flood Moon",
    "engine:lurrus_breach": "Lurrus Breach",
} | _STORM_LABELS


def _tagged(deck_id, placement, weights, event="E", **overrides):
    """A deck tagged with ``weights`` (code -> weight), its top weight primary."""
    return _deck(deck_id, event, "2026-04-25T00:00:00+00:00", placement=placement,
                 engineTags=list(weights), primaryTagWeights=weights,
                 primaryTag=max(weights, key=weights.get),
                 engineTagLabels={c: _FAMILY_LABELS[c] for c in weights}, **overrides)


def _versions(code, count, parents, first=1):
    """``count`` decks with ``code`` primary, each carrying every one of ``parents`` at 5."""
    weights = {code: 100 - 5 * len(parents)} | {p: 5 for p in parents}
    return [_tagged(f"{code.split(':')[1]}{n}", n, weights)
            for n in range(first, first + count)]


def test_a_version_is_served_under_the_archetype_its_decks_name_as_parent(tmp_path):
    # Upstream tags a version with its parent as a second, low-weight tag (Bus
    # Stop Grixis carries Grixis at 5), so when 95% of the decks a code is
    # primary on carry one parent, the code joins that parent's family (issue
    # #381). Its decks are re-coded the way an alias folds them: weights summed,
    # the primary following, under the parent's label, and the version's own
    # code is gone from the graph.
    _write_snapshot(tmp_path, [
        _tagged("g1", 1, {"engine:grixis": 100}),
        *_versions("engine:bus_stop_grixis", 5, ["engine:grixis"], first=2),
    ])

    build_graph(load_snapshot(tmp_path), tmp_path / "graph", Curation.empty())
    conn = open_for_reading(tmp_path / "graph")

    assert _archetype_edges(conn) == {
        deck: {"Grixis": (100, True)}
        for deck in ["g1", *(f"bus_stop_grixis{n}" for n in range(2, 7))]
    }
    assert {name for (name,) in rows(conn.execute(
        "MATCH (a:Archetype) RETURN a.name"))} == {"Grixis"}


def _primaries(conn):
    """Every deck's primary archetype label, as deck id -> label."""
    return dict(rows(conn.execute(
        """MATCH (d:Deck)-[r:HAS_ARCHETYPE]->(a:Archetype) WHERE r.isPrimary
           RETURN d.deckId, a.name""")))


@pytest.mark.parametrize("decks, carrying, joins", [
    (4, 4, False),    # under the deck floor: too few decks to judge
    (20, 19, True),   # exactly 95% carry the parent
    (20, 18, False),  # 90%: the parent is not what upstream declares
])
def test_a_version_joins_its_parent_only_on_enough_decks_that_carry_it(
    tmp_path, decks, carrying, joins
):
    versions = (_versions("engine:bus_stop_grixis", carrying, ["engine:grixis"])
                + _versions("engine:bus_stop_grixis", decks - carrying, [], first=carrying + 1))
    _write_snapshot(tmp_path, versions)

    build_graph(load_snapshot(tmp_path), tmp_path / "graph", Curation.empty())

    served = set(_primaries(open_for_reading(tmp_path / "graph")).values())
    assert served == ({"Grixis"} if joins else {"Bus Stop Grixis"})


def test_a_primary_upstream_does_not_tag_the_deck_with_is_no_code_to_judge(tmp_path):
    # A deck upstream left without a primary names one it does not tag the deck
    # with, so the graph files it under nothing (the suite's `engine:__none__`
    # fixtures; one Rakdos Aggro deck on the record). Five such decks carrying
    # Grixis are no version of Grixis: the rule judges a code on the decks the
    # graph serves it as primary on.
    _write_snapshot(tmp_path, [
        _tagged(f"x{n}", n, {"engine:grixis": 100}) | {"primaryTag": "engine:__none__"}
        for n in range(1, 6)])

    build_graph(load_snapshot(tmp_path), tmp_path / "graph", Curation.empty())

    report = json.loads(reconciliation_path(tmp_path / "graph").read_text())
    assert report["archetype_families"] == {"families": [], "two_parents": []}


def test_a_code_with_two_parents_stands_alone(tmp_path):
    # Jeskai Breach decks carry both Breach and Jeskai: upstream declares two
    # parents, and which family it belongs to is the owner's to rule, so until
    # then it is its own (issue #381).
    _write_snapshot(tmp_path, _versions(
        "engine:jeskai_breach", 5, ["engine:breach", "engine:jeskai"]))

    build_graph(load_snapshot(tmp_path), tmp_path / "graph", Curation.empty())

    served = set(_primaries(open_for_reading(tmp_path / "graph")).values())
    assert served == {"Jeskai Breach"}


def test_a_spelling_split_is_judged_as_the_one_code_its_alias_folds_it_into(tmp_path):
    # The rule reads tags after the spelling aliases fold (issue #381): three GF
    # Storm and two Gluten Free Storm decks are each under the floor apart, and
    # five decks of one code once the alias folds them.
    _write_snapshot(tmp_path,
                    _versions("engine:gf_storm", 3, ["engine:storm"])
                    + _versions("engine:gluten_free_storm", 2, ["engine:storm"], first=4))

    build_graph(load_snapshot(tmp_path), tmp_path / "graph", _GF_STORM)

    served = set(_primaries(open_for_reading(tmp_path / "graph")).values())
    assert served == {"Storm"}


def test_a_family_is_read_off_upstreams_tags_and_holds_the_decks_overridden_onto_it(
    tmp_path,
):
    # Five Flood Moon decks carry Dimir as their parent. A sixth upstream filed
    # under Dimir is reclassified onto Flood Moon by a [[deck_archetype]], which
    # leaves it one engine and no parent tag. The rule reads upstream's tags, so
    # the override cannot vote Flood Moon out of the family (a sixth without the
    # parent would be 83%), and the overridden deck is grouped like any other
    # Flood Moon deck (issue #381).
    _write_snapshot(tmp_path, [
        *_versions("engine:flood_moon", 5, ["engine:dimir"]),
        _tagged("m1", 6, {"engine:dimir": 100}, deckName="Dimir"),
    ])
    curation = Curation(
        merges={}, rejected=frozenset(), names={}, deck_pilots={},
        deck_archetypes={"m1": ArchetypeOverride(
            deck_name="Flood Moon", engine="engine:flood_moon",
            engine_label="Flood Moon", source="engine:dimir")},
    )

    build_graph(load_snapshot(tmp_path), tmp_path / "graph", curation)
    conn = open_for_reading(tmp_path / "graph")

    assert set(_primaries(conn).values()) == {"Dimir"}
    assert _scalar(conn, "MATCH (d:Deck {deckId: 'm1'}) RETURN d.deckName") == "Flood Moon"


def _ruled(families):
    return replace(Curation.empty(), archetype_families=families)


@pytest.mark.parametrize("decks, families, served", [
    # A grouping the rule misses: upstream gives Breachbond no parent tag.
    ([*_versions("engine:breachbond", 5, []), _tagged("b0", 9, {"engine:breach": 100})],
     {"engine:breachbond": "engine:breach"}, {"Breach"}),
    # A grouping the rule makes that the owner keeps apart.
    (_versions("engine:bus_stop_grixis", 5, ["engine:grixis"]),
     {"engine:bus_stop_grixis": "engine:bus_stop_grixis"}, {"Bus Stop Grixis"}),
    # Two parents, settled by a ruling for one of them.
    (_versions("engine:jeskai_breach", 5, ["engine:breach", "engine:jeskai"]),
     {"engine:jeskai_breach": "engine:breach"}, {"Breach"}),
    # Ruled under a version the rule files under its own parent: one family,
    # not decks split between the version's code and the parent's.
    ([*_versions("engine:breachbond", 5, []),
      *_versions("engine:lurrus_breach", 5, ["engine:breach"], first=6)],
     {"engine:breachbond": "engine:lurrus_breach"}, {"Breach"}),
])
def test_a_family_ruling_decides_where_the_rule_cannot_or_gets_it_wrong(
    tmp_path, decks, families, served
):
    # The owner rules only on exceptions (issue #381), and a ruling wins over
    # whatever the rule would have made of the code's tags.
    _write_snapshot(tmp_path, decks)

    build_graph(load_snapshot(tmp_path), tmp_path / "graph", _ruled(families))

    assert set(_primaries(open_for_reading(tmp_path / "graph")).values()) == served


@pytest.mark.parametrize("families, gone", [
    ({"engine:breachbond": "engine:grixis"}, "engine:breachbond"),
    ({"engine:bus_stop_grixis": "engine:breach"}, "engine:breach"),
    ({"engine:breachbond": "engine:breachbond"}, "engine:breachbond"),
])
def test_a_family_ruling_steps_aside_once_upstream_stops_carrying_a_code_it_names(
    tmp_path, families, gone
):
    # Upstream's later tagging is the long-term answer, as for an alias: a
    # ruling naming a code no deck carries applies nothing, the build goes on
    # under the rule, and the report lists the ruling so it can be retired.
    _write_snapshot(tmp_path, _versions("engine:bus_stop_grixis", 5, ["engine:grixis"]))

    build_graph(load_snapshot(tmp_path), tmp_path / "graph", _ruled(families))

    assert set(_primaries(open_for_reading(tmp_path / "graph")).values()) == {"Grixis"}
    report = json.loads(reconciliation_path(tmp_path / "graph").read_text())
    (row,) = [d for d in report["dead_entries"] if d["kind"] == "archetype_family"]
    assert row["key"] == next(iter(families)) and gone in row["detail"]


def test_the_report_lists_each_family_with_its_members_and_the_codes_with_two_parents(
    tmp_path,
):
    # Every build lists what it grouped and what it could not (issue #381):
    # each family with the decks each member brings, whether the rule or a
    # ruling put it there, and the codes left alone with two parents, which
    # wait on the owner. None of it stops the build. A deck counts where the
    # graph serves it: one upstream names Bus Stop Grixis as primary without
    # tagging it so (a shape the record holds once) is primary on nothing.
    _write_snapshot(tmp_path, [
        _tagged("g1", 1, {"engine:grixis": 100}),
        _tagged("b0", 2, {"engine:breach": 100}),
        _tagged("odd", 30, {"engine:grixis": 100}) | {"primaryTag": "engine:bus_stop_grixis"},
        *_versions("engine:bus_stop_grixis", 5, ["engine:grixis"], first=3),
        *_versions("engine:breachbond", 6, [], first=8),
        *_versions("engine:jeskai_breach", 7, ["engine:breach", "engine:jeskai"], first=14),
    ])

    build_graph(load_snapshot(tmp_path), tmp_path / "graph",
                _ruled({"engine:breachbond": "engine:breach"}))

    report = json.loads(reconciliation_path(tmp_path / "graph").read_text())
    assert report["archetype_families"] == {
        "families": [
            {"code": "engine:breach", "label": "Breach", "decks": 1, "members": [
                {"code": "engine:breachbond", "label": "Breachbond", "decks": 6,
                 "by": "ruling"}]},
            {"code": "engine:grixis", "label": "Grixis", "decks": 1, "members": [
                {"code": "engine:bus_stop_grixis", "label": "Bus Stop Grixis", "decks": 5,
                 "by": "rule"}]},
        ],
        "two_parents": [
            {"code": "engine:jeskai_breach", "label": "Jeskai Breach", "decks": 7,
             "parents": ["engine:breach", "engine:jeskai"]},
        ],
    }


def test_the_record_files_the_versions_the_tickets_name_as_they_measured(live_graph):
    # The cases issues #381 and #382 measured on 20261118T000557Z before any
    # ruling: versions the rule groups (some the owner may yet keep apart), the
    # one grouping it misses (upstream gives Breachbond no parent tag) and the
    # two codes it leaves alone with two parents. A ruling that moves one of
    # these updates this test in the ticket that records it.
    report = json.loads(reconciliation_path(artifact_path()).read_text())["archetype_families"]
    filed = {m["label"]: f["label"] for f in report["families"] for m in f["members"]}
    named = {
        "Bus Stop Grixis": "Grixis", "Lurrus Breach": "Breach", "Flood Moon": "Dimir",
        "Blue Tron": "Tron", "Red Esper": "Esper", "Iron Man": "Jeskai",
        "Mardu Lurrus": "Mardu", "Rakdos Eclipse": "Rakdos", "Rakdos Aggro": "Rakdos",
        "Orzhov Taxes": "Orzhov", "Warps": "Bant", "Amulet Titan": "Simic",
        "Breachbond": None,
    }

    assert {code: filed.get(code) for code in named} == named
    assert {c["label"]: c["parents"] for c in report["two_parents"]} == {
        "Jeskai Breach": ["engine:breach", "engine:jeskai"],
        "Breakfast": ["engine:bant", "engine:nadu"],
    }
    # Served under the family: no version the rule grouped is left in the graph.
    assert set(filed) & {name for (name,) in rows(live_graph.execute(
        "MATCH (a:Archetype) RETURN a.name"))} == set()


def test_pilot_identity_compares_decks_on_upstreams_codes_not_their_families(tmp_path):
    # Identity rules compare two ids' closest decks on one primary archetype
    # (ADR 0028), so families are grouped only after identity resolves (issue
    # #381). Joseph M and Jospeh M (one transposition apart) ran the same list
    # at different events, one as Grixis and one as Bus Stop Grixis: on
    # upstream's codes they share no archetype and stay watching, while read
    # through the family they would clear the typo bar and merge two people.
    joseph = _tagged("a1", 1, {"engine:grixis": 100}, event="E1", pilot="pa",
                     name="1st Joseph M - Grixis - E1")
    jospeh, *others = _versions("engine:bus_stop_grixis", 5, ["engine:grixis"])
    jospeh |= {"pilot": "pb", "name": "1st Jospeh M - Bus Stop Grixis - E"}
    _write_snapshot(tmp_path, [joseph, jospeh, *others])

    build_graph(load_snapshot(tmp_path), tmp_path / "graph", Curation.empty())

    report = json.loads(reconciliation_path(tmp_path / "graph").read_text())
    (pair,) = [p for p in report["ruled_pairs"] if set(p["pilots"]) == {"pa", "pb"}]
    assert (pair["state"], pair["archetype"]) == ("watching", None)
    assert set(_primaries(open_for_reading(tmp_path / "graph")).values()) == {"Grixis"}


def test_the_held_ids_sitting_on_a_joined_node_are_the_five_on_the_record(live_graph):
    # The population a single-id hold is watched over is that id's own decks and
    # not the node the identical-name join left (issue #241), and the difference
    # is only real where a join has folded another id in. Five of the 28 held ids
    # are in that position today, so the rule is grading live rows rather than a
    # hypothetical, and a sixth arriving is a join quietly widening the population
    # again: it reddens here instead of passing unnoticed.
    #
    # Named, not counted, because the failure a reader needs is "this id now
    # shares a node with that one". The population is every single-id [[hold]] on
    # file, not the `held_names` rows the multi-name scan surfaced: those are the
    # subset whose several surnames still reach the queue, and a hold whose row
    # stops surfacing keeps its `event_split` trigger live while dropping out of
    # this guard, which is the widening the guard exists to catch.
    curation = load_curation()
    held = {curation.canonical(*ids) for ids in curation.holds if len(ids) == 1}
    report = json.loads(reconciliation_path(artifact_path()).read_text())

    shared = {
        pilot: [m for m in join["merged"] if m != pilot]
        for join in report["joined_names"]
        for pilot in join["merged"] if pilot in held
    }

    assert shared == {
        "BraveJadeEagle": ["BraveMagentaPanda"],      # James L
        "NimbleAzureTiger": ["AmberSilverFalcon"],    # Ben H
        "LunarMagentaHawk": ["LunarPurpleWhale97E"],  # Tom C
        "Max DK": ["nan:max dk"],                     # Max DK
        "CleverVioletGecko8DC": ["LuckyJadeLynx"],    # Jose G
    }


def _identity(snapshot, db_path, curation):
    """Build the snapshot and return what a hold must never move.

    The counts, the deck-to-pilot identity the graph actually holds, and the
    reconciliation report: what the three inertness tests below compare between a
    build carrying a [[hold]] and the same build without it.
    """
    counts = build_graph(snapshot, db_path, curation)
    conn = open_for_reading(db_path)
    return counts, sorted(rows(conn.execute(
        """MATCH (d:Deck)-[:PILOTED_BY]->(p:Pilot)
           RETURN d.deckId, p.pilot, p.displayName"""))), json.loads(
        reconciliation_path(db_path).read_text())


def test_a_hold_on_one_id_moves_it_off_the_multi_name_queue_and_changes_no_graph(
    tmp_path
):
    # The same load-bearing property on the other queue (issue #232): a hold on a
    # single id is a note about a decision not taken, so deleting it has to leave
    # the same graph. Otherwise the watchlist has become a way to change who the
    # pilots are -- here, whose decks are whose -- without recording a decision.
    _write_snapshot(tmp_path, [
        _deck("d1", "E1", "2026-01-01T00:00:00+00:00",
              pilot="NimbleBlackEagle", name="01st Tom H - Grixis - E1"),
        _deck("d2", "E2", "2026-02-01T00:00:00+00:00",
              pilot="NimbleBlackEagle", name="01st Tom M - Walks - E2"),
    ])
    snapshot = load_snapshot(tmp_path)
    held = Curation(
        merges={}, rejected=frozenset(), names={}, deck_pilots={},
        holds={frozenset({"NimbleBlackEagle"}):
               Hold("event_split", "20260815T140746Z")},
    )

    with_hold, held_ids, held_report = _identity(snapshot, tmp_path / "held", held)
    without, plain_ids, plain_report = _identity(snapshot, tmp_path / "plain", Curation.empty())

    assert with_hold == without
    assert held_ids == plain_ids
    # The id moved between the two lists and nowhere else.
    assert plain_report["held_names"] == []
    assert [m["pilot"] for m in plain_report["multi_name_ids"]] == ["NimbleBlackEagle"]
    assert held_report["multi_name_ids"] == []
    assert [(h["pilot"], h["settles_on"]) for h in held_report["held_names"]] == [
        ("NimbleBlackEagle", "event_split")]
    assert {k: v for k, v in held_report.items()
            if k not in {"multi_name_ids", "held_names"}} == {
        k: v for k, v in plain_report.items()
        if k not in {"multi_name_ids", "held_names"}}


def test_a_fired_trigger_changes_the_report_and_leaves_the_graph_alone(tmp_path):
    # A trigger is evaluation, never application (issue #230). Firing says the
    # evidence to decide the id has arrived, and the decision is still a
    # human's, recorded as an edit to the dictionary; nothing about whose decks
    # these are may move on the strength of the build noticing. Graded the way
    # the inert hold above is: the same snapshot built with the hold and
    # without, compared on the identity the graph actually holds.
    _write_snapshot(tmp_path, [
        _deck("d1", "E1", "2026-01-01T00:00:00+00:00",
              pilot="NimbleBlackEagle", name="01st Tom H - Grixis - E1"),
        _deck("d2", "E1", "2026-01-01T00:00:00+00:00", placement=2,
              pilot="NimbleBlackEagle", name="02nd Tom M - Walks - E1"),
    ])
    snapshot = load_snapshot(tmp_path)
    held = Curation(
        merges={}, rejected=frozenset(), names={}, deck_pilots={},
        holds={frozenset({"NimbleBlackEagle"}): Hold("event_split", "20260815T140746Z")},
    )

    with_hold, held_ids, held_report = _identity(snapshot, tmp_path / "held", held)
    without, plain_ids, plain_report = _identity(snapshot, tmp_path / "plain", Curation.empty())

    # The id is settled by its two surnames at one event, and named with what settled it.
    fired = held_report["fired_holds"]
    assert [f["trigger"] for f in fired] == ["event_split"]
    assert sorted(fired[0]["decks"]) == ["d1", "d2"]
    assert "E1" in fired[0]["detail"]
    # And the graph is the one the same snapshot builds with no hold on file.
    assert with_hold == without
    assert held_ids == plain_ids
    assert plain_report["fired_holds"] == []
    assert {k: v for k, v in held_report.items()
            if k not in {"multi_name_ids", "held_names", "fired_holds"}} == {
        k: v for k, v in plain_report.items()
        if k not in {"multi_name_ids", "held_names", "fired_holds"}}


def test_deck_event_decision_returns_a_stranded_deck_to_its_cohort(tmp_path):
    # The source stranded one deck at a malformed event (a stringified NaN with
    # no event id), which minted a phantom Event holding it alone and left the
    # event it was really played at one deck short. A [[deck_event]] entry moves
    # it back (issue #167). Shaped after the real case: a four-deck event with a
    # hole at 3rd, and an orphan whose claimed field of 3 is its own placement
    # copied into the size, so the source scored it dead last at 1.0. The
    # cohort's claimed field is 9 so no rule corrects it (ADR 0015 amended:
    # 8 or less is always a cut now).
    _write_snapshot(tmp_path, [
        _deck("d1", "E", "2024-03-24T00:00:00+00:00", placement=1,
              placementNorm=0.0, eventSize=9),
        _deck("d2", "E", "2024-03-24T00:00:00+00:00", placement=2,
              placementNorm=0.125, eventSize=9),
        _deck("orphan", "nan", "2024-03-24T00:00:00+00:00", placement=3,
              placementNorm=1.0, eventSize=3, eventId=None),
        _deck("d4", "E", "2024-03-24T00:00:00+00:00", placement=4,
              placementNorm=0.375, eventSize=9),
        # Scored by no one: this is the deck `_mint_norms` mints, and the
        # contrast that makes the reassigned deck's own label falsifiable.
        _deck("d5", "E", "2024-03-24T00:00:00+00:00", placement=5,
              placementNorm=None, eventSize=9),
    ])
    curation = Curation(
        merges={}, rejected=frozenset(), names={}, deck_pilots={},
        deck_events={"orphan": "E"},
    )

    db_path = tmp_path / "graph"
    build_graph(load_snapshot(tmp_path), db_path, curation)
    conn = open_for_reading(db_path)

    # The phantom event is gone: nothing is left holding it up.
    assert [e for e, in rows(conn.execute("MATCH (e:Event) RETURN e.event"))] == ["E"]
    assert _scalar(conn, "MATCH (:Event {event: 'E'})<-[:PLAYED_AT]-(d:Deck) "
                         "RETURN count(d)") == 5
    # The event keeps the id its own decks carry, not the orphan's null.
    assert _scalar(conn, "MATCH (e:Event {event: 'E'}) RETURN e.eventId") == "evt_E"

    # The orphan is re-scored against the field it really played in. Its claimed
    # 3 was a fact about the event, not the deck, so it leaves with the cohort's
    # 9 and the last-place norm the source computed from it is replaced.
    #
    # It says "reassigned" and not "minted", which is the distinction the report
    # a human audits rests on: the source *did* score this deck, at an event
    # that never happened, so the score was discarded rather than never given.
    # d5 beside it is the genuine minting, and the two must not read alike.
    assert list(rows(conn.execute(
        "MATCH (d:Deck {deckId: 'orphan'}) RETURN d.placement, d.placementNorm, "
        "d.normImputed"))) == [[3, 0.25, "reassigned"]]
    assert list(rows(conn.execute(
        "MATCH (d:Deck {deckId: 'd5'}) RETURN d.placementNorm, d.normImputed"
    ))) == [[0.5, "minted"]]

    # The cohort it joined is untouched: same norms, and no field correction,
    # since the count it now makes still agrees with what those decks claimed.
    assert _scalar(conn, "MATCH (e:Event {event: 'E'}) RETURN e.fieldSize") == 9
    assert _scalar(conn, "MATCH (e:Event {event: 'E'}) RETURN e.fieldImputed") is None
    assert sorted(rows(conn.execute(
        "MATCH (d:Deck) WHERE d.deckId <> 'orphan' "
        "RETURN d.deckId, d.placementNorm, d.normImputed"))) == [
        ["d1", 0.0, None], ["d2", 0.125, None], ["d4", 0.375, None],
        ["d5", 0.5, "minted"],
    ]


def test_deck_event_decision_naming_an_event_nobody_attended_raises(tmp_path):
    # The target is an event code, so it has to be an event the snapshot holds:
    # applying it blind would rename the stranded deck's phantom rather than
    # returning it to a cohort, which is the failure the entry exists to fix.
    _write_snapshot(tmp_path, [_deck("orphan", "nan", "2024-03-24T00:00:00+00:00")])
    curation = Curation(
        merges={}, rejected=frozenset(), names={}, deck_pilots={},
        deck_events={"orphan": "CBR3"},
    )

    with pytest.raises(CurationError, match="CBR3"):
        build_graph(load_snapshot(tmp_path), tmp_path / "graph", curation)


def test_built_graph_is_queryable_with_expected_shape(tmp_path, snapshot_dir):
    snap = load_snapshot(snapshot_dir)
    db_path = tmp_path / "graph"
    build_graph(snap, db_path)

    conn = open_for_reading(db_path)
    res = conn.execute(
        "MATCH (d:Deck {deckId: $id})-[:PILOTED_BY]->(p:Pilot) RETURN p.pilot",
        {"id": "BsegXnsDsEWxh-vNbUrn0w"},
    )
    assert res.get_next()[0] == "Jordan C"

    by_board = dict(rows(conn.execute(
        "MATCH (:Deck {deckId: $id})-[c:CONTAINS]->(:Card) "
        "RETURN c.board, count(*) ORDER BY c.board",
        {"id": "BsegXnsDsEWxh-vNbUrn0w"},
    )))
    # 60 Main + 15 Side for this deck.
    assert by_board == {"Main": 60, "Side": 15}


@pytest.mark.parametrize(
    "event_type, event_size, pilots, max_placement, expected",
    [
        # A Teams event's eventSize counts teams, not people, so more pilots than
        # "field" is the normal shape there and must not read as a contradiction.
        # eventType is the only reliable discriminator (issue #140).
        ("Teams", 39, 117, 39, (39, None)),
        # A whitelist, not a blacklist: an eventType nobody has classified yet is
        # left alone rather than corrected on Tournament's assumptions.
        ("League", 5, 7, 5, (5, None)),
        # Rule A, provable: more people attended than the claimed field.
        ("Tournament", 16, 28, 16, (28, "A")),
        # Rule A, the other contradiction: someone finished past the claimed field.
        ("Tournament", 5, 3, 7, (24, "A")),
        # Rule B, domain: 7PH runs no 8-player events, so a field of 8 or less
        # is a top-8 cut reported as a field.
        ("Tournament", 8, 8, 5, (24, "B")),
        # A recorded last place does not save it: FSNS's 3rd of a claimed 3 was
        # a partial podium report, not a 3-player event, so the guard that once
        # let this case stand is gone (ADR 0015, amended).
        ("Tournament", 8, 8, 8, (24, "B")),
        # A self-consistent small event above the cut size: untouched.
        ("Tournament", 19, 19, 19, (19, None)),
        # Rule C, defensive: a null eventSize has never been observed.
        ("Tournament", None, 7, 5, (24, "C")),
        # An event nobody's finish was recorded at has a deepest placement of 0,
        # which no claimed field can be below, so it never reads as Rule A.
        ("Tournament", 19, 19, 0, (19, None)),
        # The floor is a floor, not a constant: a ninth deck at a corrected
        # 8-player event still yields the cut size, never the deck count.
        ("Tournament", 8, 9, 5, (24, "A")),
        # ... and a broken event bigger than the floor keeps its counted size.
        ("Tournament", 5, 30, 5, (30, "A")),
    ],
)
def test_corrected_field_applies_one_rule_per_event(
    event_type, event_size, pilots, max_placement, expected
):
    assert corrected_field(event_type, event_size, pilots, max_placement) == expected


def test_a_corrected_field_is_never_small_enough_to_break_the_division():
    # placementNorm divides by (field - 1), so a corrected field of 1 would be a
    # zero division and a field of 0 a negative one. Every rule floors at
    # MIN_CUT_FIELD, and with Rule B unconditional below 9 the degenerate
    # one-entrant event is always corrected well clear of the division.
    assert corrected_field("Tournament", 1, 1, 1) == (MIN_CUT_FIELD, "B")
    assert corrected_field("Tournament", 1, 2, 1) == (MIN_CUT_FIELD, "A")
    assert MIN_CUT_FIELD > 1


def _scored(event, event_size, results, **overrides):
    """One deck per ``(placement, placementNorm)`` in ``results``, at ``event``.

    The norms are the source's own numbers, ranked against ``event_size``,
    because that is what the correction has to rescale.
    """
    return [
        _deck(f"{event}{i}", event, "2025-06-01T00:00:00+00:00",
              eventSize=event_size, placement=placement, placementNorm=norm,
              **overrides)
        for i, (placement, norm) in enumerate(results)
    ]


# A "5-player" event 7 people entered: the source's field contradicts a count, so
# Rule A corrects it to the cut floor. Three of them tied for 5th and were scored
# a dead-last 1.000 for a top-8 finish (issue #140).
_BROKEN = _scored("BROKEN", 5, [(1, 0.0), (2, 0.25), (3, 0.5), (4, 0.75),
                                (5, 1.0), (5, 1.0), (5, 1.0)])
# A Teams event: eventSize counts teams, so more pilots than "field" is its
# normal shape and the whitelist leaves it alone.
_TEAMS = _scored("TEAMS", 2, [(1, 0.0), (1, 0.0), (2, 1.0)], eventType="Teams")
# A self-consistent tournament: nothing contradicts 19, so the source stands.
_CLEAN = _scored("CLEAN", 19, [(5, 0.2222222222222222), (19, 1.0)])


def test_event_carries_the_field_its_placements_are_normalised_against(tmp_path):
    _write_snapshot(tmp_path, _BROKEN + _TEAMS + _CLEAN)
    artifact = tmp_path / "graph"

    build_graph(load_snapshot(tmp_path), artifact)

    conn = open_for_reading(artifact)
    assert {
        event: (size, imputed)
        for event, size, imputed in rows(conn.execute(
            "MATCH (e:Event) RETURN e.event, e.fieldSize, e.fieldImputed"))
    } == {"BROKEN": (24, "A"), "TEAMS": (2, None), "CLEAN": (19, None)}


def test_a_corrected_field_rescales_the_norms_ranked_against_the_wrong_one(tmp_path):
    _write_snapshot(tmp_path, _BROKEN + _TEAMS + _CLEAN)
    artifact = tmp_path / "graph"

    build_graph(load_snapshot(tmp_path), artifact)

    conn = open_for_reading(artifact)
    norms = dict(rows(conn.execute(
        """MATCH (d:Deck)-[:PLAYED_AT]->(e:Event {event: 'BROKEN'})
           RETURN d.deckId, d.placementNorm""")))
    # A tied-5th of 7 was scored a dead-last 1.000 against the claimed field of
    # 5; against a 24-player field it reads a top-8 0.174. The winner stays 0.0,
    # which every field size agrees on.
    assert norms["BROKEN0"] == 0.0
    assert norms["BROKEN4"] == norms["BROKEN5"] == norms["BROKEN6"]
    assert norms["BROKEN4"] == pytest.approx(0.17391304347826086)
    assert norms["BROKEN1"] == pytest.approx(0.043478260869565216)

    # The events the source had right keep the source's own numbers, to the bit.
    untouched = dict(rows(conn.execute(
        """MATCH (d:Deck)-[:PLAYED_AT]->(e:Event)
           WHERE e.event <> 'BROKEN' RETURN d.deckId, d.placementNorm""")))
    assert untouched == {"CLEAN0": 0.2222222222222222, "CLEAN1": 1.0,
                         "TEAMS0": 0.0, "TEAMS1": 0.0, "TEAMS2": 1.0}


def test_the_report_shows_the_counts_that_refuted_each_corrected_field(tmp_path):
    # An imputed field is an assumption the build made about the data, so it is
    # listed for a human each build rather than dissolving into the graph, and with
    # the counts it was corrected on: the rule name in `imputed_values` says a
    # correction happened, this says what contradicted the source, which is the one
    # thing a rule name cannot carry (issue #162). The events the source had right
    # are not listed: only what was corrected is.
    _write_snapshot(tmp_path, _BROKEN + _TEAMS + _CLEAN)
    artifact = tmp_path / "graph"

    build_graph(load_snapshot(tmp_path), artifact)

    report = json.loads(reconciliation_path(artifact).read_text())
    assert report["field_evidence"] == [
        {"event": "BROKEN", "rule": "A", "event_size": 5, "field_size": 24,
         "pilots": 7, "max_placement": 5}
    ]


def test_the_report_generates_its_imputed_values_from_the_provenance_columns(tmp_path):
    # Every value this build decided, read back out of the columns that record it
    # rather than assembled by hand per feature. That is what keeps the report from
    # growing a bespoke list per uncertainty class: a new rule string appears here
    # the build it first fires, with no change to the report code (issue #162).
    _write_snapshot(tmp_path, _BROKEN + _CLEAN + _TITLES)
    artifact = tmp_path / "graph"

    build_graph(load_snapshot(tmp_path), artifact)

    report = json.loads(reconciliation_path(artifact).read_text())
    assert report["imputed_values"] == [
        {"property": "Deck.placement", "rule": "none", "count": 1,
         "keys": ["TITLES3"]},
        {"property": "Deck.placement", "rule": "title-range", "count": 2,
         "keys": ["TITLES1", "TITLES2"]},
        {"property": "Deck.placement", "rule": "title-single", "count": 1,
         "keys": ["TITLES0"]},
        {"property": "Deck.placementNorm", "rule": "minted", "count": 3,
         "keys": ["TITLES0", "TITLES1", "TITLES2"]},
        {"property": "Deck.placementNorm", "rule": "none", "count": 1,
         "keys": ["TITLES3"]},
        {"property": "Deck.placementNorm", "rule": "rescaled", "count": 7,
         "keys": ["BROKEN0", "BROKEN1", "BROKEN2", "BROKEN3", "BROKEN4",
                  "BROKEN5", "BROKEN6"]},
        {"property": "Event.fieldSize", "rule": "A", "count": 1,
         "keys": ["BROKEN"]},
    ]
    # The events the source had right appear nowhere: only what was decided here is
    # listed, on every property.
    assert "CLEAN" not in json.dumps(report["imputed_values"])


def test_a_duplicate_registration_is_not_a_second_attendee(tmp_path):
    # Rule A fires when more people attended than the claimed field, so it has to
    # count attendees and not registrations: the same list entered twice would
    # otherwise "contradict" a field the source had right and rescale a whole
    # event's norms off one duplicate. This is what puts the correction after
    # `resolve_pilots` and after the duplicate drop (issue #140).
    # One registration entered twice: one pilot, one title, one card-identical
    # list, so the resolution folds the pair (ADR 0004). The field is 9 so Rule
    # B stays out of it (ADR 0015 amended), and the attendee count equals the
    # claimed field, which is what keeps Rule A one miscount away.
    twins = [
        _deck(f"twin{i}", "TIGHT", "2025-06-01T00:00:00+00:00", pilot="ninth",
              name="ninth", eventSize=9, placement=9, placementNorm=1.0)
        for i in (1, 2)
    ]
    _write_snapshot(tmp_path, _scored("TIGHT", 9, [
        (1, 0.0), (2, 0.125), (3, 0.25), (4, 0.375),
        (5, 0.5), (6, 0.625), (7, 0.75), (8, 0.875)]) + twins)
    artifact = tmp_path / "graph"

    counts = build_graph(load_snapshot(tmp_path), artifact)

    # The duplicate really was dropped, so the event holds 9 decks for 9 pilots
    # against a claimed field of 9, and nothing contradicts it.
    assert (counts.decks, counts.pilots) == (9, 9)
    conn = open_for_reading(artifact)
    assert _scalar(conn, "MATCH (e:Event) RETURN e.fieldSize") == 9
    assert _scalar(conn, "MATCH (e:Event) RETURN e.fieldImputed") is None
    assert _scalar(
        conn, "MATCH (d:Deck) WHERE d.placement = 9 RETURN d.placementNorm"
    ) == 1.0


# An event the source numbers nothing at, where every rank comes off the title:
# Pats Birthday Brawl's shape. The claimed field of 24 is self-consistent (nobody
# finished beyond it and 4 people entered), so no rule corrects it and the minted
# norms are ranked against the source's own number.
_TITLES = [
    _deck("TITLES0", "TITLES", "2025-06-01T00:00:00+00:00", eventSize=24,
          placement=None, placementNorm=None, name="1st - Robert L - TITLES"),
    _deck("TITLES1", "TITLES", "2025-06-01T00:00:00+00:00", eventSize=24,
          placement=None, placementNorm=None, name="3rd/4th - Brennan C - TITLES"),
    _deck("TITLES2", "TITLES", "2025-06-01T00:00:00+00:00", eventSize=24,
          placement=None, placementNorm=None, name="5th-8th - Liam B - TITLES"),
    # The source's own explicit "unknown": no placement in this title to read.
    _deck("TITLES3", "TITLES", "2025-06-01T00:00:00+00:00", eventSize=24,
          placement=None, placementNorm=None, name="??st Andrew V - TITLES"),
]


def test_every_deck_records_which_of_its_numbers_this_build_decided(tmp_path):
    # The whole provenance vocabulary in one graph, which is the point of it: the
    # same question ("which of this deck's numbers did we decide, and under what
    # rule?") answers for every class of uncertainty at once, so the next class
    # reports itself rather than needing a screenshot to find (issue #162). A null
    # means the source's own number stands; "none" means no rule could produce one.
    _write_snapshot(tmp_path, _BROKEN + _CLEAN + _TITLES)
    artifact = tmp_path / "graph"

    build_graph(load_snapshot(tmp_path), artifact)

    conn = open_for_reading(artifact)
    assert {
        deck_id: (placement, placement_rule, norm_rule)
        for deck_id, placement, placement_rule, norm_rule in rows(conn.execute(
            """MATCH (d:Deck) WHERE d.deckId IN
                   ['CLEAN0', 'BROKEN4', 'TITLES0', 'TITLES1', 'TITLES2', 'TITLES3']
               RETURN d.deckId, d.placement, d.placementImputed, d.normImputed"""))
    } == {
        # The source scored both numbers and nothing contradicted its field.
        "CLEAN0": (5, None, None),
        # The source scored the rank; its norm was re-ranked against a corrected
        # field, which nothing recorded before this change (issue #140).
        "BROKEN4": (5, None, "rescaled"),
        # A rank read off the title, then normalised here for the first time.
        "TITLES0": (1, "title-single", "minted"),
        "TITLES1": (3, "title-range", "minted"),
        "TITLES2": (5, "title-range", "minted"),
        # Nothing recoverable: the deck stays unranked and the graph says why,
        # rather than leaving a null that reads the same as a source number.
        "TITLES3": (None, "none", "none"),
    }
    # A minted norm is ranked against the event's field like any other, so it is
    # directly comparable with the norms beside it.
    assert dict(rows(conn.execute(
        """MATCH (d:Deck) WHERE d.deckId IN ['TITLES0', 'TITLES1', 'TITLES2']
           RETURN d.deckId, d.placementNorm"""))) == {
        "TITLES0": 0.0, "TITLES1": pytest.approx(2 / 23),
        "TITLES2": pytest.approx(4 / 23),
    }


def test_a_placement_with_no_norm_is_minted_one_against_the_events_field(tmp_path):
    # A rank the source scored but never normalised. The source's own
    # `(placement - 1) / (eventSize - 1)` divides by zero at a claimed field of 1,
    # so it shipped nulls; every reader in the app gates on `placementNorm`, so
    # those decks fell out of every ranked metric while the graph held their rank
    # (issue #162). The norm is minted against the field the event was really
    # played in, the same corrected `Event.fieldSize` a rescale uses.
    _write_snapshot(tmp_path, _scored("CUT", 8, [(1, None), (2, None), (3, None),
                                                 (4, None), (5, None), (5, None),
                                                 (5, None), (5, None)]))
    artifact = tmp_path / "graph"

    build_graph(load_snapshot(tmp_path), artifact)

    conn = open_for_reading(artifact)
    assert _scalar(conn, "MATCH (e:Event) RETURN e.fieldImputed") == "B"
    assert _scalar(conn, "MATCH (e:Event) RETURN e.fieldSize") == MIN_CUT_FIELD
    minted = dict(rows(conn.execute(
        "MATCH (d:Deck) RETURN d.placement, d.placementNorm")))
    assert minted == {1: 0.0, 2: pytest.approx(1 / 23), 3: pytest.approx(2 / 23),
                      4: pytest.approx(3 / 23), 5: pytest.approx(4 / 23)}
    # The source gave these ranks, so only the norm beside them was decided here.
    assert {i for _, i in rows(conn.execute(
        "MATCH (d:Deck) RETURN d.deckId, d.placementImputed"))} == {None}
    assert {i for _, i in rows(conn.execute(
        "MATCH (d:Deck) RETURN d.deckId, d.normImputed"))} == {"minted"}


def test_a_placement_the_events_field_cannot_hold_is_not_minted_a_norm(tmp_path):
    # A norm outside 0..1 is not a finish, it is arithmetic on two numbers that do
    # not belong to each other, and it would enter every mean silently while
    # `normImputed` claimed a rule stood behind it. Both shapes are reachable from
    # the title grammar: a rank of 0 is below any field, and a rank past the field
    # survives wherever the field is not corrected (`corrected_field` whitelists
    # Tournament, so a Teams event's claimed size stands as given). Neither occurs
    # in today's record; the guard is what keeps that a fact rather than a hope,
    # since minting is meant to reach a future class of recovered rank untouched
    # (issue #162).
    _write_snapshot(tmp_path, [
        _deck("LOW", "TEAMS", "2025-06-01T00:00:00+00:00", eventType="Teams",
              eventSize=10, placement=None, placementNorm=None,
              name="0th - Robert L - TEAMS"),
        _deck("HIGH", "TEAMS", "2025-06-01T00:00:00+00:00", eventType="Teams",
              eventSize=10, placement=None, placementNorm=None,
              name="121st - Brennan C - TEAMS"),
    ])
    artifact = tmp_path / "graph"

    build_graph(load_snapshot(tmp_path), artifact)

    conn = open_for_reading(artifact)
    # The rank the title gave is kept, because the title really says it. What no
    # rule can produce is a norm, and the graph says so rather than leaving a null
    # that reads like a number the source chose not to give.
    assert {
        deck_id: (placement, norm, norm_rule)
        for deck_id, placement, norm, norm_rule in rows(conn.execute(
            """MATCH (d:Deck)
               RETURN d.deckId, d.placement, d.placementNorm, d.normImputed"""))
    } == {"LOW": (0, None, "none"), "HIGH": (121, None, "none")}


def test_an_event_a_refile_builds_carries_the_refile_rule_on_its_field_and_norms(tmp_path):
    # The field a [[refile]] states is the owner's ruling, not a count the source
    # stood behind, so the Event says a rule decided it and every norm minted
    # against it says the same (ADR 0016, issue #268). The hover reads both
    # marks off these columns. With no field stated the field rules decide as
    # for any blank `eventSize`, which for a Tournament is Rule C's floor.
    _write_snapshot(tmp_path, [
        _deck("d1", "E", "2026-01-01T00:00:00+00:00"),
        _deck("d2", "E", "2026-01-01T00:00:00+00:00", placement=3, placementNorm=0.5),
        _deck("d3", "E", "2026-01-01T00:00:00+00:00", placement=4, placementNorm=0.6),
        _deck("d4", "E", "2026-01-02T00:00:00+00:00", placement=1, placementNorm=0.0),
    ])
    repaired, _ = apply_repairs(load_snapshot(tmp_path), Repairs(
        hold_outs=(),
        refiles=(Refile(("d2", "d3"), "folded", "20260101T000000Z", "E26", "Tournament", 49),
                 Refile(("d4",), "folded", "20260101T000000Z", "E27", "Tournament", None)),
    ))

    build_graph(repaired, tmp_path / "graph")

    conn = open_for_reading(tmp_path / "graph")
    assert sorted(map(tuple, rows(conn.execute(
        "MATCH (e:Event) RETURN e.event, e.fieldSize, e.fieldImputed")))) == [
        ("E", 32, None), ("E26", 49, "refiled"), ("E27", MIN_CUT_FIELD, "C")]
    assert sorted(map(tuple, rows(conn.execute(
        "MATCH (d:Deck) RETURN d.deckId, d.placementNorm, d.normImputed")))) == [
        ("d1", 0.0, None), ("d2", 2 / 48, "refiled"), ("d3", 3 / 48, "refiled"),
        ("d4", 0.0, "refiled")]
    # A stated field was never refuted, so the report's list of counted
    # contradictions holds the event the rules corrected and not the one the
    # ruling stated (what the hover stars is the Event column above).
    report = json.loads(reconciliation_path(tmp_path / "graph").read_text())
    assert [(f["event"], f["rule"]) for f in report["field_evidence"]] == [("E27", "C")]


def test_the_report_carries_the_source_repairs_the_ingest_applied(tmp_path, snapshot_dir):
    # Applied in `ingest`, where the snapshot sequence is, and written here, where
    # the report is: the build has no view of what a repair matched, so it takes
    # the section as given and omits it for a build nobody handed one.
    section = {"applied": [{"kind": "hold_out", "deck": "x", "detail": "held out: why",
                            "snapshots": ["20260101T000000Z"]}], "dead": []}

    build_graph(load_snapshot(snapshot_dir), tmp_path / "graph", source_repairs=section)
    build_graph(load_snapshot(snapshot_dir), tmp_path / "bare")

    report = json.loads(reconciliation_path(tmp_path / "graph").read_text())
    assert report["source_repairs"] == section
    assert "source_repairs" not in json.loads(
        reconciliation_path(tmp_path / "bare").read_text())


def test_every_event_the_checked_in_repairs_build_carries_the_refile_rule(live_graph):
    # The record's side of issue #268: an event a [[refile]] built with a stated
    # field has no source count behind it, so its field must read as a rule and
    # not as the source's number standing. Read off the checked-in file rather
    # than naming the event, so the next refile is graded the build it lands.
    stated = {r.event for r in load_repairs().refiles if r.field is not None}
    assert stated, "the checked-in repairs file builds no event with a stated field"

    rules = dict(map(tuple, rows(live_graph.execute(
        "MATCH (e:Event) RETURN e.event, e.fieldImputed"))))
    assert {event: rules.get(event) for event in stated} == {
        event: "refiled" for event in stated}


def test_the_record_refuses_no_entry_on_file(live_graph):
    # The guard is only worth what the checked-in dictionary can pass: every
    # entry was stamped with the name its ids recovered on the earliest snapshot
    # holding them (scripts/entry_provenance.py), and today every one of those ids
    # still recovers that name's family. An entry reddening here is one upstream
    # has moved under since, which a rebuild also announces under a banner; the
    # remedy is retiring or re-keying the entry, never relaxing the comparison
    # (issue #283).
    report = json.loads(reconciliation_path(artifact_path()).read_text())
    assert report["refused_entries"] == []


def test_a_refused_entry_applies_nothing_and_is_listed_in_the_report(tmp_path):
    # The load-bearing property of the id-reuse guard (issue #283): an entry whose
    # id upstream handed to someone else must leave the graph exactly as a build
    # without the entry would, and say so in the report. Graded the way the hold's
    # inertness is: the same snapshot built twice, with the entry and without.
    # On different archetypes, so the identity rules, which merge Alex W onto
    # Alexander W on an identical list (issue #273), leave this pair to the entry.
    _write_snapshot(tmp_path, [
        _deck("d1", "E1", "2026-01-01T00:00:00+00:00", primaryTag="lands",
              pilot="LunarAzureViper", name="01st Alexander W - Lands - E1"),
        _deck("d2", "E2", "2026-02-01T00:00:00+00:00", primaryTag="walks",
              pilot="FrostyIvoryStag", name="01st Alex W - Walks - E2"),
    ])
    snapshot = load_snapshot(tmp_path)
    (tmp_path / "pilots.toml").write_text("""
        [[merge]]
        ids = ["LunarAzureViper", "FrostyIvoryStag"]
        canonical = "FrostyIvoryStag"
        as_of = "20260713T232944Z"
        names = { "LunarAzureViper" = "Lachie C", "FrostyIvoryStag" = "Alex W" }
    """)
    written = load_curation(tmp_path / "pilots.toml")

    with_entry, entry_ids, entry_report = _identity(snapshot, tmp_path / "refused", written)
    without, plain_ids, plain_report = _identity(snapshot, tmp_path / "plain", Curation.empty())

    assert with_entry == without and with_entry.pilots == 2
    assert entry_ids == plain_ids
    assert entry_report["refused_entries"] == [{
        "kind": "merge", "ids": ["FrostyIvoryStag", "LunarAzureViper"],
        "key": "LunarAzureViper", "written": "Lachie C", "recovered": "Alexander W",
    }]
    assert plain_report["refused_entries"] == []
    assert {k: v for k, v in entry_report.items() if k != "refused_entries"} == {
        k: v for k, v in plain_report.items() if k != "refused_entries"}
