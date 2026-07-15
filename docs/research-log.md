# Research Log

Cross-session insights and handoffs that have no other structured home.

## 2026-07-14 - Title placement tokens agree with stored `placement` (backfill is safe)

- Checked the full `decks.json` snapshot (4553 decks): where a deck both has a stored `placement` and a leading placement token in its title (e.g. `05th/08th ...`, `13th ...`), they agree in **4501/4501** cases. **Zero contradictions** where both are present.
- The only discrepancies are the **51 decks with a null stored `placement`**; of those, the title still encodes a recoverable placement for **19** (the rest have `??`/`XX` placeholders or no token). 26 of the 51 nulls are the `nan`-pilot decks.
- Why it matters: a future placement-completeness / era-bucketing ticket can backfill the 19 missing `placement` values straight from the title token without fear of overwriting or conflicting with real data. The consistency was verified across the whole dataset, so that verification does not need repeating.

## 2026-07-15 - The analytics layer is mostly bipartite groupbys, not graph-native

- Dropping archetype-unique-cards (see ADR 0002) surfaced a broader pattern: card usage, co-occurrence, and adoption are all two-hop bipartite aggregations (deck-card projections / groupbys). None uses paths, shared-neighbour structure, communities, or traversal beyond two hops. The graph store is incidental to them.
- This is in tension with ADR 0001, which chose Kùzu specifically for multi-hop traversal and neighbourhood rendering over relational stores. Right now only the pilot-network / head-to-head views are genuinely graph-shaped; the rest would run as well on a dataframe.
- Why it matters: before building the next analytic, ask whether it earns the graph or is another groupby in a graph costume. The unexplored, genuinely graph-native directions are traversal-based: pilot communities via shared decks/archetypes, archetype-similarity clusters from shared card neighbourhoods, cards that bridge two archetypes, multi-hop paths. That is where the store's cost is actually justified.

[handoff] Open direction, not a decision. When picking the next feature, weigh a traversal-based insight against yet another bipartite view.
