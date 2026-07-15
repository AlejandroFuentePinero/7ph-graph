import json

import pytest

from graph7ph.models import Card, Deck, colours_from_mana_cost, load_snapshot


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
         "colour": "colour:U", "macro": "macro:tempo", "engineTags": [],
         "engineTagLabels": {}, "primaryTag": "", "primaryTagWeights": {}}
    )
    assert deck.placement is None and deck.placement_norm is None


def test_out_of_range_card_id_raises_a_clear_error(tmp_path):
    (tmp_path / "decks.json").write_text(json.dumps([
        {"deckId": "d1", "name": "n", "deckName": "n", "pilot": "p", "event": "e",
         "eventType": "Tournament", "placement": 1, "placementNorm": 0.0,
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
