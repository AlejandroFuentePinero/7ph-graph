"""Domain models and the loader that parses a 7phstats snapshot into them.

The raw source splits its data across two files. ``decks.json`` carries deck
metadata and the pilot who registered each deck. ``cards_index.json`` carries
the card catalogue (``cards``, where a card's list position is its id) and, per
deck, the Main (``m``) and Side (``s``) boards as lists of those ids. This
module joins the two into typed Deck, Card, and Containment objects keyed on the
domain's stable identities: a Deck on its ``deckId``, a Card on its ``canon``.
"""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

Board = Literal["Main", "Side"]

_BOARDS: tuple[tuple[str, Board], ...] = (("m", "Main"), ("s", "Side"))


class _Raw(BaseModel):
    """Base for models parsed from the source's camelCase JSON."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="ignore"
    )


class Card(_Raw):
    canon: str
    name: str
    type: str
    mana_cost: str | None = None
    mana_value: float
    reserved: bool
    price_usd: float | None = None
    points: int


class Deck(_Raw):
    deck_id: str
    name: str
    pilot: str
    event: str
    event_type: str
    placement: int | None = None
    placement_norm: float | None = None


class Containment(BaseModel):
    """One card's membership of one deck, on a given board."""

    deck_id: str
    canon: str
    board: Board


class Snapshot(BaseModel):
    cards: list[Card]
    decks: list[Deck]
    containments: list[Containment]


def load_snapshot(path: Path) -> Snapshot:
    """Parse the JSON files in ``path`` into a Snapshot of domain objects."""
    path = Path(path)
    decks_raw = json.loads((path / "decks.json").read_text())
    index_raw = json.loads((path / "cards_index.json").read_text())

    cards = [Card.model_validate(c) for c in index_raw["cards"]]
    decks = [Deck.model_validate(d) for d in decks_raw]

    canon_by_id = [c.canon for c in cards]
    containments = [
        Containment(
            deck_id=deck_id,
            canon=_canon(canon_by_id, deck_id, card_id),
            board=board,
        )
        for deck_id, boards in index_raw["decks"].items()
        for key, board in _BOARDS
        for card_id in boards[key]
    ]

    return Snapshot(cards=cards, decks=decks, containments=containments)


def _canon(canon_by_id: list[str], deck_id: str, card_id: int) -> str:
    """Resolve a card id to its canon, with a clear error if it is out of range."""
    if not 0 <= card_id < len(canon_by_id):
        raise ValueError(
            f"deck {deck_id!r} references card id {card_id}, "
            f"outside the {len(canon_by_id)}-card index"
        )
    return canon_by_id[card_id]
