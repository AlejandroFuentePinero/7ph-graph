# 7 Point Highlander Graph

A knowledge graph of the Australian 7 Point Highlander (7PH) Magic: The Gathering metagame. It links events, pilots, decks, and cards down to card attributes, for exploration and analytics.

## Language

### People and events

**Pilot**:
A player who registered a deck at an event. Identified by a stable id from the upstream source, which may be a pseudonym or handle rather than a readable name.
_Avoid_: Player, user, competitor

**Display Name**:
The human-readable name for a Pilot, recovered from deck titles. A label only, never used as identity.
_Avoid_: Real name, player name (as a key)

**Event**:
A 7PH tournament or teams competition on a given date, at which pilots register decks.
_Avoid_: Meet, comp

### Decks and cards

**Deck**:
One pilot's singleton entry at one event, with a placement result. The central hub of the graph.
_Avoid_: List, entry

**Decklist**:
The cards composing a deck, split across boards.

**Board**:
The section of a decklist a card sits in: Main or Side.
_Avoid_: Maindeck, sideboard, mainboard

**Card**:
A distinct Magic card identified by its canonical name, carrying type, mana cost, mana value, colours, and point value.

**Canon**:
The canonical lowercase card name that identifies a Card and joins it to external card data.
_Avoid_: Slug, key

### Classification

**Archetype**:
A named strategy engine a deck embodies (for example Grixis, Storm, Lands). A deck may carry several, each weighted, with one primary.
_Avoid_: Deck name, tag

**Macro**:
The broad strategic class of a deck: aggro, midrange, control, tempo, combo, prison, or ramp.
_Avoid_: Strategy

**Colour**:
One of the five Magic colours (W, U, B, R, G) associated with a card or deck. A card may be several colours.
_Avoid_: Colour identity (that is a combination)

**Colour Identity**:
The specific combination of colours a deck plays (for example UBR), derived from the deck's colours.
_Avoid_: Colours (the individual atoms)

### Format rules

**Points**:
The 7PH cost assigned to a powerful card. A legal deck spends at most 7 points, or 8 with the accessibility bonus. Most cards are 0 points.
_Avoid_: Cost (that is mana), price

**Points Version**:
A dated revision of the points list. Card point values change over time as versions are released.
_Avoid_: Update

**Era**:
The period between two Points Versions, during which point values are fixed. Defines what was legal when a deck was built.
_Avoid_: Season, period

**Reserved**:
Whether a card is on Magic's Reserved List. A deck running none of them earns the accessibility bonus.

**8-Point Deck**:
A deck that runs no Reserved List cards and may therefore spend 8 points instead of 7.
_Avoid_: Accessibility deck

**Placement**:
A pilot's finishing rank at an event, and its normalised form for cross-event comparison.
_Avoid_: Rank, position, result

---

See `docs/research-log.md` for cross-session data insights and handoffs.
