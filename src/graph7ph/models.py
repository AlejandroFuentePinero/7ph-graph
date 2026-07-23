"""Domain models and the loader that parses a 7phstats snapshot into them.

The raw source splits its data across two files. ``decks.json`` carries deck
metadata and the pilot who registered each deck. ``cards_index.json`` carries
the card catalogue (``cards``, where a card's list position is its id) and, per
deck, the Main (``m``) and Side (``s``) boards as lists of those ids. This
module joins the two into typed Deck, Card, and Containment objects keyed on the
domain's stable identities: a Deck on its ``deckId``, a Card on its ``canon``.

That list position is an id inside one file and nowhere else. The catalogue is
canon-sorted, so a later fetch that inserts a card displaces every position after
it: 4728 of the 4967 positions the two held snapshots share resolve to a
different canon in the newer one. Resolving ids to canons here, before anything
downstream is keyed on them, is what keeps that drift from reaching the graph.
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic.alias_generators import to_camel

Board = Literal["Main", "Side"]

_BOARDS: tuple[tuple[str, Board], ...] = (("m", "Main"), ("s", "Side"))

# A deck title opens with the pilot's placement: a run of digits or placeholders
# (``?``, ``X`` for an unknown placement) with an optional ordinal suffix
# (``st``/``nd``/``rd``/``th``, sometimes mistyped with a trailing ``h`` or
# missing letters), optionally a ``/`` or ``-`` range, e.g. ``05th/08th``,
# ``??st``, ``05th-8th``, ``42ndh``, ``19h``, ``XXth``. It may carry a leading
# ``*`` ("*12th Ciaran C"), or read as a cut rather than a rank ("Top 8 Ben H").
# Both ends of a range are captured so the rank can be read back out of it.
#
# This is the source's title grammar, so it lives with the source parse. Pilot
# name recovery imports it to strip what it must not read as a name.
PLACEMENT_TOKEN = re.compile(
    r"^\s*\*?\s*-?\s*(?:top\s*)?([\dxX?]+)(?:st|nd|rd|th)?h?"
    r"(?:\s*[/-]\s*([\dxX?]+)(?:st|nd|rd|th)?h?)?\s+",
    re.IGNORECASE,
)


def placement_from_title(title: str | None) -> int | None:
    """The placement a title records, or ``None`` if it records none.

    An explicit range reads as its best rank, so "05th-8th" is 5th. This matches
    the source: across every deck the source itself scored whose title carries an
    explicit range, it assigns the best end 573 times out of 573 and the worst end
    0 times (issue #103). The rule used to read the worst end, justified as "the
    same convention the source uses", which was false; it was corrected to the best
    end, moving 16 title-recovered decks (14 from 8th to 5th, 2 from 4th to 3rd).

    A "Top 8" cut, which gives a single bound rather than two ends, is read here
    as that bound (8th), because the best rank of the cut cannot be read off the
    title alone: "Top 8" ranges over 1st to 8th. Its true value comes from the
    event's cohort, so :func:`resolve_cut_placements` corrects it after load where
    the cohort makes it recoverable; this function is only the first, title-only
    pass. A placeholder rank (``??st``, ``XXth``) carries no number and stays
    unknown.

    Best-of assumes an explicit range is a tie-band ("5th-8th", four people tied),
    not a cut written as a full range ("1st-8th", a top-eight where everyone is
    scored their real finish). The two are indistinguishable from the title, so
    the rule would score a source-null "1st-8th" as 1st for all eight. It is safe
    only because every range that opens at 1st is source-scored today (38 of 38),
    so the recovery never runs on one; a future source that left one null would
    need cohort handling like :func:`resolve_cut_placements`, not this branch.

    A leading digit run of four or more characters is not read as a rank. The
    largest field anywhere seats 306 and the largest placement anywhere is 306,
    so a three-character bound leaves 3.26x headroom. The hazard is not a year:
    a year in the rank position occurs 0 of 4592 times and cannot, because the
    pattern is anchored to the start of the title and years live in the event
    segment at its end. The long digit run that really sits beside a rank is a
    numeric pilot handle ("026th 106462910 - 8pt Mardu - SydneyShowdown", 30 of
    4592 titles), and all 30 carry a source-given placement, so this function
    never runs on them. The bound is on token length and not on value, so a
    zero-padded four-character rank ("0077th") would be discarded despite being
    a valid 77.
    """
    token = PLACEMENT_TOKEN.match(title or "")
    if not token:
        return None
    low, high = token.group(1), token.group(2)
    # An explicit range reads its best rank (the source's own convention); a single
    # bound, including a "Top N" cut, is read as given (issue #103).
    if high is not None and low.isdigit() and high.isdigit():
        pick = str(min(int(low), int(high)))
    else:
        pick = high or low
    return int(pick) if pick.isdigit() and len(pick) <= 3 else None


# A title that opens "Top N" is a cut, not a rank: the bound says how deep the cut
# was, not where inside it the deck finished.
_TOP_CUT = re.compile(r"^\s*\*?\s*-?\s*top\s*(\d+)", re.IGNORECASE)

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
    created_at: datetime  # when the list was registered; the proxy for the event's date (ADR 0006)
    # Classification, carried as prefixed source codes ("colour:UBR", "macro:tempo",
    # "engine:grixis"). The properties below strip the prefixes into domain values.
    colour: str
    macro: str
    engine_tags: list[str]
    engine_tag_labels: dict[str, str]
    primary_tag: str
    primary_tag_weights: dict[str, int]

    @model_validator(mode="after")
    def _placement_from_title(self) -> "Deck":
        """Recover a placement the source left null from the deck's own title.

        The source drops the placement of whole classes of entry while the title
        still records it: CanBrawl2 numbers 9th and below but leaves its top
        eight as "Top 4"/"Top 8", and Pats Birthday Brawl numbers nothing though
        every title opens "1st", "3rd/4th", "5th-8th". The title is the only
        witness, so it is read when the field is null and never otherwise.

        ``placement_norm`` stays null: the source derives it against the field
        size, which is not ours to recompute.
        """
        if self.placement is None:
            self.placement = placement_from_title(self.name)
        return self

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


def resolve_cut_placements(decks: list[Deck]) -> None:
    """Correct each "Top N" cut's placement to its cohort's best rank, in place.

    A bare "Top N" title records that a deck made the cut, not its finish inside
    it, so the title-only pass in :func:`placement_from_title` can read no better
    than the cut's worst rank (N). Where an event numbers every finisher below its
    cuts and leaves the top as nested "Top 4"/"Top 8" tiers, each tier's best rank
    is recoverable from the cohort: the deepest cut (smallest N) starts at 1st and
    each shallower tier starts one past the tier below, so "Top 4" is 1st and
    "Top 8" is 5th. A lone "Top 8" with no tier above it is 1st.

    Only decks whose title is a "Top N" cut are touched, and the source scores with
    a numeric rank rather than a "Top N" title, so this never overrides a finish
    the source recorded. On the graph today it corrects the 8 cut decks at
    CanBrawl2 (four "Top 4" to 1st, four "Top 8" to 5th), the one event that
    structures its top this way, and moves nothing else (issue #103).
    """
    by_event: dict[str, list[tuple[int, Deck]]] = {}
    for deck in decks:
        cut = _TOP_CUT.match(deck.name or "")
        if cut:
            by_event.setdefault(deck.event, []).append((int(cut.group(1)), deck))
    for cohort in by_event.values():
        # Ascending distinct bounds, so each tier starts one past the tier below:
        # [4, 8] gives Top 4 -> 1st, Top 8 -> 5th; [8] alone gives Top 8 -> 1st.
        bounds = sorted({bound for bound, _ in cohort})
        best = {b: (bounds[i - 1] if i else 0) + 1 for i, b in enumerate(bounds)}
        for bound, deck in cohort:
            deck.placement = best[bound]


def load_snapshot(path: Path) -> Snapshot:
    """Parse the JSON files in ``path`` into a Snapshot of domain objects."""
    path = Path(path)
    decks_raw = json.loads((path / "decks.json").read_text())
    index_raw = json.loads((path / "cards_index.json").read_text())

    cards = [Card.model_validate(c) for c in index_raw["cards"]]
    decks = [Deck.model_validate(d) for d in decks_raw]
    # The per-deck validator recovered each "Top N" cut as its worst rank; now that
    # every deck is loaded, correct those to the cohort's best rank (issue #103).
    resolve_cut_placements(decks)

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
    """Resolve a card id to its canon, with a clear error if it is out of range.

    Inert on the data that exists: 0 of 673,442 checked ids fall outside their own
    index, and both held indexes are exactly dense. The lower bound is the
    load-bearing half all the same, because the two ends fail differently. An id
    past the end raises unguarded; a negative id raises nothing at all and
    silently indexes from the end of the list, resolving to a real card that is
    not the one the file named.
    """
    if not 0 <= card_id < len(canon_by_id):
        raise ValueError(
            f"deck {deck_id!r} references card id {card_id}, "
            f"outside the {len(canon_by_id)}-card index"
        )
    return canon_by_id[card_id]
