"""Grade the graph's deck point totals against the source's own (issue #143).

7PH point costs are context-dependent, so the source ships two per card: what it
costs in a deck and what it costs as a companion. ``query.deck_points`` charges
one companion surcharge where a deck sideboards a companion; this script measures
that rule against the source's ``pointsToday``, which is the only independent
witness to what a deck spends, and characterises every deck the two still
disagree on.

Both derivations are reported, so the gain is visible rather than asserted: the
plain all-board sum of ``Card.points`` this replaced, and the companion rule.
Neither is read off a stored total; both are computed from the built graph, so
what is graded is the artifact a reader queries.

Usage (from the repo root, with a graph built from the snapshots)::

    uv run python scripts/points_agreement.py
    uv run python scripts/points_agreement.py --artifact data/graph --snapshots snapshots
"""

import argparse
import json
from collections import Counter
from pathlib import Path

import ladybug

from graph7ph.db import artifact_path, open_for_reading, rows
# The build's own rule for what counts as a snapshot directory, reused rather than
# restated: it skips the hidden staging directory an interrupted fetch leaves
# behind, and a grader that read one would print a denominator the build excludes.
from graph7ph.ingest import _snapshot_dirs
from graph7ph.query import deck_points

# How many decks of a residual class to name in the report. The classes are small
# enough to characterise from a sample, and the counts beside them are complete.
SAMPLE = 5


def source_points(snapshots: Path) -> dict[str, int]:
    """Each deck's ``pointsToday``, taking the newest snapshot that holds it.

    The build folds every snapshot into a union (ADR 0008), so the population being
    graded spans them all. ``pointsToday`` is the source's own reading of today's
    points list rather than a historical fact it recorded, so the newest snapshot
    carrying a deck is the one that speaks for it.

    A deck whose record carries no ``pointsToday`` is left out rather than defaulted,
    so the count this reports as gradeable is the count it really graded.
    """
    claimed = {}
    for snapshot in _snapshot_dirs(snapshots):
        decks = json.loads((snapshot / "decks.json").read_text())
        claimed |= {d["deckId"]: d["pointsToday"]
                    for d in decks if d.get("pointsToday") is not None}
    return claimed


def all_board_sum(conn: ladybug.Connection) -> dict[str, int]:
    """The derivation this replaced, reproduced as it stood: the deck cost of every
    board membership, no companion charge and no singleton de-duplication. Kept here
    rather than in the query spine because its only remaining use is as the baseline
    this measurement improves on. The de-duplication is not part of the gain being
    reported: every card the record lists on both boards of one deck is free, so
    adding it moves this number nowhere."""
    return {
        deck: int(total or 0)
        for deck, total in rows(conn.execute(
            """MATCH (d:Deck) OPTIONAL MATCH (d)-[:CONTAINS]->(c:Card)
               RETURN d.deckId, sum(c.points)"""))
    }


def companion_boards(conn: ladybug.Connection) -> dict[str, Counter]:
    """Per deck, how many companion-priced cards sit on each board.

    What a residual has to be characterised against: whether the deck the two
    sides disagree about runs a companion at all, and where.
    """
    boards: dict[str, Counter] = {}
    for deck, board, count in rows(conn.execute(
        """MATCH (d:Deck)-[cont:CONTAINS]->(c:Card) WHERE c.pointsCompanion > 0
           RETURN d.deckId, cont.board, count(c)""")):
        boards.setdefault(deck, Counter())[board] = int(count)
    return boards


def classify(delta: int, companions: Counter) -> str:
    """Why one deck's total disagrees with the source's, read off the decklist.

    ``delta`` is ours minus theirs. The two classes the record holds are the two
    directions of one thing: a companion the source's own decklist and its own
    total disagree about. Anything else is named as unexplained rather than
    absorbed, since that is what a new class of defect would arrive as.
    """
    if delta > 0 and companions["Side"]:
        return "source declines the surcharge for a sideboarded companion"
    if delta < 0 and not companions:
        return "source charges a surcharge for a companion its decklist omits"
    return "unexplained"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, default=artifact_path())
    parser.add_argument("--snapshots", type=Path, default=Path("snapshots"))
    args = parser.parse_args()

    conn = open_for_reading(args.artifact)
    claimed = source_points(args.snapshots)
    ours = deck_points(conn)
    baseline = all_board_sum(conn)
    identity = dict(rows(conn.execute("MATCH (d:Deck) RETURN d.deckId, d.name")))
    colour = dict(rows(conn.execute("MATCH (d:Deck) RETURN d.deckId, d.colourIdentity")))
    companions = companion_boards(conn)

    graded = [d for d in ours if d in claimed]
    print(f"{len(graded)} of {len(ours)} decks in {args.artifact} carry a source "
          f"pointsToday to grade against\n")
    if not graded:
        # The realistic way to get here is --snapshots pointed at a root the
        # artifact was not built from, which is a wrong invocation to report rather
        # than a division to attempt.
        raise SystemExit(f"nothing to grade: no deck in {args.artifact} appears in "
                         f"{args.snapshots}/")
    for label, derived in (("all-board sum of Card.points", baseline),
                           ("+ the companion surcharge (query.deck_points)", ours)):
        agree = sum(1 for d in graded if derived[d] == claimed[d])
        print(f"  {agree:>4} / {len(graded)} = {agree / len(graded) * 100:5.2f}%  {label}")

    residual = [d for d in graded if ours[d] != claimed[d]]
    print(f"\n{len(residual)} deck(s) still disagree, by class:")
    classes: dict[str, list[str]] = {}
    for deck in residual:
        classes.setdefault(
            classify(ours[deck] - claimed[deck], companions.get(deck, Counter())), []
        ).append(deck)
    for label, decks in sorted(classes.items(), key=lambda kv: -len(kv[1])):
        deltas = Counter(ours[d] - claimed[d] for d in decks)
        print(f"\n  {len(decks)}: {label}")
        print("     ours - theirs: "
              + ", ".join(f"{delta:+d} ({n})" for delta, n in sorted(deltas.items())))
        for deck in sorted(decks)[:SAMPLE]:
            sides = "".join(f" {board}={n}" for board, n in
                            sorted(companions.get(deck, Counter()).items()))
            print(f"     {deck}  ours={ours[deck]} theirs={claimed[deck]} "
                  f"{colour[deck]}{sides}  {identity[deck]}")
        if len(decks) > SAMPLE:
            print(f"     ... and {len(decks) - SAMPLE} more")


if __name__ == "__main__":
    main()
