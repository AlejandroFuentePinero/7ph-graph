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

## 2026-07-15 - The explorer's view-tuning arc is closed

- Everything after issue 7 closed (`bd9fe84..HEAD`, plus the hidden-gem band) was one arc: not new plumbing, but **deciding what each analytic view actually means**. Pilot identity got same-event splitting (ADR 0004); affinity gained a macro tier and head-to-head; card usage was recast as adoption rate, then re-rendered as uniform dots; co-occurrence was reworked to top-N by rate with a two-card intersection; archetype-unique-cards was **cut** rather than fixed (ADR 0002); hidden gems got a fixed, documented band (ADR 0012). Each view was taken one at a time until its definition was defensible.
- **This arc is deliberately finished, not abandoned.** Hidden gems was the last view to tune. Do not reopen view-by-view fine-tuning on a hunch: if a view's definition is questioned again, it needs a reason that its ADR does not already answer.
- The v1 epic (#1) stays open on work that is *not* view definition: deployment to a Hugging Face Space (#8), applying human pilot-identity decisions (#9), and preserving analytic metrics in query results so the v2 tool layer is not foreclosed (#12).
- Why it matters: a cold reader sees an open epic with 28 user stories and several open issues, and cannot tell which parts are settled. The views are settled. The remainder is packaging, curation, and the v2 seam.

[handoff] The bipartite-vs-traversal question above is now the live one. With view tuning closed, the next feature is a genuine choice between the open v1 remainder (#8/#9/#12) and a first traversal-native analytic; it is no longer competing with "one more pass on an existing view".

## 2026-07-20 - The golden-subgraph gate cannot be verified by its own unit tests

- `compare()` in `graph7ph/baseline.py` has 24 unit tests over synthetic subgraphs, and every one of them passed while the real behaviour was badly wrong. A version that matched rows on their raw float values reported **36 differences, listing 17 cards as both added and removed**, where the truth was 2. The synthetic fixtures had too few rows and no engine-scale float noise to expose it. Only re-running a mutation battery against the built graph caught it.
- The battery: copy `baseline/subgraphs.json`, apply one mutation, run `uv run graph7ph baseline --baseline <mutated>`. Shuffling the rows of `gems_whole_meta` or `pilot_many_events` must **pass** (they are the order-insensitive queries), and so must shifting every `mean_norm` by 5.6e-17. Reversing `cooc_pair_shared_decks` rows, removing one gem node, deleting a whole case, and shifting `mean_norm` by 8.6e-4 must each **fail**, and the removal must name the card that moved. Measured results are tabulated in the issue #45 comment thread.
- Why it matters: issues #47 through #50 are all graded by this gate, and a gate that under-reports is indistinguishable from a clean migration. Anyone editing `compare`, `_identity`, or `_same` should re-run the battery, because a green `uv run pytest` is not evidence the gate still works.

[handoff] The battery lives only in session scratch, not in the repo, because it needs the real built artifact that tests cannot reach. Rebuild it from the list above rather than trusting the unit tests.
