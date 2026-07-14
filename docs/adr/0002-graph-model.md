# Graph model: facts as edges, analytics as query tools

The stored graph is a rich dimensional spine. Nodes are Pilot, Event, Deck, Card, Archetype, Macro, Colour, and CardType. Edges exist only for irreducible facts: who piloted what, where a deck was played, deck-to-card membership (with quantity and board), and a card's colours and type. Colour is modelled as the five atomic colours so a multicolour card links to each, which lets a card reach every deck of a given colour identity.

Derived relationships (card co-occurrence, cards unique to an archetype, hidden gems, pilot archetype affinity, pilot networks) are deliberately NOT materialised as edges. They are computed on demand as a growing library of parameterized Cypher query functions, which also become the future RAG's tools.

Note (issue #6): the archetype-to-card query is exclusivity ("cards found only in this archetype's decks", with a support floor), not a lift/distinctiveness "signature". A lift-based signature was tried and rejected because it presumes a card means the same thing in every deck, which does not hold: the same card plays different roles across decks. The graph can honestly assert only where a card appears, not what it does there. Any future distinctiveness metric must account for that.

## Consequences

The interesting analytics are parameterized (by colour, era, placement), so no single materialisation would serve them, and pairwise co-occurrence as edges would explode the edge count. Keeping the spine to facts keeps the model easy to expand (Scryfall attributes, subtypes and tribes, temporal eras) without re-modelling. Colour Identity is kept as a Deck property, not a node, since it is derivable from the atomic colour edges.
