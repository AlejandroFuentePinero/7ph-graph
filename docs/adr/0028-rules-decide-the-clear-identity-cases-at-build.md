# Rules decide the clear identity cases at build

Supersedes ADR 0005's wave 1 ("only hard facts merge") and its trigger section for pairs. Extends ADR 0004 and ADR 0007, which stand: the upstream id is the node key, and an identical recovered name joins on every build.

## Context

The identity process had two halves. The automatic half (the identical-name join, the event split, the duplicate drop, rejects and splits) works on every build. The half meant to make it self-curating settled nothing: 124 holds, zero decisive triggers fired in four ingests, and a review queue that grew 35, 44, 57 across three rounds because no human sitting had happened since 2026-09-01 (issue #266). The owner's aim is no constant manual curation and no change that can regress the graph by getting a merge wrong, so before any rule was automated it was measured against the decisions on file. Issue #266 records the method and the grilling; this ADR records what shipped (issue #273).

## Decision

Every build derives one of three states for every candidate pair the name heuristics raise, writes nothing, and reports state changes. There is no human queue: a watching pair is two nodes, the graph's default.

**The composed rule.** A pair is **merged by rule** when its deck evidence clears the bar set for its name shape and it shares no event; **separated** when both ids registered at one event with different decks and neither side is a hub; **watching** otherwise. The three clauses compose, and only the composition has no labelled failure: the bar alone merges four rejected pairs on file, every one of them caught by the shared-event veto.

**Deck evidence** is the best card-set overlap (Jaccard over the whole 75) across every pair of the two ids' decks on the same primary archetype at different events, judged raw and as a percentile of the matched stranger pool. The **pool** is every pair of decks of distinct pilots on that archetype and colour whose registration gap is within a factor of two of the pair's own, the pair's gap floored at 30 days so a pair registered days apart is read against at least a month of strangers rather than a window of days (owner's ruling, 2026-11-06), scored exhaustively with no sampling cap (no archetype-colour group exceeds 400 decks, and a sampler would make a pair's pool order-dependent) and once per group. Where the two decks differ in colour the pool takes the busier side's. The pair's own two decks are in its pool, so "at or above" counts the pair itself: one more pair at the top costs a merge, never buys one. A pool under 100 pairs yields no percentile: one per cent of a smaller pool is no pair.

**The bar per name shape**, with the labelled set it was set on (202 merged and 45 rejected or split pairs in the dictionary on 2026-11-04; the engine raises 160 of the merged and 39 of the rejected at the resolved node, the rest being pairs no heuristic relates):

| shape | bar | merged on file | reproduced by rule | rejected on file | bar alone would merge | composed rule merges |
| --- | --- | --- | --- | --- | --- | --- |
| non-name typo (insertion or deletion on five or more letters, or a transposition of any length) | 0.4 raw | 26 | 15 | 0 | 0 | 0 |
| handle | 0.9 raw, or at or above the pool's 99th percentile | 53 | 11 | 9 | 1 | 0 |
| nickname | same | 49 | 12 | 13 | 3 | 0 |
| bare first name | same, and only as a single deck | 22 | 6 | 3 | 0 | 0 |
| look-alike (a substitution, or an insertion or deletion under five letters) | never | 10 | 0 | 14 | 0 | 0 |

The per-shape bars are a choice under asymmetric loss, not a measurement. A bar is bounded from below only by a rejected pair that clears it with no shared event, and there is none: the four rejects that clear a bar all share an event, and the look-alike shape, the one with rejects to spare, never merges. So the data says where a bar is safe on this record, not where it would stop being. A false merge fuses two people for every metric downstream and the event split cannot undo it; a pair left apart costs nothing. The typo bar is set at 0.4 because its shape cannot reach another real name in one slip and 0 of its 26 merges on file were rejected, and it keeps ADR 0005's rule that a name-only bet needs a deck behind it. The name shapes take 0.9 because each fits several people.

**The shared-event veto.** Two ids both registered at one event with different decks never merge by rule, hub or not (duplicate registrations are already dropped). Outside the hub guard the same fact separates the pair. The `nan` sentinel a stranded deck carries is a hole, not an event.

**Hub guard.** A bare first name and a numbered event-split family (an id the split numbers because it entered one event more than once, read off the node's own decks before the split runs) can each hold several people. Neither is ever separated by rule. A numbered family merges by rule when it shares no event with its partner. A bare first name merges by rule only when its side is a single deck.

**Canonical id.** For handle and typo shapes the real-name side; for nickname and bare-first-name pairs the busiest, id to break ties (the join copies the canonical's display name without a re-vote, so busiest-then-id would have named the Toby M / TobM7 node "TobM7"). A typo is a slip that does not accumulate decks (ADR 0005's single-deck slip), so the busier spelling is read as the real one (owner's ruling, 2026-11-06; the four typo merges on the record keep the correct spelling this way, at 6, 14, 6 and 9 decks against 1); where the slip is busier the node carries it and a `name` pin corrects it.

**Pipeline.** The rules read the resolved node after dictionary merges and the identical-name join, and fold before the event split, so a collision a merge creates is renumbered rather than fused. Rule merges sharing an id fold transitively (every such case on file is three spellings of one name inside one dictionary merge), unless the chain holds a pair kept apart, by the rule's separator or by a dictionary `reject` or `split` (issue #288): an id that matches two people kept apart would fuse them through itself, so no merge in that chain folds and each watches with that id marked as a hub (none on file). The dictionary-apart pair is read off the raw ids under the chain, since the dictionary takes it off the rule's list before the separator sees it, and the merges it holds back are listed as overridden. A dictionary `reject` or `split` beats a rule merge and a dictionary `merge` beats the separator, both by pipeline order, and every pair a rule fired on that the dictionary overrode is listed every build, derived rather than diffed, because a dictionary edit is invisible to a diff between two snapshot sequences.

**Holds retire for pairs.** The 96 pair holds were deleted and a pair `hold` is refused at load; the `shared_event`, `joined_name` and `new_decks` triggers retired with them, and the reasoning stays in git history. The 28 single-id holds and `event_split` stay: the multi-name queue is one id with several surnames, which these rules do not cover (25 of the 28 watch a dictionary merge).

**No undo, no review of dictionary merges by the pair rules.** A rule merge is recomputed every build and comes apart on its own if it stops clearing its bar. The 176 dictionary merges stay and are not re-derived: re-deriving them would split about 150 pairs the rules cannot reach, and three on file cross a shared event by human ruling (`Zachary M` = `Zachaery M`, `JamL9` = `James L 2`, `DanS109` = `Dan S 2`), all under the hub guard today.

**Report.** Three blocks, merged / separated / watching, listing only pairs whose state changed since the previous snapshot sequence (`ingest.previous_reconciliation`), a hub once with every partner and each partner's best overlap, the entries the build refused (issue #283), and one block listed every build for pairs a rule fired on that the dictionary overrode. The curation brief carries it; the ingest session pastes it in chat.

## What it does on the record

On the 2026-11-04 sequence under the dictionary on file: 9 pairs merged by rule (Alex W, Ben C, Mai F, Gianni C, Joseph M, Sebastiano C, Ben P, Toby M, and Michael W / Micahel W at 0.77 on the typo bar), 2 separated (Matthew M / Matt M, Josh W / Joshua W, both at SSB), 139 watching, no override. A reject or split is matched on every raw id under a node, not on the node id alone: two rejects on file name a raw id the identical-name join folded into its namesake, and the ruling travels with it. Darcy / Darcy E watches at 1.00: a bare first name with two decks. With the dictionary off, the composed rule run over every pair the heuristics raise merges none of the 39 rejected or split pairs and separates none of the 160 merged ones (`tests/test_identity_rules.py`, the replay), reproducing 44 of the 160 merges.

**The cost of each guard.** The shared-event veto costs 2 merges on file that clear the bar (`Dan S` / `DanS109` at 1.00 and `Zachaery M` / `Zachary M` at 0.91, both human rulings that cross a shared event). The bare-name clause costs 3 (`Nando N` / `Nando` at 0.89, `Vincent` / `Vincent C` and `Leo` / `Leo M` at 0.97). The guarded separator settles 10 of the 39 rejects; the other 29 watch, 18 of them sharing no event. Reading the review's row dump with the composed rule gives 43 reproduced merges; the engine's 44th is `DylB25` / `Dylan B` at 0.72, which clears the top percentile at 162 of 16,207 in the exhaustive pool and not in the dump's sampled one.

**The false-merge rate.** Not 0.5% at 0.9 raw, as issue #266 first said: four rejected pairs overlap at 0.9 or more (`Matthew B 1` / `Matt B 1` at 1.00, `JamM72` / `James M` 0.97, `Ben H 1` / `Benjamin H` 0.97, `Ben S 1` / `Benjamin S` 0.95), every one caught by a shared event and none by the bar, because name-alikes who share a list are teammates or family, not strangers. What has no labelled failure is the composed rule, and on pairs without a shared event that is 0 of 18 rejects, which bounds the rate at about 16% from a sample that small. The honest range per rule merge is 1% to 9%, and the replay test asserts the composed rule, never the bar alone.

**The stranger base rate**, the out-of-sample instrument, over the pools the rules judged against on 2026-11-04 (every pair of decks of distinct pilots on one primary archetype and colour, all gaps, before the rule merges fold; about 242,000 pairs): 77% overlap at 0.4 or more, 57% at 0.5, 6.3% at 0.8, 1.0% at 0.9, 0.17% at 1.0. The 0.5 bar ADR 0005 called safe is the stranger median; 0.9 is the top percentile, which is what the percentile branch of the bar reads directly. It is re-measured on every ingest by the build itself, which writes it into the reconciliation report as `stranger_base_rate` from the pools the rules judged against, and the brief prints it under Counts; `scripts/identity_figures.py` prints the labelled table above from the snapshots for the next time the bars are questioned.

**Against the figures the ticket carried.** Issue #273 quoted the review's row dump: shape counts 24/0, 49/9, 43/13, 22/3 and 9/17 for substitution, 0 of 19 rejects without a shared event, a separator settling 7 rejects and a bare-name clause costing 2 merges. The dump read names after the event split and cannot be re-run from the repo; the engine reads the node before the split and the family guard off the node's own collisions. This page carries the engine's figures, which the replay test pins and `scripts/identity_figures.py` reproduces (owner's ruling, 2026-11-06).

## Independent review

Before the bars shipped, two reviewers with no sight of the proposed bars re-derived them from the same labelled set and pool and reproduced the row dump exactly; every disagreement went to the owner one pair at a time with the raw evidence, both too loose and too tight, starting from the 78 merges on file made with same-archetype overlap under 0.5. Six rulings changed and the owner approved all six; they are the amendments on issue #266 and the rule above is the amended one.

## Consequences

- A watching pair is not work. The brief lists changes, and a human who disagrees with a rule writes a `reject` or `merge`, which wins by pipeline order and is listed every build under "rule overridden by the dictionary".
- A rule merge is immune to upstream reusing an id (issue #261, ADR 0005 amended): it reads whoever owns the id now, where a written merge inherits the exposure.
- The identity figures in the FAQ now count the watching pairs, and the glossary carries **candidate pair** and **hub** (`CONTEXT.md`).
- The multi-name queue is untouched and still a human's; a later ticket may give it rules of its own.
