import json
from datetime import datetime, timezone

import pytest

from graph7ph.models import (
    Card,
    Deck,
    colours_from_mana_cost,
    load_snapshot,
    resolve_cut_placements,
)


@pytest.mark.parametrize(
    "mana_cost, expected",
    [
        ("{B}", ["B"]),                       # mono
        ("{2}{R}", ["R"]),                    # generic pips ignored
        ("{B}{G}", ["B", "G"]),               # multicolour
        ("{1}{U}{U}", ["U"]),                 # duplicate colour deduped
        ("{U}{B}", ["U", "B"]),               # canonical WUBRG order, not input order
        ("{1}{R/G}", ["R", "G"]),             # hybrid: both colours
        ("{1}{B/P}{B/P}", ["B"]),             # Phyrexian: P is not a colour
        ("{X}{R}", ["R"]),                    # X ignored
        ("{0}", []),                          # colourless
        ("{1}{G} // {1}{R}", ["R", "G"]),     # split card: both faces, WUBRG order
        (None, []),                           # lands with no mana cost
    ],
)
def test_colours_from_mana_cost(mana_cost, expected):
    assert colours_from_mana_cost(mana_cost) == expected


def test_parses_decks_and_cards_with_domain_fields(snapshot_dir):
    snap = load_snapshot(snapshot_dir)

    assert len(snap.decks) == 3
    assert len(snap.cards) == 121

    deck = next(d for d in snap.decks if d.deck_id == "BsegXnsDsEWxh-vNbUrn0w")
    assert deck.pilot == "Jordan C"
    assert deck.event == "CFWAT25"
    assert deck.placement == 5
    # The only date the source carries for a deck (issue-26, ADR 0006).
    assert deck.created_at == datetime(2025, 12, 6, tzinfo=timezone.utc)

    # The field the source ranked placementNorm against, kept so the build can
    # check it against what it can count and correct it (issue #140).
    assert deck.event_size == 19

    card = next(c for c in snap.cards if c.canon == "arid mesa")
    assert card.name == "Arid Mesa"
    assert card.type == "Lands"
    assert card.reserved is False


def test_optional_source_fields_tolerate_nulls():
    # The real data carries nulls the small fixture doesn't: cards with no price,
    # decks with no placement (e.g. unranked entries).
    card = Card.model_validate(
        {"canon": "x", "name": "X", "type": "Lands", "manaCost": None,
         "manaValue": 0.0, "reserved": False, "priceUsd": None, "points": 0}
    )
    assert card.price_usd is None and card.mana_cost is None

    deck = Deck.model_validate(
        {"deckId": "d", "name": "n", "deckName": "n", "pilot": "p", "event": "e",
         "eventType": "Tournament", "placement": None, "placementNorm": None,
         "createdAt": "2025-06-01T00:00:00+00:00",
         "colour": "colour:U", "macro": "macro:tempo", "engineTags": [],
         "engineTagLabels": {}, "primaryTag": "", "primaryTagWeights": {}}
    )
    assert deck.placement is None and deck.placement_norm is None


def _deck(**overrides):
    return Deck.model_validate(
        {"deckId": "d", "name": "n", "deckName": "n", "pilot": "p", "event": "e",
         "eventType": "Tournament", "placement": None, "placementNorm": None,
         "createdAt": "2025-06-01T00:00:00+00:00",
         "colour": "colour:U", "macro": "macro:tempo", "engineTags": [],
         "engineTagLabels": {}, "primaryTag": "", "primaryTagWeights": {}} | overrides
    )


@pytest.mark.parametrize(
    "title, placement, expected, rule",
    [
        # A top cut is a placement the source left null: CanBrawl2 numbers 9th
        # and below, and records its top eight only as "Top 4" / "Top 8".
        ("Top 8 Ben H - Jeskai Tempo - CanBrawl2", None, 8, "title-single"),
        ("Top 4 Justin C - Jund - CanBrawl2", None, 4, "title-single"),
        ("Top 8  Benjamin S - Jund - CanBrawl2", None, 8, "title-single"),
        # Pats Birthday Brawl numbers nothing, though every title says the rank.
        ("1st - Robert L - Pats Birthday Brawl", None, 1, "title-single"),
        ("2nd - Jared A - Pats Birthday Brawl", None, 2, "title-single"),
        # An explicit range reads its best rank, as the source itself numbers them
        # (best end 573 of 573 times, issue #103). A "Top N" cut above gives a
        # single bound, not two ends, so it is still read as that bound.
        ("3rd/4th - Brennan C - Pats Birthday Brawl", None, 3, "title-range"),
        ("5th-8th - Liam B - Pats Birthday Brawl", None, 5, "title-range"),
        # A real placement always wins; the title never overrides the source, and
        # a number the source gave carries no rule at all.
        ("Top 8 Ben H - Jeskai Tempo - CanBrawl2", 3, 3, None),
        # A placeholder rank carries no number, so it stays unknown. `none` is the
        # rule that says so: it distinguishes "nothing was recoverable here" from
        # the null that means "the source's own number stands" (issue #162).
        ("??st Andrew V - Mox Jund - CFWAT25", None, None, "none"),
        ("XXth Jayden G - Storm - PogNov25", None, None, "none"),
        # No placement token at all (the null-pilot titles at Area52IQ).
        ("Darcy - Mono R - Area52IQ", None, None, "none"),
        # A leading digit run of four or more characters is not read as a rank:
        # the largest placement anywhere is 306, so a three-character bound
        # leaves 3.26x headroom. Untaken by the corpus (0 of 4592 titles open on
        # four digits), so nothing but this case holds the clause in place.
        ("2024th Ben H - Jeskai Tempo - CanBrawl2", None, None, "none"),
        # The bound is on token length and not on value, so a four-character
        # zero-pad is knowingly discarded even though 0077 is a valid 77th.
        ("0077th - X", None, None, "none"),
    ],
)
def test_placement_recovered_from_the_title_when_source_has_none(
    title, placement, expected, rule
):
    deck = _deck(name=title, placement=placement)
    # The recovered rank and the rule that recovered it, so every downstream
    # reader can tell which of a deck's numbers this project decided (issue #162).
    assert (deck.placement, deck.placement_imputed) == (expected, rule)


def test_top_n_cuts_resolve_to_their_cohort_best_rank():
    # CanBrawl2's shape: four "Top 4" and four "Top 8" sit above a numbered 9th and
    # below. The title-only pass could only read each cut's worst rank (4 and 8);
    # the cohort makes the best rank recoverable, so Top 4 is 1st and Top 8 is 5th.
    cuts = (
        [_deck(deckId=f"t4-{i}", name=f"Top 4 P{i} - Jund - CanBrawl2",
               event="CanBrawl2") for i in range(4)]
        + [_deck(deckId=f"t8-{i}", name=f"Top 8 P{i} - Jund - CanBrawl2",
                 event="CanBrawl2") for i in range(4)]
    )
    numbered = _deck(deckId="n9", name="09th P9 - Eclipse - CanBrawl2",
                     event="CanBrawl2", placement=9)
    decks = cuts + [numbered]
    resolve_cut_placements(decks)
    assert [d.placement for d in cuts[:4]] == [1, 1, 1, 1]
    assert [d.placement for d in cuts[4:]] == [5, 5, 5, 5]
    assert numbered.placement == 9  # a source-numbered finish is never touched

    # The cohort is what decided these ranks, not the title, so they say so: the
    # title-only pass read 4 and 8 and this pass overwrote both (issue #162).
    assert {d.placement_imputed for d in cuts} == {"cohort-cut"}
    assert numbered.placement_imputed is None


def test_lone_top_cut_is_first():
    # With no deeper tier above it, a "Top 8" cut spans 1st to 8th, so its best is 1st.
    decks = [_deck(deckId=f"t8-{i}", name=f"Top 8 P{i} - Jund - E", event="E")
             for i in range(4)]
    resolve_cut_placements(decks)
    assert [d.placement for d in decks] == [1, 1, 1, 1]


def test_out_of_range_card_id_raises_a_clear_error(tmp_path):
    (tmp_path / "decks.json").write_text(json.dumps([
        {"deckId": "d1", "name": "n", "deckName": "n", "pilot": "p", "event": "e",
         "eventType": "Tournament", "placement": 1, "placementNorm": 0.0,
         "createdAt": "2025-06-01T00:00:00+00:00",
         "colour": "colour:U", "macro": "macro:tempo", "engineTags": [],
         "engineTagLabels": {}, "primaryTag": "", "primaryTagWeights": {}}
    ]))
    (tmp_path / "cards_index.json").write_text(json.dumps({
        "v": 2,
        "cards": [{"canon": "island", "name": "Island", "type": "Lands",
                   "manaValue": 0.0, "reserved": False, "points": 0}],
        "decks": {"d1": {"m": [5], "s": []}},  # index 5 into a 1-card list
    }))

    with pytest.raises(ValueError, match="card id 5"):
        load_snapshot(tmp_path)


def test_resolves_deck_membership_to_canon_and_board(snapshot_dir):
    snap = load_snapshot(snapshot_dir)

    # Every deck contributes 60 Main + 15 Side cards.
    assert len(snap.containments) == 225

    for c in snap.containments:
        assert c.board in ("Main", "Side")
        # Membership resolves the raw card index to a real card's canon.
        assert c.canon in {card.canon for card in snap.cards}

    deck_boards = {
        (c.deck_id, c.board)
        for c in snap.containments
        if c.deck_id == "BsegXnsDsEWxh-vNbUrn0w"
    }
    assert deck_boards == {
        ("BsegXnsDsEWxh-vNbUrn0w", "Main"),
        ("BsegXnsDsEWxh-vNbUrn0w", "Side"),
    }
