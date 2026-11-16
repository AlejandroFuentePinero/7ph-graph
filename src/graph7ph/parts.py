"""The presentation parts every page shares: tile, table, caption and page head.

Written once here, apart from the app's query glue (issue #329), so each later page
is built from the same parts as the pilot and card pages. Pure markup: a function
takes display values and returns the HTML the app sets in a ``gr.HTML``. What each
class looks like is the stylesheet's (:func:`graph7ph.theme.build_css`).

Every string handed in is escaped, since names come from the source. The one way
round that is :class:`Markup`, for a cell or a column header the app has already
built as markup.
"""

import html
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Tile:
    """One headline figure of a page: what it is, the figure, and an optional note."""

    label: str
    value: str
    note: str | None = None


def tiles(items: Sequence[Tile]) -> str:
    """A page's headline figures as a row of tiles."""
    def tile(item: Tile) -> str:
        note = (f"<div class='tile-note'>{html.escape(item.note)}</div>"
                if item.note else "")
        return (
            "<div class='tile'>"
            f"<div class='tile-label'>{html.escape(item.label)}</div>"
            f"<div class='tile-value'>{html.escape(item.value)}</div>"
            f"{note}</div>"
        )

    return f"<div class='tiles'>{''.join(tile(item) for item in items)}</div>"


def page_head(name: str, descriptor: str, items: Sequence[Tile]) -> str:
    """The top of a subject's page: its name, a one-line descriptor, and its tiles."""
    return (
        "<div class='page-head'>"
        f"<h2 class='page-name'>{html.escape(name)}</h2>"
        f"<div class='page-descriptor'>{html.escape(descriptor)}</div>"
        f"{tiles(items)}</div>"
    )


def caption(text: str, figure: str | None = None) -> str:
    """A caption under the one caption rule: a sentence, and at most one figure.

    The rule (#329): a caption is ordinary text in the text colour, never the
    accent, which is kept for what can be clicked. It may highlight one figure,
    which goes where ``text`` has ``{}``. It is written as short sentences, not as
    fragments joined with middle dots, and it does not count nodes.
    """
    # Text between tags, so a quote in it is left as written.
    before, _, after = (html.escape(part, quote=False) for part in text.partition("{}"))
    highlighted = (f"<span class='figure'>{html.escape(figure, quote=False)}</span>"
                   if figure is not None else "")
    return f"<div class='caption'>{before}{highlighted}{after}</div>"


def card_head(title: str, *captions: str) -> str:
    """A card's head: its title, then each caption (built by :func:`caption`)."""
    return f"<div class='t-result-title'>{html.escape(title)}</div>" + "".join(captions)


class Markup(str):
    """A table cell or column header the app has already built as markup, set as
    it stands."""


def link(text: str, href: str) -> Markup:
    """A cell that opens ``href`` in a new tab."""
    return Markup(
        f"<a href='{html.escape(href, quote=True)}' target='_blank' rel='noopener'>"
        f"{html.escape(text)}</a>"
    )


def opens(kind: str, key: str, text: str) -> Markup:
    """A name that opens its subject's page on a click (issue #333).

    ``kind`` is what the subject is (a pilot, a card, an archetype) and ``key`` is
    what its picker knows it by. The link carries no address, since nothing in this
    app is reached by one: the page's own script answers the click
    (``app._JUMPS_JS``) by opening the subject's tab with its page drawn.
    """
    return Markup(
        f"<a href='#' data-opens='{html.escape(kind, quote=True)}' "
        f"data-key='{html.escape(key, quote=True)}'>{html.escape(text)}</a>"
    )


def folded(text: str, sub: str) -> Markup:
    """A cell with a second line that shows only on a narrow screen.

    How a table keeps a column's content on a phone without the column: the column
    is ``wide_only``, and the cell beside it folds the value in under its own.
    """
    return Markup(f"{html.escape(text)}<span class='sub'>{html.escape(sub)}</span>")


@dataclass(frozen=True)
class Column:
    """One column of a table: its header, text or :class:`Markup`, and how its
    cells are set.

    ``numeric`` sets the header and every cell right-aligned in tabular figures.
    ``quiet`` sets the column back, for a figure that qualifies the one beside it.
    ``wide_only`` leaves the column out on a narrow screen. ``title`` is a longer
    reading of the header, shown on hover.
    """

    header: str
    numeric: bool = False
    quiet: bool = False
    wide_only: bool = False
    title: str | None = None


@dataclass(frozen=True)
class Group:
    """A line that heads the rows under it: the group's name, text or
    :class:`Markup`, and optionally its own cells for the columns after the first,
    set as those columns are."""

    label: str
    cells: Sequence[str] = ()


@dataclass(frozen=True)
class Row:
    """A row that stands for a node of the graph it is listed beside: the node's id,
    and the row's cells. A click on it selects that node (:func:`beside_graph`)."""

    node: str
    cells: Sequence[str]


def _cell(value: str) -> str:
    return value if isinstance(value, Markup) else html.escape(value)


def table(columns: Sequence[Column], rows: Sequence[Group | Row | Sequence[str]]) -> str:
    """The one table every page draws: hairline rows, numbers set to the right.

    A row is its cells in column order, a :class:`Row` naming the graph node those
    cells are about, or a :class:`Group` heading the rows that follow it. A cell is
    text, escaped here, or :class:`Markup`.
    """
    def classes(column: Column) -> str:
        names = [name for name, on in (("num", column.numeric), ("quiet", column.quiet),
                                       ("wide-only", column.wide_only)) if on]
        return f" class='{' '.join(names)}'" if names else ""

    def header(column: Column) -> str:
        title = f" title='{html.escape(column.title, quote=True)}'" if column.title else ""
        return f"<th{classes(column)}{title}>{_cell(column.header)}</th>"

    def row(cells: Group | Sequence[str]) -> str:
        if isinstance(cells, Group):
            # Alone on its line, the name spans the table. With figures of its own,
            # it takes the first column and they sit under theirs.
            span = "" if cells.cells else f" colspan='{len(columns)}'"
            return (
                f"<tr class='group'><th{span} scope='rowgroup'>"
                f"{_cell(cells.label)}</th>" + "".join(
                    f"<td{classes(column)}>{_cell(value)}</td>"
                    for column, value in zip(columns[1:], cells.cells)
                ) + "</tr>"
            )
        node = ""
        if isinstance(cells, Row):
            node, cells = f" data-node='{html.escape(cells.node, quote=True)}'", cells.cells
        return f"<tr{node}>" + "".join(
            f"<td{classes(column)}>{_cell(value)}</td>"
            for column, value in zip(columns, cells)
        ) + "</tr>"

    return (
        "<table class='data-table'><thead><tr>"
        f"{''.join(header(column) for column in columns)}</tr></thead>"
        f"<tbody>{''.join(row(cells) for cells in rows)}</tbody></table>"
    )


# --- A graph beside its panel (issue #330) ---


@dataclass(frozen=True)
class Linked:
    """A neighbour named under a node's facts: its id, its name, the colour it is
    drawn in, and an optional note on what ties the two (a rate, a finish)."""

    node: str
    label: str
    colour: str
    note: str = ""


@dataclass(frozen=True)
class Facts:
    """What the panel says about one node of a graph when it is clicked.

    Its id, kind and name, the colour it is drawn in, and ``rows`` of label and
    value. The name and a value are each text or :class:`Markup`. ``note`` is a line
    under the rows, for what one of them needs said about it. ``link`` is somewhere
    to go from it (built by :func:`link`). ``linked`` names some of its neighbours,
    and ``more`` counts the ones left out.
    """

    node: str
    kind: str
    label: str
    colour: str
    rows: Sequence[tuple[str, str]]
    note: str | None = None
    link: Markup | None = None
    linked: Sequence[Linked] = ()
    more: int = 0


# What the panel says until a node is clicked.
PANEL_PROMPT = "Click a node, or a row of the list, to see its details."


def _dot(colour: str) -> str:
    # The colour is one of the palette's own hexes, never text from the source.
    return f"<span class='key-dot' style='background:{colour}'></span>"


def _facts_block(facts: Facts) -> str:
    rows = "".join(
        f"<dt>{html.escape(label)}</dt><dd>{_cell(value)}</dd>"
        for label, value in facts.rows
    )
    note = f"<div class='facts-note'>{html.escape(facts.note)}</div>" if facts.note else ""
    link = f"<div class='node-link'>{facts.link}</div>" if facts.link else ""
    linked = ""
    if facts.linked:
        items = "".join(
            f"<li data-node='{html.escape(other.node, quote=True)}'>{_dot(other.colour)}"
            f"<span class='linked-name'>{html.escape(other.label)}</span>"
            f"<span class='linked-note'>{html.escape(other.note)}</span></li>"
            for other in facts.linked
        )
        more = (f"<div class='linked-more'>and {facts.more:,} more</div>"
                if facts.more else "")
        linked = (f"<div class='panel-eyebrow'>Linked to</div>"
                  f"<ul class='linked'>{items}</ul>{more}")
    return (
        f"<div class='node-facts' data-node='{html.escape(facts.node, quote=True)}' hidden>"
        f"<div class='node-kind'>{_dot(facts.colour)}{html.escape(facts.kind)}</div>"
        f"<div class='node-name'>{_cell(facts.label)}</div>"
        f"<dl class='facts'>{rows}</dl>{note}{link}{linked}</div>"
    )


def beside_graph(
    frame: str, key: Sequence[tuple[str, str]], facts: Sequence[Facts],
    list_title: str, list_body: str,
) -> str:
    """A graph beside the panel that answers it: ``frame`` (the graph, as markup),
    then the colour key, the facts of whichever node is clicked, and a list.

    ``key`` is each colour's name and hex. ``facts`` holds a block for every node
    drawn, all hidden: the graph's own document shows the clicked node's and hides
    the prompt (``render._ANSWER``), and selects the node a list row or a named
    neighbour stands for. ``list_body`` is markup the caller has built, usually a
    caption and a :func:`table` of :class:`Row`.
    """
    chips = "".join(
        f"<span class='key-chip'>{_dot(colour)}{html.escape(name)}</span>"
        for name, colour in key
    )
    return (
        f"<div class='graph-split'>{frame}<aside class='graph-panel'>"
        "<section class='panel-key'><div class='panel-eyebrow'>Colour key</div>"
        f"<div class='key-chips'>{chips}</div></section>"
        "<section class='panel-answer'><div class='panel-eyebrow'>Selected</div>"
        f"<div class='panel-prompt'>{PANEL_PROMPT}</div>"
        f"{''.join(_facts_block(node) for node in facts)}</section>"
        f"<section class='panel-list'><div class='panel-eyebrow'>{html.escape(list_title)}"
        f"</div>{list_body}</section></aside></div>"
    )
