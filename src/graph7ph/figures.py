"""The corpus figures the FAQ quotes, derived from a built graph (issue #264)."""

import json
import statistics
from itertools import combinations
from pathlib import Path

import ladybug

from graph7ph.db import rows
from graph7ph.query import hidden_gems_subgraph
from graph7ph.trends import (
    MIN_SHARED_EVENTS,
    NotEnoughHistory,
    _cut_only_events,
    _tail_bounds,
    archetype_timeline,
    archetypes_with_history,
    beats_a_coin,
    comparable_points,
    pilot_performance_over_time,
    player_leaderboard,
)


def figures_path(artifact: Path) -> Path:
    """Where the figures file is written for the bundle at ``artifact``.

    Inside the artifact directory beside the reports, so it promotes and rolls
    back with the graph as one bundle: the figures describe exactly the graph
    they sit beside.
    """
    return Path(artifact) / "figures.json"


def derive(conn: ladybug.Connection, reconciliation: dict, cut_floor: int) -> dict:
    """Every corpus figure the FAQ quotes, read off the graph at ``conn``.

    ``cut_floor`` is the field the build puts under a top cut reported as a
    field (``build.MIN_CUT_FIELD``), handed in rather than imported because the
    build is what calls this.
    """
    events = list(rows(conn.execute(
        "MATCH (e:Event) RETURN e.fieldSize, e.fieldImputed, e.eventType"
    )))
    # A settled field is one the Event node says this project decided, under
    # whichever rule, a refiled event's stated field included: the claim is about
    # who decided the number, not how.
    settled = [field for field, imputed, _ in events if imputed is not None]
    return {
        "events": len(events),
        "settled_fields": len(settled),
        "settled_at_floor": sum(1 for field in settled if field == cut_floor),
        "teams_events": sum(1 for *_, kind in events if kind == "Teams"),
        "decks": _scalar(conn, "MATCH (d:Deck) RETURN count(*)"),
        "decks_multi_archetype": _scalar(
            conn, "MATCH (d:Deck)-[:HAS_ARCHETYPE]->(:Archetype) "
                  "WITH d, count(*) AS n WHERE n > 1 RETURN count(*)"),
        # The FAQ's worked example of a tag against an engine (ADR 0023).
        "golgari_tagged": _scalar(
            conn, "MATCH (d:Deck)-[:HAS_ARCHETYPE]->(:Archetype {tag: 'golgari'}) "
                  "RETURN count(DISTINCT d)"),
        "golgari_primary": _scalar(
            conn, "MATCH (d:Deck)-[:HAS_ARCHETYPE {isPrimary: true}]->"
                  "(:Archetype {tag: 'golgari'}) RETURN count(DISTINCT d)"),
        "pilots_offered": _scalar(conn, "MATCH (p:Pilot) RETURN count(*)"),
        # One id the project read as several people (ADR 0004): the split ids
        # carry a numbered suffix, and the career that kept the bare id is named
        # "... 1" to match.
        "pilots_numbered": _scalar(
            conn, "MATCH (p:Pilot) WHERE p.pilot =~ '.*#[0-9]+' RETURN count(*)"
        ) + _scalar(
            conn, "MATCH (p:Pilot) WHERE p.displayName =~ '.* 1' RETURN count(*)"),
        # Several ids the project has not read as one person: every pair the
        # rules left watching, which is two nodes until the evidence says
        # otherwise (issue #273). A pair merged or separated by rule is no
        # longer a question.
        "pilots_paired": len({
            pilot
            for entry in reconciliation["ruled_pairs"] if entry["state"] == "watching"
            for pilot in entry["pilots"]
        }),
    } | _finishes(conn) | _archetypes(conn) | _leaderboard(conn) | _gems(conn) \
        | _head_to_head_pairs(conn)


def _leaderboard(conn: ladybug.Connection) -> dict:
    """The leaderboard's size, and how far it sits from the performance headline.

    The performance answer says the two measures "typically put a pilot about a
    dozen places apart": each leaderboard pilot's career headline (their yearly
    averages weighted by events, as the chart's headline computes it) ranks
    them among the leaderboard pilots who have one, and the gap is the distance
    between that rank and their leaderboard rank. A leaderboard pilot without
    two charted years has no headline and is left out of the gap.
    """
    try:
        career = {cell.pilot: cell.rank for cell in player_leaderboard(conn).cells}
    except NotEnoughHistory:
        career = {}
    headline = {}
    for pilot in career:
        try:
            cells = [c for c in pilot_performance_over_time(conn, pilot).cells
                     if c.mean_norm is not None]
        except NotEnoughHistory:
            continue
        events = sum(c.events for c in cells)
        if events:
            headline[pilot] = sum(c.mean_norm * c.events for c in cells) / events
    by_headline = {
        pilot: rank for rank, pilot in enumerate(sorted(headline, key=headline.get), 1)
    }
    gaps = [abs(by_headline[pilot] - career[pilot]) for pilot in headline]
    return {
        "leaderboard_pilots": len(career),
        "rank_gap_median": statistics.median(gaps) if gaps else None,
        "rank_gap_pilots": len(gaps),
    }


def _gems(conn: ladybug.Connection) -> dict:
    """The gem certainty answer: how long the list is, and how much of it is luck."""
    gems = hidden_gems_subgraph(conn)
    return {
        "gems_listed": sum(1 for node in gems.nodes if node.kind == "Card"),
        "gems_expected_by_luck": round(float(gems.expected_by_luck or 0.0), 3),
    }


def _finishes(conn: ladybug.Connection) -> dict:
    """The finish answer's comparison, and the events a tail bound names.

    A finish is the app's score, one minus the norm. The bracket events are the
    ones :func:`trends._cut_only_events` drops from every averaging chart, and
    the comparison is why: their average is taken over the good finishes alone.
    Each bounded event is listed with the slot its tail starts under and its
    field, which is how the rivalry answers describe it ("published 16 of its
    28 finishes").
    """
    cut_only = _cut_only_events(conn)
    norms = {"cut": [], "full": []}
    for event, norm in rows(conn.execute(
        "MATCH (d:Deck)-[:PLAYED_AT]->(e:Event) WHERE d.placementNorm IS NOT NULL "
        "RETURN e.event, d.placementNorm"
    )):
        norms["cut" if event in cut_only else "full"].append(norm)
    bounds = _tail_bounds(conn, cut_only)
    published = {event: (last, field) for event, last, field in rows(conn.execute(
        "MATCH (d:Deck)-[:PLAYED_AT]->(e:Event) WHERE e.event IN $events "
        "RETURN e.event, max(d.placement), e.fieldSize", {"events": sorted(bounds)}
    ))}
    return {
        "mean_finish_cut_only": _mean_finish(norms["cut"]),
        "mean_finish_full": _mean_finish(norms["full"]),
        "tail_bounds": [
            {"event": event, "published": published[event][0],
             "field": published[event][1], "bound": round(bound, 3)}
            for event, bound in sorted(bounds.items())
        ],
    }


def _mean_finish(norms: list[float]) -> float | None:
    return round(1 - sum(norms) / len(norms), 3) if norms else None


def _archetypes(conn: ladybug.Connection) -> dict:
    """The archetype timeline answer: the picker, its coin-close headlines, its pairs.

    Each figure is read through the chart's own functions, so what the answer
    says the chart does is what the chart does: an archetype is offered where
    :func:`archetypes_with_history` offers it, its solo headline is coin close
    where :func:`beats_a_coin` would hedge it, and a pair is drawn where
    :func:`archetype_timeline` draws it rather than refusing, bounded where any
    of its points is (ADR 0024). The pair sweep is the slow part of this file.
    """
    offered = archetypes_with_history(conn)
    coin_close = 0
    for _, tag, _ in offered:
        points = comparable_points(archetype_timeline(conn, tag).cells, paired=False)
        led = sum(1 for point in points if point.mean_norm_a < 0.5)
        if not beats_a_coin(led, len(points)):
            coin_close += 1
    drawn = bounded = 0
    for (_, a, _), (_, b, _) in combinations(offered, 2):
        try:
            cells = archetype_timeline(conn, a, b).cells
        except NotEnoughHistory:
            continue
        drawn += 1
        if any(is_bound for cell in cells for _, is_bound in cell.drawn()):
            bounded += 1
    return {
        "archetypes_offered": len(offered),
        "archetypes_coin_close": coin_close,
        "archetype_pairs_drawn": drawn,
        "archetype_pairs_bounded": bounded,
    }


def _head_to_head_pairs(conn: ladybug.Connection) -> dict:
    """How many pilot pairs the head-to-head chart draws, and what a bound did to them.

    The chart's own rule (ADR 0024): a meeting is comparable where both were
    scored, or where one was scored at an event whose tail is bounded; a pair
    draws once it has :data:`MIN_SHARED_EVENTS` comparable meetings. Among the
    drawn pairs, "bounded" holds a meeting the bound settled, and "break" holds a
    meeting the chart breaks over (one side unscored with no bound to settle it,
    or neither side scored) and no bounded one, so the two are disjoint and
    "affected" is their sum. Enumerated here rather than through the timeline
    function because the chart draws one pair at a time and this counts them all.
    """
    bounds = _tail_bounds(conn, _cut_only_events(conn))
    attended: dict[str, set[str]] = {}
    scored: dict[str, set[str]] = {}
    for pilot, event, norm in rows(conn.execute(
        "MATCH (p:Pilot)<-[:PILOTED_BY]-(d:Deck)-[:PLAYED_AT]->(e:Event) "
        "RETURN p.pilot, e.event, d.placementNorm"
    )):
        attended.setdefault(event, set()).add(pilot)
        if norm is not None:
            scored.setdefault(event, set()).add(pilot)
    comparable: dict[tuple[str, str], int] = {}
    bounded: set[tuple[str, str]] = set()
    broken: set[tuple[str, str]] = set()
    for event, pilots in attended.items():
        finished = scored.get(event, set())
        for pair in combinations(sorted(pilots), 2):
            on_record = sum(1 for pilot in pair if pilot in finished)
            if on_record == 2:
                comparable[pair] = comparable.get(pair, 0) + 1
            elif on_record == 1 and event in bounds:
                comparable[pair] = comparable.get(pair, 0) + 1
                bounded.add(pair)
            else:
                broken.add(pair)
    drawn = {pair for pair, n in comparable.items() if n >= MIN_SHARED_EVENTS}
    bounded &= drawn
    broken = (broken & drawn) - bounded
    return {
        "h2h_pairs_drawn": len(drawn),
        "h2h_pairs_bounded": len(bounded),
        "h2h_pairs_break": len(broken),
        "h2h_pairs_affected": len(bounded) + len(broken),
    }


def _scalar(conn: ladybug.Connection, query: str):
    return next(rows(conn.execute(query)))[0]


def write_figures(
    artifact: Path, conn: ladybug.Connection, reconciliation: dict, cut_floor: int
) -> None:
    figures_path(artifact).write_text(
        json.dumps(derive(conn, reconciliation, cut_floor), indent=2)
    )
