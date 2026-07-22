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

import ladybug

from graph7ph.db import rows

# The per-``(archetype, year)`` cell floor: the fewest decks of an archetype in a
# year for its share that year to be a point worth plotting. An absolute count,
# not a share, following ADR 0012 and ``MIN_GEM_DECKS``: evidence is sample size
# and does not scale with the meta, so the floor holds the same in a thin year and
# a fat one. A cell below it is a gap, not a zero, so a line reading "share is near
# zero" is told apart from one too thin to say.
#
# Tuned against real counts the way ``MIN_GEM_DECKS`` was: of 372 primary-archetype
# by-year cells, 172 hold one to four decks (a one-off to a handful of
# registrations), and a line drawn through them manufactures a trend from noise.
# Five gaps those and keeps the 200 cells of five-plus. In the thinnest honest year
# (2023, 192 decks) five decks is 2.6% of the meta; in the fattest (2025, 2095) it
# is 0.24%: the floor governs a cell's evidence, not the size of the share it backs.
# It lands on the same value as ``MIN_GEM_DECKS`` by the same reasoning, kept as its
# own constant because it is a distinct floor over a distinct population.
MIN_CELL_DECKS = 5


@dataclass(frozen=True)
class SeriesCell:
    """One archetype's share of the meta in one year.

    ``n`` is the count of decks of this archetype (by its primary archetype) in
    this year, always the honest figure. ``share`` is ``n / year_total`` where the
    cell clears :data:`MIN_CELL_DECKS`, and ``None`` where it does not: a gap, so a
    thin cell is not read as a near-zero share. ``year_total`` is every deck that
    year, the base the share is of, returned so a coarse year is visible (a thin
    year is honest, not dropped). ``tag`` is the archetype's stable key, ``archetype``
    its display name (two tags can share a name, so the tag is what identifies it).
    """

    tag: str
    archetype: str
    year: int
    n: int
    share: float | None
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


@dataclass
class Series:
    """A tabular trend result: the full matrix of cells, no truncation.

    Distinct from ``Subgraph`` (nodes and edges the renderer draws). The trend tab
    reads these as a line chart and the future v2 agent reads them as numbers, off
    the same result (ADR 0013). Each trend fills the matrix with its own cell type:
    :class:`SeriesCell` for the meta-share matrix, :class:`AdoptionCell` for one
    card's per-year adoption.
    """

    cells: list[SeriesCell] | list[AdoptionCell]


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


SeriesSpec = MetaShareOverTime | CardAdoptionOverTime


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
    every cell carrying its year's total N; a cell below :data:`MIN_CELL_DECKS` comes
    back a gap (``share`` is ``None``), never a silent zero and never dropped. Thin
    years are kept whole, since a coarse year is honest as long as its N is visible.
    An ``(archetype, year)`` pair with no decks is simply absent from the matrix, a
    genuine zero, distinct from a gap (present that year but too thin to trust).
    The trend tab, not this tool, decides which of the ~125 archetypes to draw.
    """
    year_total = _year_totals(conn)
    cells = [
        SeriesCell(
            tag=tag,
            archetype=name,
            year=year,
            n=n,
            share=(n / year_total[year] if n >= MIN_CELL_DECKS else None),
            year_total=year_total[year],
        )
        for tag, name, year, n in rows(conn.execute(
            """MATCH (d:Deck)-[:HAS_ARCHETYPE {isPrimary: true}]->(a:Archetype),
                     (d)-[:PLAYED_AT]->(:Event)-[:IN_YEAR]->(y:Year)
               RETURN a.tag, a.name, y.year, count(DISTINCT d)"""
        ))
    ]
    return Series(cells=cells)


def card_adoption_over_time(
    conn: ladybug.Connection, canon: str, board: str | None = None
) -> Series:
    """One card's adoption per year: decks running it, its share, and the year base.

    Decks running the card are grouped by their event's year via the ``IN_YEAR``
    edge and counted. Unlike ``meta_share``, adoption carries no floor: it is a
    direct observation, so a low count is the signal of a card entering the format,
    not noise to gap (ADR 0013). Every year in the graph gets a cell, so a year the
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
        case _:
            raise TypeError(f"unknown series spec: {spec!r}")


def pooled_share_cut(series: Series, cut: float = 0.50) -> list[str]:
    """The archetype tags to draw for a cumulative-share ``cut`` (default 50%).

    A display cut, not a data cut: the tool returns every archetype, but drawing
    all ~125 as lines is a hairball. The archetypes are ranked by their pooled deck
    count across all years (deck-weighted, so recent fat years dominate the pick),
    and the strongest are kept until their cumulative share of all decks reaches
    ``cut``. Computed once over the pooled all-year population, so the same set of
    lines spans the whole x-axis rather than entering and leaving per year; the
    trend tab's manual panel is the escape hatch for an archetype large only early.
    Returned in pooled-rank order, strongest first.
    """
    pooled: dict[str, int] = {}
    for cell in series.cells:
        pooled[cell.tag] = pooled.get(cell.tag, 0) + cell.n
    total = sum(pooled.values())
    if not total:
        return []
    ranked = sorted(pooled.items(), key=lambda kv: (-kv[1], kv[0]))
    kept: list[str] = []
    cumulative = 0
    for tag, n in ranked:
        kept.append(tag)
        cumulative += n
        if cumulative / total >= cut:
            break
    return kept
