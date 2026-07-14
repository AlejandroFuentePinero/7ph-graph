"""The query spine: a library of parameterized queries over the built graph.

Each query function turns its parameters into Cypher and returns a Subgraph the
renderer can draw. The derived relationships ADR 0002 keeps out of the stored
model (card usage and co-occurrence, cards unique to an archetype, hidden gems,
pilot affinity) live here as query functions instead. A ``QuerySpec`` names one query
and its parameters, and ``run_query`` is the single seam that maps a spec to its
function, so v1's controls and v2's RAG agent drive the same layer.

Node ids are namespaced by kind (``pilot:``/``deck:``/``card:``/``arch:``) so
nodes of different kinds can never collide on a shared string.
"""

from dataclasses import dataclass
from typing import Literal

import kuzu

from graph7ph.db import rows

Kind = Literal["Pilot", "Deck", "Card", "Archetype"]


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    kind: Kind
    # An optional analytic weight the renderer sizes the node by (e.g. a pilot's
    # event count per archetype). ``None`` renders at the default size.
    weight: int | None = None


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    label: str


@dataclass
class Subgraph:
    nodes: list[Node]
    edges: list[Edge]


# The query spec: a serialisable description of one query and its parameters.
# v1's controls emit these; v2's RAG agent will emit the same, and both reach
# the graph through the single ``run_query`` seam below (ADR 0002, issue #1).


@dataclass(frozen=True)
class PilotNeighbourhood:
    pilot: str


@dataclass(frozen=True)
class CardUsage:
    canon: str


@dataclass(frozen=True)
class CardCooccurrence:
    canon: str
    min_shared: int = 2


@dataclass(frozen=True)
class ArchetypeUniqueCards:
    tag: str
    min_decks: int = 3


@dataclass(frozen=True)
class HiddenGems:
    min_decks: int
    max_decks: int
    max_norm: float
    colour: str | None = None
    archetype: str | None = None


@dataclass(frozen=True)
class PilotAffinity:
    pilot: str


QuerySpec = (
    PilotNeighbourhood
    | CardUsage
    | CardCooccurrence
    | ArchetypeUniqueCards
    | HiddenGems
    | PilotAffinity
)


def pilot_subgraph(conn: kuzu.Connection, pilot: str) -> Subgraph:
    """The pilot, their decks, and the cards those decks contain.

    The pilot is keyed on the upstream id but labelled by display name.
    """
    res = conn.execute(
        """MATCH (p:Pilot {pilot: $pilot})<-[:PILOTED_BY]-(d:Deck)
           OPTIONAL MATCH (d)-[c:CONTAINS]->(card:Card)
           RETURN p.pilot, p.displayName, d.deckId, d.name,
                  card.canon, card.name, c.board""",
        {"pilot": pilot},
    )

    nodes: dict[str, Node] = {}
    edges: list[Edge] = []

    for pilot_key, pilot_name, deck_id, deck_name, canon, card_name, board in rows(res):
        pid = f"pilot:{pilot_key}"
        did = f"deck:{deck_id}"
        nodes.setdefault(pid, Node(pid, pilot_name, "Pilot"))
        if did not in nodes:
            nodes[did] = Node(did, deck_name, "Deck")
            edges.append(Edge(did, pid, "PILOTED_BY"))

        if canon is not None:
            cid = f"card:{canon}"
            nodes.setdefault(cid, Node(cid, card_name, "Card"))
            edges.append(Edge(did, cid, f"CONTAINS:{board}"))

    return Subgraph(nodes=list(nodes.values()), edges=edges)


def card_usage_subgraph(conn: kuzu.Connection, canon: str) -> Subgraph:
    """The card and every deck that runs it, each linked up to its pilot.

    Answers "who plays this card, and how does it place" (user story 7): the
    card is the hub, its decks fan out, and each deck reaches its pilot so the
    card's reach across pilots is visible. Pilots are labelled by display name.
    """
    res = conn.execute(
        """MATCH (card:Card {canon: $canon})
           OPTIONAL MATCH (card)<-[c:CONTAINS]-(d:Deck)-[:PILOTED_BY]->(p:Pilot)
           RETURN card.canon, card.name, d.deckId, d.name, c.board,
                  p.pilot, p.displayName""",
        {"canon": canon},
    )

    nodes: dict[str, Node] = {}
    edges: list[Edge] = []

    for canon_v, card_name, deck_id, deck_name, board, pilot_key, pilot_name in rows(res):
        cid = f"card:{canon_v}"
        nodes.setdefault(cid, Node(cid, card_name, "Card"))
        if deck_id is None:
            continue
        did = f"deck:{deck_id}"
        pid = f"pilot:{pilot_key}"
        nodes.setdefault(pid, Node(pid, pilot_name, "Pilot"))
        if did not in nodes:
            nodes[did] = Node(did, deck_name, "Deck")
            edges.append(Edge(did, cid, f"CONTAINS:{board}"))
            edges.append(Edge(did, pid, "PILOTED_BY"))

    return Subgraph(nodes=list(nodes.values()), edges=edges)


def card_cooccurrence_subgraph(
    conn: kuzu.Connection, canon: str, min_shared: int = 2
) -> Subgraph:
    """The card and the cards it shares at least ``min_shared`` decks with.

    Surfaces card packages (user story 15): the seed card is the hub and each
    edge is labelled with the number of decks the two share. Only same-board
    pairings count: a card in the main and another in the side of the same deck
    are not a functional pairing, so they do not co-occur here. ``min_shared``
    bounds the result to genuine pairings rather than every incidental overlap.
    """
    centre = conn.execute(
        "MATCH (card:Card {canon: $canon}) RETURN card.name", {"canon": canon}
    )
    centre_row = next(rows(centre), None)
    if centre_row is None:
        return Subgraph(nodes=[], edges=[])

    cid = f"card:{canon}"
    nodes: dict[str, Node] = {cid: Node(cid, centre_row[0], "Card")}
    edges: list[Edge] = []

    res = conn.execute(
        """MATCH (card:Card {canon: $canon})<-[a:CONTAINS]-(d:Deck)-[b:CONTAINS]->(other:Card)
           WHERE other.canon <> card.canon AND a.board = b.board
           WITH other, count(DISTINCT d) AS shared
           WHERE shared >= $minShared
           RETURN other.canon, other.name, shared""",
        {"canon": canon, "minShared": min_shared},
    )
    for o_canon, o_name, shared in rows(res):
        oid = f"card:{o_canon}"
        nodes.setdefault(oid, Node(oid, o_name, "Card"))
        edges.append(Edge(cid, oid, f"COOCCURS:{shared}"))

    return Subgraph(nodes=list(nodes.values()), edges=edges)


def archetype_unique_cards_subgraph(
    conn: kuzu.Connection, tag: str, min_decks: int = 3
) -> Subgraph:
    """The cards found only in this archetype's decks, seen in enough of them.

    A card is unique to the archetype when every deck that runs it carries the
    archetype, i.e. it appears nowhere outside it. This is exclusivity of
    appearance, not a claim about the card's role: the same card can play very
    differently across decks, so we assert only where it turns up, never what it
    means. ``min_decks`` requires the card to show up in at least that many of
    the archetype's decks, so a card that is exclusive only because a single
    deck happened to run it is filtered out as noise. Cards run by more of the
    archetype's decks come first, and each edge is labelled with that count.
    """
    name_row = next(
        rows(conn.execute(
            "MATCH (a:Archetype {tag: $tag}) RETURN a.name", {"tag": tag}
        )),
        None,
    )
    if name_row is None:
        return Subgraph(nodes=[], edges=[])

    in_arch = list(rows(conn.execute(
        """MATCH (:Archetype {tag: $tag})<-[:HAS_ARCHETYPE]-(d:Deck)-[:CONTAINS]->(c:Card)
           RETURN c.canon, c.name, count(DISTINCT d)""",
        {"tag": tag},
    )))
    # Only the archetype's own cards need a global deck count, so scope the
    # totals to them rather than scanning the whole card catalogue.
    canons = [canon for canon, _name, _count in in_arch]
    totals = {
        canon: total
        for canon, total in rows(conn.execute(
            "MATCH (c:Card)<-[:CONTAINS]-(d:Deck) WHERE c.canon IN $canons "
            "RETURN c.canon, count(DISTINCT d)",
            {"canons": canons},
        ))
    }

    # Unique = every deck holding the card is an archetype deck (none outside),
    # and enough of them run it to be a property of the archetype, not one list.
    unique = [
        (arch_count, canon, card_name)
        for canon, card_name, arch_count in in_arch
        if totals[canon] == arch_count and arch_count >= min_decks
    ]
    unique.sort(key=lambda u: (-u[0], u[1]))

    aid = f"arch:{tag}"
    nodes: list[Node] = [Node(aid, name_row[0], "Archetype")]
    edges: list[Edge] = []
    for arch_count, canon, card_name in unique:
        cid = f"card:{canon}"
        nodes.append(Node(cid, card_name, "Card"))
        edges.append(Edge(aid, cid, f"UNIQUE:{arch_count}"))

    return Subgraph(nodes=nodes, edges=edges)


def hidden_gems_subgraph(
    conn: kuzu.Connection,
    min_decks: int,
    max_decks: int,
    max_norm: float,
    colour: str | None = None,
    archetype: str | None = None,
) -> Subgraph:
    """Cards seen in a narrow band of decks that nonetheless place highly.

    A gem is a card in between ``min_decks`` and ``max_decks`` decks whose mean
    placement (as a normalised rank, lower is better) is at most ``max_norm``
    (user story 14). The band is the point: ``min_decks`` demands enough decks to
    trust the overperformance, separating a real gem from a card that got lucky
    in one or two lists; ``max_decks`` keeps it rare, so once a card spreads into
    a staple it stops being a hidden gem. Both bounds and the placement are
    measured over the decks whose rank is known: a deck with no recorded
    placement cannot confirm over- or under-performance, so it is left out of
    the count and the mean entirely rather than padding the band. ``colour`` and
    ``archetype`` narrow the slice, so "gems within Grixis" means cards in that
    band among Grixis decks, not globally rare cards that merely appear in one.
    With no filter the slice is every deck. Returns each gem with the ranked
    decks that run it, so its placement is visible.
    """
    slice_ids = _deck_slice(conn, colour, archetype)
    if slice_ids is not None and not slice_ids:
        return Subgraph(nodes=[], edges=[])

    # Only rank-bearing decks count, optionally narrowed to the slice.
    ranked = "d.placementNorm IS NOT NULL" + (
        " AND d.deckId IN $slice" if slice_ids is not None else ""
    )
    params: dict = {"minDecks": min_decks, "maxDecks": max_decks, "maxNorm": max_norm}
    if slice_ids is not None:
        params["slice"] = slice_ids

    gems = {
        canon: name
        for canon, name in rows(conn.execute(
            f"""MATCH (d:Deck)-[:CONTAINS]->(c:Card)
               WHERE {ranked}
               WITH DISTINCT c, d
               WITH c, count(d) AS decks, avg(d.placementNorm) AS meanNorm
               WHERE decks >= $minDecks AND decks <= $maxDecks
                     AND meanNorm <= $maxNorm
               RETURN c.canon, c.name""",
            params,
        ))
    }
    if not gems:
        return Subgraph(nodes=[], edges=[])

    nodes: dict[str, Node] = {
        f"card:{canon}": Node(f"card:{canon}", name, "Card")
        for canon, name in gems.items()
    }
    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()
    edge_params: dict = {"gems": list(gems)}
    if slice_ids is not None:
        edge_params["slice"] = slice_ids
    res = conn.execute(
        f"""MATCH (d:Deck)-[ct:CONTAINS]->(c:Card)
           WHERE c.canon IN $gems AND {ranked}
           RETURN d.deckId, d.name, c.canon, ct.board""",
        edge_params,
    )
    for deck_id, deck_name, canon, board in rows(res):
        did = f"deck:{deck_id}"
        cid = f"card:{canon}"
        nodes.setdefault(did, Node(did, deck_name, "Deck"))
        if (did, cid) not in seen:
            seen.add((did, cid))
            edges.append(Edge(did, cid, f"CONTAINS:{board}"))

    return Subgraph(nodes=list(nodes.values()), edges=edges)


def pilot_affinity_subgraph(conn: kuzu.Connection, pilot: str) -> Subgraph:
    """A pilot and the archetypes they play, weighted by distinct events.

    Shows whether a pilot is a specialist or a generalist (user story 16): the
    pilot is the hub and each archetype node is sized (and its edge labelled) by
    the number of distinct events the pilot registered that archetype at. Events
    rather than decks, so a pilot who entered several variants of one archetype
    on a single day counts once for showing up, not once per list. The pilot is
    keyed on the upstream id but labelled by display name.
    """
    res = conn.execute(
        """MATCH (p:Pilot {pilot: $pilot})
           OPTIONAL MATCH (p)<-[:PILOTED_BY]-(d:Deck)-[:HAS_ARCHETYPE]->(a:Archetype)
           OPTIONAL MATCH (d)-[:PLAYED_AT]->(e:Event)
           WITH p, a, count(DISTINCT e) AS events
           RETURN p.pilot, p.displayName, a.tag, a.name, events
           ORDER BY events DESC, a.tag""",
        {"pilot": pilot},
    )

    nodes: dict[str, Node] = {}
    edges: list[Edge] = []

    for pilot_key, pilot_name, a_tag, a_name, events in rows(res):
        pid = f"pilot:{pilot_key}"
        nodes.setdefault(pid, Node(pid, pilot_name, "Pilot"))
        if a_tag is None:
            continue
        aid = f"arch:{a_tag}"
        nodes.setdefault(aid, Node(aid, a_name, "Archetype", weight=events))
        edges.append(Edge(pid, aid, f"PLAYS:{events}"))

    return Subgraph(nodes=list(nodes.values()), edges=edges)


def _deck_slice(
    conn: kuzu.Connection, colour: str | None, archetype: str | None
) -> list[str] | None:
    """The deck ids the gem hunt runs within, intersecting the given colour atom
    and archetype tag. ``None`` means no filter (every deck), so the caller can
    skip the id list rather than enumerate the whole graph."""
    if colour is None and archetype is None:
        return None
    ids: set[str] | None = None
    if colour is not None:
        ids = _deck_ids(
            conn, "MATCH (d:Deck)-[:DECK_COLOUR]->(:Colour {colour: $v})", colour
        )
    if archetype is not None:
        arch_ids = _deck_ids(
            conn, "MATCH (d:Deck)-[:HAS_ARCHETYPE]->(:Archetype {tag: $v})", archetype
        )
        ids = arch_ids if ids is None else ids & arch_ids
    return list(ids)


def _deck_ids(conn: kuzu.Connection, match: str, value: str) -> set[str]:
    """The distinct deck ids matched by ``match`` (which binds ``d`` and ``$v``)."""
    return {row[0] for row in rows(
        conn.execute(f"{match} RETURN DISTINCT d.deckId", {"v": value})
    )}


def run_query(conn: kuzu.Connection, spec: QuerySpec) -> Subgraph:
    """Map a query spec to its query function and return the resulting subgraph.

    The single entry point over the query-function library: the v1 controls and
    the future v2 RAG agent both drive the graph through here. A new query means
    a function, a spec dataclass, its member in the ``QuerySpec`` union, and a
    case below.
    """
    match spec:
        case PilotNeighbourhood(pilot):
            return pilot_subgraph(conn, pilot)
        case CardUsage(canon):
            return card_usage_subgraph(conn, canon)
        case CardCooccurrence(canon, min_shared):
            return card_cooccurrence_subgraph(conn, canon, min_shared)
        case ArchetypeUniqueCards(tag, min_decks):
            return archetype_unique_cards_subgraph(conn, tag, min_decks)
        case HiddenGems(min_decks, max_decks, max_norm, colour, archetype):
            return hidden_gems_subgraph(
                conn, min_decks, max_decks, max_norm, colour, archetype
            )
        case PilotAffinity(pilot):
            return pilot_affinity_subgraph(conn, pilot)
        case _:
            raise TypeError(f"unknown query spec: {spec!r}")
