"""Load a parsed Snapshot into a Kùzu graph.

The graph is the fact spine of ADR 0002: Pilot, Deck, and Card nodes joined by
``PILOTED_BY`` (who registered a deck) and ``CONTAINS`` (which cards a deck runs,
on which board). A build is a full rebuild into a fresh database file; counts are
read back out of the graph so callers can assert they match the source.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path

import kuzu
from pydantic import BaseModel

from graph7ph.db import rows
from graph7ph.models import Card, Deck, Snapshot

_SCHEMA = [
    "CREATE NODE TABLE Pilot(pilot STRING, PRIMARY KEY(pilot))",
    """CREATE NODE TABLE Deck(
        deckId STRING, name STRING, event STRING, eventType STRING,
        placement INT64, placementNorm DOUBLE, PRIMARY KEY(deckId))""",
    """CREATE NODE TABLE Card(
        canon STRING, name STRING, type STRING, manaValue DOUBLE,
        reserved BOOLEAN, priceUsd DOUBLE, points INT64, PRIMARY KEY(canon))""",
    "CREATE REL TABLE PILOTED_BY(FROM Deck TO Pilot)",
    "CREATE REL TABLE CONTAINS(FROM Deck TO Card, board STRING)",
]

_BATCH = 5000

# Which model fields land as node properties. One list per node type drives the
# CREATE Cypher and the row projection, so the two never drift (the DDL above
# still declares the column types separately).
_DECK_FIELDS = ("deck_id", "name", "event", "event_type", "placement", "placement_norm")
_CARD_FIELDS = ("canon", "name", "type", "mana_value", "reserved", "price_usd", "points")


@dataclass
class BuildCounts:
    pilots: int
    decks: int
    cards: int
    piloted_by: int
    contains: int


def build_graph(snapshot: Snapshot, db_path: Path) -> BuildCounts:
    """Build a fresh Kùzu database at ``db_path`` and return its counts."""
    db_path = Path(db_path)
    _remove(db_path)

    conn = kuzu.Connection(kuzu.Database(str(db_path)))
    for ddl in _SCHEMA:
        conn.execute(ddl)

    pilots = sorted({d.pilot for d in snapshot.decks})
    _load(conn, "UNWIND $rows AS r CREATE (:Pilot {pilot: r.pilot})",
          [{"pilot": p} for p in pilots])
    _create_nodes(conn, "Deck", Deck, _DECK_FIELDS, snapshot.decks)
    _create_nodes(conn, "Card", Card, _CARD_FIELDS, snapshot.cards)
    _load(conn,
          """UNWIND $rows AS r
             MATCH (d:Deck {deckId: r.deckId}), (p:Pilot {pilot: r.pilot})
             CREATE (d)-[:PILOTED_BY]->(p)""",
          [{"deckId": d.deck_id, "pilot": d.pilot} for d in snapshot.decks])
    _load(conn,
          """UNWIND $rows AS r
             MATCH (d:Deck {deckId: r.deckId}), (c:Card {canon: r.canon})
             CREATE (d)-[:CONTAINS {board: r.board}]->(c)""",
          [{"deckId": c.deck_id, "canon": c.canon, "board": c.board}
           for c in snapshot.containments])

    return BuildCounts(
        pilots=_count(conn, "MATCH (p:Pilot) RETURN count(p)"),
        decks=_count(conn, "MATCH (d:Deck) RETURN count(d)"),
        cards=_count(conn, "MATCH (c:Card) RETURN count(c)"),
        piloted_by=_count(conn, "MATCH ()-[r:PILOTED_BY]->() RETURN count(r)"),
        contains=_count(conn, "MATCH ()-[r:CONTAINS]->() RETURN count(r)"),
    )


def _create_nodes(
    conn: kuzu.Connection,
    label: str,
    model: type[BaseModel],
    fields: tuple[str, ...],
    objects: list,
) -> None:
    """Batch-create nodes, deriving the property map from the model's fields."""
    aliases = [model.model_fields[f].alias or f for f in fields]
    props = ", ".join(f"{a}: r.{a}" for a in aliases)
    _load(conn, f"UNWIND $rows AS r CREATE (:{label} {{{props}}})",
          [o.model_dump(by_alias=True, include=set(fields)) for o in objects])


def _load(conn: kuzu.Connection, query: str, batch: list[dict]) -> None:
    for start in range(0, len(batch), _BATCH):
        conn.execute(query, {"rows": batch[start:start + _BATCH]})


def _count(conn: kuzu.Connection, query: str) -> int:
    return next(rows(conn.execute(query)))[0]


def _remove(db_path: Path) -> None:
    if db_path.is_dir():
        shutil.rmtree(db_path)
    elif db_path.exists():
        db_path.unlink()
    wal = db_path.with_name(db_path.name + ".wal")
    if wal.exists():
        wal.unlink()
