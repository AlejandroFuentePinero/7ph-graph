"""The one numeric convention (v1 visual direction §4), tested on its strings."""

from graph7ph.numfmt import count_of, score, share


def test_share_is_a_trimmed_two_decimal_percent():
    assert share(0.0673) == "6.73%"


def test_share_keeps_two_decimals_below_one_percent():
    # The fringe case the .2~% axis format exists for: a card in a handful of a
    # 2000-deck year sits under 1%, and a whole-percent rounding would floor it to
    # "0%" or a "<1%" that erases the ratio.
    assert share(0.0012) == "0.12%"


def test_share_trims_insignificant_trailing_zeros():
    assert share(0.067) == "6.7%"
    assert share(0.1) == "10%"
    assert share(1.0) == "100%"
    assert share(0.0) == "0%"


def test_count_of_total_is_thousands_commad_with_a_unit():
    assert count_of(134, 2000, "decks") == "134 / 2,000 decks"


def test_count_of_total_takes_no_unit_when_the_denominator_has_none():
    # The head-to-head field size is teams or ranking slots, not decks, so the
    # placement-over-field reads without a unit: "5 / 143", not "5 / 143 decks".
    assert count_of(5, 143, "") == "5 / 143"


def test_score_is_two_decimals_carrying_the_sense_once():
    assert score(0.62) == "0.62 (1 = 1st)"


def test_score_keeps_both_decimals_and_rounds_to_two():
    assert score(1.0) == "1.00 (1 = 1st)"
    assert score(0.617) == "0.62 (1 = 1st)"
