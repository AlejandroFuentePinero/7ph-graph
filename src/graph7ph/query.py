"""The query spine: a library of parameterized queries over the built graph.

Each query function turns its parameters into Cypher and returns a Subgraph the
renderer can draw. The derived relationships ADR 0002 keeps out of the stored
model (card usage and co-occurrence, hidden gems, pilot affinity) live here as
query functions instead. A ``QuerySpec`` names one query
and its parameters, and ``run_query`` is the single seam that maps a spec to its
function, so v1's controls and v2's RAG agent drive the same layer.

Node ids are namespaced by kind (``pilot:``/``deck:``/``card:``/``arch:``/
``macro:``/``event:``/``placement:``, plus ``both:`` for the two-card
co-occurrence intersection hub) so nodes of different kinds can never collide on
a shared string.
"""

import math
from dataclasses import dataclass
from typing import Literal

import ladybug

from graph7ph.db import rows

Kind = Literal[
    "Pilot", "Deck", "Card", "Archetype", "Macro", "Event", "Placement", "Intersection"
]

# The hidden-gem band, fixed rather than exposed as controls (ADR 0005): the
# question "which rare cards overperform?" has one answer, not one per dial.
MIN_GEM_DECKS = 5  # trust floor: an absolute count, so it holds at any slice size
MAX_GEM_SHARE = 0.10  # rarity ceiling: a share, so it means the same in any slice
MAX_GEM_MEAN_NORM = 0.33  # overperformance: mean placement in the slice's top third

# The two bounds cross here: below this many ranked decks the ceiling falls under
# the floor and the band is empty by construction, because "rare" and "attested
# by 5 decks" are contradictory in a small slice (5 decks IS a quarter of a
# 20-deck archetype). That is not a bug to paper over: the slice genuinely cannot
# support a gem claim, so we say so rather than lower the floor and report noise.
# Rounded UP, never to nearest: the smallest slice admitted must satisfy
# `MIN_GEM_DECKS <= MAX_GEM_SHARE * MIN_GEM_SLICE`, and rounding down would admit
# a slice whose band is still inverted, silently restoring the bug this prevents.
MIN_GEM_SLICE = math.ceil(MIN_GEM_DECKS / MAX_GEM_SHARE)


# A deck with no recorded placement cannot confirm over- or under-performance,
# so the gem hunt ignores it. Written once and shared by every query that has to
# agree on what "ranked" means: the slice, the band, and the offered archetypes.
_RANKED = "d.placementNorm IS NOT NULL"


class SliceTooSmall(ValueError):
    """Raised when a slice has too few ranked decks to support a gem claim."""


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
    # An optional fixed ``(x, y)`` position. ``None`` lets the physics layout place
    # the node; set, it pins the node there (physics off) for a deterministic
    # layout, used to separate the two co-occurrence seeds and centre their
    # shared cards.
    pin: tuple[float, float] | None = None
    # The analytic values behind the node, kept as numbers rather than folded
    # into the label: how many decks it counts (a gem's rarity, an intersection's
    # size, a card's play-rate), the base that count is a share of where it is
    # one, and a gem's mean placement. The renderer ignores them; a label is for
    # display, these are for a consumer that wants the value, so v2's tool layer
    # need not re-derive what was computed here (issue #12).
    decks: int | None = None
    total_decks: int | None = None
    mean_norm: float | None = None


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    label: str
    # By default the label is a hover tooltip; ``True`` draws it on the edge, used
    # where the edge carries the readable name (the node itself shows a number).
    visible: bool = False
    # The analytic values behind the edge, kept as numbers rather than folded
    # into the label: how many decks it counts (shared by two cards, or running
    # a card at this tier), the base that count is a share of, and a pilot's
    # event count. Named as on ``Node``, so ``decks`` is the count and
    # ``total_decks`` the base wherever it appears. A label like ``"75%"`` is a
    # rounded rendering of the first pair, and ``"<1%"`` erases the ratio
    # entirely; a consumer that wants the value reads it here rather than
    # parsing display text (issue #12).
    decks: int | None = None
    total_decks: int | None = None
    events: int | None = None


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
    # An optional second seed card; empty or unset leaves the single card's
    # neighbourhood, set turns it into a two-card shared-package view.
    canon2: str | None = None
    # How many partners to keep per seed: the top ``top_n`` cards by co-occurrence
    # rate, so a popular seed refines to its strongest packages instead of flooding.
    top_n: int = 15
    # Exclude land cards, which co-occur with nearly everything and read as noise.
    drop_lands: bool = False


@dataclass(frozen=True)
class HiddenGems:
    archetype: str | None = None


@dataclass(frozen=True)
class PilotAffinity:
    pilot: str


QuerySpec = (
    PilotNeighbourhood
    | CardUsage
    | CardCooccurrence
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
    conn: ladybug.Connection, pilot: str, pilot2: str | None = None
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
    conn: ladybug.Connection, canon: str, board: str | None = None
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
    prevalence dimension, distinct from co-occurrence (card packages) and hidden
    gems (rarity times performance).

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

    # Play-rate over decks directly, not summed per-macro, so it holds even if a
    # deck ever carried more than one macro.
    meta_total = next(rows(conn.execute("MATCH (d:Deck) RETURN count(d)")))[0]
    meta_run = next(rows(conn.execute(
        f"""MATCH (:Card {{canon: $canon}})<-[cont:CONTAINS]-(d:Deck)
            {where} RETURN count(DISTINCT d)""", params
    )))[0]

    card_id = f"card:{canon}"
    nodes: list[Node] = [
        Node(
            card_id, f"{card_name} ({_pct_label(meta_run, meta_total)} of meta)", "Card",
            decks=meta_run, total_decks=meta_total,
        )
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
        # Tag last, because two tags can share a display name: without it a tie on
        # adoption, size and name would fall back to the row order of a query with
        # no ORDER BY, so the view would move with the engine rather than the data.
        key=lambda k: (-k[0], -k[1], k[3], k[2]),
    )

    # Each tier reads as a named default dot (card -> macro -> archetype); the
    # adoption percent rides the edge that reaches it. Dots keep every node a
    # uniform size with its name beside it, where a circle would stretch to fit
    # the text and read as a bigger node for no analytic reason.
    # Strongest adoption first, ties broken on name as they are for archetypes
    # below: two macros can round to the same percent, and without the tie-break
    # their order falls out of an unordered set, so the same query on the same
    # graph answers differently between runs.
    shown_macros = {dominant[tag][1] for _, _, tag, _ in kept}
    for macro in sorted(
        shown_macros, key=lambda m: (-pct(macro_run.get(m, 0), macro_total[m]), m)
    ):
        mid = f"macro:{macro}"
        nodes.append(Node(mid, macro, "Macro"))
        edges.append(
            _rate_edge(card_id, mid, macro_run.get(macro, 0), macro_total[macro], visible=True)
        )
    for _p, total, tag, name in kept:
        aid = f"arch:{tag}"
        nodes.append(Node(aid, name, "Archetype"))
        edges.append(
            _rate_edge(f"macro:{dominant[tag][1]}", aid, arch_run.get(tag, 0), total, visible=True)
        )

    return Subgraph(nodes=nodes, edges=edges)


# Lands run in nearly every deck, so they co-occur with everything and mostly
# read as noise; the co-occurrence views can filter them out by card type.
_LAND_TYPE = "Lands"


def _no_lands(alias: str, drop_lands: bool) -> str:
    """A Cypher fragment excluding land-typed cards bound to ``alias``, or ``''``
    to keep them."""
    return f" AND {alias}.type <> '{_LAND_TYPE}'" if drop_lands else ""


def _cooccurrence_partners(
    conn: ladybug.Connection, canon: str, top_n: int, drop_lands: bool = False
) -> list[tuple[str, str, int]]:
    """A card's top ``top_n`` same-board co-occurrence partners, strongest first:
    ``[(canon, name, shared), ...]``.

    ``shared`` is the count of decks where the partner sits in the same board as
    the seed. Only same-board pairings count: a card in the main and another in
    the side of the same deck are not a functional pairing. The seed's deck count
    is constant across partners, so ranking by ``shared`` ranks by co-occurrence
    rate; the cut is pushed into Cypher rather than sorting every partner in
    Python. ``drop_lands`` excludes land partners.
    """
    # Kùzu wants a literal LIMIT, not a parameter; ``top_n`` is a trusted int.
    # Ladybug takes it as a parameter; the workaround goes with #50, not #48.
    res = conn.execute(
        f"""MATCH (card:Card {{canon: $canon}})<-[a:CONTAINS]-(d:Deck)-[b:CONTAINS]->(other:Card)
           WHERE other.canon <> card.canon AND a.board = b.board{_no_lands("other", drop_lands)}
           WITH other, count(DISTINCT d) AS shared
           RETURN other.canon, other.name, shared
           ORDER BY shared DESC, other.name, other.canon
           LIMIT {int(top_n)}""",
        {"canon": canon},
    )
    return [(o_canon, o_name, shared) for o_canon, o_name, shared in rows(res)]


def _card_and_deck_count(
    conn: ladybug.Connection, canon: str
) -> tuple[str, int] | None:
    """``(name, deck_count)`` for a card, or ``None`` when no such card exists."""
    return next(rows(conn.execute(
        """MATCH (card:Card {canon: $canon})
           OPTIONAL MATCH (card)<-[:CONTAINS]-(d:Deck)
           RETURN card.name, count(DISTINCT d)""",
        {"canon": canon},
    )), None)


def _pct_label(shared: int, total: int) -> str:
    """A co-occurrence rate as a label; a present-but-tiny share reads ``<1%``,
    not the misleading ``0%`` that rounding would give."""
    share = 100 * shared / total if total else 0
    return "<1%" if 0 < share < 0.5 else f"{round(share)}%"


def _rate_edge(
    source: str, target: str, decks: int, total: int, visible: bool = False
) -> Edge:
    """An edge showing a deck rate: the percent as its label, both its terms as
    numbers.

    Built in one place so the numbers a consumer reads and the percent the
    renderer draws can never disagree about which ratio they describe. Shared by
    the two rates the library draws, co-occurrence and adoption, which differ in
    what they count but not in how they read.
    """
    return Edge(
        source, target, _pct_label(decks, total),
        visible=visible, decks=decks, total_decks=total,
    )


def _plays_edge(source: str, target: str, events: int) -> Edge:
    """A pilot-affinity edge: the event count as its label, and as a number."""
    return Edge(source, target, f"PLAYS:{events}", events=events)


# The two-seed layout, in vis.js units. The two seeds and the intersection hub
# anchor on the left (the seeds stacked, the hub between them and the cards); the
# shared cards line up in a column on the right, ordered by double rate. Pinning
# everything and hanging each card off the single hub keeps the graph readable
# where a physics cloud overlaps into a ball. ``_SEED_X``/``_SEED_DY`` place the
# seeds, ``_HUB_X`` the hub, ``_CARD_X`` the column, ``_COL_GAP`` the row height.
_SEED_X, _SEED_DY = 800.0, 150.0
_HUB_X = 350.0
_CARD_X = 300.0
_COL_GAP = 80.0


def _shared_deck_cooccurrence(
    conn: ladybug.Connection, canon_a: str, canon_b: str, top_n: int, drop_lands: bool = False
) -> tuple[int, list[tuple[str, str, int]]]:
    """The double co-occurrence: decks that run both seeds, and the ``top_n`` cards
    those decks most often also run.

    Returns ``(both_decks, [(canon, name, shared), ...])`` where ``both_decks`` is
    the count of decks running both seeds (the denominator) and ``shared`` is the
    count of those decks that also run the card, strongest first. A deck runs a
    card when it appears in either board, so this is deck-level membership rather
    than the same-board pairing the single-seed view uses. ``drop_lands`` excludes
    lands (before the cut, so the cut keeps ``top_n`` non-lands), which co-occur
    with nearly everything and mostly read as noise.
    """
    both = next(rows(conn.execute(
        """MATCH (a:Card {canon: $a})<-[:CONTAINS]-(d:Deck)-[:CONTAINS]->(b:Card {canon: $b})
           RETURN count(DISTINCT d)""",
        {"a": canon_a, "b": canon_b},
    )))[0]
    if not both:
        return 0, []
    # Kùzu wants a literal LIMIT, not a parameter; ``top_n`` is a trusted int.
    # Ladybug takes it as a parameter; the workaround goes with #50, not #48.
    res = conn.execute(
        f"""MATCH (a:Card {{canon: $a}})<-[:CONTAINS]-(d:Deck)-[:CONTAINS]->(b:Card {{canon: $b}})
            MATCH (d)-[:CONTAINS]->(p:Card)
            WHERE p.canon <> $a AND p.canon <> $b{_no_lands("p", drop_lands)}
            WITH p, count(DISTINCT d) AS shared
            RETURN p.canon, p.name, shared
            ORDER BY shared DESC, p.name, p.canon
            LIMIT {int(top_n)}""",
        {"a": canon_a, "b": canon_b},
    )
    return both, [(c, n, s) for c, n, s in rows(res)]


def card_cooccurrence_subgraph(
    conn: ladybug.Connection,
    canon: str,
    canon2: str | None = None,
    top_n: int = 15,
    drop_lands: bool = False,
) -> Subgraph:
    """One card's top co-occurrence partners, or two cards' shared cards.

    Surfaces card packages (user story 15). With one seed the hub is the card and
    each edge is labelled with the co-occurrence rate, the percent of the seed's
    own decks that also run the partner; the top ``top_n`` partners by that rate
    are kept, so a popular seed refines to its strongest packages rather than
    flooding the view with every card it ever shared a deck with.

    With a second seed the view answers "what do these two cards share": it keeps
    the top ``top_n`` cards by the *double* co-occurrence rate, the percent of the
    decks running both seeds that also run the card. An intersection hub node
    ("Both", labelled with that shared-deck count) anchors the graph: each seed
    links to the hub with the fraction of its decks in the intersection, and every
    shared card hangs off the hub with one edge (its double rate). That single hub
    keeps the edges informative instead of a redundant fan to both seeds, lines
    the shared cards up in a readable column, and generalises to the three-plus
    card intersections a future agent will drive. Each seed carries its own colour
    group and all shared cards share one, so they read apart at a glance.

    ``drop_lands`` excludes land cards from the results (both views): lands run in
    nearly every deck, so they co-occur with everything and mostly read as noise;
    dropping them surfaces the ``top_n`` non-land packages instead.
    """
    seed_a = _card_and_deck_count(conn, canon)
    if seed_a is None:
        return Subgraph(nodes=[], edges=[])
    name_a, decks_a = seed_a

    # A second seed only when a distinct, existing card is chosen; the same card
    # twice, or a missing one, collapses to the single-seed view.
    seed_b = _card_and_deck_count(conn, canon2) if canon2 and canon2 != canon else None

    cid_a = f"card:{canon}"
    if seed_b is None:
        nodes = {cid_a: Node(cid_a, name_a, "Card", group=f"seed:{canon}")}
        edges: list[Edge] = []
        for o_canon, o_name, shared in _cooccurrence_partners(conn, canon, top_n, drop_lands):
            oid = f"card:{o_canon}"
            nodes[oid] = Node(oid, o_name, "Card", group="cooccur")
            edges.append(_rate_edge(cid_a, oid, shared, decks_a))
        return Subgraph(nodes=list(nodes.values()), edges=edges)

    name_b, decks_b = seed_b
    cid_b = f"card:{canon2}"
    both, shared = _shared_deck_cooccurrence(conn, canon, canon2, top_n, drop_lands)

    # When the two cards never share a deck there is nothing to anchor: show them
    # as two disconnected seeds rather than an empty "Both · 0 decks" hub.
    if not both:
        return Subgraph(
            nodes=[
                Node(cid_a, name_a, "Card", group=f"seed:{canon}"),
                Node(cid_b, name_b, "Card", group=f"seed:{canon2}"),
            ],
            edges=[],
        )

    # The intersection hub is the "decks running both seeds" node that justifies a
    # graph: each seed links to it (the fraction of that seed's decks that fall in
    # the intersection) and every shared card hangs off it with one edge (its
    # double rate), so edges carry information instead of a redundant double fan.
    # The deck count on the hub is the denominator every percent is read against.
    # Deliberately a synthetic count node, not a real macro/archetype: the decks
    # running a given pair span many macros with no dominant one (e.g. Blood Moon +
    # Price of Progress split aggro 48% / tempo 26% / control 12% / ...), so a real
    # higher-level anchor misrepresents the mix. This was tried and reverted.
    hub_id = f"both:{canon}|{canon2}"
    nodes = {
        cid_a: Node(cid_a, name_a, "Card", group=f"seed:{canon}", pin=(-_SEED_X, _SEED_DY)),
        cid_b: Node(cid_b, name_b, "Card", group=f"seed:{canon2}", pin=(-_SEED_X, -_SEED_DY)),
        hub_id: Node(
            hub_id, f"Both · {both} decks", "Intersection",
            shape="circle", pin=(-_HUB_X, 0.0), decks=both,
        ),
    }
    edges = [
        _rate_edge(cid_a, hub_id, both, decks_a),
        _rate_edge(cid_b, hub_id, both, decks_b),
    ]
    # Shared cards in a centred column (strongest at the top) so they line up and
    # stay readable, each with a single edge from the hub.
    for i, (o_canon, o_name, cnt) in enumerate(shared):
        oid = f"card:{o_canon}"
        y = (i - (len(shared) - 1) / 2) * _COL_GAP
        nodes[oid] = Node(oid, o_name, "Card", group="cooccur", pin=(_CARD_X, y))
        edges.append(_rate_edge(hub_id, oid, cnt, both))

    return Subgraph(nodes=list(nodes.values()), edges=edges)


def hidden_gems_subgraph(
    conn: ladybug.Connection,
    archetype: str | None = None,
) -> Subgraph:
    """Cards rare within their slice that nonetheless place highly.

    A gem is a card in at least ``MIN_GEM_DECKS`` decks and at most
    ``MAX_GEM_SHARE`` of the slice, whose mean placement (a normalised rank,
    lower is better) is at most ``MAX_GEM_MEAN_NORM`` (user story 14). The two
    bounds answer different questions, which is why only one of them is a share
    (ADR 0005): the floor asks "is there enough evidence to trust this?", a
    property of sample size that does not scale with the meta; the ceiling asks
    "is this still rare?", which is meaningless except relative to the slice.

    Both bounds and the placement are measured over the decks whose rank is
    known: a deck with no recorded placement cannot confirm over- or
    under-performance, so it is left out of the count and the mean entirely
    rather than padding the band. ``archetype`` narrows the slice, so "gems
    within Grixis" means cards rare among Grixis decks, not globally rare cards
    that merely appear in one; with no filter the slice is every ranked deck.
    Returns each gem with the ranked decks that run it, so its placement is
    visible. A slice with fewer than ``MIN_GEM_SLICE`` ranked decks raises
    ``SliceTooSmall``: there, the band is empty by construction, and an answer of
    "none" would read as "no gems here" when the truth is "not enough decks to
    tell". Callers should offer only the archetypes ``gem_archetypes`` lists.
    """
    # The slice is ranked decks only, so its length is the base the ceiling is a
    # share of; no separate counting query, and an archetype nobody ranked in is
    # refused below before an empty $slice can reach Kùzu (which cannot infer an
    # empty list parameter's element type and aborts rather than raising). Ladybug
    # returns cleanly on the empty list; the guard goes with #50, not #48.
    slice_ids = _ranked_deck_slice(conn, archetype)
    ranked_decks = len(slice_ids) if slice_ids is not None else _ranked_deck_total(conn)
    if ranked_decks < MIN_GEM_SLICE:
        raise SliceTooSmall(
            f"{archetype or 'this slice'} has {ranked_decks} ranked decks; "
            f"identifying a rare-but-winning card needs at least {MIN_GEM_SLICE}"
        )

    ranked = _RANKED + (" AND d.deckId IN $slice" if slice_ids is not None else "")
    params: dict = {
        "minDecks": MIN_GEM_DECKS,
        "maxNorm": MAX_GEM_MEAN_NORM,
        # The ceiling is a share of the slice's ranked size.
        "maxDecks": MAX_GEM_SHARE * ranked_decks,
    }
    if slice_ids is not None:
        params["slice"] = slice_ids

    # The rarity and the mean placement are returned, not just filtered on: they
    # are the answer to "which gems, and how well do they place", so they ride
    # out on the node rather than being recomputed downstream (issue #12).
    gems = list(rows(conn.execute(
        f"""MATCH (d:Deck)-[:CONTAINS]->(c:Card)
           WHERE {ranked}
           WITH DISTINCT c, d
           WITH c, count(d) AS decks, avg(d.placementNorm) AS meanNorm
           WHERE decks >= $minDecks AND decks <= $maxDecks
                 AND meanNorm <= $maxNorm
           RETURN c.canon, c.name, decks, meanNorm""",
        params,
    )))
    if not gems:
        return Subgraph(nodes=[], edges=[])

    nodes: dict[str, Node] = {
        f"card:{canon}": Node(
            f"card:{canon}", name, "Card", decks=decks, mean_norm=mean_norm
        )
        for canon, name, decks, mean_norm in gems
    }
    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()
    edge_params: dict = {"gems": [canon for canon, *_ in gems]}
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


def pilot_affinity_subgraph(conn: ladybug.Connection, pilot: str) -> Subgraph:
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
        edges.append(_plays_edge(pilot_id, mid, len(events)))
    for a_tag, events in sorted(arch_events.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        aid = f"arch:{a_tag}"
        nodes.append(Node(aid, arch_names[a_tag], "Archetype", weight=len(events)))
    for (macro, a_tag), events in sorted(
        macro_arch_events.items(), key=lambda kv: (kv[0][0], -len(kv[1]), kv[0][1])
    ):
        edges.append(_plays_edge(f"macro:{macro}", f"arch:{a_tag}", len(events)))

    return Subgraph(nodes=nodes, edges=edges)


def gem_archetypes(conn: ladybug.Connection) -> list[tuple[str, str]]:
    """``(name, tag)`` for the archetypes whose slice can support a gem claim.

    The gem view offers these and no others, so a slice too small to answer is
    never put to the user as though it might (ADR 0005). Ordered by name, to
    drop straight into a dropdown. Counts the same population
    ``_ranked_deck_slice`` does, so every tag offered is one the gem query
    accepts; ``test_gem_archetypes_offer_only_the_slices_that_can_answer`` holds
    the two to that promise.
    """
    return [(name, tag) for name, tag in rows(conn.execute(
        f"""MATCH (d:Deck)-[:HAS_ARCHETYPE]->(a:Archetype)
            WHERE {_RANKED}
            WITH a, count(DISTINCT d) AS ranked
            WHERE ranked >= $minSlice
            RETURN a.name, a.tag ORDER BY a.name""",
        {"minSlice": MIN_GEM_SLICE},
    ))]


def pilot_catalogue(conn: ladybug.Connection) -> list[tuple[str, str]]:
    """``(displayName, pilot)`` for every pilot, in label order for a dropdown."""
    return [(name, key) for name, key in rows(conn.execute(
        "MATCH (p:Pilot) RETURN p.displayName, p.pilot ORDER BY p.displayName"
    ))]


def card_catalogue(conn: ladybug.Connection) -> list[tuple[str, str]]:
    """``(name, canon)`` for every card, in label order for a dropdown."""
    return [(name, canon) for name, canon in rows(conn.execute(
        "MATCH (c:Card) RETURN c.name, c.canon ORDER BY c.name"
    ))]


def _ranked_deck_slice(conn: ladybug.Connection, archetype: str | None) -> list[str] | None:
    """The ranked deck ids the gem hunt runs within, by archetype tag.

    ``None`` means no filter (every ranked deck), so the caller can skip the id
    list rather than enumerate the whole graph. Unranked decks are excluded here
    rather than by the callers, so the slice can be counted with ``len`` and every
    query downstream agrees on the same population.
    """
    if archetype is None:
        return None
    return [row[0] for row in rows(conn.execute(
        f"""MATCH (d:Deck)-[:HAS_ARCHETYPE]->(:Archetype {{tag: $v}})
            WHERE {_RANKED}
            RETURN DISTINCT d.deckId""",
        {"v": archetype},
    ))]


def _ranked_deck_total(conn: ladybug.Connection) -> int:
    """How many decks in the whole graph carry a placement: the base the gem
    ceiling is a share of when no archetype narrows the slice."""
    return next(rows(conn.execute(
        f"MATCH (d:Deck) WHERE {_RANKED} RETURN count(d)"
    )))[0]


def run_query(conn: ladybug.Connection, spec: QuerySpec) -> Subgraph:
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
        case CardCooccurrence(canon, canon2, top_n, drop_lands):
            return card_cooccurrence_subgraph(conn, canon, canon2, top_n, drop_lands)
        case HiddenGems(archetype):
            return hidden_gems_subgraph(conn, archetype)
        case PilotAffinity(pilot):
            return pilot_affinity_subgraph(conn, pilot)
        case _:
            raise TypeError(f"unknown query spec: {spec!r}")
