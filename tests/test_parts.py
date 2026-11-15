"""The shared presentation parts (issue #329): tile, table, caption and page head.

Pure markup, tested on what it emits. What the stylesheet makes of that markup is
measured on a real browser in ``tests/test_explorer_page.py``.
"""

import re

from graph7ph import theme
from graph7ph.parts import (
    Column, Group, Tile, caption, card_head, folded, link, page_head, table, tiles,
)


def test_a_tile_states_its_label_its_value_and_its_note():
    row = tiles([Tile("Events played", "34"), Tile("Best finish", "1st", "3 times")])

    assert row.count("class='tile'") == 2
    assert row.index("Events played") < row.index("34") < row.index("Best finish")
    assert row.index("1st") < row.index("3 times")
    # A tile with no note draws no empty line for one.
    assert row.count("class='tile-note'") == 1


def test_a_tile_escapes_the_names_it_is_handed():
    # An archetype's name is free text from the source.
    row = tiles([Tile("Most-played archetype", "A<b>", "at 2 events & more")])

    assert "A&lt;b&gt;" in row and "<b>" not in row
    assert "&amp; more" in row


def test_a_page_opens_with_its_name_then_a_descriptor_then_its_tiles():
    head = page_head("Ada <L>", "Pilot, on record from 2023 to 2026.",
                     [Tile("Events played", "34")])

    assert (head.index("page-name") < head.index("Ada &lt;L&gt;")
            < head.index("Pilot, on record") < head.index("Events played"))
    assert "<L>" not in head


def test_a_caption_is_a_sentence_with_at_most_one_highlighted_figure():
    # The caption rule (#329): ordinary text, and one figure the eye lands on.
    line = caption("Finishes ahead of {} of the field, over 12 events.", "74%")

    assert line.count("class='figure'") == 1
    assert "Finishes ahead of <span class='figure'>74%</span> of the field" in line

    plain = caption("Counted on the main board.")
    assert "figure" not in plain and "Counted on the main board." in plain

    # Names reach a caption too, and are escaped wherever they land.
    named = caption("The share of A<b>'s {} decks.", "1<2")
    assert "A&lt;b&gt;" in named and "1&lt;2" in named


def test_a_card_is_headed_by_its_title_and_its_captions():
    head = card_head("Record", caption("{} results, newest first.", "34"))

    assert head.index("Record") < head.index("34")
    assert "t-result-title" in head  # the insight title's role, as every card's is
    assert card_head("Usage").count("caption") == 0


def _rows(markup: str) -> list[str]:
    return re.findall(r"<tr[^>]*>.*?</tr>", markup)


def test_a_table_sets_its_numbers_apart_and_escapes_its_names():
    out = table(
        [Column("Archetype"), Column("Decks", numeric=True),
         Column("Rank range", numeric=True, quiet=True)],
        [["Grixis <Tempo>", "12 / 48", "1 to 4"], ["Storm", "3 / 9", "2 to 9"]],
    )

    header, first, second = _rows(out)
    assert header.count("<th") == 3 and first.count("<td") == 3
    assert "Grixis &lt;Tempo&gt;" in first and "<Tempo>" not in first
    # A number column is marked on its header and on every cell under it, so both
    # line up on the right; a text column carries no mark.
    assert "<th>Archetype</th>" in header
    assert "<th class='num'>Decks</th>" in header
    assert "<td class='num'>12 / 48</td>" in first
    assert "<td class='num quiet'>2 to 9</td>" in second


def test_a_table_cell_can_carry_a_link_and_a_line_a_phone_folds_in():
    out = table(
        [Column("Event"), Column("Date", wide_only=True), Column("Decklist")],
        [[folded("SSWam", "12 Mar 2025"), "12 Mar 2025",
          link("Open", "https://moxfield.com/decks/a&b")]],
    )

    header, row = _rows(out)
    # The date has a column of its own on a wide screen, and rides under the event
    # on a narrow one, where that column is left out.
    assert "<th class='wide-only'>Date</th>" in header
    assert "<td class='wide-only'>12 Mar 2025</td>" in row
    assert "SSWam<span class='sub'>12 Mar 2025</span>" in row
    # A link is markup the table leaves alone, with its address escaped.
    assert "<a href='https://moxfield.com/decks/a&amp;b'" in row and ">Open</a>" in row
    assert "target='_blank'" in row and "rel='noopener'" in row


def test_a_table_groups_its_rows_under_a_line_of_their_own():
    out = table(
        [Column("Archetype"), Column("Decks", numeric=True, quiet=True),
         Column("Share", numeric=True)],
        [Group("Tempo", ["12 / 480", "2.5%"]), ["Grixis", "9 / 36", "25%"],
         Group("Combo <A>"), ["Storm", "3 / 100", "3%"]],
    )

    _, tempo, grixis, combo, _ = _rows(out)
    # A group with figures of its own sets them under the columns they belong to,
    # as the rows under it do.
    assert "class='group'" in tempo and "colspan" not in tempo
    assert "<th scope='rowgroup'>Tempo</th>" in tempo
    assert "<td class='num quiet'>12 / 480</td><td class='num'>2.5%</td>" in tempo
    # A group with none is its name alone, across the whole table.
    assert "<th colspan='3' scope='rowgroup'>Combo &lt;A&gt;</th>" in combo
    assert "<td" not in combo
    assert "group" not in grixis


def test_every_class_the_parts_emit_is_one_the_stylesheet_draws():
    # Raw markup on a dark card: a class named here and nowhere in the stylesheet
    # draws as the browser's default.
    markup = (
        page_head("Ada", "Pilot.", [Tile("Events", "3", "since 2023")])
        + card_head("Record", caption("{} results.", "3"))
        + table(
            [Column("Event"), Column("Date", wide_only=True),
             Column("Finish", numeric=True, quiet=True), Column("Decklist")],
            [Group("2025"), Group("2026", ["", "1 / 2", ""]),
             [folded("SSWam", "12 Mar 2025"), "12 Mar 2025", "3 / 24", link("Open", "#")]],
        )
    )
    css = theme.build_css()

    classes = set(re.findall(r"class='([^']+)'", markup))
    for name in {c for group in classes for c in group.split()}:
        assert f".{name}" in css, name
