"""CLI: ``graph7ph fetch | build | app | baseline``.

Wires the fetch, build, and app seams into the three commands issue 2 asks for.
Paths default under the repo's ``data/`` directory and are overridable by flag.
"""

import argparse
import json
from pathlib import Path

from graph7ph.baseline import BASELINE_PATH, MalformedBaseline, capture, check
from graph7ph.build import YearStraddle, reconciliation_path
from graph7ph.db import (
    NotABundle,
    UnopenableGraph,
    artifact_path,
    database_path,
    open_for_reading,
)
from graph7ph.fetch import fetch_snapshot
from graph7ph.ingest import SchemaError, ingest, ingest_report_path
from graph7ph.provenance import staleness

# Build outputs live under data/, not the repo root: the graph and its sidecar
# reports are derived artifacts, kept out of the working tree's top level.
SNAPSHOTS_ROOT = Path("snapshots")
DB_PATH = artifact_path()


def _fetch(args: argparse.Namespace) -> None:
    snap = fetch_snapshot(args.snapshots)
    print(f"Fetched snapshot: {snap}")


def _build(args: argparse.Namespace) -> None:
    # The build folds the snapshot sequence, gating each snapshot against the
    # accumulated union of every one before it, not just the newest transition
    # (ADR 0008), then promotes atomically with a retained backup (ADR 0003). A
    # flagged immutable fact is held at its pre-change value until a human
    # resolves it, so the flag is a required action rather than a notice.
    try:
        report, counts = ingest(args.snapshots, args.db)
    except (SchemaError, YearStraddle, NotABundle) as exc:
        raise SystemExit(f"Build aborted, live graph untouched: {exc}")

    print(f"Built {args.db} ({report.status}):")
    print(f"  nodes: pilots={counts.pilots} decks={counts.decks} cards={counts.cards} "
          f"events={counts.events} archetypes={counts.archetypes} "
          f"macros={counts.macros} colours={counts.colours} "
          f"cardTypes={counts.card_types} years={counts.years}")
    print(f"  edges: piloted_by={counts.piloted_by} contains={counts.contains} "
          f"played_at={counts.played_at} has_archetype={counts.has_archetype} "
          f"has_macro={counts.has_macro} deck_colour={counts.deck_colour} "
          f"card_colour={counts.card_colour} has_type={counts.has_type} "
          f"in_year={counts.in_year}")
    if report.flags:
        print(f"  {len(report.flags)} record(s) flagged for review "
              f"(dropped ids or changed facts): {ingest_report_path(args.db)}")

    recon = json.loads(reconciliation_path(args.db).read_text())
    dupes, joined, candidates, curated, multi, splits = (
        len(recon["dropped_duplicates"]), len(recon["joined_names"]),
        len(recon["under_merges"]), recon["curated"],
        len(recon.get("multi_name_ids", [])),
        len(recon.get("name_splits", [])),
    )
    if dupes:
        print(f"  {dupes} duplicate registration(s) dropped (logged in the report)")
    if joined:
        print(f"  {joined} id group(s) joined on an identical display name")
    if splits:
        print(f"  {splits} display name(s) split into separate people by curation")
    if multi:
        print(f"  {multi} id(s) recovered more than one surname (review the report)")
    print(f"  pilot identity: {candidates} candidate(s) to review, {curated} already curated")
    print(f"  reconciliation report: {reconciliation_path(args.db)}")
    # The promotion renamed the directory an already-running app opened, and its
    # catalogues were read at startup, so it will serve the old graph in silence.
    print("  restart any running `graph7ph app`: it still serves the old graph")


def _baseline(args: argparse.Namespace) -> None:
    # Every failure below is a user-facing abort rather than a traceback, as the
    # build is: later tickets run this as a gate, where a crash and a regression
    # must not look alike.
    # The artifact is a directory holding the database, so an existing directory is
    # not yet a graph: the database inside it is what can be graded.
    if not database_path(args.db).exists():
        raise SystemExit(f"No graph at {args.db}: run `uv run graph7ph build` first.")
    # An artifact built from other sources cannot be graded at all: the gate re-runs
    # the queries live, so a query change is graded honestly, but an ingest, build,
    # schema or curation change never runs, and grading would report "no regression"
    # about code the artifact predates (issue #55). Checked before --capture too,
    # since capturing from a stale graph writes the wrong answer into the oracle.
    if (complaint := staleness(args.db)) is not None:
        raise SystemExit(f"Cannot grade: {complaint}.")
    # Read-only, so the gate can grade an artifact the app is already serving.
    try:
        conn = open_for_reading(args.db)
    except UnopenableGraph as exc:
        # The bundle holds a file of the right name and the engine will not have
        # it. An abort like every failure above, which is what lets the deploy
        # preflight tell a bundle it cannot grade from a crash (issue #71). The
        # error already reads as a sentence about the bundle, so it is passed on
        # rather than dressed up again here.
        raise SystemExit(str(exc))
    if args.capture:
        _capture(conn, args)
        return

    try:
        diffs = check(conn, args.baseline)
    except FileNotFoundError:
        raise SystemExit(f"No baseline at {args.baseline}: capture one with --capture.")
    except MalformedBaseline as exc:
        raise SystemExit(f"The baseline at {args.baseline} cannot be graded against: {exc}")
    if diffs:
        print(f"{len(diffs)} difference(s) against {args.baseline}:")
        for line in diffs:
            print(f"  {line}")
        raise SystemExit(1)
    print(f"{args.db} reproduces {args.baseline}: no regression.")


def _write_baseline(conn, args: argparse.Namespace) -> None:
    args.baseline.parent.mkdir(parents=True, exist_ok=True)
    args.baseline.write_text(json.dumps(capture(conn), indent=2) + "\n")
    print(f"Captured the baseline from {args.db} into {args.baseline}")


def _capture(conn, args: argparse.Namespace) -> None:
    # Recapturing overwrites the checked-in oracle wholesale, and the diff is too
    # large to read (issue #67). So at exactly the moment the data moves and the
    # gate goes red, a blind --capture would rubber-stamp any real regression along
    # with it. Grade against the existing baseline first, and refuse to overwrite a
    # differing or ungradeable one unless --force says so out loud.
    try:
        diffs = check(conn, args.baseline)
    except FileNotFoundError:
        # No oracle yet: the first capture has nothing to override.
        _write_baseline(conn, args)
        return
    except MalformedBaseline as exc:
        # A corrupt-but-nearly-good oracle is the worst thing to overwrite silently:
        # a refusal requiring --force, not a clean slate.
        if not args.force:
            raise SystemExit(
                f"The baseline at {args.baseline} cannot be graded against: {exc}. "
                f"Pass --force to overwrite it."
            )
        _write_baseline(conn, args)
        return
    if diffs and not args.force:
        raise SystemExit(
            f"{len(diffs)} difference(s) against {args.baseline}; recapturing would "
            f"overwrite the oracle. Pass --force to overwrite it anyway."
        )
    print(f"{len(diffs)} difference(s) against {args.baseline}.")
    _write_baseline(conn, args)


def _app(args: argparse.Namespace) -> None:
    from graph7ph.app import build_app
    from graph7ph.serve import APP_KWARGS

    build_app(args.db).launch(app_kwargs=APP_KWARGS)


def main() -> None:
    parser = argparse.ArgumentParser(prog="graph7ph")
    sub = parser.add_subparsers(required=True)

    p_fetch = sub.add_parser("fetch", help="Download 7phstats data into a snapshot")
    p_fetch.add_argument("--snapshots", type=Path, default=SNAPSHOTS_ROOT)
    p_fetch.set_defaults(func=_fetch)

    p_build = sub.add_parser("build", help="Build the graph from a snapshot")
    p_build.add_argument("--snapshots", type=Path, default=SNAPSHOTS_ROOT)
    p_build.add_argument("--db", type=Path, default=DB_PATH)
    p_build.set_defaults(func=_build)

    p_baseline = sub.add_parser(
        "baseline", help="Grade the graph against the checked-in golden subgraphs"
    )
    p_baseline.add_argument("--db", type=Path, default=DB_PATH)
    p_baseline.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    p_baseline.add_argument(
        "--capture", action="store_true", help="Rewrite the baseline from this graph"
    )
    p_baseline.add_argument(
        "--force", action="store_true",
        help="With --capture, overwrite a baseline that differs or cannot be graded",
    )
    p_baseline.set_defaults(func=_baseline)

    p_app = sub.add_parser("app", help="Launch the Gradio explorer")
    p_app.add_argument("--db", type=Path, default=DB_PATH)
    p_app.set_defaults(func=_app)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
