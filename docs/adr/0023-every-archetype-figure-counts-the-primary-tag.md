# Every archetype figure counts the primary tag, adoption rates and pilot affinity included

ADR 0020 fixed the primary tag as the archetype unit and said so without qualification: "an archetype is a deck's primary tag and nothing else". Two surfaces never took it. `card_usage` and the pilot archetype affinity graph match `HAS_ARCHETYPE` with no `isPrimary`, filed as #198. This is that ticket, and unlike ADR 0022's split it does not split: both surfaces take the filter.

Nothing printed was arithmetically wrong. Both sides of every ratio shared the wider definition, so a rate over all-tag decks divided by all-tag decks was sound on its own terms. What was wrong is that the population differed from every other archetype figure in the app and nothing said so, and that the population itself is not the thing the label names.

## The two readings are far enough apart to matter

Over the artifact's 4,591 decks and 126 archetypes, all-tag denominators sum to 7,370 (161% of decks) against primary-only's 4,590. Two archetypes exist only as other decks' secondary labels and disappear entirely: Black Walks (28 all-tag decks) and Deadpool Walks (25).

The tags being counted are mostly token commitments. Of 2,780 secondary tags the median weight is 5 and 71% sit at 5 or below, so under the all-tag reading a deck 5% committed to Storm is one full Storm deck, weighted identically to a deck that is Storm outright.

On `card_usage`, 47,864 (card, archetype) adoption pairs are drawn. **10,830 of them (22.6%) have no primary-only counterpart at all**, and of the 37,034 that survive:

| | |
|---|---|
| identical percent | 45.6% |
| within 5pp | 82.2% |
| 10pp or more apart | 3,878 (10.5%) |
| 25pp or more apart | 768 (2.1%) |
| median / mean / p95 / max | 1pp / 3.4pp / 16pp / 96pp |

The median pair barely moves, which is why this went unnoticed. The tail is where the surface breaks, and it concentrates on archetypes that are almost purely secondary labels:

| archetype | all-tag decks | primary decks | share primary |
|---|---|---|---|
| Sultai | 217 | 9 | 4% |
| Golgari | 121 | 8 | 7% |
| Bant | 160 | 18 | 11% |
| Nadu | 203 | 92 | 45% |
| Grixis | 538 | 386 | 72% |
| Jund | 354 | 283 | 80% |

Troll of Khazad-dûm reads 11% of Golgari decks under all-tag and 88% under primary-only. Deathrite Shaman reads 26% of Sultai against 100%. Crop Rotation reads 77% of Golgari against 12%.

## The pilot graph was answering its own question backwards

`pilot_archetype_affinity` exists to say whether a pilot is a specialist or a generalist (user story 16). Counting every tag manufactures generalists:

| | all-tag | primary-only |
|---|---|---|
| archetype nodes drawn | 3,828 | 2,543 |
| pilots reading as pure specialists | 284 (26.2%) | 596 (54.9%) |

**The specialist population doubles under the filter**, and 696 of 1,086 pilots (64.1%) see their archetype-node count change. A pilot who entered one Grixis deck carrying a Storm side-tag at weight 5 was drawn playing two engines. Here the wider reading was not a different defensible population, it was the wrong answer to the only question the surface asks.

Of the 2,543 surviving nodes, 87.9% keep an identical event count. The change is about which nodes exist, not how they are sized, so no edge label a reader has learned to read moves under it.

## A bigger pool is not a better sample

One measurement argues the other way, and it is worth recording because it is real. Denominator health of the archetype nodes `card_usage` actually draws gets **worse** under the filter:

| denominator | all-tag | primary-only |
|---|---|---|
| under 10 decks | 9.7% | 18.2% |
| 10 to 29 | 16.7% | 28.7% |
| 30 or more | 73.7% | 53.1% |

That is sample size, and sample size is the wrong test. ADR 0020 already measured the right one: "pooling secondary tags makes a slice a mixture of sub-archetypes". A denominator of 121 Golgari decks of which 8 are Golgari is not a larger sample of Golgari, it is a precise description of a different population wearing Golgari's name. The sharpest case in that ADR is Lands, which lost only 16 of its 210 decks to the filter and still lost four of its six gems, because what pooling had damaged was the slice's homogeneity rather than its size.

Sample size answers how noisy a figure is. It never answers what the figure is of.

One half of ADR 0020's reasoning does **not** carry here, and over-reading it would be a mistake. Gems break a hypergeometric null that assumes one homogeneous pool, so there the mixture invalidates the arithmetic. An adoption rate is descriptive and has no null to break. The case for the filter on `card_usage` is that the population should be the one the label names, not that the statistics were invalid. The 22.6% of pairs that disappear are not a loss of signal being suppressed; they are rates that were never about the archetype they were filed under.

## The thin-slice cost is accepted, not dismissed

18.2% of drawn archetype nodes will sit on fewer than 10 decks, and at that size a whole percent moves by more than 10 points per deck. ADR 0020 met the same wall and answered it with a floor (`MIN_GEM_SLICE`) rather than by pooling secondary tags back in, which is the precedent this follows in principle. Whether the adoption tier needs its own floor is left open rather than decided here: `_rate_edge` already carries `decks` and `total_decks` on every adoption edge, so the sample size is in the payload and a floor can be added later without changing what the numbers mean.

## This narrows what ADR 0014 cleared

ADR 0014 audited the affinity macro row and passed it: "a cover, not a partition ... children total 1.85x their parent over 1144 of 1904 rows, by the documented multi-label model", verdict **label-only**. That verdict was correct on its own terms, and it is withdrawn rather than overturned on new evidence. It asked whether the drawing matched the documented model, and it did. What it did not ask is whether the model answers the question the surface poses, and for the specialist read it does not. Over-covering is now 0 of 1907 rows, though as a property of this artifact rather than a guarantee: two decks a pilot entered at one event under one macro could still carry different primaries.

The same audit's adoption row ("correct number, wrong-looking placement") is untouched in kind. That finding is about a rate hanging under a macro that holds few of its decks, which the primary filter narrows but does not resolve.

Both surfaces already disclosed their population, which #198 asserts they did not. `faq-affinity` said a deck "is counted under every one of them", and `faq-usage` said an archetype's share "counts every deck tagged with that archetype, not only the decks whose main archetype it is". So AC3 of that ticket was already met, and the defect was never silence: it was two surfaces truthfully describing a population that answers the wrong question. Both entries are rewritten here, because accurate copy about a reading that has been withdrawn is worse than no copy.

## Consequences

Four patterns in `query.py` take `{isPrimary: true}`, joining the three that already carry it (906, 944, 1261): `arch_total`, `arch_run`, `dominant` and the pilot affinity `OPTIONAL MATCH`. **All three of `card_usage`'s move together.** `dominant` chooses the macro each archetype hangs under, and filtering the two rate queries while leaving it wide would split the population inside one function, which is the defect this ADR closes rather than a smaller version of it. Both docstrings now name the population, as `_gem_slices` and `_gem_card_decks` already did.

`CONTEXT.md` was where the drift started. It scoped the rule to "shares and finishes" and justified it only by the 161% sum, which reads as permission for any figure that does not sum. It now keys every archetype figure to the primary tag and records the mixture reason beside the summing one.

One test pinned the old reading deliberately, and inverting it is how this shipped: `test_pilot_affinity_macro_edges_over_cover_a_multi_tag_deck` asserted the Grixis node a secondary tag produced, and is now `test_pilot_affinity_reads_a_multi_tag_deck_as_its_primary_only`. `card_usage` had no multi-tag coverage at all and gains two tests, one for the two rate terms and one for the grouping macro, since a fixture that moved only one of the three would let the others stay wide. `_archetype_fields` already accepted a list of tags, so no shared fixture changed: the bespoke snapshots carry the secondary tags at weight 50.

One live-graph guard was rewritten rather than regraded. The affinity nesting invariant was pinned to a hand-counted population (`sole_parent == 3460`) inside `test_the_luck_count_is_the_same_number_on_every_call`, a test named for the first of two unrelated properties it asserted. This change moves that count to 2349, and regrading the pin to what the changed code prints would have turned the guard into a transcript of the implementation: a number nobody can re-derive asserts nothing. The invariant is now its own test, `test_pilot_affinity_draws_the_deck_rows_own_record_and_nests_parts_in_wholes`, and the population is not a number at all: the parent macros of every drawn archetype node must equal the (pilot, primary archetype, macro) triples read straight off the deck rows, with no `pilot_affinity_subgraph` in the derivation. A query that re-widens to every tag now disagrees with the graph's own rows; re-widening each of the four patterns one at a time was run as a mutation check, and each mutant is killed by a named test.

Two archetypes vanish from the surfaces this rule governs. Black Walks and Deadpool Walks are nobody's primary, so they hold no decks under the rule and stop being drawn on card usage and pilot affinity, which is what the rule means rather than a side effect of it. They do not leave the app: the meta-share matrix stays rectangular over the `Archetype` nodes on purpose (ADR 0014), so both remain in the manual picker as lines of real zeros, the honest statement that no deck ever led with them.

The no-regression oracle goes red with this change and stays red until recaptured. `baseline/subgraphs.json` holds four `usage_*` and two `affinity_*` cases whose answers this ADR moves on purpose (`affinity_second_pilot` alone loses 8 archetype nodes), and `docs/development.md` requires the `--force` recapture to land in its own commit with nothing else in the diff, saying which lines are real, as ADR 0020's recapture did. So this ships as two commits: the change, then the regrade, and a red gate between them is the procedure working rather than a defect.
