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

# The five Magic colours, in canonical WUBRG order.
COLOURS: tuple[str, ...] = ("W", "U", "B", "R", "G")


def colours_from_mana_cost(mana_cost: str | None) -> list[str]:
    """Derive a card's colours from its mana cost, in canonical WUBRG order.

    A colour is any WUBRG pip in the cost (``{U}``, hybrid ``{R/G}``, Phyrexian
    ``{B/P}``), across both faces of a split cost. Generic (``{2}``), ``{X}``,
    ``{0}``, and colourless costs contribute none; ``None`` (e.g. lands) is
    empty. This is the v1 approximation of ADR 0002's card-to-colour edges.
    """
    if mana_cost is None:
        return []
    return [c for c in COLOURS if c in mana_cost]


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

    @property
    def colours(self) -> list[str]:
        """The card's colours, derived from its mana pips (ADR 0002)."""
        return colours_from_mana_cost(self.mana_cost)


class DeckArchetype(BaseModel):
    """One archetype a deck embodies, with its weight and whether it is primary."""

    tag: str
    name: str
    weight: int
    primary: bool


class Deck(_Raw):
    deck_id: str
    name: str  # the full deck title: "<placement> <pilot> - <deck> - <event>"
    deck_name: str  # just the deck's name, e.g. "Grixis", clean of placement and pilot
    pilot: str
    event: str
    event_id: str | None = None
    event_type: str
    placement: int | None = None
    placement_norm: float | None = None
    # Classification, carried as prefixed source codes ("colour:UBR", "macro:tempo",
    # "engine:grixis"). The properties below strip the prefixes into domain values.
    colour: str
    macro: str
    engine_tags: list[str]
    engine_tag_labels: dict[str, str]
    primary_tag: str
    primary_tag_weights: dict[str, int]

    @property
    def colour_identity(self) -> str:
        """The deck's colour identity, e.g. ``UBR`` (or ``unknown``)."""
        return _strip_prefix(self.colour)

    @property
    def colour_atoms(self) -> list[str]:
        """The atomic colours of the identity, in canonical WUBRG order."""
        return [c for c in COLOURS if c in self.colour_identity]

    @property
    def macro_code(self) -> str:
        """The deck's macro strategy, e.g. ``tempo``."""
        return _strip_prefix(self.macro)

    @property
    def archetypes(self) -> list[DeckArchetype]:
        return [
            DeckArchetype(
                tag=_strip_prefix(tag),
                name=self.engine_tag_labels.get(tag, tag),
                weight=self.primary_tag_weights.get(tag, 0),
                primary=tag == self.primary_tag,
            )
            for tag in self.engine_tags
        ]


def _strip_prefix(value: str) -> str:
    """Drop a ``kind:`` prefix from a source code (``colour:UBR`` -> ``UBR``)."""
    return value.split(":", 1)[1] if ":" in value else value


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
