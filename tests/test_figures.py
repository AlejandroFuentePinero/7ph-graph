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
          event_type="Tournament"):
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
        "createdAt": "2025-06-01T00:00:00+00:00",
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
    open_pairs = report["under_merges"] + report["held_merges"]
    assert figures["pilots_paired"] == len({pid for e in open_pairs for pid in e["pilots"]})
    assert figures["leaderboard_pilots"] == len(
        {cell.pilot for cell in player_leaderboard(live_graph).cells})
