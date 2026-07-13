# Graph model: facts as edges, analytics as query tools

The stored graph is a rich dimensional spine. Nodes are Pilot, Event, Deck, Card, Archetype, Macro, Colour, and CardType. Edges exist only for irreducible facts: who piloted what, where a deck was played, deck-to-card membership (with quantity and board), and a card's colours and type. Colour is modelled as the five atomic colours so a multicolour card links to each, which lets a card reach every deck of a given colour identity.

Derived relationships (card co-occurrence, archetype signature cards, hidden gems, pilot archetype affinity, pilot networks) are deliberately NOT materialised as edges. They are computed on demand as a growing library of parameterized Cypher query functions, which also become the future RAG's tools.

## Consequences

The interesting analytics are parameterized (by colour, era, placement), so no single materialisation would serve them, and pairwise co-occurrence as edges would explode the edge count. Keeping the spine to facts keeps the model easy to expand (Scryfall attributes, subtypes and tribes, temporal eras) without re-modelling. Colour Identity is kept as a Deck property, not a node, since it is derivable from the atomic colour edges.
