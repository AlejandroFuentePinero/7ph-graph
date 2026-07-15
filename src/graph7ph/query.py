"""The query spine: a library of parameterized queries over the built graph.

Each query function turns its parameters into Cypher and returns a Subgraph the
renderer can draw. The derived relationships ADR 0002 keeps out of the stored
model (card usage and co-occurrence, cards unique to an archetype, hidden gems,
pilot affinity) live here as query functions instead. A ``QuerySpec`` names one query
and its parameters, and ``run_query`` is the single seam that maps a spec to its
function, so v1's controls and v2's RAG agent drive the same layer.

Node ids are namespaced by kind (``pilot:``/``deck:``/``card:``/``arch:``/
``macro:``/``event:``/``placement:``) so nodes of different kinds can never
collide on a shared string.
"""

from dataclasses import dataclass
from typing import Literal

import kuzu

from graph7ph.db import rows

Kind = Literal["Pilot", "Deck", "Card", "Archetype", "Macro", "Event", "Placement"]


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    kind: Kind
    # An optional analytic weight the renderer sizes the node by (e.g. a pilot's
    # event count per archetype). ``None`` renders at the default size.
    weight: int | None = None
    # An optional grouping the renderer colours by instead of kind, used to tint
    # a head-to-head by player. ``None`` falls back to the kind colour.
    group: str | None = None
    # An optional vis.js node shape override. ``None`` is the default dot (label
    # beside it, sized by weight); ``"circle"`` draws the label inside the node.
    shape: str | None = None


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    label: str
    # By default the label is a hover tooltip; ``True`` draws it on the edge, used
    # where the edge carries the readable name (the node itself shows a number).
    visible: bool = False


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
    # An optional second pilot turns the view into a head-to-head; empty or unset
    # leaves it the single pilot's neighbourhood.
    pilot2: str | None = None


@dataclass(frozen=True)
class CardUsage:
    canon: str
    # Which board a deck must run the card in to count: ``None`` counts it in
    # either, ``"Main"`` or ``"Side"`` restricts to that board.
    board: str | None = None


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


def _ordinal(placement: int) -> str:
    """A placement as a human ordinal: ``1`` -> ``1st``, ``12`` -> ``12th``."""
    suffix = "th" if 10 <= placement % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(
        placement % 10, "th"
    )
    return f"{placement}{suffix}"


def pilot_subgraph(
    conn: kuzu.Connection, pilot: str, pilot2: str | None = None
) -> Subgraph:
    """One pilot's record, or two pilots' head-to-head, as event-rooted chains.

    Each pilot is a hub; every event they played branches off, and off each event
    hangs the deck they ran there and, off the deck, where it placed. The deck is
    labelled by its own name (e.g. "Grixis"), free of the placement and pilot that
    clutter the full deck title. One deck per pilot per event (ADR 0004) keeps
    every branch a clean line. Cards are left out on purpose: a pilot's whole card
    pool floods the view without telling this story. A pilot is keyed on the
    upstream id but labelled by display name; a placement is a leaf per deck so
    shared ranks never collapse decks together.

    With ``pilot2`` the view narrows to the head-to-head: only events both pilots
    played are kept, each a neutral node the two reach, with each pilot's own deck
    under it (no deck is ever shared, ADR 0004). The two chains are tinted by
    player so it reads at a glance which pilot ran which deck. Events only one of
    them played are dropped, as they are not a head-to-head. An empty ``pilot2``
    (or the same pilot twice) falls back to the first pilot's full record alone.
    """
    head_to_head = bool(pilot2) and pilot2 != pilot
    keys = [pilot, pilot2] if head_to_head else [pilot]
    res = conn.execute(
        """MATCH (p:Pilot)<-[:PILOTED_BY]-(d:Deck)-[:PLAYED_AT]->(e:Event)
           WHERE p.pilot IN $keys
           RETURN p.pilot, p.displayName, e.event, d.deckId, d.deckName,
                  d.placement""",
        {"keys": keys},
    )
    records = list(rows(res))

    # Which pilots played each event, so the head-to-head can keep only the ones
    # both did and tint each kept chain by its player.
    event_pilots: dict[str, set[str]] = {}
    for pilot_key, _, event, *_ in records:
        event_pilots.setdefault(f"event:{event}", set()).add(f"pilot:{pilot_key}")

    def owner(pid: str) -> str | None:
        return pid if head_to_head else None

    nodes: dict[str, Node] = {}
    edges: list[Edge] = []
    played: set[tuple[str, str]] = set()  # (pilot, event), so a shared event

    for pilot_key, pilot_name, event, deck_id, deck_name, placement in records:
        pid = f"pilot:{pilot_key}"
        eid = f"event:{event}"
        did = f"deck:{deck_id}"
        if head_to_head and len(event_pilots[eid]) < 2:
            continue  # only events both pilots played are a head-to-head
        nodes.setdefault(pid, Node(pid, pilot_name, "Pilot", group=owner(pid)))
        nodes.setdefault(eid, Node(eid, event, "Event"))  # neutral: both played it
        if (pid, eid) not in played:  # keeps both pilots' edges to a shared event
            played.add((pid, eid))
            edges.append(Edge(pid, eid, "PLAYED_AT"))
        nodes.setdefault(did, Node(did, deck_name, "Deck", group=owner(pid)))
        edges.append(Edge(eid, did, "ENTERED"))
        if placement is not None:
            plid = f"placement:{deck_id}"
            nodes.setdefault(
                plid, Node(plid, _ordinal(placement), "Placement", group=owner(pid))
            )
            edges.append(Edge(did, plid, "PLACED"))

    return Subgraph(nodes=list(nodes.values()), edges=edges)


def card_usage_subgraph(
    conn: kuzu.Connection, canon: str, board: str | None = None
) -> Subgraph:
    """The card's prevalence in the meta, as an adoption rate at each tier.

    Answers "how prevalent is this card, and where is it a staple" (user story 7)
    by measuring adoption, not raw reach: card -> macro -> archetype, where every
    node reads as one thing, "the percent of the decks at this level that run the
    card". The card node is its share of the whole meta (its play-rate); a macro
    node is the percent of that strategy's decks that run it; an archetype node is
    the percent of that archetype's decks that run it. Adoption normalises for
    slice size, so a card that is core to an archetype stands out from one merely
    carried by a big archetype (which raw counts cannot tell apart). This owns the
    prevalence dimension, distinct from co-occurrence (card packages), archetype
    unique cards (exclusivity), and hidden gems (rarity times performance).

    Because archetypes span several macros (Grixis decks are mostly tempo but also
    midrange, control, ...), each archetype hangs under the macro where its own
    card-running decks sit, so the macro above it always contains decks running the
    card and its tier percent never reads a contradictory zero above an adopted
    archetype; the archetype's shown adoption stays the honest archetype-wide
    figure. Every archetype the card appears in is drawn, strongest adoption first,
    so a staple that runs everywhere may exceed the render limit and refine rather
    than draw. Pilot and event are left out on purpose: this is a card-level view.

    ``board`` scopes the numerator: ``None`` counts a deck running the card in
    either board, ``"Main"`` or ``"Side"`` only the decks running it there. The
    denominator is always the slice's whole deck count. A deck running the card in
    both boards still counts once.
    """
    name_row = next(rows(conn.execute(
        "MATCH (c:Card {canon: $canon}) RETURN c.name", {"canon": canon}
    )), None)
    if name_row is None:
        return Subgraph(nodes=[], edges=[])  # no such card
    card_name = name_row[0]

    where = "WHERE cont.board = $board" if board else ""
    params = {"canon": canon, "board": board} if board else {"canon": canon}

    # Denominators: every macro's and archetype's own deck count. Numerators: the
    # decks of that slice running the card, scoped to the chosen board.
    macro_total = dict(rows(conn.execute(
        "MATCH (m:`Macro`)<-[:HAS_MACRO]-(d:Deck) RETURN m.name, count(DISTINCT d)"
    )))
    macro_run = dict(rows(conn.execute(
        f"""MATCH (m:`Macro`)<-[:HAS_MACRO]-(d:Deck)-[cont:CONTAINS]->(:Card {{canon: $canon}})
            {where} RETURN m.name, count(DISTINCT d)""", params
    )))
    arch_total = {tag: (name, total) for tag, name, total in rows(conn.execute(
        "MATCH (a:Archetype)<-[:HAS_ARCHETYPE]-(d:Deck) RETURN a.tag, a.name, count(DISTINCT d)"
    ))}
    arch_run = dict(rows(conn.execute(
        f"""MATCH (a:Archetype)<-[:HAS_ARCHETYPE]-(d:Deck)-[cont:CONTAINS]->(:Card {{canon: $canon}})
            {where} RETURN a.tag, count(DISTINCT d)""", params
    )))
    # The macro where each archetype's card-running decks sit, so the grouping
    # macro always contains decks running the card. Ties resolve on macro name, so
    # the choice is stable regardless of the query's row order.
    dominant: dict[str, tuple[int, str]] = {}
    for tag, macro, n in rows(conn.execute(
        f"""MATCH (a:Archetype)<-[:HAS_ARCHETYPE]-(d:Deck)-[cont:CONTAINS]->(:Card {{canon: $canon}})
            {where}
            MATCH (d)-[:HAS_MACRO]->(m:`Macro`)
            RETURN a.tag, m.name, count(DISTINCT d)""", params
    )):
        cur = dominant.get(tag)
        if cur is None or n > cur[0] or (n == cur[0] and macro < cur[1]):
            dominant[tag] = (n, macro)

    def pct(run: int, total: int) -> int:
        return round(100 * run / total) if total else 0

    def pct_label(run: int, total: int) -> str:
        """The adoption as a label; a present-but-tiny share reads ``<1%``, not the
        misleading ``0%`` that rounding would give."""
        share = 100 * run / total if total else 0
        return "<1%" if 0 < share < 0.5 else f"{round(share)}%"

    # Play-rate over decks directly, not summed per-macro, so it holds even if a
    # deck ever carried more than one macro.
    meta_total = next(rows(conn.execute("MATCH (d:Deck) RETURN count(d)")))[0]
    meta_run = next(rows(conn.execute(
        f"""MATCH (:Card {{canon: $canon}})<-[cont:CONTAINS]-(d:Deck)
            {where} RETURN count(DISTINCT d)""", params
    )))[0]

    card_id = f"card:{canon}"
    nodes: list[Node] = [
        Node(card_id, f"{card_name} ({pct_label(meta_run, meta_total)} of meta)", "Card")
    ]
    edges: list[Edge] = []

    # Every archetype the card appears in, strongest adoption first; ties broken by
    # the larger archetype, then name. An archetype without a grouping macro (a
    # card-running deck missing a macro) is skipped, so the edge target below is
    # always a macro node that exists.
    kept = sorted(
        (
            (pct(arch_run.get(tag, 0), total), total, tag, name)
            for tag, (name, total) in arch_total.items()
            if arch_run.get(tag, 0) and tag in dominant
        ),
        key=lambda k: (-k[0], -k[1], k[3]),
    )

    # The readable name sits inside each circle; the adoption percent rides the
    # edge that reaches it, so the name stays in the node and the number outside.
    shown_macros = {dominant[tag][1] for _, _, tag, _ in kept}
    for macro in sorted(shown_macros, key=lambda m: -pct(macro_run.get(m, 0), macro_total[m])):
        mid = f"macro:{macro}"
        nodes.append(Node(mid, macro, "Macro", shape="circle"))
        edges.append(
            Edge(card_id, mid, pct_label(macro_run.get(macro, 0), macro_total[macro]), visible=True)
        )
    for _p, total, tag, name in kept:
        aid = f"arch:{tag}"
        nodes.append(Node(aid, name, "Archetype", shape="circle"))
        edges.append(
            Edge(f"macro:{dominant[tag][1]}", aid, pct_label(arch_run.get(tag, 0), total), visible=True)
        )

    return Subgraph(nodes=nodes, edges=edges)


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
    """A pilot's play grouped through macro strategy to archetype, by events.

    Shows whether a pilot is a specialist or a generalist (user story 16) with a
    macro tier between the pilot and the noisy archetype names: pilot -> macro
    (aggro, midrange, ...) -> archetype (Rakdos Eclipse, Grixis, ...). The macro
    is the deck's broad strategic class, so it collapses the unstandardised
    archetype names ("Rakdos", "Rakdos Aggro", "Rakdos Eclipse") under one
    readable class. Every node is sized, and every edge labelled, by the number
    of distinct events the pilot registered it at: the macro by its own events,
    the archetype by its events across all macros, and the macro->archetype edge
    by the events the pilot played that archetype within that macro. Events
    rather than decks, so entering several variants on a single day counts once
    for showing up. An archetype that a pilot played under two macros is one
    shared node with an edge from each. The pilot is keyed on the upstream id
    but labelled by display name.
    """
    res = conn.execute(
        """MATCH (p:Pilot {pilot: $pilot})
           OPTIONAL MATCH (p)<-[:PILOTED_BY]-(d:Deck)-[:HAS_MACRO]->(m:`Macro`)
           OPTIONAL MATCH (d)-[:PLAYED_AT]->(e:Event)
           OPTIONAL MATCH (d)-[:HAS_ARCHETYPE]->(a:Archetype)
           RETURN p.pilot, p.displayName, m.name, a.tag, a.name, e.event""",
        {"pilot": pilot},
    )

    pilot_id: str | None = None
    pilot_label = pilot
    macro_events: dict[str, set[str]] = {}
    arch_events: dict[str, set[str]] = {}
    macro_arch_events: dict[tuple[str, str], set[str]] = {}
    arch_names: dict[str, str] = {}

    for pilot_key, pilot_name, macro, a_tag, a_name, event in rows(res):
        pilot_id = f"pilot:{pilot_key}"
        pilot_label = pilot_name
        if macro is None:
            continue
        macro_events.setdefault(macro, set())
        if event is not None:
            macro_events[macro].add(event)
        if a_tag is None:
            continue
        arch_names[a_tag] = a_name
        arch_events.setdefault(a_tag, set())
        macro_arch_events.setdefault((macro, a_tag), set())
        if event is not None:
            arch_events[a_tag].add(event)
            macro_arch_events[(macro, a_tag)].add(event)

    if pilot_id is None:  # no such pilot; MATCH bound nothing
        return Subgraph(nodes=[], edges=[])

    nodes: list[Node] = [Node(pilot_id, pilot_label, "Pilot")]
    edges: list[Edge] = []

    # Macros first, then archetypes, each biggest affinity first for a stable
    # order the renderer can lay out consistently.
    for macro, events in sorted(macro_events.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        mid = f"macro:{macro}"
        nodes.append(Node(mid, macro, "Macro", weight=len(events)))
        edges.append(Edge(pilot_id, mid, f"PLAYS:{len(events)}"))
    for a_tag, events in sorted(arch_events.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        aid = f"arch:{a_tag}"
        nodes.append(Node(aid, arch_names[a_tag], "Archetype", weight=len(events)))
    for (macro, a_tag), events in sorted(
        macro_arch_events.items(), key=lambda kv: (kv[0][0], -len(kv[1]), kv[0][1])
    ):
        edges.append(Edge(f"macro:{macro}", f"arch:{a_tag}", f"PLAYS:{len(events)}"))

    return Subgraph(nodes=nodes, edges=edges)


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
        case PilotNeighbourhood(pilot, pilot2):
            return pilot_subgraph(conn, pilot, pilot2)
        case CardUsage(canon, board):
            return card_usage_subgraph(conn, canon, board)
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
