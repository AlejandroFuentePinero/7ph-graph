# Graph model: facts as edges, analytics as query tools

The stored graph is a rich dimensional spine. Nodes are Pilot, Event, Deck, Card, Archetype, Macro, Colour, and CardType. Edges exist only for irreducible facts: who piloted what, where a deck was played, deck-to-card membership (with quantity and board), and a card's colours and type. Colour is modelled as the five atomic colours so a multicolour card links to each, which lets a card reach every deck of a given colour identity.

Derived relationships (card co-occurrence, hidden gems, pilot archetype affinity, pilot networks) are deliberately NOT materialised as edges. They are computed on demand as a growing library of parameterized Cypher query functions, which also become the future RAG's tools.

Note (issue #6, archetype unique cards, removed): the archetype-to-card query computed exclusivity ("cards found only in this archetype's decks", with a support floor), deliberately not a lift/distinctiveness "signature". A lift-based signature had been tried and rejected because it presumes a card means the same thing in every deck, which does not hold: the same card plays different roles across decks, and the graph can honestly assert only where a card appears, not what it does there. That left exclusivity as the only honest framing, and exclusivity did not earn its place, so the feature was dropped. Two grounds, and their combination is what made it a cut rather than a rework:

- Not graph-native. It reduced to a two-hop bipartite aggregation (per card, decks-in-archetype vs decks-anywhere, keep the equal ones), a groupby that never used paths, shared-neighbour structure, or connectivity. The graph was incidental.
- Low yield. Over the real data it was empty for 39% of archetypes (49 of 125) and had a median of one card, with the counts dominated by a few tribal/combo decks (Goblins, Elves, Scales). The "goodstuff" midrange archetypes, which share the format's best cards, returned almost nothing.

A query function that returns empty or near-empty for most inputs is worse than absent as a future RAG tool: the agent spends a call to learn nothing, or over-reads a one-card result. The honest reframe (a lift signature) was already off the table, so there was no fix that preserved both honesty and coverage. The self-containment count it produced as a byproduct (how many exclusive cards an archetype has) is the one salvageable signal, but that too is a groupby, not a graph result, and did not justify keeping the feature.

Note (pilot head-to-head): comparing two pilots is the shared-event overlap (events both entered), not the union of their two records. A union laid out as a top-down hierarchy (pilot -> event -> deck -> placement) was built and rejected: over a prolific pilot's full history it renders as an unreadable hairball, and the tree did not make deck ownership legible either. Narrowing the query to shared events, with each player's chain colour-tinted for attribution, is what keeps it readable. The lever is shrinking the result, not dressing up an oversized one, consistent with render-vs-refine (issue #7).

## Consequences

The interesting analytics are parameterized (by colour, era, placement), so no single materialisation would serve them, and pairwise co-occurrence as edges would explode the edge count. Keeping the spine to facts keeps the model easy to expand (Scryfall attributes, subtypes and tribes, temporal eras) without re-modelling. Colour Identity is kept as a Deck property, not a node, since it is derivable from the atomic colour edges.
