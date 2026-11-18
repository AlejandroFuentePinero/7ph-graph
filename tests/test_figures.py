"""The corpus figures the FAQ quotes, derived by the build into the bundle (issue #264)."""

import json

from graph7ph.build import build_graph
from graph7ph.figures import figures_path
from graph7ph.models import load_snapshot


def test_a_build_seals_the_figures_file_inside_the_bundle(snapshot_dir, tmp_path):
    # Beside the database and the reports, so the one rename that promotes the
    # bundle promotes the figures with it and a rollback carries its own (issue
    # #264): a sibling would not travel, and a deploy would ship a graph with
    # figures read off another one.
    artifact = tmp_path / "graph"

    build_graph(load_snapshot(snapshot_dir), artifact)

    assert figures_path(artifact).parent == artifact
    assert isinstance(json.loads(figures_path(artifact).read_text()), dict)


def _deck(deck_id, pilot, event, placement, norm, field, tags=("deck",), primary="deck",
          event_type="Tournament", created="2025-06-01"):
    """One deck record, titled the way the source titles a scored or unscored deck."""
    engine = [f"engine:{t}" for t in tags]
    return {
        "deckId": deck_id,
        "name": (f"{placement}st {pilot} - Deck - {event}" if placement is not None
                 else f"{pilot} - Deck - {event}"),
        "deckName": "Deck",
        "pilot": pilot,
        "event": event,
        "eventId": f"evt_{event}",
        "eventType": event_type,
        "eventSize": field,
        "placement": placement,
        "placementNorm": norm,
        "createdAt": f"{created}T00:00:00+00:00",
        "colour": "colour:U",
        "macro": "macro:combo",
        "engineTags": engine,
        "engineTagLabels": {t: t.removeprefix("engine:").title() for t in engine},
        "primaryTag": f"engine:{primary}",
        "primaryTagWeights": {t: 100 // len(engine) for t in engine},
    }


def _scored(event, field, pilots, start=1, **kw):
    """``pilots`` placed ``start``, ``start + 1``, ... at ``event``, normed against ``field``."""
    return [_deck(f"{event}-{p}", p, event, start + i, (start + i - 1) / (field - 1), field, **kw)
            for i, p in enumerate(pilots)]


def _unscored(event, field, pilots, **kw):
    return [_deck(f"{event}-{p}", p, event, None, None, field, **kw) for p in pilots]


def _fillers(event, n):
    return [f"filler-{event}-{i}" for i in range(n)]


def _corpus(root):
    """A small corpus with one of each thing the FAQ figures count.

    Six events. E1 published its whole field of 9 (eight scored decks). E2, also a
    field of 9, published seven of its eight decks and left ``cy`` unscored, so it
    carries a tail bound (ADR 0024). E3 declared a field of 24 and published a
    top eight, a bracket, with ``bob``, ``cy`` and ``dee`` registered unscored. E4
    is a Teams event, three decks on two of its three team slots. E5 claimed a
    field of 5, which Rule B floors to 24; E6 claimed 20 against a recorded 30th,
    which Rule A corrects to 30 (issue #140). ``ann`` plays Storm with a Golgari splash on every deck and
    ``bob`` plays Golgari outright.
    """
    storm = {"tags": ("golgari", "storm"), "primary": "storm"}
    golgari = {"tags": ("golgari",), "primary": "golgari"}
    decks = [
        *_scored("E1", 9, ["ann"], **storm), *_scored("E1", 9, ["bob"], start=2, **golgari),
        *_scored("E1", 9, ["cy", "dee", *_fillers("E1", 4)], start=3),
        *_scored("E2", 9, ["ann"], **storm), *_scored("E2", 9, ["bob"], start=2, **golgari),
        *_scored("E2", 9, _fillers("E2", 5), start=3), *_unscored("E2", 9, ["cy"]),
        *_scored("E3", 24, ["ann"], **storm), *_scored("E3", 24, _fillers("E3", 7), start=2),
        *_unscored("E3", 24, ["bob"], **golgari), *_unscored("E3", 24, ["cy", "dee"]),
        *_scored("E4", 3, ["t1", "t2"], event_type="Teams"),
        *_scored("E4", 3, ["t3"], start=2, event_type="Teams"),
        *_scored("E5", 5, ["u1", "u2", "u3"]),
        *_scored("E6", 20, ["v1"]), *_scored("E6", 20, ["v2"], start=30),
    ]
    snap = root / "snap"
    snap.mkdir()
    (snap / "decks.json").write_text(json.dumps(decks))
    (snap / "cards_index.json").write_text(json.dumps({
        "v": 1, "cards": [], "decks": {d["deckId"]: {"m": [], "s": []} for d in decks},
    }))
    return snap


def _figures(root):
    artifact = root / "graph"
    build_graph(load_snapshot(_corpus(root)), artifact)
    return json.loads(figures_path(artifact).read_text())


def test_the_event_figures_count_settled_fields_the_floor_and_teams_events(tmp_path):
    # The finish answer's first paragraph. "Settled" is any field this project
    # decided rather than accepted, under whichever rule; "at the floor" is the
    # subset Rule B put at the cut floor nobody counted. Expected by hand from the
    # corpus above, never by re-running the rules: E5 is the one floored event, E6
    # the one corrected to a counted field, E4 the one Teams event.
    figures = _figures(tmp_path)

    assert figures["events"] == 6
    assert figures["settled_fields"] == 2
    assert figures["settled_at_floor"] == 1
    assert figures["teams_events"] == 1


def test_the_deck_and_pilot_figures_count_decks_tags_and_names_offered(tmp_path):
    # The pilot-archetype and archetype answers. A deck is multi-archetype when it
    # carries more than one tag; the Golgari example counts a deck tagged Golgari
    # anywhere against one whose engine it is (ADR 0023). By hand: ``ann``'s three
    # Storm decks carry the splash, ``bob``'s three are Golgari outright; 35 decks
    # under 28 distinct pilots.
    figures = _figures(tmp_path)

    assert figures["decks"] == 35
    assert figures["decks_multi_archetype"] == 3
    assert figures["golgari_tagged"] == 6
    assert figures["golgari_primary"] == 3
    assert figures["pilots_offered"] == 28


def test_the_head_to_head_figures_classify_every_pair_the_chart_will_draw(tmp_path):
    # The head-to-head answer's four counts (ADR 0024). A pair is drawn once the
    # record can compare it at two meetings: both scored, or one scored at an
    # event whose tail is bounded. It is "bounded" if such a meeting is among
    # them, and "keeps the break" if a meeting had one side unscored where no
    # bound exists or both sides unscored, and no bounded meeting at all; the two
    # are disjoint and "affected" is their sum. By hand over the corpus above:
    # ann-bob compare at E1 and E2 and break at the bracket E3 (bob unscored);
    # ann-cy and bob-cy compare at E1 and at the bounded E2, and so are bounded
    # whatever E3 did to them; dee shares E1 with everyone and the bracket E3
    # unscored, which is one comparable meeting, so none of dee's pairs draw;
    # fillers meet anyone once at most.
    figures = _figures(tmp_path)

    assert figures["h2h_pairs_drawn"] == 3
    assert figures["h2h_pairs_bounded"] == 2
    assert figures["h2h_pairs_break"] == 1
    assert figures["h2h_pairs_affected"] == 3


def test_the_finish_figures_average_the_brackets_apart_and_name_each_bounded_event(
    tmp_path,
):
    # The finish answer's "misleadingly high" comparison and the "16 of its 28"
    # event the rivalry answers name. A finish is the app's score, one minus the
    # norm, averaged over scored decks at the bracket events (E3, E5 floored to 24
    # and E6 corrected to 30, all under half their field on record) against the
    # rest. By hand after the build's rescaling: the brackets' 13 scored decks sum
    # to 28/23 + 3/23 + 1 in norm, the other 18 to 3.5 + 2.625 + 1.0 (E4's three
    # decks sit on slots 1, 2 and 2 of a field of 3). E2 is the
    # one event with a tail to bound: it published 7 slots of 9.
    figures = _figures(tmp_path)

    assert figures["mean_finish_cut_only"] == 0.819
    assert figures["mean_finish_full"] == 0.604
    assert figures["tail_bounds"] == [
        {"event": "E2", "published": 7, "field": 9, "bound": 0.875}
    ]


def test_the_archetype_figures_count_the_picker_the_coin_close_and_the_pairs_drawn(
    tmp_path,
):
    # The archetype timeline answer. The picker offers an archetype scored at two
    # events that published a field (ADR 0022); its headline is "coin close" where
    # the events it led out of those scored is a count a coin hands out (issue
    # #175); a pair draws once both were scored at two shared events. By hand:
    # Deck, Storm and Golgari each score at E1 and E2 (Deck at E4 too), the
    # brackets E3, E5 and E6 scoring nobody. Two of two and one of three are both
    # coin counts, and every pair of the three shares E1 and E2.
    figures = _figures(tmp_path)

    assert figures["archetypes_offered"] == 3
    assert figures["archetypes_coin_close"] == 3
    assert figures["archetype_pairs_drawn"] == 3
    assert figures["archetype_pairs_bounded"] == 0


def test_a_graph_too_thin_for_the_trend_tools_still_gets_its_figures(
    snapshot_dir, tmp_path
):
    # Every fixture build goes through this seam, and the three-deck fixture
    # reaches none of the trend floors: no major for the leaderboard, no career
    # for the rank gap, no rare card for the gem list, no numbered or paired
    # name. The file still seals, saying so with zeros and nulls rather than
    # aborting the build or omitting the keys the prose will ask for.
    artifact = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), artifact)

    figures = json.loads(figures_path(artifact).read_text())

    assert figures["leaderboard_pilots"] == 0
    assert figures["rank_gap_median"] is None
    assert figures["rank_gap_pilots"] == 0
    assert figures["gems_listed"] == 0
    assert figures["gems_expected_by_luck"] == 0.0
    assert figures["pilots_numbered"] == 0
    assert figures["pilots_paired"] == 0


def test_the_cheap_figures_in_the_live_bundle_are_what_the_graph_holds(live_graph):
    # Graded against the real record by queries of their own, never by re-running
    # the derivation (issue #264): the figures file is what the prose will quote,
    # so a figure that drifted from the graph it sits beside is the stale FAQ
    # number this file exists to retire. The slow figures (pairs, rank gap) are
    # held by the hand-authored corpus above; these are the ones a count settles.
    from graph7ph.db import artifact_path, rows
    from graph7ph.trends import player_leaderboard

    figures = json.loads(figures_path(artifact_path()).read_text())

    def count(cypher):
        return list(rows(live_graph.execute(cypher)))[0][0]

    assert figures["events"] == count("MATCH (e:Event) RETURN count(*)")
    assert figures["settled_fields"] == count(
        "MATCH (e:Event) WHERE e.fieldImputed IS NOT NULL RETURN count(*)")
    assert figures["settled_at_floor"] == count(
        "MATCH (e:Event) WHERE e.fieldImputed IS NOT NULL AND e.fieldSize = 24 "
        "RETURN count(*)")
    assert figures["teams_events"] == count(
        "MATCH (e:Event) WHERE e.eventType = 'Teams' RETURN count(*)")
    assert figures["decks"] == count("MATCH (d:Deck) RETURN count(*)")
    tags_per_deck = {}
    for deck, in rows(live_graph.execute(
            "MATCH (d:Deck)-[:HAS_ARCHETYPE]->(:Archetype) RETURN d.deckId")):
        tags_per_deck[deck] = tags_per_deck.get(deck, 0) + 1
    assert figures["decks_multi_archetype"] == sum(1 for n in tags_per_deck.values() if n > 1)
    assert figures["golgari_tagged"] == count(
        "MATCH (d:Deck)-[:HAS_ARCHETYPE]->(a:Archetype) WHERE a.tag = 'golgari' "
        "RETURN count(DISTINCT d)")
    assert figures["golgari_primary"] == count(
        "MATCH (d:Deck)-[r:HAS_ARCHETYPE]->(a:Archetype) "
        "WHERE a.tag = 'golgari' AND r.isPrimary RETURN count(DISTINCT d)")
    assert figures["pilots_offered"] == count("MATCH (p:Pilot) RETURN count(*)")
    # The two identity figures, as `test_app` grades them against the prose today.
    numbered = count("MATCH (p:Pilot) WHERE p.pilot =~ '.*#[0-9]+' RETURN count(*)")
    stems = count("MATCH (p:Pilot) WHERE p.displayName =~ '.* 1' RETURN count(*)")
    assert figures["pilots_numbered"] == numbered + stems
    report = json.loads((artifact_path() / "reconciliation.json").read_text())
    watching = [e for e in report["ruled_pairs"] if e["state"] == "watching"]
    assert figures["pilots_paired"] == len({pid for e in watching for pid in e["pilots"]})
    assert figures["leaderboard_pilots"] == len(
        {cell.pilot for cell in player_leaderboard(live_graph).cells})


def _card(name):
    return {"canon": name.lower(), "name": name, "type": "Instants", "manaCost": None,
            "manaValue": 0.0, "reserved": False, "priceUsd": 1.0, "points": 0,
            "pointsCompanion": 0}


def _community_corpus(root):
    """A small corpus with one of each thing the Community page counts (issue #346).

    Six events over three years. Open24 (a field of 12) and Cup24 (10) fall in March
    2024; Open24's first list was registered on the 10th and its other two on the
    12th. Major25 declared a field of 90 and published 46 finishes, over half, so it
    is the one major. Teams25 is a Teams event of two teams and four pilots.
    Bracket26 declared a field of 80 and published a top eight: a field over the
    major line that is no major. Open26 is a field of 9.

    ``ann`` and ``bob`` play in all three years, ``cy`` in 2024 and 2025, ``dee`` in
    2024 alone, ``eve`` in 2025 and 2026. ``ann`` plays Storm with a Golgari splash,
    ``bob`` Golgari until 2026, and ``ivy``'s one deck carries a second tag no deck
    has as its primary. Every deck holds Island; ``ann``'s hold Bolt too, and
    ``bob``'s first holds Opt on its Side. One card of the catalogue is in no deck.
    """
    storm = {"tags": ("golgari", "storm"), "primary": "storm"}
    golgari = {"tags": ("golgari",), "primary": "golgari"}
    major_fillers = [f"filler-Major25-{i}" for i in range(43)]
    bracket_fillers = [f"filler-Bracket26-{i}" for i in range(5)]
    decks = [
        *_scored("Open24", 12, ["ann"], created="2024-03-10", **storm),
        *_scored("Open24", 12, ["bob"], start=2, created="2024-03-12", **golgari),
        *_scored("Open24", 12, ["cy"], start=3, created="2024-03-12"),
        *_scored("Cup24", 10, ["ann"], created="2024-03-25", **storm),
        *_scored("Cup24", 10, ["dee"], start=2, created="2024-03-25"),
        *_scored("Major25", 90, ["ann"], created="2025-07-05", **storm),
        *_scored("Major25", 90, ["bob"], start=2, created="2025-07-05", **golgari),
        *_scored("Major25", 90, ["eve", *major_fillers], start=3, created="2025-07-05"),
        *_scored("Teams25", 2, ["cy", "fay"], event_type="Teams", created="2025-09-01"),
        *_scored("Teams25", 2, ["gus", "hal"], event_type="Teams", created="2025-09-01"),
        *_scored("Bracket26", 80, ["ann"], created="2026-01-15", **storm),
        *_scored("Bracket26", 80, ["eve"], start=2, created="2026-01-15"),
        *_scored("Bracket26", 80, ["ivy"], start=3, created="2026-01-15",
                 tags=("deck", "zoo")),
        *_scored("Bracket26", 80, bracket_fillers, start=4, created="2026-01-15"),
        *_scored("Open26", 9, ["bob", "jan"], created="2026-02-20"),
    ]
    island, bolt, opt = 0, 1, 2
    boards = {d["deckId"]: {"m": [island], "s": []} for d in decks}
    for deck in decks:
        if deck["pilot"] == "ann":
            boards[deck["deckId"]]["m"].append(bolt)
    boards["Open24-bob"]["s"].append(opt)
    snap = root / "snap"
    snap.mkdir()
    (snap / "decks.json").write_text(json.dumps(decks))
    (snap / "cards_index.json").write_text(json.dumps({
        "v": 1, "cards": [_card(n) for n in ("Island", "Bolt", "Opt", "Unplayed")],
        "decks": boards,
    }))
    return snap


def _community(root):
    artifact = root / "graph"
    build_graph(load_snapshot(_community_corpus(root)), artifact)
    return json.loads(figures_path(artifact).read_text())["community"]


def test_the_community_totals_count_what_is_on_record_and_what_the_newest_year_holds(
    tmp_path,
):
    # The six figures' five counts, each with the newest year's beneath (issue
    # #346). By hand from the corpus above. Pilots: four first seen in 2024, eve,
    # the 43 fillers and the three new teams pilots in 2025, ivy, five fillers and
    # jan in 2026. Decks: 3 + 2 + 46 + 4 + 8 + 2. A card is one a deck holds on
    # either board, so the catalogue's fourth is not counted, and 2026 holds no
    # Opt. An archetype is one some deck has as its primary (ADR 0023), so ivy's
    # second tag is not counted, and nobody's primary is Golgari in 2026.
    community = _community(tmp_path)

    assert community["years"] == [2024, 2025, 2026]
    assert community["totals"] == {
        "pilots": 58, "events": 6, "decks": 65, "cards": 3, "archetypes": 3}
    assert community["this_year"] == {
        "pilots": 10, "events": 2, "decks": 10, "cards": 2, "archetypes": 2,
        "played_before": 3}


def test_each_event_is_placed_at_its_first_registration_named_and_marked_if_a_major(
    tmp_path,
):
    # The swarm's dots (issue #346). An event sits at the earliest registration
    # among its decks, the date the leaderboard places it by (ADR 0013): Open24's
    # lists were registered on the 10th and the 12th. Its players are its field,
    # except at the Teams event, whose field of 2 counts teams: there it is the
    # four pilots on record. A major is the leaderboard's: a field over 64 that
    # published its standings, so Major25 is one (46 of 90) and Bracket26, a top
    # eight of 80, is not. Every event's name is written (issue #340), and the
    # largest field's only because it is the major.
    community = _community(tmp_path)

    assert community["events"] == [
        {"date": "2024-03-10", "players": 12, "name": "Open24", "major": False},
        {"date": "2024-03-25", "players": 10, "name": "Cup24", "major": False},
        {"date": "2025-07-05", "players": 90, "name": "Major25", "major": True},
        {"date": "2025-09-01", "players": 4, "name": "Teams25", "major": False},
        {"date": "2026-01-15", "players": 80, "name": "Bracket26", "major": False},
        {"date": "2026-02-20", "players": 9, "name": "Open26", "major": False},
    ]
    assert community["majors"] == 1
    assert community["other_events"] == 5
    assert community["major_field_over"] == 64
    assert community["largest_field"] == {"players": 90, "year": 2025, "major": "Major25"}
    assert community["through"] == "2026-02-20"


def test_pilots_are_counted_from_their_first_event_and_events_by_their_month(tmp_path):
    # The two stacked panels (issue #346). A pilot is first recorded at the date
    # of their first event, which is the event's first registration and not
    # their own list's: bob's Open24 list was registered on the 12th and he is on
    # record from the 10th with ann and cy. By hand, the running count steps at
    # each event that brought someone new: three at Open24, dee at Cup24, eve and
    # the 43 fillers at Major25, three of the four teams pilots, ivy and five
    # fillers at Bracket26, jan at Open26. March 2024 holds two events and no
    # other month more than one; a month with none is not listed.
    community = _community(tmp_path)

    assert community["pilots_on_record"] == [
        ["2024-03-10", 3], ["2024-03-25", 4], ["2025-07-05", 48],
        ["2025-09-01", 51], ["2026-01-15", 57], ["2026-02-20", 58],
    ]
    assert community["events_by_month"] == [
        ["2024-03", 2], ["2025-07", 1], ["2025-09", 1], ["2026-01", 1], ["2026-02", 1],
    ]


def test_each_years_pilots_are_split_by_the_year_they_first_played(tmp_path):
    # The yearly bars and the line under them (issue #346). By hand: 2024 holds
    # its own four. 2025 holds ann, bob and cy from 2024 and 47 of its own (eve,
    # the 43 fillers, three teams pilots). 2026 holds ann and bob from 2024, eve
    # from 2025 and seven of its own. ann and bob are the two on record in every
    # year; dee is in one year and is counted nowhere as having left.
    community = _community(tmp_path)

    assert community["pilots_by_year"] == [
        {"year": 2024, "pilots": 4, "first_played": {"2024": 4}},
        {"year": 2025, "pilots": 50, "first_played": {"2024": 3, "2025": 47}},
        {"year": 2026, "pilots": 10, "first_played": {"2024": 2, "2025": 1, "2026": 7}},
    ]
    assert community["played_every_year"] == 2


def test_the_community_figures_in_the_live_bundle_are_what_the_graph_holds(live_graph):
    # Graded against the real record by a second route (issue #346). The figures
    # reach a year through the Event's Year node and a date through each event's
    # first registration; these read every deck's own registration date instead,
    # and count nodes where the figures count what decks hold. The two agree
    # because the build refuses an event whose decks span two calendar years.
    from collections import Counter

    from graph7ph.db import artifact_path, rows

    community = json.loads(figures_path(artifact_path()).read_text())["community"]

    def count(cypher):
        return list(rows(live_graph.execute(cypher)))[0][0]

    decks = list(rows(live_graph.execute(
        "MATCH (p:Pilot)<-[:PILOTED_BY]-(d:Deck)-[:PLAYED_AT]->(e:Event) "
        "RETURN d.deckId, p.pilot, e.event, d.createdAt")))
    years = sorted({created.year for *_, created in decks})
    latest = years[-1]
    assert community["years"] == years

    primaries = list(rows(live_graph.execute(
        "MATCH (d:Deck)-[r:HAS_ARCHETYPE]->(a:Archetype) WHERE r.isPrimary "
        "RETURN d.deckId, a.tag")))
    holds = list(rows(live_graph.execute(
        "MATCH (d:Deck)-[:CONTAINS]->(c:Card) RETURN DISTINCT d.deckId, c.canon")))
    this_year = {deck for deck, *_, created in decks if created.year == latest}
    assert community["totals"] == {
        "pilots": count("MATCH (p:Pilot) RETURN count(*)"),
        "events": count("MATCH (e:Event) RETURN count(*)"),
        "decks": count("MATCH (d:Deck) RETURN count(*)"),
        "cards": count("MATCH (c:Card) RETURN count(*)"),
        "archetypes": len({tag for _, tag in primaries}),
    }

    seen: dict[str, set[int]] = {}
    for _, pilot, _, created in decks:
        seen.setdefault(pilot, set()).add(created.year)
    assert community["this_year"] == {
        "pilots": sum(1 for played in seen.values() if latest in played),
        "events": len({event for _, _, event, created in decks if created.year == latest}),
        "decks": len(this_year),
        "cards": len({canon for deck, canon in holds if deck in this_year}),
        "archetypes": len({tag for deck, tag in primaries if deck in this_year}),
        "played_before": sum(
            1 for played in seen.values() if latest in played and min(played) < latest),
    }
    assert community["pilots_by_year"] == [
        {"year": year,
         "pilots": sum(1 for played in seen.values() if year in played),
         "first_played": {
             str(first): n for first, n in sorted(Counter(
                 min(played) for played in seen.values() if year in played).items())}}
        for year in years
    ]
    assert community["played_every_year"] == sum(
        1 for played in seen.values() if played == set(years))

    # Each event's first registration, taken here over the deck rows.
    first: dict[str, object] = {}
    for _, _, event, created in decks:
        first[event] = min(created, first.get(event, created))
    assert [event["date"] for event in community["events"]] == sorted(
        day.date().isoformat() for day in first.values())
    assert community["through"] == max(first.values()).date().isoformat()
    assert community["events_by_month"] == [
        [month, n] for month, n in sorted(
            Counter(f"{day:%Y-%m}" for day in first.values()).items())]
    # On record from the first registration of a pilot's first event: each pilot's
    # earliest such day, taken over the deck rows, then counted up day by day.
    first_day: dict[str, object] = {}
    for _, pilot, event, _ in decks:
        first_day[pilot] = min(first[event], first_day.get(pilot, first[event]))
    on_record, running = [], 0
    for day, new in sorted(Counter(day.date().isoformat() for day in first_day.values()).items()):
        running += new
        on_record.append([day, running])
    assert community["pilots_on_record"] == on_record

    # Players: a Tournament's field, a Teams event's pilots on record.
    fields = Counter(field for field, in rows(live_graph.execute(
        "MATCH (e:Event) WHERE e.eventType <> 'Teams' RETURN e.fieldSize")))
    teams = {event for event, in rows(live_graph.execute(
        "MATCH (e:Event) WHERE e.eventType = 'Teams' RETURN e.event"))}
    team_pilots = Counter(
        len({pilot for _, pilot, at, _ in decks if at == event}) for event in teams)
    assert Counter(event["players"] for event in community["events"]) == fields + team_pilots
    # The largest field, with the year its event was first registered in. Read off
    # the graph's own rows: the page's figure must be that event's.
    largest = community["largest_field"]
    assert largest["players"] == max(fields + team_pilots)
    sized = {event: field for event, field in rows(live_graph.execute(
        "MATCH (e:Event) WHERE e.eventType <> 'Teams' RETURN e.event, e.fieldSize"))}
    sized |= {event: len({pilot for _, pilot, at, _ in decks if at == event})
              for event in teams}
    at_the_top = {event for event, players in sized.items() if players == largest["players"]}
    assert largest["year"] in {first[event].year for event in at_the_top}
    assert largest["major"] is None or largest["major"] in at_the_top


def test_the_community_page_and_the_leaderboard_count_the_same_majors(live_graph):
    # One function says which events are majors, and both read it (issue #346):
    # the count the page's key gives, the dots it draws solid and names, and the
    # pool the leaderboard says its scores are drawn from.
    from graph7ph.db import artifact_path
    from graph7ph.trends import player_leaderboard

    community = json.loads(figures_path(artifact_path()).read_text())["community"]
    leaderboard = player_leaderboard(live_graph).cells[0].major_events

    assert community["majors"] == leaderboard
    assert sum(event["major"] for event in community["events"]) == leaderboard
    assert community["majors"] + community["other_events"] == len(community["events"])
    # And each event the page names is one whose field is over the line, read off
    # the Event node: the half of the rule a count settles.
    from graph7ph.db import rows

    over = {event for event, in rows(live_graph.execute(
        "MATCH (e:Event) WHERE e.fieldSize > 64 RETURN e.event"))}
    named = {event["name"] for event in community["events"] if event["major"]}
    assert named <= over and len(named) == leaderboard
    # Every event carries its own name, once (issue #340).
    assert sorted(event["name"] for event in community["events"]) == sorted(
        event for event, in rows(live_graph.execute("MATCH (e:Event) RETURN e.event")))
