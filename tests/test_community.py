"""The Community page, built from a bundle's figures (issue #346)."""

import calendar
import html
import json
import re

from graph7ph import community, theme

# A record of six events over three years, one of them a major, in the shape a
# build writes it (``figures._community``). The counts are not one corpus's: the
# ten of the figures row are all different, so one set in another's place shows.
RECORD = {
    "years": [2024, 2025, 2026],
    "totals": {"pilots": 1058, "events": 6, "decks": 65, "cards": 8, "archetypes": 4},
    "this_year": {"pilots": 10, "events": 2, "decks": 12, "cards": 5, "archetypes": 1,
                  "played_before": 3},
    "events": [
        {"date": "2024-03-10", "players": 12, "name": "Open24", "major": False},
        {"date": "2024-03-25", "players": 10, "name": "Cup24", "major": False},
        {"date": "2025-07-05", "players": 90, "name": "Major25", "major": True},
        {"date": "2025-09-01", "players": 4, "name": "Teams25", "major": False},
        {"date": "2026-01-15", "players": 80, "name": "Bracket26", "major": False},
        {"date": "2026-02-20", "players": 9, "name": "Open26", "major": False},
    ],
    "through": "2026-02-20",
    "majors": 1,
    "other_events": 5,
    "major_field_over": 64,
    "largest_field": {"players": 90, "year": 2025, "major": "Major25"},
    "pilots_on_record": [
        ["2024-03-10", 3], ["2024-03-25", 4], ["2025-07-05", 48],
        ["2025-09-01", 51], ["2026-01-15", 57], ["2026-02-20", 1058],
    ],
    "events_by_month": [
        ["2024-03", 2], ["2025-07", 1], ["2025-09", 1], ["2026-01", 1], ["2026-02", 1],
    ],
    "pilots_by_year": [
        {"year": 2024, "pilots": 4, "first_played": {"2024": 4}},
        {"year": 2025, "pilots": 50, "first_played": {"2024": 3, "2025": 47}},
        {"year": 2026, "pilots": 10, "first_played": {"2024": 2, "2025": 1, "2026": 7}},
    ],
    "played_every_year": 2,
}


def _text(markup: str) -> str:
    """What a reader sees of ``markup``: its text, tags dropped, spaces single."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", markup)).split())


def _in_order(text: str, *phrases: str) -> bool:
    at = -1
    for phrase in phrases:
        at = text.find(phrase, at + 1)
        if at < 0:
            return False
    return True


def test_the_page_runs_title_figures_source_three_charts_then_doorways():
    # The parts, top to bottom, with the title and the source line word for word
    # (issue #346). The title stands straight over the figures: the line on what
    # the format and the app are was taken off the page (issue #340).
    text = _text(community.page(RECORD))

    assert text.startswith("Australian 7 Point Highlander Pilots")
    assert "singleton" not in text and "Spikes" not in text
    assert _in_order(
        text,
        "Australian 7 Point Highlander",
        "Pilots", "Events", "Decks", "Cards", "Archetypes", "Largest field",
        "Built from results the community reports on Moxfield.",
        "Every event on record",
        "Pilots on record, and events each month",
        "Pilots each year, by the year they first played",
        "Metagame", "Archetypes", "Cards", "Hidden gems", "Pilots", "Player leaderboard",
    )
    assert text.count("Built from results the community reports on Moxfield.") == 1


def test_the_six_figures_each_show_the_count_and_the_newest_years_beneath():
    # Thousands-comma'd (§4), and no `*` on any of them: the largest field may be
    # one the build settled, and this page does not mark it (issue #346).
    text = _text(community.page(RECORD))

    assert _in_order(
        text,
        "Pilots 1,058 10 in 2026", "Events 6 2 in 2026", "Decks 65 12 in 2026",
        "Cards 8 5 in 2026", "Archetypes 4 1 in 2026", "Largest field 90 Major25, 2025",
    )
    assert "*" not in text


def test_a_largest_field_that_is_no_major_shows_its_year_and_no_name():
    # The figure names its event only where that is a major (issue #346), and the
    # largest field on a small record need not be one.
    record = RECORD | {"largest_field": {"players": 80, "year": 2026, "major": None}}

    assert "Largest field 80 2026 Built from" in _text(community.page(record))


def test_the_key_counts_the_majors_and_the_other_events_in_the_leaderboards_words():
    text = _text(community.page(RECORD))

    assert "Major, a field over 64 that published its standings (1)" in text
    assert "Other events (5)" in text


def test_each_year_is_a_bar_split_by_first_played_year_with_its_total_at_the_end():
    # Oldest first-played year at the left, the year's total after its bar, and
    # the newest year marked as a part year by the last date on record. Beneath,
    # the one line: who had played before, and who has played every year.
    text = _text(community.page(RECORD))

    assert _in_order(
        text,
        "First played 2024", "First played 2025", "First played 2026",
        "2024 4 4", "2025 3 47 50", "2026 to 20 Feb 2 1 7 10",
        "3 of 2026's 10 pilots had played before. "
        "2 have played every year since 2024.",
    )


def _day(year: int, month: int, day: int) -> int:
    """A date as the page script reads one back: whole days of the UTC epoch."""
    return calendar.timegm((year, month, day, 0, 0, 0)) // 86400


def test_the_charts_are_handed_every_event_the_running_count_and_the_months():
    # What the page script draws from, as numbers: a date is its day since 1970,
    # a month its place counted from the first January on record, and an event
    # carries its name and whether it is a major (every event is named under the
    # pointer since issue #340). Nothing else of the record is in it.
    page = community.page(RECORD)
    handed = json.loads(html.unescape(re.search(r"data-record='([^']*)'", page).group(1)))

    assert handed == {
        "years": [2024, 2025, 2026],
        "through": _day(2026, 2, 20),
        "events": [
            [_day(2024, 3, 10), 12, "Open24", 0], [_day(2024, 3, 25), 10, "Cup24", 0],
            [_day(2025, 7, 5), 90, "Major25", 1], [_day(2025, 9, 1), 4, "Teams25", 0],
            [_day(2026, 1, 15), 80, "Bracket26", 0], [_day(2026, 2, 20), 9, "Open26", 0],
        ],
        "pilots": [
            [_day(2024, 3, 10), 3], [_day(2024, 3, 25), 4], [_day(2025, 7, 5), 48],
            [_day(2025, 9, 1), 51], [_day(2026, 1, 15), 57], [_day(2026, 2, 20), 1058],
        ],
        "months": [[2, 2], [18, 1], [20, 1], [24, 1], [25, 1]],
    }


def test_a_part_of_a_bar_carries_its_pilots_and_its_share_of_the_year_for_the_hover():
    # What a part says under the pointer or on focus: 3 of 2025's 50 pilots is 6%
    # and the other 47 are 94%; 2 of 2026's 10 is 20%.
    page = html.unescape(community.page(RECORD))

    assert "data-tip='3 pilots' data-tip-more='first played in 2024 · 6% of 2025'" in page
    assert "data-tip='47 pilots' data-tip-more='first played in 2025 · 94% of 2025'" in page
    assert "data-tip='2 pilots' data-tip-more='first played in 2024 · 20% of 2026'" in page


def test_a_share_is_written_as_every_share_in_the_app_is():
    # §4's one form for a share, a trimmed two-decimal percent: 1 of 8 is 12.5%,
    # not a rounded 12% or 13%.
    record = RECORD | {"pilots_by_year": [
        {"year": 2024, "pilots": 8, "first_played": {"2024": 8}},
        {"year": 2025, "pilots": 8, "first_played": {"2024": 1, "2025": 7}},
        {"year": 2026, "pilots": 10, "first_played": {"2024": 2, "2025": 1, "2026": 7}},
    ]}

    assert "first played in 2024 · 12.5% of 2025" in html.unescape(community.page(record))


def _parts(record: dict) -> list[tuple[str, str]]:
    """Each part of the newest year's bar, oldest first-played year first: the hue
    it is filled with and the ink its count is set in, as the page names them."""
    newest = community.page(record).rsplit("class='cm-row'", 1)[1]
    return re.findall(r"class='cm-seg'[^>]*--k:(#[0-9a-f]{6});color:var\(--(\w+)\)", newest)


def _part_hues(record: dict) -> list[str]:
    return [hue for hue, _ in _parts(record)]


def _years_on_record(count: int) -> dict:
    """``RECORD`` with ``count`` years on record up to its newest, and one bar: the
    newest year's, holding a part for each of them."""
    newest = RECORD["years"][-1]
    years = list(range(newest - count + 1, newest + 1))
    return RECORD | {
        "years": years,
        "pilots_by_year": [{"year": newest, "pilots": 10 * count,
                            "first_played": {str(year): 10 for year in years}}],
    }


def test_the_parts_of_a_bar_step_from_deep_to_pale_oldest_first():
    # The first-played ramp (the design doc's "The Community page"): four years on
    # record take its four stops as they are, the pilot orange second. For any
    # other number the oldest year keeps the deep stop and the later years are
    # spread from the orange to the pale end, through the third stop (issue #363).
    four = RECORD | {
        "years": [2023, 2024, 2025, 2026],
        "pilots_by_year": [{"year": 2026, "pilots": 10, "first_played": {
            "2023": 1, "2024": 2, "2025": 3, "2026": 4}}],
    }

    assert _part_hues(four) == ["#9c3a12", "#d95926", "#ee8f5f", "#f6c3a3"]
    assert _part_hues(RECORD) == ["#9c3a12", "#d95926", "#f6c3a3"]
    # Six years: five from orange to pale, so the third of them is the third stop.
    six = _part_hues(_years_on_record(6))
    assert (six[0], six[1], six[3], six[5]) == ("#9c3a12", "#d95926", "#ee8f5f", "#f6c3a3")
    # Two years: the one later year starts the spread, so it is the orange.
    assert _part_hues(_years_on_record(2)) == ["#9c3a12", "#d95926"]


def test_the_count_on_every_part_is_legible_however_many_years_are_on_record():
    # WCAG AA for text the size of a part's count is 4.5 to 1. Both colours are
    # read off the page: the hue the part is filled with and the ink its count is
    # set in. With five to eight years the ramp used to hold one step between the
    # deep stop and the pilot orange, too light for the light ink and too dark for
    # the dark one (issue #363).
    floor = 4.5
    below = []
    for count in range(2, 9):
        parts = _parts(_years_on_record(count))
        assert len(parts) == count
        below += [(count, hue, ink) for hue, ink in parts
                  if theme.contrast_ratio(theme.TOKENS[ink], hue) < floor]

    assert below == []


def _whole_words(names, text: str) -> list[str]:
    """Those of ``names`` that ``text`` holds as whole words."""
    return sorted(name for name in names
                  if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text))


def test_the_page_built_from_the_real_record_names_no_pilot_and_an_event_only_on_its_dot(
    live_graph,
):
    # No pilot is named anywhere on the page (issue #346), asked of the page as the
    # app serves it over the real record, the numbers handed to its script
    # included, against every pilot's name and id in the graph. Every event's name
    # is handed to the script, which states it under the pointer on that event's
    # dot (issue #340, where it was a major's alone). What the page prints without
    # a pointer still names no event but a major, the leaderboard's own.
    from graph7ph.db import artifact_path, rows
    from graph7ph.figures import figures_path
    from graph7ph.trends import major_events

    record = json.loads(figures_path(artifact_path()).read_text())["community"]
    page = html.unescape(community.page(record))
    pilots = {name for row in rows(live_graph.execute(
        "MATCH (p:Pilot) RETURN p.pilot, p.displayName")) for name in row if name}
    events = {event for event, in rows(live_graph.execute("MATCH (e:Event) RETURN e.event"))}
    majors = major_events(live_graph)

    handed = json.loads(re.search(r"data-record='([^']*)'", page).group(1))
    printed = re.sub(r"data-record='[^']*'", "", page)

    assert len(pilots) > 1000 and len(events) > 100
    assert _whole_words(pilots, page) == []
    assert sorted(name for _, _, name, _ in handed["events"]) == sorted(events)
    assert {name for _, _, name, major in handed["events"] if major} == majors
    assert _whole_words(events - majors, printed) == []
