"""CLI: ``graph7ph fetch | build | app``.

Wires the fetch, build, and app seams into the three commands issue 2 asks for.
Paths default under the repo's ``data/`` directory and are overridable by flag.
"""

import argparse
from pathlib import Path

from graph7ph.build import reconciliation_path
from graph7ph.fetch import fetch_snapshot
from graph7ph.ingest import SchemaError, ingest, ingest_report_path

# Build outputs live under data/, not the repo root: the graph and its sidecar
# reports are derived artifacts, kept out of the working tree's top level.
SNAPSHOTS_ROOT = Path("snapshots")
DB_PATH = Path("data/graph.kuzu")


def _fetch(args: argparse.Namespace) -> None:
    snap = fetch_snapshot(args.snapshots)
    print(f"Fetched snapshot: {snap}")


def _build(args: argparse.Namespace) -> None:
    # The build unions every snapshot and gates the newest against what we hold,
    # then promotes atomically with a retained backup (ADR 0003).
    try:
        report, counts = ingest(args.snapshots, args.db)
    except SchemaError as exc:
        raise SystemExit(f"Build aborted, live graph untouched: {exc}")

    print(f"Built {args.db} ({report.status}):")
    print(f"  nodes: pilots={counts.pilots} decks={counts.decks} cards={counts.cards} "
          f"events={counts.events} archetypes={counts.archetypes} "
          f"macros={counts.macros} colours={counts.colours} cardTypes={counts.card_types}")
    print(f"  edges: piloted_by={counts.piloted_by} contains={counts.contains} "
          f"played_at={counts.played_at} has_archetype={counts.has_archetype} "
          f"has_macro={counts.has_macro} deck_colour={counts.deck_colour} "
          f"card_colour={counts.card_colour} has_type={counts.has_type}")
    if report.flags:
        print(f"  {len(report.flags)} record(s) flagged for review "
              f"(dropped ids or changed facts): {ingest_report_path(args.db)}")
    print(f"  reconciliation report: {reconciliation_path(args.db)}")


def _app(args: argparse.Namespace) -> None:
    from graph7ph.app import build_app

    build_app(args.db).launch()


def main() -> None:
    parser = argparse.ArgumentParser(prog="graph7ph")
    sub = parser.add_subparsers(required=True)

    p_fetch = sub.add_parser("fetch", help="Download 7phstats data into a snapshot")
    p_fetch.add_argument("--snapshots", type=Path, default=SNAPSHOTS_ROOT)
    p_fetch.set_defaults(func=_fetch)

    p_build = sub.add_parser("build", help="Build the Kùzu graph from a snapshot")
    p_build.add_argument("--snapshots", type=Path, default=SNAPSHOTS_ROOT)
    p_build.add_argument("--db", type=Path, default=DB_PATH)
    p_build.set_defaults(func=_build)

    p_app = sub.add_parser("app", help="Launch the Gradio explorer")
    p_app.add_argument("--db", type=Path, default=DB_PATH)
    p_app.set_defaults(func=_app)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
