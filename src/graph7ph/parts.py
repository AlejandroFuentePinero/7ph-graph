"""The presentation parts every page shares: tile, table, caption and page head.

Written once here, apart from the app's query glue (issue #329), so each later page
is built from the same parts as the pilot and card pages. Pure markup: a function
takes display values and returns the HTML the app sets in a ``gr.HTML``. What each
class looks like is the stylesheet's (:func:`graph7ph.theme.build_css`).

Every string handed in is escaped, since names come from the source. The one way
round that is :class:`Markup`, for a cell the app has already built as markup.
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
    """A table cell the app has already built as markup, set as it stands."""


def link(text: str, href: str) -> Markup:
    """A cell that opens ``href`` in a new tab."""
    return Markup(
        f"<a href='{html.escape(href, quote=True)}' target='_blank' rel='noopener'>"
        f"{html.escape(text)}</a>"
    )


def folded(text: str, sub: str) -> Markup:
    """A cell with a second line that shows only on a narrow screen.

    How a table keeps a column's content on a phone without the column: the column
    is ``wide_only``, and the cell beside it folds the value in under its own.
    """
    return Markup(f"{html.escape(text)}<span class='sub'>{html.escape(sub)}</span>")


@dataclass(frozen=True)
class Column:
    """One column of a table: its header, and how its cells are set.

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
    """A line that heads the rows under it: the group's name, and optionally its own
    cells for the columns after the first, set as those columns are."""

    label: str
    cells: Sequence[str] = ()


def _cell(value: str) -> str:
    return value if isinstance(value, Markup) else html.escape(value)


def table(columns: Sequence[Column], rows: Sequence[Group | Sequence[str]]) -> str:
    """The one table every page draws: hairline rows, numbers set to the right.

    A row is its cells in column order, or a :class:`Group` heading the rows that
    follow it. A cell is text, escaped here, or :class:`Markup`.
    """
    def classes(column: Column) -> str:
        names = [name for name, on in (("num", column.numeric), ("quiet", column.quiet),
                                       ("wide-only", column.wide_only)) if on]
        return f" class='{' '.join(names)}'" if names else ""

    def header(column: Column) -> str:
        title = f" title='{html.escape(column.title, quote=True)}'" if column.title else ""
        return f"<th{classes(column)}{title}>{html.escape(column.header)}</th>"

    def row(cells: Group | Sequence[str]) -> str:
        if isinstance(cells, Group):
            # Alone on its line, the name spans the table. With figures of its own,
            # it takes the first column and they sit under theirs.
            span = "" if cells.cells else f" colspan='{len(columns)}'"
            return (
                f"<tr class='group'><th{span} scope='rowgroup'>"
                f"{html.escape(cells.label)}</th>" + "".join(
                    f"<td{classes(column)}>{_cell(value)}</td>"
                    for column, value in zip(columns[1:], cells.cells)
                ) + "</tr>"
            )
        return "<tr>" + "".join(
            f"<td{classes(column)}>{_cell(value)}</td>"
            for column, value in zip(columns, cells)
        ) + "</tr>"

    return (
        "<table class='data-table'><thead><tr>"
        f"{''.join(header(column) for column in columns)}</tr></thead>"
        f"<tbody>{''.join(row(cells) for cells in rows)}</tbody></table>"
    )
