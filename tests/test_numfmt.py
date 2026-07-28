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


def test_count_of_marks_each_half_on_its_own_provenance():
    # An imputed value is one a rule here produced, not one the source supplied
    # (issue #166): Pats Birthday Brawl's field of 24 is MIN_CUT_FIELD's floor and
    # SSWam's 88 is the source's own, and unmarked they read alike. The halves are
    # marked apart because they are decided apart: 27 decks carry a placement read
    # off a deck title or derived from a cut, against fields the source counted.
    assert count_of(3, 24, total_imputed=True) == "3 / 24*"
    assert count_of(3, 88, count_imputed=True) == "3* / 88"
    assert count_of(3, 24, count_imputed=True, total_imputed=True) == "3* / 24*"
    assert count_of(5, 88) == "5 / 88"


def test_score_is_two_decimals_carrying_the_sense_once():
    assert score(0.62) == "0.62 (1 = 1st)"


def test_score_keeps_both_decimals_and_rounds_to_two():
    assert score(1.0) == "1.00 (1 = 1st)"
    assert score(0.617) == "0.62 (1 = 1st)"


def test_score_marks_a_finish_the_project_decided():
    # The other half of issue #166's disclosure: a minted or rescaled norm is a
    # finish a pass here produced, and the mark sits on the number rather than
    # after the parenthetical, which belongs to every score alike.
    assert score(0.62, imputed=True) == "0.62* (1 = 1st)"
    assert score(0.62, sense=False, imputed=True) == "0.62*"


def test_score_drops_the_sense_where_the_reader_is_already_told_it():
    # The sense is stated once, not once per number: a readout that sits beside its own
    # labelled axis would print it twice, which is the "same quantity two ways" this
    # module exists to prevent. The value is unchanged, only the parenthetical goes.
    assert score(0.62, sense=False) == "0.62"
    assert score(0.617, places=3, sense=False) == "0.617"
