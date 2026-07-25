"""Load a parsed Snapshot into a Ladybug graph.

The graph is the fact spine of ADR 0002. Entity nodes (Pilot, Deck, Card) and
dimension nodes (Event, Archetype, Macro, Colour, CardType, Year) are joined by
edges for irreducible facts only: who piloted a deck, where and when it was
played, which cards it runs, its archetypes/macro/colours, and each card's type
and colours. Year is the one derived dimension (ADR 0006). A build is a full
rebuild into a fresh database file; counts are read back out of the graph so
callers can assert they match the source.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import ladybug
from pydantic import BaseModel

from graph7ph.curation import Curation, load_curation
from graph7ph.db import open_for_writing, remove_artifact, rows
from graph7ph.models import COLOURS, Card, Containment, Deck, Snapshot
from graph7ph.pilots import PilotResolution, resolve_pilots
from graph7ph.provenance import stamp

_SCHEMA = [
    # A Pilot is keyed on the upstream id; displayName is a recovered label and
    # lowConfidence marks the re-keyed null-pilot decks (ADR 0004).
    "CREATE NODE TABLE Pilot(pilot STRING, displayName STRING, "
    "lowConfidence BOOLEAN, PRIMARY KEY(pilot))",
    # createdAt is the list's registration timestamp, stored on the Deck so the
    # head-to-head timeline can read it as a sub-year x-axis (ADR 0013 amends ADR
    # 0006's "not stored on the Deck node"): we date the registration, a hard
    # per-deck fact, not the event, which stays year-only. Every other trend still
    # groups by the Year node; only this one reads the per-deck date.
    """CREATE NODE TABLE Deck(
        deckId STRING, name STRING, deckName STRING, placement INT64,
        placementNorm DOUBLE, colourIdentity STRING, createdAt TIMESTAMP,
        PRIMARY KEY(deckId))""",
    """CREATE NODE TABLE Card(
        canon STRING, name STRING, type STRING, manaValue DOUBLE,
        reserved BOOLEAN, priceUsd DOUBLE, points INT64, PRIMARY KEY(canon))""",
    # fieldSize is the field the event's placements are normalised against, and
    # fieldImputed the rule that corrected the source's own `eventSize`, or null
    # where it stood (see `corrected_field`). Both are on the Event rather than
    # the Deck because a field size is a fact about the event, and carrying it
    # here lets a reader tell an arithmetic correction from a domain one.
    "CREATE NODE TABLE Event(event STRING, eventId STRING, eventType STRING, "
    "fieldSize INT64, fieldImputed STRING, PRIMARY KEY(event))",
    "CREATE NODE TABLE Archetype(tag STRING, name STRING, PRIMARY KEY(tag))",
    # The `Macro` label is backtick-escaped everywhere and its value lives on
    # `name` rather than a `macro` column. Both shapes are inherited from the
    # previous engine, which reserved the words. That constraint lifted with the
    # swap (Ladybug parses them verbatim), but the backticked naming is kept as
    # the convention here rather than renamed: renaming a node label touches
    # roughly ten sites across this module and `query.py` and forces
    # `baseline/subgraphs.json` to be re-captured, which is the one thing the
    # golden harness exists to avoid.
    "CREATE NODE TABLE `Macro`(name STRING, PRIMARY KEY(name))",
    "CREATE NODE TABLE Colour(colour STRING, PRIMARY KEY(colour))",
    "CREATE NODE TABLE CardType(type STRING, PRIMARY KEY(type))",
    "CREATE NODE TABLE Year(year INT64, PRIMARY KEY(year))",
    "CREATE REL TABLE PILOTED_BY(FROM Deck TO Pilot)",
    "CREATE REL TABLE CONTAINS(FROM Deck TO Card, board STRING)",
    "CREATE REL TABLE PLAYED_AT(FROM Deck TO Event)",
    # The primary-archetype flag is spelled `isPrimary`, not `primary`, and is
    # kept for the same reason as `Macro` above.
    "CREATE REL TABLE HAS_ARCHETYPE(FROM Deck TO Archetype, weight INT64, isPrimary BOOLEAN)",
    "CREATE REL TABLE HAS_MACRO(FROM Deck TO `Macro`)",
    "CREATE REL TABLE DECK_COLOUR(FROM Deck TO Colour)",
    "CREATE REL TABLE CARD_COLOUR(FROM Card TO Colour)",
    "CREATE REL TABLE HAS_TYPE(FROM Card TO CardType)",
    "CREATE REL TABLE IN_YEAR(FROM Event TO Year)",
]

class YearStraddle(ValueError):
    """An event's decks span more than one calendar year, so it cannot be dated.

    Raised before anything is written, so the live graph is untouched. The CLI
    reports it as an abort rather than a crash, alongside ``SchemaError``: both
    mean the snapshot cannot honestly be built (ADR 0003, ADR 0006).
    """


_BATCH = 5000

# The smallest field a corrected event is normalised against: 7PH's top-8 cuts
# come out of events of at least this size, so it is the floor a corrected field
# can never fall below (issue #140). The two events it moves across the hidden-gem
# band are robust to the choice: 16, 19, 24, 33 and 40 all produce the same pair.
MIN_CUT_FIELD = 24

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
    years: int
    piloted_by: int
    contains: int
    played_at: int
    has_archetype: int
    has_macro: int
    deck_colour: int
    card_colour: int
    has_type: int
    in_year: int


def reconciliation_path(artifact: Path) -> Path:
    """Where the reconciliation report is written for the bundle at ``artifact``.

    Inside the artifact directory (not a sibling) so it promotes and rolls back
    atomically with the graph as one bundle (issue #38, F13).
    """
    return Path(artifact) / "reconciliation.json"


def build_graph(
    snapshot: Snapshot, artifact: Path, curation: Curation | None = None
) -> BuildCounts:
    """Build a fresh artifact bundle at ``artifact`` and return its counts.

    The bundle is a directory holding the database, opened through
    :func:`db.open_for_writing`, alongside its reports, so one rename promotes
    the lot (issue #47). Pilots are resolved to keyed, named nodes, applying the
    checked-in curation dictionary
    (issue #9); a reconciliation report is written beside the database at
    :func:`reconciliation_path` (ADR 0004). Duplicate registrations the resolution
    drops are excluded from the graph entirely.

    Everything that can reject the data runs before the bundle is touched, so a
    build that aborts (a year straddle, a bad curation dictionary) leaves no
    half-made bundle behind and cannot damage an artifact it was pointed at.
    """
    artifact = Path(artifact)
    # Whether the dictionary came off disk decides what the bundle can claim: a
    # curation handed in by a caller is reproducible from no state of the working
    # tree, so the stamp below records no digest rather than the digest of a file
    # this build never read.
    from_disk = curation is None
    curation = curation if curation is not None else load_curation()
    snapshot = _apply_deck_archetypes(snapshot, curation)
    pilots = resolve_pilots(snapshot.decks, curation, _decklists(snapshot.containments))
    # Deliberately above the duplicate drop, so the straddle guard reads the
    # population the source gave rather than the one de-duplication left. The
    # survivor rule never consults a date (it tie-breaks on placement, then deck
    # id), so a dropped registration is free to be the only deck carrying its
    # event's second year: rewriting the one real duplicate loser's createdAt
    # across a New Year makes this call raise YearStraddle for NHC26
    # (2025/2026), while calling it after the drop returns a confident 2026 with
    # no signal of any kind (issue #103). Free on the data held: the two
    # populations return identical dicts across all 108 events, and reading the
    # larger one cannot lose an event key, since the survivor rule always keeps
    # one member of every group.
    years = _event_years(snapshot.decks)
    if pilots.dropped_decks:
        snapshot = _without_decks(snapshot, pilots.dropped_decks)
    # After the drop as well as after the resolution: a duplicate registration is
    # not a second attendee, and Rule A fires on a count of attendees exceeding
    # the claimed field, so counting the dropped registrations would correct a
    # field the source had right (issue #140).
    fields = _event_fields(snapshot.decks, pilots.deck_pilot)
    snapshot = _rescale_norms(snapshot, fields)

    remove_artifact(artifact)

    # The write seam settles the write-ahead log on the way out, so what gets
    # promoted is a settled file holding exactly the graph these counts were read
    # out of, even if the load below fails partway (see `db.open_for_writing`).
    with open_for_writing(artifact) as conn:
        for ddl in _SCHEMA:
            conn.execute(ddl)

        _load_nodes(conn, snapshot, pilots, years, fields)
        _load_edges(conn, snapshot, pilots, years)

        # The pilot resolution's own report, plus every event whose field size
        # this build corrected: both are assumptions made about data the source
        # gave, listed each build so they stay legible (ADR 0004, issue #140).
        reconciliation_path(artifact).write_text(json.dumps(
            asdict(pilots.report)
            | {"imputed_fields": [asdict(f) for f in fields.values() if f.rule]},
            indent=2,
        ))
        # Stamped here rather than in `ingest`, so every path that seals a bundle
        # seals a self-describing one, and the baseline gate can tell whether the
        # artifact it is about to grade came from the code standing here (#55).
        stamp(artifact, reproducible=from_disk)
        return graph_counts(conn)


def graph_counts(conn: ladybug.Connection) -> BuildCounts:
    """The 18 table counts read back out of a built graph.

    Read from the graph rather than from the snapshot, so a caller can assert the
    build loaded what the source held, and so the golden-subgraph harness can take
    the same 18 numbers off an artifact it did not build (issue #45).
    """
    return BuildCounts(
        pilots=_count(conn, "MATCH (p:Pilot) RETURN count(p)"),
        decks=_count(conn, "MATCH (d:Deck) RETURN count(d)"),
        cards=_count(conn, "MATCH (c:Card) RETURN count(c)"),
        events=_count(conn, "MATCH (e:Event) RETURN count(e)"),
        archetypes=_count(conn, "MATCH (a:Archetype) RETURN count(a)"),
        macros=_count(conn, "MATCH (m:`Macro`) RETURN count(m)"),
        colours=_count(conn, "MATCH (c:Colour) RETURN count(c)"),
        card_types=_count(conn, "MATCH (t:CardType) RETURN count(t)"),
        years=_count(conn, "MATCH (y:Year) RETURN count(y)"),
        piloted_by=_count(conn, "MATCH ()-[r:PILOTED_BY]->() RETURN count(r)"),
        contains=_count(conn, "MATCH ()-[r:CONTAINS]->() RETURN count(r)"),
        played_at=_count(conn, "MATCH ()-[r:PLAYED_AT]->() RETURN count(r)"),
        has_archetype=_count(conn, "MATCH ()-[r:HAS_ARCHETYPE]->() RETURN count(r)"),
        has_macro=_count(conn, "MATCH ()-[r:HAS_MACRO]->() RETURN count(r)"),
        deck_colour=_count(conn, "MATCH ()-[r:DECK_COLOUR]->() RETURN count(r)"),
        card_colour=_count(conn, "MATCH ()-[r:CARD_COLOUR]->() RETURN count(r)"),
        has_type=_count(conn, "MATCH ()-[r:HAS_TYPE]->() RETURN count(r)"),
        in_year=_count(conn, "MATCH ()-[r:IN_YEAR]->() RETURN count(r)"),
    )


def _decklists(containments: list[Containment]) -> dict[str, tuple]:
    """Each deck's card-for-card signature, for spotting duplicate registrations.

    A deck's identity for de-duplication is the set of cards on each board, so
    two registrations with the same 75 hash equal however their rows are ordered.
    """
    main: dict[str, set] = {}
    side: dict[str, set] = {}
    for c in containments:
        (main if c.board == "Main" else side).setdefault(c.deck_id, set()).add(c.canon)
    ids = main.keys() | side.keys()
    return {
        deck_id: (frozenset(main.get(deck_id, ())), frozenset(side.get(deck_id, ())))
        for deck_id in ids
    }


def _apply_deck_archetypes(snapshot: Snapshot, curation: Curation) -> Snapshot:
    """Reclassify decks the curation dictionary corrects (issue #9).

    The source tags a deck's archetype off its title, so a mistitled list (e.g.
    "Blue Moon" for what the cards show is UR Prowess) lands on the wrong
    archetype. A ``[[deck_archetype]]`` entry replaces that classification with
    the human-confirmed one, collapsing the deck onto the single corrected
    engine so it joins that archetype's convention.
    """
    overrides = curation.deck_archetypes
    if not overrides:
        return snapshot
    decks = []
    for d in snapshot.decks:
        override = overrides.get(d.deck_id)
        if override is None:
            decks.append(d)
            continue
        decks.append(d.model_copy(update={
            "deck_name": override.deck_name,
            "engine_tags": [override.engine],
            "engine_tag_labels": {override.engine: override.engine_label},
            "primary_tag": override.engine,
            "primary_tag_weights": {override.engine: 100},
        }))
    return Snapshot(
        cards=snapshot.cards, decks=decks, containments=snapshot.containments
    )


def _without_decks(snapshot: Snapshot, dropped: frozenset[str]) -> Snapshot:
    """A copy of the snapshot with the dropped decks and their cards removed."""
    return Snapshot(
        cards=snapshot.cards,
        decks=[d for d in snapshot.decks if d.deck_id not in dropped],
        containments=[c for c in snapshot.containments if c.deck_id not in dropped],
    )


def _event_years(decks: list[Deck]) -> dict[str, int]:
    """Each event's year, derived from the ``createdAt`` of its decks.

    There is no event date in the source, so deck creation is the proxy for when
    an event happened, and year is the granularity that proxy supports (ADR
    0006). ``createdAt`` is UTC throughout the source, so these are UTC years.

    The derivation is only honest while an event's decks all fall in one
    calendar year, so a straddling event raises :class:`YearStraddle` rather
    than silently picking one. That guard is what makes the read-out below a
    read-out: every set it sees holds exactly one year, and ``min`` only spells
    out which year that is when the set is a singleton.
    """
    seen: dict[str, set[int]] = {}
    for deck in decks:
        seen.setdefault(deck.event, set()).add(deck.created_at.year)
    straddling = {e: sorted(ys) for e, ys in seen.items() if len(ys) > 1}
    if straddling:
        raise YearStraddle(
            "events span more than one calendar year, so createdAt cannot date "
            "them: " + ", ".join(f"{e} ({'/'.join(str(y) for y in ys)})"
                                 for e, ys in sorted(straddling.items()))
        )
    return {event: min(ys) for event, ys in seen.items()}


@dataclass(frozen=True)
class EventField:
    """The field one event's placements are normalised against, and its evidence.

    ``rule`` is null where the source's own ``eventSize`` stood, and a letter
    where :func:`corrected_field` corrected it. The counts it was corrected on
    ride along, so the reconciliation report can show the contradiction rather
    than assert it.
    """

    event: str
    rule: str | None
    event_size: int | None  # what the source claimed
    field_size: int | None  # what the placements are normalised against
    pilots: int
    max_placement: int


def corrected_field(
    event_type: str, event_size: int | None, pilots: int, max_placement: int
) -> tuple[int | None, str | None]:
    """The field an event's placements should be normalised against, and the rule.

    The source ships ``eventSize`` on every deck, and at 9 of 108 events that
    number is wrong. Because ``placementNorm = (placement - 1) / (eventSize - 1)``,
    the error lands on every metric that reads a normalised finish: four pilots
    sat at a career mean of 1.000, dead last in field, and every one of them
    earned it by making a top 8 (issue #140). The returned rule letter is null
    where the source's own number stands, so a reader can tell a corrected field
    from an accepted one, and which kind of correction it was.

    ``pilots`` is the number of distinct *canonical* pilots who registered, and
    ``max_placement`` the deepest finish recorded (0 where none is), so both are
    counted facts about the event rather than claims about it.

    **Rule A's contradiction is provable, its replacement is not.** ``eventSize``
    contradicts something countable: more people attended than the claimed field,
    or someone finished at a rank beyond it, which is impossible under any
    reading. That is what the letter records. The number that replaces it is a
    different claim, and mostly the same domain one Rule B rests on: at 5 of the
    6 Rule A events the floor decides (7 or 8 pilots against a claimed 5, all
    landing on 24), and only ``GGWAD`` takes its field from a count (28 pilots
    against a claimed 16). So an ``A`` in the report means "the source's number
    is refuted", never "the new number is measured".

    **Rule B is domain knowledge.** Per the repo owner, 7PH runs no 8-player
    events, so an ``eventSize`` of 8 or less is a top-8 cut reported as a field.
    The ``max_placement < event_size`` guard anchors it in evidence, though only
    partly: an event somebody finished last in has its small field corroborated
    by its own standings, and MazeIQ and Pats Birthday Brawl show a deepest
    finish of 5th against a claimed 8, the bracket signature. It is satisfied
    vacuously at DeckaDiceIQ, where the winner is the only finish recorded at
    all, so nothing there corroborates or contradicts the 6 the source claims and
    the domain rule decides it alone.

    **Rule C is defensive only.** A null ``eventSize`` has never been observed.

    Only ``Tournament`` is corrected, by whitelist rather than blacklist. A
    ``Teams`` event's ``eventSize`` counts teams and not people (TMCTeams25 is 39
    against 117 decks), so Rule A's contradiction is that event's normal shape,
    and an ``eventType`` nobody has classified yet is left alone rather than
    corrected on Tournament's assumptions. ``eventType`` is the only reliable
    discriminator: a structural detector was tried and rejected, because
    decks-per-distinct-placement does not separate the two (top-8 brackets with
    ties reach 4.00 and overlap TMCTeams25's 3.08).

    The floor is ``max(counted, MIN_CUT_FIELD)`` rather than a bare constant, so
    the correction stays a floor as more lists arrive: MazeIQ at 9 decks still
    yields 24, a broken 30-pilot event yields 30, and the field is never set
    below something we counted.
    """
    if event_type != "Tournament":
        return event_size, None
    if event_size is None:
        return max(pilots, max_placement, MIN_CUT_FIELD), "C"
    if pilots > event_size or max_placement > event_size:
        return max(pilots, max_placement, MIN_CUT_FIELD), "A"
    if event_size <= 8 and max_placement < event_size:
        return max(pilots, MIN_CUT_FIELD), "B"
    return event_size, None


def _event_fields(
    decks: list[Deck], deck_pilot: dict[str, str]
) -> dict[str, EventField]:
    """Each event's field size, corrected where the source's contradicts a count.

    Counted over canonical pilot identities rather than raw source ids, which is
    why this runs after :func:`pilots.resolve_pilots`: Rule A's floor asserts "at
    least N people attended", and a merge the curation dictionary confirms
    collapses two ids onto one person, the one direction that floor must not err
    in. It reads recovered placements for the same reason: Pats Birthday Brawl
    has placements only through :func:`models.placement_from_title`, and
    :func:`models.resolve_cut_placements` rewrites CanBrawl2's nested tiers, and
    both settle at load. An event nobody's finish was recorded at counts a
    deepest placement of 0, which no field size can be below.

    ``eventSize`` is a property of the event that every one of its decks repeats,
    identically at all 108 events held, so the first one that carries a number
    speaks for the cohort.
    """
    counted: dict[str, tuple[str, int | None, set[str], int]] = {}
    for deck in decks:
        _, size, people, deepest = counted.setdefault(
            deck.event, (deck.event_type, deck.event_size, set(), 0)
        )
        people.add(deck_pilot[deck.deck_id])
        counted[deck.event] = (
            # The last deck's type, which is the one `_load_nodes` writes to the
            # Event node, so the type that decided the whitelist is the type the
            # graph shows. They agree at all 108 events held; reading them off
            # different decks is what would make a disagreement unexplainable.
            deck.event_type,
            size if size is not None else deck.event_size,
            people,
            max(deepest, deck.placement or 0),
        )
    fields = {}
    for event, (event_type, size, people, deepest) in counted.items():
        field_size, rule = corrected_field(event_type, size, len(people), deepest)
        fields[event] = EventField(event, rule, size, field_size, len(people), deepest)
    return fields


def _rescale_norms(snapshot: Snapshot, fields: dict[str, EventField]) -> Snapshot:
    """Re-rank each corrected event's norms against its corrected field.

    ``placementNorm = (placement - 1) / (fieldSize - 1)``, the source's own
    formula, applied to the field the event was really played in. Only a
    corrected event is touched, and within it only a deck the source itself
    scored: a placement the source left unranked stays unranked, so this
    rescales 54 decks and mints no norm the source never had, which keeps
    ``_ranked_deck_total`` and the gem denominators where they were (issue #140).
    Every corrected field is at least :data:`MIN_CUT_FIELD`, so the division is
    safe.
    """
    corrected = {e: f.field_size for e, f in fields.items() if f.rule}
    if not corrected:
        return snapshot
    decks = [
        d.model_copy(update={
            "placement_norm": (d.placement - 1) / (corrected[d.event] - 1)
        })
        if d.event in corrected and d.placement_norm is not None
        and d.placement is not None
        else d
        for d in snapshot.decks
    ]
    return Snapshot(
        cards=snapshot.cards, decks=decks, containments=snapshot.containments
    )


def _load_nodes(
    conn: ladybug.Connection,
    snapshot: Snapshot,
    pilots: PilotResolution,
    years: dict[str, int],
    fields: dict[str, EventField],
) -> None:
    _load(conn,
          "UNWIND $rows AS r CREATE (:Pilot {pilot: r.pilot, "
          "displayName: r.displayName, lowConfidence: r.lowConfidence})",
          [{"pilot": p.pilot, "displayName": p.display_name,
            "lowConfidence": p.low_confidence} for p in pilots.pilots])

    _load(conn,
          """UNWIND $rows AS r CREATE (:Deck {deckId: r.deckId, name: r.name,
             deckName: r.deckName, placement: r.placement,
             placementNorm: r.placementNorm, colourIdentity: r.colourIdentity,
             createdAt: r.createdAt})""",
          [{"deckId": d.deck_id, "name": d.name, "deckName": d.deck_name,
            "placement": d.placement, "placementNorm": d.placement_norm,
            "colourIdentity": d.colour_identity, "createdAt": d.created_at}
           for d in snapshot.decks])
    _create_nodes(conn, "Card", Card, _CARD_FIELDS, snapshot.cards)

    # Dimension nodes, deduped from the decks and cards that reference them.
    # Event is keyed on the event code, not eventId: the code is always present
    # (one deck has a null eventId) and is 1:1 with eventId in the source, so it
    # is the safe stable key. eventId is retained as a property for later joins.
    events = {d.event: (d.event_id, d.event_type) for d in snapshot.decks}
    _load(conn,
          "UNWIND $rows AS r CREATE (:Event {event: r.event, eventId: r.eventId, "
          "eventType: r.eventType, fieldSize: r.fieldSize, "
          "fieldImputed: r.fieldImputed})",
          [{"event": e, "eventId": eid, "eventType": et,
            "fieldSize": fields[e].field_size, "fieldImputed": fields[e].rule}
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

    _load(conn, "UNWIND $rows AS r CREATE (:Year {year: r.year})",
          [{"year": y} for y in sorted(set(years.values()))])


def _load_edges(
    conn: ladybug.Connection,
    snapshot: Snapshot,
    pilots: PilotResolution,
    years: dict[str, int],
) -> None:
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
    _load(conn,
          """UNWIND $rows AS r
             MATCH (e:Event {event: r.event}), (y:Year {year: r.year})
             CREATE (e)-[:IN_YEAR]->(y)""",
          [{"event": e, "year": y} for e, y in years.items()])


def _create_nodes(
    conn: ladybug.Connection,
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


def _load(conn: ladybug.Connection, query: str, batch: list[dict]) -> None:
    for start in range(0, len(batch), _BATCH):
        conn.execute(query, {"rows": batch[start:start + _BATCH]})


def _count(conn: ladybug.Connection, query: str) -> int:
    return next(rows(conn.execute(query)))[0]

