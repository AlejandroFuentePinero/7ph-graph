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
import statistics
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

import ladybug

from graph7ph import numfmt
from graph7ph.db import rows

Kind = Literal[
    "Pilot", "Deck", "Card", "Archetype", "Macro", "Event", "Placement", "Intersection"
]

# The hidden-gem rule, fixed rather than exposed as controls (ADR 0012): the
# question "which rare cards overperform?" has one answer, not one per dial. Every
# one of the four was swept rather than chosen, over 144 combinations scored on
# genuine finds (found minus what chance alone would find) under the node budget
# below, and this cell won by a clear margin (ADR 0020). The score is exact rather
# than simulated, the null being a convolution of hypergeometric tails, so a cell
# that wins by a tenth of a find wins by a tenth of a find.
MIN_GEM_DECKS = 5  # trust floor: an absolute count, so it holds at any slice size
MAX_GEM_SHARE = 0.15  # rarity ceiling: a share, so it means the same in any slice
GEM_TOP_CUT = 0.20  # the archetype's own best fifth, the cut a gem must crowd
MAX_GEM_LUCK = 0.010  # how often chance alone may explain that crowding

# How much of a deck's finish is explained by who piloted it, over every ranked deck
# (one-way random-effects ICC of `placementNorm` grouped by pilot, unbalanced, 4567
# decks over 1075 pilots). Measured, not chosen, and reported by `scripts/gem_sweep.py`
# so it is re-read whenever the corpus grows.
#
# It is here because the null above counts decks and the claim needs people. A card in
# seven of an archetype's best decks reads as seven players independently arriving at
# the same idea, which is evidence; the same seven decks from three players is one
# player's habit counted several times, which is not. Strong pilots finish high
# repeatedly and carry every card in the 60 up with them, including the ones doing
# nothing, so a pet card inherits its pilot's skill as though it were its own.
#
# Stratifying by event does not touch this: ADR 0004 puts one deck per pilot per event,
# so within a stratum every deck is already a different player and the correlation lives
# entirely across events. It is a second, independent break of the same exchangeability
# assumption ADR 0020 leans on.
PILOT_ICC = 0.226

# Held once: `NormalDist` carries no state between calls and the deflation asks it for
# two quantiles per screened card.
_NORMAL = statistics.NormalDist()

# The two bounds cross here: below this many ranked decks the ceiling falls under
# the floor and the rule is empty by construction, because "rare" and "attested
# by 5 decks" are contradictory in a small slice (5 decks IS a seventh of a
# 34-deck archetype). That is not a bug to paper over: the slice genuinely cannot
# support a gem claim, so it is skipped rather than lowering the floor and
# reporting noise. Rounded UP, never to nearest: the smallest slice admitted must
# satisfy `MIN_GEM_DECKS <= MAX_GEM_SHARE * MIN_GEM_SLICE`, and rounding down
# would admit a slice whose band is still inverted, restoring the bug this
# prevents.
MIN_GEM_SLICE = math.ceil(MIN_GEM_DECKS / MAX_GEM_SHARE)

# The node budget the drawn list is cut to. The tab draws every gem in the format
# at once and has no control to narrow with, so the list cannot be allowed to
# outgrow the canvas as the corpus fills: `MAX_GEM_LUCK` is a threshold, not a
# length, and admits more cards from more decks on every ingest. Held here rather
# than imported because `explore` reads this module and the import would close a
# cycle; `test_the_gem_node_budget_is_the_render_threshold` holds the two equal.
MAX_GEM_NODES = 250

# There is deliberately no cap on how many of a gem's top-cut decks the picture draws,
# so what the card node counts and what the canvas shows are the same set and the
# table's "In top" column can be checked against the picture rather than explained away.
#
# One was carried until the null was stratified by event, on the grounds that the deck
# layer collapses: many of an archetype's best decks run the same one card, so their
# nodes share a neighbourhood, a force layout has nothing to separate them by, and
# they settle on one another with their labels on top. That collapse is real and the cap
# was not what fixed it. Measured on the built graph against the current list, a cap of
# five ties every one of the 27 decks it draws, where drawing all 38 ties 33 of them
# (87%). Collapsing is a property of how gems overlap inside an archetype rather than of
# how many decks are drawn, and the capped picture is if anything the worse of the two,
# so the cap bought 11 fewer nodes and no legibility, against a picture that disagreed
# with its own table.
# The node budget above is what guards the canvas, and it now cuts the list of gems
# rather than the evidence behind each one.


# A deck with no recorded placement cannot confirm over- or under-performance,
# so the gem hunt ignores it. Written once and shared by every query that has to
# agree on what "ranked" means: the slice, the cut, and the offered archetypes.
_RANKED = "d.placementNorm IS NOT NULL"


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
    # size, a card's play-rate), and the base that count is a share of where it is
    # one. The renderer ignores them; a label is for display, these are for a consumer
    # that wants the value, so v2's tool layer need not re-derive what was computed
    # here (issue #12).
    decks: int | None = None
    total_decks: int | None = None
    # How many distinct pilots stand behind those decks. A count, not a rate: it says
    # whether a record is the format's or one pilot's, which the deck count cannot,
    # since a deck is steered by whoever piloted it and pilot level is the strongest
    # reliable signal in this data (issue #175). Carried on a gem, where a median 44%
    # of the edge over the slice is explained by the pilots alone (issue #176).
    pilots: int | None = None
    # How many of those decks are in the archetype's own top cut: a gem's whole
    # evidence, against ``decks`` as its rarity and ``total_decks`` as the archetype
    # it is rare in (issue #184).
    top_decks: int | None = None
    # How often chance alone would put this many of a card this rare in that cut: the
    # exact tail the gem was admitted on (`_gem_tails`, read inside the events the card
    # turned up at), riding out as a value rather than only as the filter that produced
    # it. Low is the claim.
    gem_luck: float | None = None


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
    # How many of the drawn nodes chance alone would have put here. A property of the
    # whole list rather than of any node in it, because it sums over every card the
    # rule screened and rejected as well as the ones it kept, which no drawn node can
    # see. Carried only by the gem view, where a list admitted on a probability
    # threshold is a list with a known false-positive count and saying so is the
    # difference between a finding and a shortlist (issue #184); ``None`` everywhere
    # else, where nothing was screened.
    expected_by_luck: float | None = None


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
    """The gem view takes no parameters: it is one unfiltered picture of the format's
    concentrated rare cards, discovered inside each archetype (issue #184)."""


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
    shared ranks never collapse decks together, and one this project decided rather
    than the source counting it carries the imputed mark.

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
                  d.placement, d.placementImputed""",
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

    for (pilot_key, pilot_name, event, deck_id, deck_name, placement,
         placement_rule) in records:
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
            # A rank this project decided rather than one the source counted takes the
            # app's one imputed mark (issue #199), the same glyph and the same
            # predicate the head-to-head hover uses (issue #166): a non-null rule
            # means the value is ours, whether a title carried it or the cohort did.
            # The rule itself stays queryable rather than drawn, since the reader is
            # being told the rank is ours, not which pass minted it. ``none`` never
            # reaches here, as a rule that recovered nothing leaves the placement null
            # and draws no node at all.
            mark = numfmt.IMPUTED_MARK if placement_rule is not None else ""
            nodes.setdefault(
                plid,
                Node(plid, _ordinal(placement) + mark, "Placement", group=owner(pid)),
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
    figure. Counted by the **primary** tag alone, the rule ``CONTEXT.md`` fixes for
    every archetype figure (ADR 0023): a slice pooling every tag is a mixture of
    other engines rather than a larger sample of this one, so the denominator, the
    numerator and the grouping macro all read the decks this archetype is the
    engine of. Every archetype the card appears in is drawn, strongest adoption first
    at whole-percent resolution, then the larger archetype, so a staple that runs
    everywhere may exceed the render limit and refine rather than draw. Pilot and
    event are left out on purpose: this is a card-level view.

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
        "MATCH (a:Archetype)<-[:HAS_ARCHETYPE {isPrimary: true}]-(d:Deck) RETURN a.tag, a.name, count(DISTINCT d)"
    ))}
    arch_run = dict(rows(conn.execute(
        f"""MATCH (a:Archetype)<-[:HAS_ARCHETYPE {{isPrimary: true}}]-(d:Deck)-[cont:CONTAINS]->(:Card {{canon: $canon}})
            {where} RETURN a.tag, count(DISTINCT d)""", params
    )))
    # The macro where each archetype's card-running decks sit, so the grouping
    # macro always contains decks running the card. Ties resolve on macro name, so
    # the choice is stable regardless of the query's row order.
    dominant: dict[str, tuple[int, str]] = {}
    for tag, macro, n in rows(conn.execute(
        f"""MATCH (a:Archetype)<-[:HAS_ARCHETYPE {{isPrimary: true}}]-(d:Deck)-[cont:CONTAINS]->(:Card {{canon: $canon}})
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

    # Every archetype the card appears in, strongest adoption first at whole-percent
    # resolution, then the larger archetype, then name. Only at whole-percent
    # resolution because pct() rounds to an int before it is used as a sort key, so
    # two adoption rates that round to the same percent fall through to the size and
    # name tie-break and the weaker one can be drawn first. That is invisible rather
    # than misleading: 9169 of 9169 pairs drawn weaker-first carry identical drawn
    # labels, and no surface renders this order as a rank. Sorting on the unrounded
    # rate was costed at 16,703 archetype positions moved across 2398 of 4995 cards
    # for 0 changed labels, so it was not taken. An archetype without a grouping
    # macro (a card-running deck missing a macro) is skipped, so the edge target
    # below is always a macro node that exists.
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
    # Strongest adoption first at whole-percent resolution, then name, as for the
    # archetypes below: this sort carries the same rounded pct(), so two macros
    # whose adoption rates round to the same percent are separated only by the name
    # tie-break, and without it their order falls out of an unordered set, so the
    # same query on the same graph answers differently between runs.
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
    res = conn.execute(
        f"""MATCH (card:Card {{canon: $canon}})<-[a:CONTAINS]-(d:Deck)-[b:CONTAINS]->(other:Card)
           WHERE other.canon <> card.canon AND a.board = b.board{_no_lands("other", drop_lands)}
           WITH other, count(DISTINCT d) AS shared
           RETURN other.canon, other.name, shared
           ORDER BY shared DESC, other.name, other.canon
           LIMIT $topN""",
        {"canon": canon, "topN": top_n},
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
    res = conn.execute(
        f"""MATCH (a:Card {{canon: $a}})<-[:CONTAINS]-(d:Deck)-[:CONTAINS]->(b:Card {{canon: $b}})
            MATCH (d)-[:CONTAINS]->(p:Card)
            WHERE p.canon <> $a AND p.canon <> $b{_no_lands("p", drop_lands)}
            WITH p, count(DISTINCT d) AS shared
            RETURN p.canon, p.name, shared
            ORDER BY shared DESC, p.name, p.canon
            LIMIT $topN""",
        {"a": canon_a, "b": canon_b, "topN": top_n},
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
    own decks where the partner sits in the *same board* as the seed. The two
    terms are scoped differently on purpose: the numerator counts same-board
    pairings (``_cooccurrence_partners``) while the denominator counts the seed's
    decks across both boards (``_card_and_deck_count``), so a deck running both
    cards in opposite boards still counts in the denominator but not in the
    numerator, and the rate reads below deck-level membership. That is the
    deliberate scoping ``_cooccurrence_partners`` argues for (a card in the main
    and another in the side are not a functional pairing) rather than a mismatch
    to repair: 2899 of the 6000 edges drawn over the 400 seeds the audit swept
    differ from the deck-level reading, 425 of them by 25 percentage points or
    more, and moving the numerator to deck level was measured at those same 2899
    relabelled edges plus a different top-15 node set for 391 of 400 seeds. The
    number therefore stands and this sentence is what moved. The top ``top_n``
    partners by that rate are kept, so a popular seed refines to its strongest
    packages rather than flooding the view with every card it ever shared a deck
    with.

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
    # The deck count on the hub is the denominator every hub-to-card edge is read
    # against, true of 9780 of 9780 measured hub edges. The two seed edges are the
    # exception: each is read against that seed's own deck count, which differs
    # from the hub count on 1299 of 1312 measured seed edges and is rendered on no
    # surface, so a reader applying the hub's number to a seed edge reads it wrong.
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


# Cached because a format holds far fewer distinct `(total, top, decks)` shapes than it
# holds cards, and the screen asks for the same shape once per card sharing it.
@lru_cache(maxsize=None)
def _hypergeometric_pmf(total: int, top: int, decks: int) -> tuple[float, ...]:
    """``P(exactly i land in the cut)``, drawing ``decks`` of ``total`` that hold ``top``.

    One stratum of :func:`_gem_tails`. Exact, not simulated: integer binomials divided
    once, so nothing is drawn, there is no seed to hold, and two calls on one artifact
    agree bit for bit.
    """
    base = math.comb(total, decks)
    # `comb` is 0 where a term is impossible (more hits than the cut holds, or more
    # misses than sit outside it), so the range needs no bounds of its own.
    return tuple(math.comb(top, i) * math.comb(total - top, decks - i) / base
                 for i in range(decks + 1))


# One stratum of the null: an event, the archetype's ranked decks played there, how many
# of those sit in the archetype's cut, and how many of them the card is in.
Stratum = tuple[int, int, int]


@lru_cache(maxsize=None)
def _gem_tails(strata: tuple[Stratum, ...]) -> tuple[float, ...]:
    """``P(at least i land in the cut)`` for every ``i``, under the event-stratified null.

    The null the whole rule rests on, and the one thing about it that is not obvious:
    it is asked **inside each event**, not across the archetype. A card that does
    nothing sits at the events it sits at, and within each of those it is a random one
    of that archetype's decks there, so its hits at one event are hypergeometric and
    the whole is their convolution. Entry ``i`` is the chance at least ``i`` land in
    the cut, which is what an observed count is read against and, at the other end,
    how often chance alone clears the bar.

    **Inside the event, because the event decides most of whether a deck is in the
    cut.** 26 of the 107 events publish only a top cut rather than a field: SSWam
    records 7 decks against a field of 88, so all 7 score between 0.00 and 0.05 while
    the ~81 entrants who missed the bracket carry no decklist here at all. That is not
    a field-size error and the correction ADR 0015 makes does not reach it, the field
    being right and the record short. Correcting a field in fact *strengthens* the
    tilt, since recognising a bracket as the top of a 24-player field is recognising
    those decks as good: Pats Birthday Brawl's mean norm moves 0.375 to 0.114 under
    Rule B. Measured over the offered archetypes, a deck from such an event lands in
    its archetype's cut 70% of the time against 18% for a deck from a full-coverage
    event, so two cards of the same rarity are not exchangeable and an unstratified
    tail is not the probability it prints.

    That was ADR 0020's one wrong claim, that "the hypergeometric null is exact
    whatever decks the cut is drawn from". Simulating the unstratified screen under
    this null puts its true expected-by-luck at 9.3 where it reported 7.0, and 13 of
    the 20 cards it admitted do not clear the bar once the question is asked inside
    the event. What it costs is list length rather than evidence: 12 cards at 3.5
    expected by luck, against 20 at a true 9.3. Charging those 12 for their pilots
    (:func:`_pilot_deflated`) then takes the shipped list to 7.

    A card whose decks all sit at one event, or in an archetype fielding one deck per
    event, folds to forced strata and comes back 1.0. That is an answer rather than a
    failure: there is nothing to compare the card against, so nothing about it is being
    said.

    ``strata`` arrives canonically sorted, which the cache needs and float addition
    needs: the convolution is not associative, so an order that moved between calls
    would move the last digits of a number the FAQ tells a reader to take as settled.
    """
    dist = (1.0,)
    for stratum in strata:
        pmf = _hypergeometric_pmf(*stratum)
        folded = [0.0] * (len(dist) + len(pmf) - 1)
        for i, carried in enumerate(dist):
            if not carried:
                continue
            for j, chance in enumerate(pmf):
                folded[i + j] += carried * chance
        dist = tuple(folded)
    tails = [0.0] * (len(dist) + 1)
    for i in range(len(dist) - 1, -1, -1):
        tails[i] = tails[i + 1] + dist[i]
    return tuple(tails[:len(dist)])


def _pilot_deflated(tail: float, decks: int, pilots: int) -> float:
    """One tail, charged for the pilots behind it rather than the decks.

    The correction is the survey-statistics one: a design effect
    ``1 + (mean decks per pilot - 1) * PILOT_ICC`` says how many independent results
    this card's decks are really worth, and the tail is deflated by it. A card whose
    decks are all different players has a mean of 1, a design effect of 1, and comes
    back untouched, which is the point: only repetition is charged for.

    The deflation runs on the z scale, the standard Rao-Scott move, because the tail
    itself has no variance to divide. That is a normal approximation to a discrete
    tail, so it is honest about direction and size and not about the fourth digit: it
    is here to stop a card resting on one player's habit, not to price one exactly.

    What it does is pull the statistic **toward the null**, which is the whole content
    of a reduced effective sample size, so read "deflated" as "moved toward 0.5" and not
    as "made larger". Below 0.5 the two are the same thing and that is the only region
    the rule reads: a tail is only ever compared against :data:`MAX_GEM_LUCK`. Above
    0.5 the tail shrinks instead (0.8 comes back 0.75), which is the same correction
    applied to a card finishing *under* expectation and is equally right, but it means
    a caller must not treat this as a monotone one-way inflation over the whole 0-to-1
    range. Nothing here does; the caller looks for the first tail under a bar of 0.01.
    """
    if pilots >= decks:
        return tail
    design = 1 + (decks / pilots - 1) * PILOT_ICC
    z = _NORMAL.inv_cdf(1 - min(max(tail, 1e-15), 1 - 1e-15))
    return 1 - _NORMAL.cdf(z / math.sqrt(design))


def _luck_of_clearing(
    strata: tuple[Stratum, ...], decks: int, pilots: int, threshold: float
) -> float:
    """How often chance alone admits a card of this shape at ``threshold``.

    Not ``threshold`` itself, and the difference is why the count is summed this way.
    A card in five decks has only six possible outcomes, so the smallest tail it can
    reach may sit well under the bar (five of five in a 20% cut of 200 lands at 0.00026,
    against a bar of :data:`MAX_GEM_LUCK`) while a card in twenty has finer steps. Each
    card's own chance of clearing
    is its first tail at or under the threshold, since the tails fall as the hit count
    rises; ``0.0`` for a card so common that even all of its decks in the cut would
    not be surprising, and for one whose strata leave it nothing to vary.

    Deflated exactly as the screen deflates, so the count is summed over the bar the
    screen actually applied rather than the one it would have without pilots.
    """
    return next(
        (deflated
         for tail in _gem_tails(strata)
         if (deflated := _pilot_deflated(tail, decks, pilots)) <= threshold),
        0.0,
    )


@dataclass(frozen=True)
class _Gem:
    """One admitted card: what it rests on, and what drawing it would cost."""

    luck: float
    tag: str
    canon: str
    name: str
    arch_name: str
    total: int  # the archetype's ranked decks, the base its rarity is a share of
    decks: int
    # Its decks in the archetype's top cut, in that cut's own order, so the best of them
    # is the front of the tuple. This is the whole claim and the whole picture at once:
    # every one of these is drawn, so `top_decks` on the card node counts exactly the
    # deck nodes a reader can see hanging off it.
    top: tuple[str, ...]
    pilots: int


@dataclass(frozen=True)
class _Slice:
    """One archetype big enough to ask: its cut, and the strata a card is read against.

    ``cut`` is in its own order, best finish first, so a gem's best decks are a prefix
    of it and no second reading of the finishes is needed to draw them. ``event`` and
    ``field`` are what :func:`_gem_tails` needs and nothing else reads: which event each
    of the archetype's ranked decks was played at, and, per event, how many of the
    archetype's decks sat there and how many of those are in the cut.
    """

    name: str
    total: int
    cut: tuple[str, ...]
    event: dict[str, str]  # deck id -> its event
    field: dict[str, tuple[int, int]]  # event -> (the archetype's decks, of them in cut)


def _gem_slices(conn: ladybug.Connection) -> dict[str, _Slice]:
    """Each archetype big enough to ask, with the cut and strata a gem is scored against.

    The cut is the leading :data:`GEM_TOP_CUT` of the archetype's ranked decks by
    finish. Ties are broken on the deck id, so two decks that finished identically can
    land on opposite sides of the cut: arbitrary, and deliberate, since a cut has to
    fall somewhere and only a deterministic rule makes one artifact draw one answer.
    It is also close to free here: of the 40 slices and 711 cut decks on the built
    graph, 2 slices have their cut boundary land on a tie at all, 4 decks sharing those
    two boundary finishes between them, and no gem changes under a randomised
    tie-break.
    Counted by the **primary** tag alone, the aggregate rule ``CONTEXT.md`` fixes for
    exactly this reason: a deck may carry several weighted archetype tags, so counting
    every one of them sums to about 160% of the record and would put one deck in several
    archetypes' cuts at once. Archetypes under :data:`MIN_GEM_SLICE` are dropped here
    rather than answered for,
    because below it the rarity ceiling has fallen under the trust floor and the rule
    is empty by construction (ADR 0012). No slice is ever put to a reader now, so this
    is a silent skip rather than the refusal that rule used to raise.

    The event rides along because the null is asked inside it (:func:`_gem_tails`), and
    it is read here rather than in a query of its own so that the decks the strata are
    counted over and the decks the cut is drawn from are the same rows.
    """
    ranked: dict[str, tuple[str, list[tuple[float, str]], dict[str, str]]] = {}
    for tag, name, deck_id, norm, event in rows(conn.execute(
        f"""MATCH (d:Deck)-[:HAS_ARCHETYPE {{isPrimary: true}}]->(a:Archetype),
                  (d)-[:PLAYED_AT]->(e:Event)
           WHERE {_RANKED}
           RETURN a.tag, a.name, d.deckId, d.placementNorm, e.event"""
    )):
        held = ranked.setdefault(tag, (name, [], {}))
        held[1].append((norm, deck_id))
        held[2][deck_id] = event
    slices = {}
    for tag, (name, decks, event) in ranked.items():
        if len(decks) < MIN_GEM_SLICE:
            continue
        decks.sort()
        cut = tuple(deck_id for _, deck_id in decks[:max(1, round(len(decks) * GEM_TOP_CUT))])
        in_cut = set(cut)
        field: dict[str, tuple[int, int]] = {}
        for _, deck_id in decks:
            held, topped = field.get(event[deck_id], (0, 0))
            field[event[deck_id]] = (held + 1, topped + (deck_id in in_cut))
        slices[tag] = _Slice(name, len(decks), cut, event, field)
    return slices


def _gem_candidates(
    conn: ladybug.Connection, tags: set[str]
) -> dict[tuple[str, str], tuple[str, set[str]]]:
    """``(tag, canon) -> (card name, that archetype's decks running it)``.

    By the primary tag, the same rule :func:`_gem_slices` counts under, so a card's
    rarity is a share of the decks the cut was drawn from. One scan over every ranked
    deck-card pair rather than a query per archetype: the
    hunt screens every card of every archetype, so a per-slice version would run one
    query per archetype to read the same rows back. Pairs are deduplicated to decks,
    since the format is singleton and a card the source lists in both boards of one
    deck is one deck running it.
    """
    candidates: dict[tuple[str, str], tuple[str, set[str]]] = {}
    for tag, canon, name, deck_id in rows(conn.execute(
        f"""MATCH (a:Archetype)<-[:HAS_ARCHETYPE {{isPrimary: true}}]-(d:Deck)
                 -[:CONTAINS]->(c:Card)
           WHERE {_RANKED} AND a.tag IN $tags
           WITH DISTINCT a, c, d
           RETURN a.tag, c.canon, c.name, d.deckId""",
        {"tags": sorted(tags)},
    )):
        candidates.setdefault((tag, canon), (name, set()))[1].add(deck_id)
    return candidates


def _deck_pilots(conn: ladybug.Connection) -> dict[str, str]:
    """Who piloted each ranked deck.

    A gem's distinct pilots are counted off the deck ids it already holds rather than
    by a second aggregate over cards, so the two counts cannot describe different sets
    of decks: the pilot count exists precisely to qualify the deck count beside it. It
    matters because a deck's finish is steered by whoever piloted it and pilot level is
    the strongest reliable signal in this data (issue #175), so one pilot's six decks
    and six pilots' six decks are different evidence for the same card.
    """
    return dict(rows(conn.execute(
        f"MATCH (d:Deck)-[:PILOTED_BY]->(p:Pilot) WHERE {_RANKED} RETURN d.deckId, p.pilot"
    )))


def _strata(slice_: _Slice, deck_ids: set[str]) -> tuple[Stratum, ...]:
    """One card's shape under the null: a stratum per event it turned up at.

    Sorted, so the tuple is canonical for :func:`_gem_tails`'s cache and for the
    float determinism its convolution needs. Two cards with the same shape are the
    same question whichever events they happen to name, so the event itself is not
    part of the key.
    """
    here: dict[str, int] = {}
    for deck_id in deck_ids:
        event = slice_.event[deck_id]
        here[event] = here.get(event, 0) + 1
    return tuple(sorted(
        (*slice_.field[event], drawn) for event, drawn in here.items()
    ))


def _screen_gems(
    conn: ladybug.Connection,
) -> tuple[list[_Gem], list[tuple[tuple[Stratum, ...], int, int]]]:
    """Every card the rule admits, and the shape of every card it looked at.

    The second return is what makes the first honest. It is one shape per *screened*
    card, admitted or not, and it is the population the expected-by-luck count is
    summed over: the cards that failed are most of the evidence about how often this
    bar is cleared by accident, so a list of survivors alone cannot say how many
    survivors are accidents. Each carries its deck and pilot counts, since the bar a
    card was held to depends on both (:func:`_pilot_deflated`).
    """
    slices = _gem_slices(conn)
    candidates = _gem_candidates(conn, set(slices))
    pilots = _deck_pilots(conn)
    found: list[_Gem] = []
    screened: list[tuple[tuple[Stratum, ...], int, int]] = []
    for (tag, canon), (name, deck_ids) in candidates.items():
        slice_ = slices[tag]
        count = len(deck_ids)
        if count < MIN_GEM_DECKS or count > MAX_GEM_SHARE * slice_.total:
            continue
        strata = _strata(slice_, deck_ids)
        pilot_count = len({pilots[deck_id] for deck_id in deck_ids})
        screened.append((strata, count, pilot_count))
        # Kept in the cut's order, so the best of them is the front of the tuple.
        top = tuple(deck_id for deck_id in slice_.cut if deck_id in deck_ids)
        luck = _pilot_deflated(_gem_tails(strata)[len(top)], count, pilot_count)
        if luck <= MAX_GEM_LUCK:
            found.append(_Gem(
                luck, tag, canon, name, slice_.name, slice_.total, count, top,
                pilot_count,
            ))
    # Longest odds first, then archetype and card, so one artifact draws one list and
    # the node budget below cuts from the weakest end.
    found.sort(key=lambda gem: (gem.luck, gem.tag, gem.canon))
    return found, screened


def _fit_to_budget(found: list[_Gem]) -> list[_Gem]:
    """The leading gems that fit within :data:`MAX_GEM_NODES`, in their own order.

    A gem costs a card node, its archetype where no stronger gem already brought it,
    and each of its top-cut decks not already drawn, so what a gem costs depends on what
    precedes it. Taken as a prefix and stopped at the first gem that does not fit,
    rather than packed with whatever still would: the drawn list has to stay "the
    strongest N", or the picture is of a set the reader cannot name.

    This is the only cap on the picture now, and it cuts whole gems rather than the
    decks behind one, which is the trade worth naming: a shorter list of fully evidenced
    findings beats a longer list whose deck layer is a sample the reader cannot see the
    edge of. It does not bind today, at 49 nodes against 250.
    """
    kept: list[_Gem] = []
    archetypes: set[str] = set()
    decks: set[str] = set()
    for gem in found:
        cost = 1 + (gem.tag not in archetypes) + len(set(gem.top) - decks)
        if len(kept) + len(archetypes) + len(decks) + cost > MAX_GEM_NODES:
            break
        kept.append(gem)
        archetypes.add(gem.tag)
        decks |= set(gem.top)
    return kept


def hidden_gems_subgraph(conn: ladybug.Connection) -> Subgraph:
    """The format's rare cards that crowd their own archetype's best decks.

    A gem is a card in at least :data:`MIN_GEM_DECKS` of one archetype's ranked decks
    and at most :data:`MAX_GEM_SHARE` of them, with enough of those decks in that
    archetype's own top :data:`GEM_TOP_CUT` that chance alone would manage it no more
    than :data:`MAX_GEM_LUCK` of the time (user story 14, ADR 0020). Every term is
    measured inside the archetype: how rare the card is there, how its decks finished
    against that archetype's other decks, and how surprising the two together are.
    Nothing is compared across archetypes except the probability bar, which every
    drawn card clears identically.

    That last question is asked one level finer still, **inside each event** the card
    turned up at (:func:`_gem_tails`), because a quarter of the corpus's events publish
    only a top cut and a deck from one lands in its archetype's cut 70% of the time
    against 18% for a deck from a full field. Crowding the cut by having played where
    only winners were recorded is not the card's doing, and an unstratified tail books
    it as though it were.

    This replaces the absolute performance bar ADR 0012 fixed and #176 qualified,
    because that bar tracked a gem's *archetype* rather than the card: an average card
    in Storm scored 46% before its own record was read, and 39 of the 44 archetypes
    with gems could not put a single card over 10%. Measured inside the archetype, the
    archetype cancels.

    Only decks with a recorded finish count, for either bound or the cut: a deck with
    no placement cannot say whether a card over- or under-performed, so it pads
    nothing. The picture is ``Archetype -> Card <- Deck``, drawing every one of a
    gem's top-cut decks so each claim on screen can be opened and read, and so the
    ``In top`` column counts exactly the deck nodes hanging off that card. A drawn deck
    is wired to every gem of its archetype it runs, so what is on screen is true of what
    is on screen. A card that is a gem in two archetypes is two nodes, since those are
    two findings resting on two sets of decks.

    The list is cut to :data:`MAX_GEM_NODES` (:func:`_fit_to_budget`) and carries
    ``expected_by_luck``: how many of the drawn cards chance alone would have put
    there. For a list admitted on a probability threshold that number exists whether or
    not anything reads it, so it rides on the answer and the baseline oracle grades it:
    a rule whose false-positive rate moved is a changed rule, whatever the list looks
    like. The app states the magnitude in the FAQ rather than beside the table, since a
    count of false positives raised next to the rows asks which ones and no row can be
    named (ADR 0020).
    """
    found, screened = _screen_gems(conn)
    gems = _fit_to_budget(found)
    # The threshold the drawn list actually ran at: the bar, unless the node budget cut
    # the list short, in which case it is the weakest drawn gem's own odds. Read at
    # what the reader is looking at, so the count describes the list on screen rather
    # than the longer one the rule would have admitted. A budget that cuts through a
    # tie drops cards at the threshold it reports, so the count is then a shade
    # generous, which is the right direction for a caveat to err in.
    threshold = gems[-1].luck if gems and len(gems) < len(found) else MAX_GEM_LUCK
    # Summed in the terms' own order, not the order the rows arrived in. Float addition
    # is not associative and Ladybug hands the same rows back in a different order
    # between calls, so summing ~1700 tails as they come reports a number whose last
    # digits move between two runs over one artifact. The drift is ~5e-15 and would
    # never trip the oracle's tolerance, which is exactly the problem: the FAQ tells a
    # reader that a number which moved means the evidence moved.
    expected = sum(sorted(
        _luck_of_clearing(shape, decks, pilots, threshold)
        for shape, decks, pilots in screened
    ))

    nodes: list[Node] = []
    edges: list[Edge] = []
    seen: set[str] = set()
    for gem in gems:
        arch_id, card_id = f"arch:{gem.tag}", f"card:{gem.tag}:{gem.canon}"
        if arch_id not in seen:
            seen.add(arch_id)
            nodes.append(Node(arch_id, gem.arch_name, "Archetype", decks=gem.total))
        nodes.append(Node(
            card_id, gem.name, "Card", decks=gem.decks, total_decks=gem.total,
            pilots=gem.pilots, top_decks=len(gem.top), gem_luck=gem.luck,
        ))
        edges.append(Edge(
            arch_id, card_id, f"IN:{gem.decks} of {gem.total} decks",
            decks=gem.decks, total_decks=gem.total,
        ))
    if not gems:
        return Subgraph(nodes=nodes, edges=edges, expected_by_luck=expected)

    # The drawn decks, read once for their labels and the board each gem sits in. Keyed
    # through the gem's own (archetype, card) pairs, so a deck that runs a card which
    # is a gem in some *other* archetype is not wired to that other archetype's node.
    #
    # Labelled by pilot and finish rather than by ``d.name``, which everywhere else is
    # the deck's whole Moxfield title ("05th-8th Max A - Bant Academy - JoltIQ"). This
    # view draws dozens of decks at once around a handful of cards, and at that density
    # forty-character titles overlap into an unreadable mat: the picture's job is to
    # show *which* good decks run a card, and "who, and how well they finished" is that
    # in four words. The title is one click away on Moxfield.
    # A drawn deck is wired to every gem of its archetype it runs, not only to the one
    # that brought it in. Otherwise the picture shows a deck that visibly does not run a
    # card it runs, and the overlaps between gems are exactly what it is drawn for. This
    # adds no node: every drawn deck is inside its archetype's cut already, so a gem it
    # runs holds it in `top` by construction.
    drawn = {deck_id for gem in gems for deck_id in gem.top}
    decks = sorted(drawn)
    by_deck: dict[str, str] = {}
    boards: dict[tuple[str, str], set[str]] = {}
    for deck_id, pilot, placement, canon, board in rows(conn.execute(
        """MATCH (p:Pilot)<-[:PILOTED_BY]-(d:Deck)-[ct:CONTAINS]->(c:Card)
           WHERE d.deckId IN $decks AND c.canon IN $gems
           RETURN d.deckId, p.displayName, d.placement, c.canon, ct.board""",
        {"decks": decks, "gems": sorted({gem.canon for gem in gems})},
    )):
        by_deck[deck_id] = (
            f"{pilot} · {_ordinal(placement)}" if placement is not None else pilot
        )
        boards.setdefault((deck_id, canon), set()).add(board)
    for gem in gems:
        for deck_id in (deck_id for deck_id in gem.top if deck_id in drawn):
            node_id = f"deck:{deck_id}"
            if node_id not in seen:
                seen.add(node_id)
                nodes.append(Node(node_id, by_deck[deck_id], "Deck"))
            board = "/".join(sorted(boards[(deck_id, gem.canon)]))
            edges.append(Edge(
                node_id, f"card:{gem.tag}:{gem.canon}", f"CONTAINS:{board}"
            ))
    return Subgraph(nodes=nodes, edges=edges, expected_by_luck=expected)


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
    shared node with an edge from each. Counted by the **primary** tag alone
    (ADR 0023): crediting a pilot with every tag their decks carried read half the
    specialists in the record as generalists, which is the one thing this view is
    for. The pilot is keyed on the upstream id but labelled by display name.
    """
    res = conn.execute(
        """MATCH (p:Pilot {pilot: $pilot})
           OPTIONAL MATCH (p)<-[:PILOTED_BY]-(d:Deck)-[:HAS_MACRO]->(m:`Macro`)
           OPTIONAL MATCH (d)-[:PLAYED_AT]->(e:Event)
           OPTIONAL MATCH (d)-[:HAS_ARCHETYPE {isPrimary: true}]->(a:Archetype)
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

    The hunt runs within these and no others, so a slice too small to tell a rare
    card from an absent one is never answered for (ADR 0012). No longer a dropdown
    since #184 drew every archetype at once, but still the population that answer
    is drawn from, so it stays a stated list rather than a filter buried in the
    hunt: ``test_gems_only_come_from_the_archetypes_big_enough_to_ask`` holds
    :func:`hidden_gems_subgraph` to it. Ordered by name.
    """
    return [(name, tag) for name, tag in rows(conn.execute(
        f"""MATCH (d:Deck)-[:HAS_ARCHETYPE {{isPrimary: true}}]->(a:Archetype)
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


@dataclass(frozen=True)
class Coverage:
    """The scope of the graph a visitor is looking at: how many of each entity it
    holds and the span of years it covers (issue #115)."""

    events: int
    pilots: int
    decks: int
    cards: int
    first_year: int
    last_year: int


def coverage(conn: ladybug.Connection) -> Coverage:
    """Count the graph's headline entities and its year span, for the on-screen
    provenance surface (issue #115).

    One node count per entity and the Year bounds, all read from the built graph
    so the surface reports exactly what this artifact holds rather than a figure
    baked in at build time that a later graph would silently outdate.
    """
    def count(node: str) -> int:
        return next(rows(conn.execute(f"MATCH (n:{node}) RETURN count(n)")))[0]

    first, last = next(rows(conn.execute(
        "MATCH (y:Year) RETURN min(y.year), max(y.year)"
    )))
    return Coverage(
        events=count("Event"),
        pilots=count("Pilot"),
        decks=count("Deck"),
        cards=count("Card"),
        first_year=first,
        last_year=last,
    )


def deck_points(conn: ladybug.Connection) -> dict[str, int]:
    """What each deck spends under the points list the graph was built from.

    The 7PH cost of a card depends on the context it is played in, and the source
    ships both: ``Card.points`` is what it costs in a deck and
    ``Card.pointsCompanion`` what it costs as a companion. They differ for the two
    cards that can be one, Lurrus of the Dream-Den and Lutri, the Spellchaser,
    free in the deck and 3 points as a companion. So a total is what every card in
    the deck costs across **both boards** (a sideboard card counts toward the
    limit), with one sideboarded companion charged its companion cost in place of
    its deck cost (issue #143).

    **Charged by the board, because the board is what the record says.** A
    companion in the main board is an ordinary creature and costs its 0; the same
    card in the sideboard is there to be companioned and costs 3. The source's
    two boards are all a decklist here says about the matter, so this reads them
    rather than a companion designation nothing in the snapshot carries.

    **One surcharge per deck, not one per card.** A deck names a single
    companion. Two decks in the record sideboard both cards, and the source
    charges 3 for one of them and nothing for the other, never 6.

    Derived here rather than stored on the Deck, because a total is a fact about a
    points list at a moment rather than about the deck: card values change with
    every points revision (the source ships ``pointsAtCreation`` beside
    ``pointsToday`` for exactly that reason), so a column would freeze whichever
    list the build happened to read (ADR 0002).

    Measured against the source's own ``pointsToday`` across the 4591 decks the
    graph holds, this agrees on 4524 of them (98.54%), against 91.00% for the
    plain all-board sum it replaces (``scripts/points_agreement.py`` re-measures
    both). The 67 that remain are characterised in
    ``docs/research/7phstats-insight-gap.md``; in every one it is the source's
    number that its own decklist cannot account for.
    """
    # OPTIONAL, and the null it sums to read as 0: the two source files can
    # disagree, so a deck the card index lists no boards for reaches the graph
    # with no CONTAINS edges. It costs nothing and is still one of the decks,
    # which a reader taking the share of the record over the limit divides by.
    # `sum` comes back as a Decimal, so both terms are cast to the ints these
    # values are.
    # DISTINCT because the sum is over cards and the match is over board
    # memberships: the format is singleton, so a card the source lists in both
    # boards of one deck (8 listings across 7 decks, all of them free) is one card
    # listed twice and pays once.
    points = {
        deck: int(total or 0)
        for deck, total in rows(conn.execute(
            """MATCH (d:Deck) OPTIONAL MATCH (d)-[:CONTAINS]->(c:Card)
               WITH DISTINCT d, c RETURN d.deckId, sum(c.points)"""))
    }
    # The companion cost replaces that card's deck cost rather than stacking on it,
    # which is what the difference charges: the sum above already counted the
    # sideboarded companion at its deck cost. Both companions are free in the deck
    # today, so replacing and stacking agree on every deck in the record and the
    # measurement below cannot tell them apart; replacing is what the source's field
    # says, and is what stops a revision that prices a companion in the deck from
    # silently charging it twice.
    for deck, surcharge in rows(conn.execute(
        """MATCH (d:Deck)-[cont:CONTAINS]->(c:Card)
           WHERE cont.board = 'Side' AND c.pointsCompanion > 0
           RETURN d.deckId, max(c.pointsCompanion - c.points)""")):
        points[deck] += int(surcharge)
    return points


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
        case HiddenGems():
            return hidden_gems_subgraph(conn)
        case PilotAffinity(pilot):
            return pilot_affinity_subgraph(conn, pilot)
        case _:
            raise TypeError(f"unknown query spec: {spec!r}")
