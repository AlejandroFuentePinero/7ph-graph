# 7 Point Highlander Graph

A knowledge graph of the Australian 7 Point Highlander (7PH) Magic: The Gathering metagame. It links events, pilots, decks, and cards down to card attributes, for exploration and analytics.

## Language

### People and events

**Pilot**:
A player who registered a deck at an event. Keyed in the graph by a canonical upstream id, which may be a pseudonym or handle rather than a readable name.
_Avoid_: Player, user, competitor
_Exception_: the field-wide race says **player**. The "Best player race" tab, its heading, and the FAQ entries about it are a public ranking of named people, read by the community those people are in, where "player" is the word they use for themselves and "pilot" is only the schema's; the plain-English rule that governs user-facing copy (issue #156) wins on that surface (issue #200). The line is the scope of the claim, not the surface: one deck or one entrant is a **pilot** ("every pilot in the race", "the standings rank 137 pilots"), while the field-wide race as a whole is a **player** race. No other surface takes the exception.

**Display Name**:
The human-readable name for a Pilot, recovered from deck titles. The primary player identity (ADR 0007): ids that recover an identical name (case-insensitively) are the same person, joined automatically on every build, unless a curated `[[split]]` or `[[reject]]` keeps them apart (ADR 0009). Not the graph key: the node keeps a canonical upstream id.
_Avoid_: Real name, player name (as a key)

**Event**:
A 7PH tournament or teams competition at which pilots register decks. It happened at some point in time, but the source records no date for it, so the graph places it no more precisely than its Year.
_Avoid_: Meet, comp

**Year**:
The calendar year an Event took place, derived from the earliest deck creation among its decks. The graph's temporal dimension: the axis for slicing the metagame over time. A proxy, since the source carries no event date, and the finest granularity that proxy honestly supports (ADR 0006).
_Avoid_: Era (a rules period, not a calendar one), season, date

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
A named strategy engine a deck embodies (for example Grixis, Storm, Lands). A deck may carry several, each weighted, with one primary. The weights are usually lopsided, so a deck normally belongs to one engine outright: median primary weight is 100 and median secondary 5. The tail is real, though, with about a fifth of secondary tags above 25 and 111 of 4590 primaries below 50. Key every archetype figure to the primary tag alone (ADR 0020, widened to every figure by ADR 0023), since counting every tag sums to roughly 160 percent of decks where figures sum, and pools a mixture of other engines rather than a larger sample of this one where they do not (Golgari carries 121 decks at any weight and 8 as a primary).
_Avoid_: Deck name, tag

**Macro**:
The broad strategic class of a deck: aggro, midrange, control, tempo, combo, prison, or ramp. No surface prints the word "Macro", so user-facing copy calls it a **broad class** and glosses it with the values ("aggro, control and the like"); the term itself would be jargon on a page written for any visitor, and "strategy" is the reading it must not drift back to (issue #142).
_Avoid_: Strategy

**Colour**:
One of the five Magic colours (W, U, B, R, G) associated with a card or deck. A card may be several colours.
_Avoid_: Colour identity (that is a combination)

**Colour Identity**:
The specific combination of colours a deck plays (for example UBR), derived from the deck's colours.
_Avoid_: Colours (the individual atoms)

### Format rules

**Points**:
The 7PH cost assigned to a powerful card. A legal deck spends at most 7 points, or 8 with the accessibility bonus. Most cards are 0 points. A card has two costs, not one, because the cost depends on the context it is played in: see Companion. A deck's total is derived (`query.deck_points`) and never stored, since it is a fact about a points list at a moment rather than about the deck.
_Avoid_: Cost (that is mana), price

**Companion**:
A card a deck names from its sideboard instead of playing in the deck, at a point cost the same card in the main board does not pay. Two cards in the format can be one, Lurrus of the Dream-Den and Lutri, the Spellchaser: free in the deck, 3 points as a companion. A deck names at most one, so a total charges the surcharge once (issue #143).
_Avoid_: Sideboard creature, partner

**Points Version**:
A dated revision of the points list. Card point values change over time as versions are released.
_Avoid_: Update

**Era**:
The period between two Points Versions, during which point values are fixed. Defines what was legal when a deck was built. Not a Year: an Era is bounded by points-list revisions, so its boundaries fall wherever a version lands, and one year may hold several Eras.
_Avoid_: Season, period, year

**Reserved**:
Whether a card is on Magic's Reserved List. A deck running none of them earns the accessibility bonus.

**8-Point Deck**:
A deck that runs no Reserved List cards and may therefore spend 8 points instead of 7.
_Avoid_: Accessibility deck

**Placement**:
A pilot's finishing rank at an event, and its normalised form for cross-event comparison. Every metric reads the normalised form, so a rank the project knows is always normalised, against the event's Field Size, whether the source scored it or the project recovered it (ADR 0016).
_Avoid_: Rank, position, result

**Field Size**:
The number of entrants a Placement is normalised against. The source ships one per Deck, and where a count contradicts it the build corrects it and re-ranks that event's norms, recording which rule decided (ADR 0015). Stored on the Event, and read from there rather than recovered by inverting a norm.
_Avoid_: Event size, tournament size, entrant count (the corrected field is not always an entrant count)

**Imputed**:
A value the project decided rather than the source supplying it. Recorded beside the value as the name of the rule that produced it: null where the source's own number stands, a rule name where a pass here produced it, and `none` where a rule was looked for and none fit. Every uncertain value carries one (`Deck.placementImputed`, `Deck.normImputed`, `Event.fieldImputed`), so "which of this thing's numbers did we decide, and under what rule?" is one query for every class of uncertainty (ADR 0016).
_Avoid_: Inferred, guessed, flag, derived (a derived value like Year is not an uncertain one)

---

See `docs/research-log.md` for cross-session data insights and handoffs.
