"""CLI: ``graph7ph fetch | build | app``.

Wires the fetch, build, and app seams into the three commands issue 2 asks for.
Paths default to the repo root and are overridable by flag.
"""

import argparse
from pathlib import Path

from graph7ph.build import build_graph, reconciliation_path
from graph7ph.fetch import fetch_snapshot
from graph7ph.models import load_snapshot

SNAPSHOTS_ROOT = Path("snapshots")
DB_PATH = Path("graph.kuzu")


def _latest_snapshot(root: Path) -> Path:
    # Ignore hidden dirs, e.g. an interrupted fetch's ".<ts>.partial-*" staging.
    snaps = sorted(
        p for p in root.glob("*") if p.is_dir() and not p.name.startswith(".")
    )
    if not snaps:
        raise SystemExit(f"No snapshots in {root}/: run `graph7ph fetch` first.")
    return snaps[-1]


def _fetch(args: argparse.Namespace) -> None:
    snap = fetch_snapshot(args.snapshots)
    print(f"Fetched snapshot: {snap}")


def _build(args: argparse.Namespace) -> None:
    # v1 builds the latest snapshot; unioning all snapshots is later work (ADR 0003).
    snap = _latest_snapshot(args.snapshots)
    counts = build_graph(load_snapshot(snap), args.db)
    print(f"Built {args.db} from {snap}:")
    print(f"  nodes: pilots={counts.pilots} decks={counts.decks} cards={counts.cards} "
          f"events={counts.events} archetypes={counts.archetypes} "
          f"macros={counts.macros} colours={counts.colours} cardTypes={counts.card_types}")
    print(f"  edges: piloted_by={counts.piloted_by} contains={counts.contains} "
          f"played_at={counts.played_at} has_archetype={counts.has_archetype} "
          f"has_macro={counts.has_macro} deck_colour={counts.deck_colour} "
          f"card_colour={counts.card_colour} has_type={counts.has_type}")
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
