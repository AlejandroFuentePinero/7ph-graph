"""Sweep the four gem constants over the built graph, and grade the field-size question.

The hidden gem rule (ADR 0020) is four numbers: how deep an archetype's own cut runs
(``GEM_TOP_CUT``), how rare a card must be inside it (``MIN_GEM_DECKS`` and
``MAX_GEM_SHARE``), and how often chance alone may explain the crowding
(``MAX_GEM_LUCK``). None of the four is derivable; each is a cell in a grid, and the
grid is scored against the corpus that exists. So the cell the app ships is a
measurement, and this script is the measurement.

Every cell is scored on ``found - luck``: how many cards clear the bar, less how many
of them the null itself expects to clear it. Maximising the raw count is what a
threshold always rewards and is the one thing the rule must not do. A cell qualifies
only if it draws inside ``MAX_GEM_NODES`` and no more than half its list is luck; the
half is a floor on being a finding at all, not a target. The shipped cell clears it by
little: charging each card for the pilots behind its decks rather than the decks
(``query.PILOT_ICC``) leaves the winner at 3.3 luck in a list of 7. That is the honest
size of this finding on this corpus, and no cell in the grid does better.

The null is imported rather than restated, so what is swept is the arithmetic the app
runs. What is *not* imported is the screen itself: `query` screens at the shipped
constants alone, and a sweep needs every cell.

The field-size section answers the question ADR 0020 leaves open: whether a gem should
have to prove itself at a major (``trends.MAJOR_FIELD_SIZE``), by restricting either
the cut or the whole pool to majors and reading what each does to the genuine count.

Re-run this whenever the artifact grows: the winning cell is a property of the corpus,
not of the rule, and it has moved before.

Usage (from the repo root, with a graph built from the snapshots)::

    uv run python scripts/gem_sweep.py
    uv run python scripts/gem_sweep.py --artifact data/graph
"""

import argparse
import math
from pathlib import Path

import ladybug

from graph7ph.db import artifact_path, open_for_reading, rows
from graph7ph.query import (
    _RANKED,
    GEM_TOP_CUT,
    MAX_GEM_LUCK,
    MAX_GEM_NODES,
    MAX_GEM_SHARE,
    MIN_GEM_DECKS,
    _gem_tails,
    _pilot_deflated,
)
from graph7ph.trends import MAJOR_FIELD_SIZE

# The grid. Three of the four axes bracket the shipped value on both sides, so a cell
# winning at an edge shows up as an edge and can be widened rather than silently taken.
#
# `SHARES` is the exception, and deliberately: 0.15 is the top of the axis *and* the
# shipped value, because it is a definition rather than a measurement. Widening it wins
# on this script's own score every time (0.20 scores 5.9 genuine against 0.15's 3.7,
# and 0.30 scores 8.6, with no sign of a peak), and the premise refuses it: a card in
# 30% of an archetype's decks is a staple, not hidden tech, so a ceiling chosen by the
# score would be answering a different question (ADR 0020). Read a high-share cell as
# out of scope rather than as a finding this grid missed.
TOP_CUTS = (0.10, 0.20, 0.25, 0.33)
SHARES = (0.05, 0.10, 0.15)
FLOORS = (5, 6, 8)
LUCKS = (0.05, 0.01, 0.005, 0.001)

# A cell whose list is more than this fraction luck is not a finding, whatever its
# count. The bar is deliberately weak: it rejects the degenerate corners of the grid,
# and the cell that wins on `found - luck` clears it with room to spare.
MAX_LUCK_FRACTION = 0.5

# How many cells to print. The ranking is what the choice rests on, and the tail of it
# is uninformative once the shape of the leaders is visible.
LEADERS = 8

# Only primary archetype tags count (CONTEXT.md): a deck carries several tags and one
# primary, and pooling them puts a deck in ~1.6 archetypes' cuts at once, which makes
# each slice a mixture and breaks the homogeneity the null assumes.
_PRIMARY = "[:HAS_ARCHETYPE {isPrimary: true}]"


def read_corpus(conn: ladybug.Connection) -> tuple[dict, dict, dict]:
    """The three tables a sweep needs, read once: every cell rescreens the same rows.

    Returns each archetype's ranked decks in cut order, each ranked deck's field size
    and event, and every (archetype, card) pair's deck set. Decks are ordered on
    ``(placementNorm, deckId)`` here rather than per cell, since the cut is a prefix of
    one order and only its depth changes down the grid. The event rides along because
    the null is asked inside it (``query._gem_tails``).
    """
    norms, by_tag, fields, events, cards, pilots = {}, {}, {}, {}, {}, {}
    for tag, name, deck, norm in rows(conn.execute(
        f"MATCH (d:Deck)-{_PRIMARY}->(a:Archetype) WHERE {_RANKED} "
        "RETURN a.tag, a.name, d.deckId, d.placementNorm"
    )):
        norms[deck] = norm
        by_tag.setdefault(tag, (name, []))[1].append(deck)
    for deck, field, event in rows(conn.execute(
        f"MATCH (d:Deck)-[:PLAYED_AT]->(e:Event) WHERE {_RANKED} "
        "RETURN d.deckId, e.fieldSize, e.event"
    )):
        fields[deck] = field or 0
        events[deck] = event
    for tag, canon, deck in rows(conn.execute(
        f"MATCH (a:Archetype)<-{_PRIMARY}-(d:Deck)-[:CONTAINS]->(c:Card) "
        f"WHERE {_RANKED} WITH DISTINCT a, c, d RETURN a.tag, c.canon, d.deckId"
    )):
        cards.setdefault((tag, canon), set()).add(deck)
    for deck, who in rows(conn.execute(
        f"MATCH (d:Deck)-[:PILOTED_BY]->(p:Pilot) WHERE {_RANKED} "
        "RETURN d.deckId, p.pilot"
    )):
        pilots[deck] = who
    ordered = {tag: (name, sorted(decks, key=lambda d: (norms[d], d)))
               for tag, (name, decks) in by_tag.items()}
    return ordered, fields, events, cards, pilots


def screen(corpus, cut, share, floor, luck, majors="off"):
    """One cell: what it finds, what the null expects it to find, and what it draws.

    ``majors`` is the field-size question. ``cut`` restricts the archetype's cut to
    decks from a major, so a card must crowd the best decks *of the majors*;  ``pool``
    restricts the whole slice before the cut is taken, so a small event's decks never
    enter. Both are measured against ``off``, where field size plays no part.

    Expected-by-luck is summed over every screened card, admitted or rejected, at that
    card's own first tail under the threshold rather than at the threshold: a card in
    six decks has seven possible outcomes, so the bar it actually cleared is coarser
    than the bar it was held to, and charging it the threshold understates the luck.
    """
    ordered, fields, events, cards, pilots = corpus
    slice_min = math.ceil(floor / share)
    tops = {}
    for tag, (name, decks) in ordered.items():
        if len(decks) < slice_min:
            continue
        pool = ([d for d in decks if fields[d] > MAJOR_FIELD_SIZE]
                if majors == "pool" else decks)
        top = set(pool[:max(1, round(len(pool) * cut))])
        if majors == "cut":
            top = {d for d in top if fields[d] > MAJOR_FIELD_SIZE}
        if top:
            # Per event, how many of the archetype's ranked decks sat there and how many
            # of those are in the cut: the strata the null is convolved over.
            field_shape = {}
            for deck in decks:
                held, topped = field_shape.get(events[deck], (0, 0))
                field_shape[events[deck]] = (held + 1, topped + (deck in top))
            tops[tag] = (name, len(decks), top, field_shape)

    found, expected, archetypes, drawn, listing = 0, 0.0, set(), set(), []
    for (tag, canon), decks in cards.items():
        if tag not in tops:
            continue
        name, total, top, field_shape = tops[tag]
        if len(decks) < floor or len(decks) > share * total:
            continue
        here = {}
        for deck in decks:
            here[events[deck]] = here.get(events[deck], 0) + 1
        tails = _gem_tails(tuple(sorted(
            (*field_shape[event], drawn) for event, drawn in here.items()
        )))
        # Charged for the pilots behind the decks, exactly as `query` charges.
        npilots = len({pilots[d] for d in decks})
        tails = tuple(_pilot_deflated(t, len(decks), npilots) for t in tails)
        hits = decks & top
        if tails[len(hits)] <= luck:
            found += 1
            archetypes.add(tag)
            drawn |= hits
            listing.append((tails[len(hits)], name, canon, len(decks), len(hits)))
        expected += next((tail for tail in tails if tail <= luck), 0.0)
    nodes = len(archetypes) + found + len(drawn)
    return found, expected, nodes, len(archetypes), listing


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, default=artifact_path())
    args = parser.parse_args()

    corpus = read_corpus(open_for_reading(args.artifact))
    ordered, _, _, cards, _ = corpus
    print(f"{len(ordered)} archetypes, {sum(len(d) for _, d in ordered.values())} "
          f"primary-tagged ranked decks, {len(cards)} (archetype, card) pairs in "
          f"{args.artifact}\n")

    graded = []
    for cut in TOP_CUTS:
        for share in SHARES:
            for floor in FLOORS:
                for luck in LUCKS:
                    found, expected, nodes, archetypes, _ = screen(
                        corpus, cut, share, floor, luck)
                    if found and nodes <= MAX_GEM_NODES \
                            and expected / found <= MAX_LUCK_FRACTION:
                        graded.append((found - expected, found, expected, nodes,
                                       cut, share, floor, luck, archetypes))
    print(f"{len(TOP_CUTS) * len(SHARES) * len(FLOORS) * len(LUCKS)} cells swept, "
          f"{len(graded)} qualify\n")
    print("  genuine  found   luck  nodes | top cut  share  floor   max p | archetypes")
    for genuine, found, expected, nodes, cut, share, floor, luck, na in \
            sorted(graded, reverse=True)[:LEADERS]:
        print(f"  {genuine:7.1f} {found:6} {expected:6.1f} {nodes:6} |"
              f"   {cut:.2f}   {share:.2f}      {floor}   {luck:5.3f} | {na:3}")

    print(f"\n== field size, at the shipped cell "
          f"{GEM_TOP_CUT}/{MAX_GEM_SHARE}/{MIN_GEM_DECKS}/{MAX_GEM_LUCK} ==")
    shipped = None
    for majors in ("off", "cut", "pool"):
        found, expected, nodes, archetypes, listing = screen(
            corpus, GEM_TOP_CUT, MAX_GEM_SHARE, MIN_GEM_DECKS, MAX_GEM_LUCK, majors)
        shipped = shipped or listing
        print(f"  majors {majors:4}  found {found:3}  luck {expected:5.1f}  "
              f"genuine {found - expected:5.1f}  nodes {nodes:4}  "
              f"archetypes {archetypes:3}")

    print(f"\n== the shipped list, {len(shipped)} gems ==")
    for p, name, canon, decks, hits in sorted(shipped):
        print(f"  {p:.4f}  {name:24} {canon:32} {hits:3}/{decks:3}")


if __name__ == "__main__":
    main()
