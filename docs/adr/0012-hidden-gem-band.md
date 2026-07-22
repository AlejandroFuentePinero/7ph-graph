# Hidden gems: a fixed band, with a share as the ceiling and a count as the floor

A hidden gem is a card that is rare within its slice yet places highly (user story 14). The definition is three fixed constants in `graph7ph.query`, not user controls:

| Constant | Value | Question it answers |
| --- | --- | --- |
| `MIN_GEM_DECKS` | 5 ranked decks | Is there enough evidence to trust this? |
| `MAX_GEM_SHARE` | 10% of the slice | Is this still rare? |
| `MAX_GEM_MEAN_NORM` | 0.33 | Is it actually overperforming? |

All three are measured over rank-bearing decks only. A deck with no recorded placement cannot confirm over- or under-performance, so it counts toward neither bound nor the mean rather than padding the band.

## Only the ceiling is a share

The two bounds look symmetric and are not. They answer different questions, and only one of them is relative to the meta.

The ceiling asks whether a card is still rare, which is meaningless except against the size of the slice it sits in. This was the original bug: an absolute `max_decks` of 10 meant "fringe tech" against 4501 global decks (0.2%) and "near staple" within the 97-deck Goblins slice (10%). One number, two incompatible meanings. A share means the same thing in every slice, which is why the ceiling became one.

The floor asks whether we have enough decks to believe the result, which is a property of sample size and does not scale with the meta around it. Five decks is the same amount of evidence whether the format has 100 decks or 100,000. Making the floor a share fails in both directions at once:

- **Too strict globally.** A 1% floor is 45 decks. Regression to the mean means almost nothing appearing in 45+ decks still averages top-third, so the whole-meta gem view collapses to 4 cards.
- **Too loose in small slices.** A 1% floor within Storm's 106 decks is 1.06 decks, admitting 2-deck cards with a 0.128 mean: precisely the lucky-draw noise the floor exists to reject.

We rejected `max(5 decks, 1% of slice)` as a compromise. The absolute guard does all the work in every slice small enough to matter, and the share half still collapses the global view to 4 gems. It is the drawback of a share floor with extra arithmetic.

With an absolute floor and a share ceiling, every slice size behaves: Global 34 gems, Grixis 8, Jund 5, Storm 19, Oracle 9, Goblins 1. (These counts, and the archetype and deck totals stated later, were measured against the graph built 2026-07-22, provenance `built_at: 2026-07-22T00:29:04Z`. They move as decks are added, so a later mismatch is data movement rather than regression.)

## Below 50 ranked decks there is no answer

A floor of 5 and a ceiling of 10% cross at 50 ranked decks (`MIN_GEM_SLICE`). Under that, the ceiling falls below the floor and the band asks for a card in "at least 5 decks and at most 2", which nothing satisfies. 75 of the format's 126 archetypes sit in that range.

This is not an arithmetic slip to patch. It is the bounds correctly detecting that the slice cannot support the claim: in a 20-deck archetype, 5 decks *is* a quarter of the meta, so "rare" and "attested by 5 decks" are contradictory. There is no band that satisfies both, at any setting.

So `hidden_gems_subgraph` raises `SliceTooSmall`, and the gem view offers only the archetypes `gem_archetypes` lists (those with at least `MIN_GEM_SLICE` ranked decks). Refusing beats returning "none", which would read as "no gems here" when the truth is "not enough decks to tell".

We rejected making the floor a share to dissolve the crossover. It works arithmetically and fails empirically: a 1% floor is *below one deck* in every slice under 100, so the floor stops existing exactly where it is needed. Abzan then returns 65 gems with 83% of them resting on one or two decks, and Tron's best is a card in a single winning deck. That converts a dead zone into a noise zone, which is worse: an empty view is honest, whereas 65 fabricated gems look like an answer.

We also rejected `ceiling = max(floor, share)`, which degenerates the band to exactly `[5, 5]` in small slices and would call a card in 5 of a 10-deck archetype (50% of it) a hidden gem.

## Fixed, not dials

The band, the ceiling, and the placement bar were all `gr.Number` controls. They are now constants. "Which rare cards overperform?" has one answer, not one per dial setting, and three interacting numeric knobs invited the user to manufacture a result rather than read one. Fixing them makes the view a claim the tool stands behind. Changing a constant is a code change with an ADR, which is the level of deliberation these numbers deserve.

## No colour filter

The gem hunt filters by archetype only. Colour was dropped because guild-named archetypes (`grixis`, `jund`, `bant`, `esper`) already carry it, making the pair mostly redundant, and colour alone was the weak "globally rare green cards" query.

This is a real loss, not a no-op: colourless archetype tags (`storm`, `lands`, `nadu`, `reanimator`, `bots`, `oracle`) no longer expose their colour axis at all. Accepted, because within one of those archetypes the interesting question is which tech overperforms, not which colour it happens to be. The `DECK_COLOUR` edge remains in the stored model, so a colour slice can return without a rebuild.

## Consequences

The archetype dropdown offers 51 of the format's 126 archetypes. The other 75 hold 18% of the ranked deck-pairs, so the long tail of the format has no gem view at all. Accepted: those slices could only ever have produced noise.

Of the 51 offered, 8 legitimately return no gems (Breach, at 127 ranked decks, among them). An empty result is 0 nodes, which is under the render threshold and would draw as a blank canvas reading as a broken app, so the view states that nothing matched instead of drawing nothing.

The unfiltered gem view returns 34 gems but drags in the 308 ranked decks that run them, over the 250-node `RENDER_THRESHOLD`. It therefore refuses to draw and refines instead, pointing the user at the archetype filter (the only remaining control). Every archetype-scoped view renders. The whole-meta view is reachable as a number but not as a picture, which is a consequence of the node budget rather than of the band.
