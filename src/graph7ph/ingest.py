"""Seam B: robust ingestion, the superset gate (ADR 0003, issue #5).

Our store is the system of record, not a mirror of the source. Each fetch is an
append-only snapshot; the build unions all of them by stable id so a record that
leaves the newest fetch is never lost from the graph. A gate hashes each entity's
immutable projection (its historical facts) and flags a dropped id or a changed
fact for review, while volatile fields silently take the latest value. A corrupt
or shape-shifted snapshot hard-fails before the live graph is touched.
"""

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from graph7ph.build import BuildCounts, build_graph, reconciliation_path
from graph7ph.models import Card, Containment, Deck, Snapshot, load_snapshot

# The gate's closed vocabularies, following the repo idiom (models.py `Board`).
FlagKind = Literal["dropped", "changed"]
Entity = Literal["deck", "card"]
GateStatus = Literal["promote", "flag"]


class SchemaError(Exception):
    """A snapshot is not valid 7phstats data: not JSON, HTML, or shape-shifted."""


def load_checked(path: Path) -> Snapshot:
    """Parse and schema-validate a snapshot directory (ADR 0003).

    A non-JSON body (e.g. an HTML error page), a missing file or key, or a
    shape-shifted record raises :class:`SchemaError` so the build hard-fails
    before the live graph is touched, rather than absorbing corruption.
    """
    try:
        return load_snapshot(path)
    except (OSError, json.JSONDecodeError, KeyError, ValidationError, ValueError) as exc:
        raise SchemaError(f"snapshot {Path(path)} failed validation: {exc}") from exc


@dataclass
class Flag:
    """One entity the newest fetch dropped or whose historical facts changed."""

    kind: FlagKind
    entity: Entity
    id: str


@dataclass
class GateReport:
    status: GateStatus  # promote = clean superset; flag = needs review
    flags: list[Flag] = field(default_factory=list)


@dataclass
class GateResult:
    status: GateStatus
    snapshot: Snapshot  # the unioned superset, safe to build
    report: GateReport


def _conts_by_deck(snapshot: Snapshot) -> dict[str, list[Containment]]:
    by_deck: dict[str, list[Containment]] = {}
    for c in snapshot.containments:
        by_deck.setdefault(c.deck_id, []).append(c)
    return by_deck


def union_snapshots(snapshots: list[Snapshot]) -> Snapshot:
    """Union snapshots by stable id, oldest to newest.

    The newest snapshot to carry an id wins (so volatile fields take the latest
    value), and an id no record in a later snapshot carries is retained from the
    last snapshot that held it. A deck's containments travel with the deck.
    """
    decks = {}
    conts: dict[str, list[Containment]] = {}
    cards = {}
    for snap in snapshots:  # oldest -> newest; later writes overwrite earlier
        by_deck = _conts_by_deck(snap)
        for d in snap.decks:
            decks[d.deck_id] = d
            conts[d.deck_id] = by_deck.get(d.deck_id, [])
        for c in snap.cards:
            cards[c.canon] = c
    return Snapshot(
        cards=list(cards.values()),
        decks=list(decks.values()),
        containments=[c for cs in conts.values() for c in cs],
    )


def _hash(projection: object) -> str:
    return hashlib.sha256(
        json.dumps(projection, sort_keys=True).encode()
    ).hexdigest()


def _deck_hash(deck: Deck, conts: list[Containment]) -> str:
    """Hash a deck's immutable projection: its historical facts (ADR 0003).

    Pilot, event, placement, and decklist are historical and flagged when they
    change; everything else (name, colour, macro, classification) is volatile.
    """
    return _hash({
        "pilot": deck.pilot,
        "event": deck.event,
        "placement": deck.placement,
        "decklist": sorted((c.canon, c.board) for c in conts),
    })


def _card_hash(card: Card) -> str:
    """Hash a card's immutable projection. Points and price are volatile."""
    return _hash({"name": card.name, "type": card.type})


def gate(prior: Snapshot, incoming: Snapshot) -> GateResult:
    """Compare what we hold against the newest fetch and union the two.

    Both snapshots are already schema-valid (see :func:`load_checked`). An id in
    ``prior`` that ``incoming`` drops, or one whose immutable projection changed,
    is flagged for review; the union still retains it, so the built graph is
    always a superset. A clean superset promotes with no flags.
    """
    flags: list[Flag] = []

    inc_decks = {d.deck_id: d for d in incoming.decks}
    inc_conts = _conts_by_deck(incoming)
    prior_conts = _conts_by_deck(prior)
    for d in prior.decks:
        if d.deck_id not in inc_decks:
            flags.append(Flag("dropped", "deck", d.deck_id))
        elif _deck_hash(d, prior_conts.get(d.deck_id, [])) != _deck_hash(
            inc_decks[d.deck_id], inc_conts.get(d.deck_id, [])
        ):
            flags.append(Flag("changed", "deck", d.deck_id))

    inc_cards = {c.canon: c for c in incoming.cards}
    for c in prior.cards:
        if c.canon not in inc_cards:
            flags.append(Flag("dropped", "card", c.canon))
        elif _card_hash(c) != _card_hash(inc_cards[c.canon]):
            flags.append(Flag("changed", "card", c.canon))

    status = "flag" if flags else "promote"
    return GateResult(status, union_snapshots([prior, incoming]), GateReport(status, flags))


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def promote(incoming: Path, live: Path, backup: Path) -> None:
    """Atomically swap a rebuilt artifact into place, keeping the old one.

    The current live artifact (if any) is moved aside to ``backup`` for rollback,
    then the rebuilt one is renamed into the live path. Renames are atomic on the
    same filesystem, and the backup guarantees the previous artifact survives. A
    first build has no live artifact, so no backup is produced. Used for the graph
    directory and for each of its sidecar reports, so a rollback to ``backup``
    carries its own matching reports.
    """
    if live.exists():
        _remove(backup)
        live.rename(backup)
    incoming.rename(live)


def ingest_report_path(db_path: Path) -> Path:
    """Where the gate's review report is written for a graph at ``db_path``."""
    db_path = Path(db_path)
    return db_path.with_name(db_path.name + ".ingest.json")


def _snapshot_dirs(root: Path) -> list[Path]:
    # Sorted oldest to newest by their timestamped name; hidden dirs (e.g. an
    # interrupted fetch's staging) are ignored.
    return sorted(
        p for p in Path(root).glob("*") if p.is_dir() and not p.name.startswith(".")
    )


def ingest(snapshots_root: Path, db_path: Path) -> tuple[GateReport, BuildCounts]:
    """Build the live graph from every snapshot, gated and atomically promoted.

    All snapshots are schema-validated and unioned by stable id; the newest is
    gated against everything held before it. A corrupt snapshot raises
    :class:`SchemaError` before anything is built, leaving the live graph
    untouched. Otherwise the union is built into a temporary graph and swapped in
    with its sidecar reports, retaining the previous graph and its reports as a
    self-consistent backup for rollback (ADR 0003).
    """
    db_path = Path(db_path)
    snapshots = [load_checked(d) for d in _snapshot_dirs(snapshots_root)]
    if not snapshots:
        raise SchemaError(f"no snapshots in {Path(snapshots_root)}/")

    result = gate(union_snapshots(snapshots[:-1]), snapshots[-1])

    # Build the graph and both sidecar reports at the temp location first, then
    # promote them as a bundle so a rollback finds reports matching its graph.
    incoming_db = db_path.with_name(db_path.name + ".incoming")
    _remove(incoming_db)
    counts = build_graph(result.snapshot, incoming_db)  # writes reconciliation_path(incoming_db)
    ingest_report_path(incoming_db).write_text(json.dumps(asdict(result.report), indent=2))

    backup_db = db_path.with_name(db_path.name + ".backup")
    promote(incoming_db, db_path, backup_db)
    promote(reconciliation_path(incoming_db), reconciliation_path(db_path),
            reconciliation_path(backup_db))
    promote(ingest_report_path(incoming_db), ingest_report_path(db_path),
            ingest_report_path(backup_db))

    return result.report, counts
