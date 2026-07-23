"""Temporal trends: a tabular ``Series`` result and its own seam (ADR 0013).

A trend is a value per year, which is neither a node nor an edge, so it does not
flow through the ``Subgraph``-returning query spine or ``run_query``. It gets its
own return type, a tabular ``Series``, and its own seam, ``run_series``. Two
result kinds, two seams, neither overloaded.

``meta_share_over_time`` is the tracer: archetype share of the meta per ``Year``,
the first real consumer of the ``IN_YEAR`` edge. Later trend tools (card adoption,
pilot performance) reuse this plumbing; head-to-head, the non-year one, is built
last (ADR 0013).
"""

from dataclasses import dataclass
from datetime import datetime

import ladybug

from graph7ph.db import rows

# There is deliberately no meta-share cell floor here. A share is a count over a
# known denominator, not an estimate, so it is a direct observation and ADR 0013's
# rule exempts it; the floor this module used to hold was removed for that reason
# (see the amendment in ADR 0013 before adding one back).

# The per-``(pilot, year)`` event floor: the fewest distinct events a pilot needs in
# a year for that year's mean ``placementNorm`` to be a mean worth plotting rather
# than a single point (ADR 0013, and ADR 0005's refuse-rather-than-report-noise).
# The unit is the **event**, not the deck: an event is one independent tournament
# finish, so a list a pilot reused across events is separate evidence, but two decks
# at one event would not be. In the current graph the two coincide (ADR 0004 already
# folds a pilot to one deck per event, so events equal decks in every one of the 1833
# cells), but the event is the honest unit and guards data where they diverge.
#
# Two, not more: of 1833 ``(pilot, year)`` cells, 947 hold a single event, where a
# "mean" is really that one finish, so the floor gaps those; the rest (two or more
# events) are kept, and each point is labelled on the chart with the event count it
# averages, so a thin two-event mean carries its own sample size rather than being
# silently trusted or silently dropped. Kept as its own constant, distinct from
# the gem floor: that governs a mean over the decks running a card, this a pilot's
# own mean over its own finishes.
MIN_PILOT_YEAR_EVENTS = 2

# A pilot needs this many qualifying years or the tool returns "not enough history"
# rather than a lone point on an empty line (ADR 0013). Two, because a line through
# one point is not a trajectory. Named, not a bare literal, because the rule lives in
# two places (the tool and its dropdown catalogue) and must not drift between them.
MIN_QUALIFYING_YEARS = 2

# A pilot pair needs this many shared events or it is a dot, not a timeline, so the
# head-to-head tool returns nothing rather than a lone point (ADR 0013). Each point
# is one real registration, so there is no within-point floor; the floor is on the
# pair, not the event. Two, the same reason as MIN_QUALIFYING_YEARS: one point is not
# a trajectory.
MIN_SHARED_EVENTS = 2


@dataclass(frozen=True)
class SeriesCell:
    """One archetype's share of the meta in one year.

    ``n`` is the count of decks of this archetype (by its primary archetype) in
    this year, and ``share`` is ``n / year_total``, always stated and never
    withheld: a share is a direct observation, so a low count is the signal of an
    archetype entering or leaving the format, not the noise a floor exists to gap
    (the same reading :class:`AdoptionCell` gives a card, ADR 0013). ``year_total``
    is every deck that year, the base the share is of, returned so a coarse year is
    visible (a thin year is honest, not dropped) and so a small share is read
    against the sample it came from. A cell of ``n == 0`` is a real zero, the
    archetype absent that year. ``tag`` is the archetype's stable key, ``archetype``
    its display name (two tags can share a name, so the tag is what identifies it).
    """

    tag: str
    archetype: str
    year: int
    n: int
    share: float
    year_total: int


@dataclass(frozen=True)
class AdoptionCell:
    """One card's adoption in one year: a direct observation, so no floor.

    ``count`` is the decks running the card that year, ``year_total`` every deck
    that year, and ``share`` is ``count / year_total`` always, never withheld: a
    low count is the signal (a card entering the format), not the noise a floor
    exists to gap (ADR 0013). The base rides along so 2 of 192 in a thin year is
    not misread against 2 of 2095 in a fat one. A count of zero is a real year the
    card sat out, kept so the timeline shows it entering rather than a gap.
    """

    year: int
    count: int
    share: float
    year_total: int


@dataclass(frozen=True)
class PerformanceCell:
    """One pilot's mean finish in one qualifying year: an aggregate, so a floor.

    ``mean_norm`` is the mean ``placementNorm`` of the pilot's ranked decks that
    year (0 is a win, 1 is last), and ``events`` the count of distinct events those
    decks were played at, the number of independent finishes the mean is taken over,
    always alongside so the reader has the sample size in hand. A cell only exists
    where ``events`` clears :data:`MIN_PILOT_YEAR_EVENTS`: a thinner year is a gap
    (dropped), never a lone point, and a pilot short of two such years yields no
    cells at all ("not enough history", ADR 0013).
    """

    year: int
    mean_norm: float
    events: int


@dataclass(frozen=True)
class HeadToHeadPoint:
    """One shared event in two pilots' rivalry: a direct observation, so no floor.

    Each point is one real registration, not an aggregate, so it carries no
    within-point floor (ADR 0013); the floor lives on the pair (at least
    :data:`MIN_SHARED_EVENTS` shared events) rather than the event. ``date`` is the
    event's registration date, the earliest ``createdAt`` across its whole field,
    the same proxy ADR 0006 dates the event by but at day rather than year
    granularity, so both pilots' points share one x per event and the two lines
    align. ``field_size`` is the entrant count the placement was ranked against,
    recovered from the norm rather than counted from the decks: a top-cut event
    records only its top finishers and a teams event folds many decks onto few
    places, so the decks-at-event count is neither, and a raw finish is only
    readable against the field the norm actually used. ``placement_a``/``norm_a``
    are pilot ``a``'s raw finish and ``placementNorm``, ``_b`` pilot ``b``'s; a
    placement or norm the source never scored is ``None``. The y-axis is ``norm``
    (comparable across field sizes); the raw placement and field size ride along for
    the point's label.
    """

    event: str
    date: datetime
    field_size: int
    placement_a: int | None
    norm_a: float | None
    placement_b: int | None
    norm_b: float | None


@dataclass
class Series:
    """A tabular trend result: the full matrix of cells, no truncation.

    Distinct from ``Subgraph`` (nodes and edges the renderer draws). The trend tab
    reads these as a line chart and the future v2 agent reads them as numbers, off
    the same result (ADR 0013). Each trend fills the matrix with its own cell type:
    :class:`SeriesCell` for the meta-share matrix, :class:`AdoptionCell` for one
    card's per-year adoption.
    """

    cells: (
        list[SeriesCell] | list[AdoptionCell] | list[PerformanceCell]
        | list[HeadToHeadPoint]
    )


@dataclass(frozen=True)
class MetaShareOverTime:
    """Spec for :func:`meta_share_over_time`; takes no argument (the whole meta)."""


@dataclass(frozen=True)
class CardAdoptionOverTime:
    """Spec for :func:`card_adoption_over_time`; takes a card's ``canon``.

    ``board`` scopes the count: ``None`` counts a deck running the card in either
    board, ``"Main"`` or ``"Side"`` only the decks running it there, mirroring the
    board filter the card-usage query already offers.
    """

    canon: str
    board: str | None = None


@dataclass(frozen=True)
class PilotPerformanceOverTime:
    """Spec for :func:`pilot_performance_over_time`; takes a pilot's ``pilot`` key."""

    pilot: str


@dataclass(frozen=True)
class HeadToHeadTimeline:
    """Spec for :func:`head_to_head_timeline`; takes two pilots' ``pilot`` keys."""

    a: str
    b: str


SeriesSpec = (
    MetaShareOverTime | CardAdoptionOverTime | PilotPerformanceOverTime
    | HeadToHeadTimeline
)


def _year_totals(conn: ladybug.Connection) -> dict[int, int]:
    """Every year's total deck count, the base each year's shares are read against.

    The shared year denominator ADR 0013 anticipates: the thin internal helper the
    group-by trends may share (meta share and card adoption both divide by it). A
    year exists here only because decks played in it, so a year's total is always
    positive and no share ever divides by zero.
    """
    return {
        year: total
        for year, total in rows(conn.execute(
            """MATCH (d:Deck)-[:PLAYED_AT]->(:Event)-[:IN_YEAR]->(y:Year)
               RETURN y.year, count(DISTINCT d)"""
        ))
    }


def meta_share_over_time(conn: ladybug.Connection) -> Series:
    """Each archetype's share of the meta per year: the full matrix, no truncation.

    Decks are grouped by their primary archetype and their event's year (via the
    ``IN_YEAR`` edge). The share's base is every deck that year, so a year's shares
    sum to one only where every deck carries a primary archetype; a deck without
    one is left uncounted in the numerators and dilutes the shares honestly rather
    than inflating them. The full ``(archetype, year, share, n)`` matrix is returned,
    every cell carrying its year's total N and its share, which is never withheld:
    a share is a direct observation, so a two-deck year states the 0.21% it is
    rather than a hole the eye reads as a zero (ADR 0013). Thin years are kept
    whole, since a coarse year is honest as long as its N is visible.

    The matrix is rectangular: every archetype gets a cell in every year of the
    graph, so an ``(archetype, year)`` pair with no decks comes back a real zero
    rather than a missing row, exactly as ``card_adoption`` fills a year a card sat
    out. A line then drops to zero across a year its archetype was absent instead
    of jumping the gap and reading as continuous presence. The trend tab, not this
    tool, decides which of the ~125 archetypes to draw.
    """
    year_total = _year_totals(conn)
    names: dict[str, str] = {}
    counts: dict[tuple[str, int], int] = {}
    for tag, name, year, n in rows(conn.execute(
        """MATCH (d:Deck)-[:HAS_ARCHETYPE {isPrimary: true}]->(a:Archetype),
                 (d)-[:PLAYED_AT]->(:Event)-[:IN_YEAR]->(y:Year)
           RETURN a.tag, a.name, y.year, count(DISTINCT d)"""
    )):
        names[tag] = name
        counts[(tag, year)] = n
    cells = []
    years = sorted(year_total.items())
    for tag, name in sorted(names.items()):
        for year, total in years:
            n = counts.get((tag, year), 0)
            cells.append(SeriesCell(
                tag=tag,
                archetype=name,
                year=year,
                n=n,
                share=n / total,
                year_total=total,
            ))
    return Series(cells=cells)


def card_adoption_over_time(
    conn: ladybug.Connection, canon: str, board: str | None = None
) -> Series:
    """One card's adoption per year: decks running it, its share, and the year base.

    Decks running the card are grouped by their event's year via the ``IN_YEAR``
    edge and counted. Adoption carries no floor, the same as ``meta_share``: both
    are direct observations, so a low count is the signal of a card entering the
    format, not noise to gap (ADR 0013). Every year in the graph gets a cell, so a year the
    card sat out comes back a real ``count`` of zero rather than a missing row, and
    each cell carries its year's total decks so a thin year's small count is not
    misread against a fat year's. A card absent from the whole graph returns a zero
    in every year, never an empty series.

    ``board`` scopes the numerator only: ``None`` counts a deck running the card in
    either board, ``"Main"`` or ``"Side"`` only the decks running it there. The year
    base is always every deck that year, so the board filter narrows what counts as
    adoption without narrowing what it is a share of.
    """
    where = "WHERE cont.board = $board" if board else ""
    params = {"canon": canon, "board": board} if board else {"canon": canon}
    adoption = dict(rows(conn.execute(
        f"""MATCH (:Card {{canon: $canon}})<-[cont:CONTAINS]-(d:Deck)
                  -[:PLAYED_AT]->(:Event)-[:IN_YEAR]->(y:Year)
           {where}
           RETURN y.year, count(DISTINCT d)""",
        params,
    )))
    year_total = _year_totals(conn)
    cells = []
    for year, total in sorted(year_total.items()):
        count = adoption.get(year, 0)
        cells.append(
            AdoptionCell(year=year, count=count, share=count / total, year_total=total)
        )
    return Series(cells=cells)


def pilot_performance_over_time(conn: ladybug.Connection, pilot: str) -> Series:
    """One pilot's mean ``placementNorm`` per year, for years with real history.

    The pilot's decks are grouped by their event's year via the ``IN_YEAR`` edge and
    each year's mean finish taken over that year's **ranked** decks (a null
    ``placementNorm`` is an unfinished record the source never scored, left out so it
    neither shifts the mean nor pads the event count). ``placementNorm`` is an
    aggregate, so it carries a floor (ADR 0013): a year below
    :data:`MIN_PILOT_YEAR_EVENTS` distinct events is dropped, its mean too thin to be
    honest, and the pilot needs at least two surviving years or the series comes back
    empty, the "not enough history" answer that refuses a lone point on an empty line
    rather than drawing one. The floor counts events, not decks, so a list reused
    across events counts as the several finishes it is. Cells are ordered by year;
    the connecting line asserts no direction, only joins the points.
    """
    qualifying = sorted(
        (
            PerformanceCell(year=year, mean_norm=mean, events=events)
            for year, mean, events in rows(conn.execute(
                """MATCH (:Pilot {pilot: $pilot})<-[:PILOTED_BY]-(d:Deck)
                         -[:PLAYED_AT]->(e:Event)-[:IN_YEAR]->(y:Year)
                   WHERE d.placementNorm IS NOT NULL
                   RETURN y.year, avg(d.placementNorm), count(DISTINCT e)""",
                {"pilot": pilot},
            ))
            if events >= MIN_PILOT_YEAR_EVENTS
        ),
        key=lambda c: c.year,
    )
    return Series(cells=qualifying if len(qualifying) >= MIN_QUALIFYING_YEARS else [])


def head_to_head_timeline(conn: ladybug.Connection, a: str, b: str) -> Series:
    """Two pilots' rivalry over their shared events, one row per shared event.

    A shared event is one both pilots entered. Each row carries the event's field
    size and registration date (the earliest ``createdAt`` across the event's whole
    field, ADR 0006's proxy at day granularity, so both pilots' points share one x)
    and each pilot's raw placement and ``placementNorm``. This is the only trend to
    read the per-deck ``createdAt`` rather than group by the ``Year`` node (ADR
    0013): its x-axis needs a coordinate finer than year, or two events shared in
    one year collapse onto the same x.

    The rows are direct observations, so they carry no within-point floor; the floor
    is on the pair. A pair sharing fewer than :data:`MIN_SHARED_EVENTS` events is a
    dot, not a timeline, so the series comes back empty (refused) rather than drawing
    a lone point. Rows are ordered by date, the x-axis order; the connecting line
    asserts no direction, only joins the points.

    A pilot has no rivalry with themselves, so ``a == b`` is refused here rather than
    matching every event the pilot played to itself and drawing two identical lines.
    The guard lives in the tool, not only the app, because the tool is the seam an
    agent reaches without the UI's distinct-pilot check.
    """
    if a == b:
        return Series(cells=[])
    points = sorted(
        (
            HeadToHeadPoint(
                event=event,
                date=date,
                field_size=round(implied) if implied is not None else deck_count,
                placement_a=placement_a,
                norm_a=norm_a,
                placement_b=placement_b,
                norm_b=norm_b,
            )
            for event, date, implied, deck_count, placement_a, norm_a,
            placement_b, norm_b
            in rows(conn.execute(
                # field_size is the entrant count placementNorm was ranked against,
                # recovered from the norm (norm = (placement-1)/(field-1), so field =
                # (placement-1)/norm + 1), not the deck count: a top-cut event records
                # only its top finishers and a teams event folds many decks onto few
                # placements, so the decks-at-event count is neither the field the norm
                # uses nor the readable denominator for a raw finish. It is constant
                # across an event's placed decks (max just reads it off one), so a
                # winner (norm 0) does not have to yield it. An event with no placed
                # deck at all falls back to the deck count, the only field left.
                """MATCH (:Pilot {pilot: $a})<-[:PILOTED_BY]-(da:Deck)
                         -[:PLAYED_AT]->(e:Event),
                         (:Pilot {pilot: $b})<-[:PILOTED_BY]-(db:Deck)
                         -[:PLAYED_AT]->(e),
                         (f:Deck)-[:PLAYED_AT]->(e)
                   RETURN e.event, min(f.createdAt),
                          max(CASE WHEN f.placementNorm > 0
                              THEN (f.placement - 1) / f.placementNorm + 1
                              ELSE NULL END),
                          count(DISTINCT f),
                          da.placement, da.placementNorm,
                          db.placement, db.placementNorm""",
                {"a": a, "b": b},
            ))
        ),
        key=lambda p: p.date,
    )
    return Series(cells=points if len(points) >= MIN_SHARED_EVENTS else [])


def pilots_with_history(conn: ladybug.Connection) -> list[tuple[str, str]]:
    """``(displayName, pilot)`` for every pilot the performance trend can draw.

    A pilot qualifies exactly when :func:`pilot_performance_over_time` would return a
    trajectory: at least two years each clearing :data:`MIN_PILOT_YEAR_EVENTS` distinct
    events. The floor rule lives in both places (the same constant and the same
    two-year gate), so the trend tab offers only pilots that draw rather than letting
    a pick land on "not enough history", the way ``gem_archetypes`` offers only the
    slices the gem band can answer for. The meta-share tab has no such catalogue:
    its measure carries no floor, so every archetype draws.
    """
    return [(name, key) for name, key in rows(conn.execute(
        """MATCH (p:Pilot)<-[:PILOTED_BY]-(d:Deck)
                 -[:PLAYED_AT]->(e:Event)-[:IN_YEAR]->(y:Year)
           WHERE d.placementNorm IS NOT NULL
           WITH p, y.year AS year, count(DISTINCT e) AS events
           WHERE events >= $floor
           WITH p, count(year) AS years
           WHERE years >= $min_years
           RETURN p.displayName, p.pilot
           ORDER BY p.displayName""",
        {"floor": MIN_PILOT_YEAR_EVENTS, "min_years": MIN_QUALIFYING_YEARS},
    ))]


def run_series(conn: ladybug.Connection, spec: SeriesSpec) -> Series:
    """Map a series spec to its trend function: the sibling of ``run_query``.

    The single seam over the trend tools, kept apart from ``run_query`` because a
    trend is a ``Series``, not a ``Subgraph`` (ADR 0013). A new trend means a
    function, a spec dataclass, its member in ``SeriesSpec``, and a case here.
    """
    match spec:
        case MetaShareOverTime():
            return meta_share_over_time(conn)
        case CardAdoptionOverTime(canon, board):
            return card_adoption_over_time(conn, canon, board)
        case PilotPerformanceOverTime(pilot):
            return pilot_performance_over_time(conn, pilot)
        case HeadToHeadTimeline(a, b):
            return head_to_head_timeline(conn, a, b)
        case _:
            raise TypeError(f"unknown series spec: {spec!r}")


def latest_deck_year(series: Series) -> int | None:
    """The latest year the series has a deck in, or ``None`` for a series with none.

    Not simply the latest year the matrix holds: the matrix is rectangular, so a year
    whose decks all reached the graph without a primary archetype still holds a cell
    per archetype, every one of them zero. That year has nothing to rank or title a
    chart with, so the year the chart speaks for is the latest one with a deck behind
    it. Shared by the cut and the app's chart title so the two name the same year.
    """
    years = [cell.year for cell in series.cells if cell.n]
    return max(years) if years else None


def latest_year_share_cut(series: Series, cut: float = 0.50) -> list[str]:
    """The archetype tags to draw for a cumulative-share ``cut`` (default 50%).

    A display cut, not a data cut: the tool returns every archetype, but drawing
    all ~125 as lines is a hairball. The archetypes are ranked by their deck count
    in the **latest year the series holds a deck in** (whichever that is, read from
    the data rather than pinned to a year), and the strongest are kept until their
    cumulative share of that year's decks reaches ``cut``. The question the chart
    answers is "what is the meta now, and how did it get here", so today's top
    archetypes are the ones worth tracing back; a pooled all-year ranking instead lets
    a dead archetype with a fat past crowd out a live one. The set is still computed
    once, so the same lines span the whole x-axis rather than entering and leaving per
    year; the trend tab's manual panel is the escape hatch for an archetype large
    only earlier. Returned in rank order, strongest first.
    """
    latest = latest_deck_year(series)
    if latest is None:
        return []
    counts = {cell.tag: cell.n for cell in series.cells if cell.year == latest}
    total = sum(counts.values())
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    kept: list[str] = []
    cumulative = 0
    for tag, n in ranked:
        kept.append(tag)
        cumulative += n
        if cumulative / total >= cut:
            break
    return kept
