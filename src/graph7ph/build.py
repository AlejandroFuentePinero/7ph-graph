"""Load a parsed Snapshot into a Kùzu graph.

The graph is the fact spine of ADR 0002. Entity nodes (Pilot, Deck, Card) and
dimension nodes (Event, Archetype, Macro, Colour, CardType) are joined by edges
for irreducible facts only: who piloted a deck, where it was played, which cards
it runs, its archetypes/macro/colours, and each card's type and colours. A build
is a full rebuild into a fresh database file; counts are read back out of the
graph so callers can assert they match the source.
"""

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

import kuzu
from pydantic import BaseModel

from graph7ph.db import rows
from graph7ph.models import COLOURS, Card, Snapshot
from graph7ph.pilots import PilotResolution, resolve_pilots

_SCHEMA = [
    # A Pilot is keyed on the upstream id; displayName is a recovered label and
    # lowConfidence marks the re-keyed null-pilot decks (ADR 0004).
    "CREATE NODE TABLE Pilot(pilot STRING, displayName STRING, "
    "lowConfidence BOOLEAN, PRIMARY KEY(pilot))",
    """CREATE NODE TABLE Deck(
        deckId STRING, name STRING, placement INT64, placementNorm DOUBLE,
        colourIdentity STRING, PRIMARY KEY(deckId))""",
    """CREATE NODE TABLE Card(
        canon STRING, name STRING, type STRING, manaValue DOUBLE,
        reserved BOOLEAN, priceUsd DOUBLE, points INT64, PRIMARY KEY(canon))""",
    "CREATE NODE TABLE Event(event STRING, eventId STRING, eventType STRING, PRIMARY KEY(event))",
    "CREATE NODE TABLE Archetype(tag STRING, name STRING, PRIMARY KEY(tag))",
    # `Macro` is a Kùzu reserved keyword, so the label is backtick-escaped
    # everywhere and its value lives on `name` (not a reserved `macro` column).
    "CREATE NODE TABLE `Macro`(name STRING, PRIMARY KEY(name))",
    "CREATE NODE TABLE Colour(colour STRING, PRIMARY KEY(colour))",
    "CREATE NODE TABLE CardType(type STRING, PRIMARY KEY(type))",
    "CREATE REL TABLE PILOTED_BY(FROM Deck TO Pilot)",
    "CREATE REL TABLE CONTAINS(FROM Deck TO Card, board STRING)",
    "CREATE REL TABLE PLAYED_AT(FROM Deck TO Event)",
    # `primary` is a Kùzu reserved keyword, so the primary-archetype flag is `isPrimary`.
    "CREATE REL TABLE HAS_ARCHETYPE(FROM Deck TO Archetype, weight INT64, isPrimary BOOLEAN)",
    "CREATE REL TABLE HAS_MACRO(FROM Deck TO `Macro`)",
    "CREATE REL TABLE DECK_COLOUR(FROM Deck TO Colour)",
    "CREATE REL TABLE CARD_COLOUR(FROM Card TO Colour)",
    "CREATE REL TABLE HAS_TYPE(FROM Card TO CardType)",
]

_BATCH = 5000

# Which Card fields land as node properties. This list drives both the CREATE
# Cypher and the row projection, so the two never drift (the DDL above still
# declares the column types separately). Deck can't use this reflective path
# because its colourIdentity is a derived property, not a stored model field.
_CARD_FIELDS = ("canon", "name", "type", "mana_value", "reserved", "price_usd", "points")


@dataclass
class BuildCounts:
    pilots: int
    decks: int
    cards: int
    events: int
    archetypes: int
    macros: int
    colours: int
    card_types: int
    piloted_by: int
    contains: int
    played_at: int
    has_archetype: int
    has_macro: int
    deck_colour: int
    card_colour: int
    has_type: int


def reconciliation_path(db_path: Path) -> Path:
    """Where the reconciliation report is written for a graph at ``db_path``."""
    db_path = Path(db_path)
    return db_path.with_name(db_path.name + ".reconciliation.json")


def build_graph(snapshot: Snapshot, db_path: Path) -> BuildCounts:
    """Build a fresh Kùzu database at ``db_path`` and return its counts.

    Pilots are resolved to keyed, named nodes and a reconciliation report is
    written alongside the database at :func:`reconciliation_path` (ADR 0004).
    """
    db_path = Path(db_path)
    _remove(db_path)

    pilots = resolve_pilots(snapshot.decks)

    conn = kuzu.Connection(kuzu.Database(str(db_path)))
    for ddl in _SCHEMA:
        conn.execute(ddl)

    _load_nodes(conn, snapshot, pilots)
    _load_edges(conn, snapshot, pilots)

    reconciliation_path(db_path).write_text(json.dumps(asdict(pilots.report), indent=2))

    return BuildCounts(
        pilots=_count(conn, "MATCH (p:Pilot) RETURN count(p)"),
        decks=_count(conn, "MATCH (d:Deck) RETURN count(d)"),
        cards=_count(conn, "MATCH (c:Card) RETURN count(c)"),
        events=_count(conn, "MATCH (e:Event) RETURN count(e)"),
        archetypes=_count(conn, "MATCH (a:Archetype) RETURN count(a)"),
        macros=_count(conn, "MATCH (m:`Macro`) RETURN count(m)"),
        colours=_count(conn, "MATCH (c:Colour) RETURN count(c)"),
        card_types=_count(conn, "MATCH (t:CardType) RETURN count(t)"),
        piloted_by=_count(conn, "MATCH ()-[r:PILOTED_BY]->() RETURN count(r)"),
        contains=_count(conn, "MATCH ()-[r:CONTAINS]->() RETURN count(r)"),
        played_at=_count(conn, "MATCH ()-[r:PLAYED_AT]->() RETURN count(r)"),
        has_archetype=_count(conn, "MATCH ()-[r:HAS_ARCHETYPE]->() RETURN count(r)"),
        has_macro=_count(conn, "MATCH ()-[r:HAS_MACRO]->() RETURN count(r)"),
        deck_colour=_count(conn, "MATCH ()-[r:DECK_COLOUR]->() RETURN count(r)"),
        card_colour=_count(conn, "MATCH ()-[r:CARD_COLOUR]->() RETURN count(r)"),
        has_type=_count(conn, "MATCH ()-[r:HAS_TYPE]->() RETURN count(r)"),
    )


def _load_nodes(conn: kuzu.Connection, snapshot: Snapshot, pilots: PilotResolution) -> None:
    _load(conn,
          "UNWIND $rows AS r CREATE (:Pilot {pilot: r.pilot, "
          "displayName: r.displayName, lowConfidence: r.lowConfidence})",
          [{"pilot": p.pilot, "displayName": p.display_name,
            "lowConfidence": p.low_confidence} for p in pilots.pilots])

    _load(conn,
          """UNWIND $rows AS r CREATE (:Deck {deckId: r.deckId, name: r.name,
             placement: r.placement, placementNorm: r.placementNorm,
             colourIdentity: r.colourIdentity})""",
          [{"deckId": d.deck_id, "name": d.name, "placement": d.placement,
            "placementNorm": d.placement_norm, "colourIdentity": d.colour_identity}
           for d in snapshot.decks])
    _create_nodes(conn, "Card", Card, _CARD_FIELDS, snapshot.cards)

    # Dimension nodes, deduped from the decks and cards that reference them.
    # Event is keyed on the event code, not eventId: the code is always present
    # (one deck has a null eventId) and is 1:1 with eventId in the source, so it
    # is the safe stable key. eventId is retained as a property for later joins.
    events = {d.event: (d.event_id, d.event_type) for d in snapshot.decks}
    _load(conn,
          "UNWIND $rows AS r CREATE (:Event {event: r.event, eventId: r.eventId, "
          "eventType: r.eventType})",
          [{"event": e, "eventId": eid, "eventType": et}
           for e, (eid, et) in events.items()])

    archetypes = {a.tag: a.name for d in snapshot.decks for a in d.archetypes}
    _load(conn, "UNWIND $rows AS r CREATE (:Archetype {tag: r.tag, name: r.name})",
          [{"tag": t, "name": n} for t, n in archetypes.items()])

    macros = sorted({d.macro_code for d in snapshot.decks})
    _load(conn, "UNWIND $rows AS r CREATE (:`Macro` {name: r.name})",
          [{"name": m} for m in macros])

    _load(conn, "UNWIND $rows AS r CREATE (:Colour {colour: r.colour})",
          [{"colour": c} for c in COLOURS])

    card_types = sorted({c.type for c in snapshot.cards})
    _load(conn, "UNWIND $rows AS r CREATE (:CardType {type: r.type})",
          [{"type": t} for t in card_types])


def _load_edges(conn: kuzu.Connection, snapshot: Snapshot, pilots: PilotResolution) -> None:
    _load(conn,
          """UNWIND $rows AS r
             MATCH (d:Deck {deckId: r.deckId}), (p:Pilot {pilot: r.pilot})
             CREATE (d)-[:PILOTED_BY]->(p)""",
          [{"deckId": d.deck_id, "pilot": pilots.deck_pilot[d.deck_id]}
           for d in snapshot.decks])
    _load(conn,
          """UNWIND $rows AS r
             MATCH (d:Deck {deckId: r.deckId}), (c:Card {canon: r.canon})
             CREATE (d)-[:CONTAINS {board: r.board}]->(c)""",
          [{"deckId": c.deck_id, "canon": c.canon, "board": c.board}
           for c in snapshot.containments])
    _load(conn,
          """UNWIND $rows AS r
             MATCH (d:Deck {deckId: r.deckId}), (e:Event {event: r.event})
             CREATE (d)-[:PLAYED_AT]->(e)""",
          [{"deckId": d.deck_id, "event": d.event} for d in snapshot.decks])
    _load(conn,
          """UNWIND $rows AS r
             MATCH (d:Deck {deckId: r.deckId}), (a:Archetype {tag: r.tag})
             CREATE (d)-[:HAS_ARCHETYPE {weight: r.weight, isPrimary: r.isPrimary}]->(a)""",
          [{"deckId": d.deck_id, "tag": a.tag, "weight": a.weight, "isPrimary": a.primary}
           for d in snapshot.decks for a in d.archetypes])
    _load(conn,
          """UNWIND $rows AS r
             MATCH (d:Deck {deckId: r.deckId}), (m:`Macro` {name: r.name})
             CREATE (d)-[:HAS_MACRO]->(m)""",
          [{"deckId": d.deck_id, "name": d.macro_code} for d in snapshot.decks])
    _load(conn,
          """UNWIND $rows AS r
             MATCH (d:Deck {deckId: r.deckId}), (c:Colour {colour: r.colour})
             CREATE (d)-[:DECK_COLOUR]->(c)""",
          [{"deckId": d.deck_id, "colour": col}
           for d in snapshot.decks for col in d.colour_atoms])
    _load(conn,
          """UNWIND $rows AS r
             MATCH (card:Card {canon: r.canon}), (c:Colour {colour: r.colour})
             CREATE (card)-[:CARD_COLOUR]->(c)""",
          [{"canon": c.canon, "colour": col}
           for c in snapshot.cards for col in c.colours])
    _load(conn,
          """UNWIND $rows AS r
             MATCH (card:Card {canon: r.canon}), (t:CardType {type: r.type})
             CREATE (card)-[:HAS_TYPE]->(t)""",
          [{"canon": c.canon, "type": c.type} for c in snapshot.cards])


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
