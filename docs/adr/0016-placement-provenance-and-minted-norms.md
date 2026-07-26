# Placement provenance: every value we decide names its rule, and a known placement is always normalised

Every surface in the app plots or averages `placementNorm` and reads `placement` nowhere, so a deck holding a placement without a norm falls out of every ranked metric while the graph looks complete. 28 decks sat in exactly that state. Their placements were decided long ago (`placement_from_title` reads a tie band at its best end per ADR 0014, `resolve_cut_placements` derives CanBrawl2's nested tiers from its cohort), and ADR 0015 then deliberately declined to normalise them: "norms are rescaled, never minted", with minting left separable as its own change. This is that change, plus the record-keeping that would have made the gap visible without a screenshot.

The build now mints a norm for every deck whose placement is known, and every value this project decides carries the name of the rule that decided it.

## The gap was a directional bias, not one broken line

The 28 decks span 8 events and 27 pilots. 27 of the 28 are top-8 finishes and 6 are outright wins, mean norm 0.0883 against a population mean of 0.4792. The excluded set is not a random sample of the record: it is almost entirely the good finishes, so every `avg(placementNorm)` the app drew was biased toward worse finishes, in one direction, hitting hardest the pilots and archetypes that made the cut.

It was visible on exactly one surface. The pilot head-to-head timeline draws a line through each pilot's finishes, so Chifley C against Brennan C broke between May 2024 and January 2025 with a `None` sitting where TGC2024's tied-5th belonged. Everywhere else the omission rendered as a plausible number.

The 28 break down as 16 placements read from a title's explicit range, 8 derived from CanBrawl2's cohort, 3 read from a title's single bound, and one case where the source scored the placement itself and shipped a null norm anyway: Area52IQ claimed an `eventSize` of 1 for a 7-deck event, and `(1 - 1) / (1 - 1)` is a division by zero. The source failed to normalise its own data.

## Minting is a rule over the population, not a fix for 28 decks

`_mint_norms` runs once in the build, beside `_rescale_norms`, over every deck. It asks two questions: is a placement known, and is a field known. It consults nothing about *how* the placement became known, which is what makes it general. A future class of recovered placement is normalised the build it lands, with no change to this pass. That matters because the metrics resting on placement are growing, not settling.

It is a separate pass rather than a branch inside `_rescale_norms` because the formula is shared but the domain is not: a deck arriving at the rescale carries a norm and leaves with a different one, while a deck arriving here carries none and leaves with its first.

The denominator is `EventField.field_size`, the same corrected field the rescale uses, so a minted norm and a rescaled one at the same event are ranked against the same number.

Where the field cannot serve as a denominator, the deck stays unranked and records why. A null field size, or the field of 1 Area52IQ claimed before Rule A refuted it, is the division the source itself failed on. So is a rank the field cannot hold, and this is checked rather than argued: the title grammar accepts a leading `0th`, which scores below 0.0, and a rank past the field scores above 1.0 wherever the field is not corrected, since `corrected_field` whitelists `Tournament` and a Teams event's claimed size stands as given. Neither shape exists in today's record. The guard is what keeps that a measured fact instead of a property of the current corpus, because a norm outside 0..1 is not a finish, it is arithmetic on two numbers that do not belong to each other, and it would average as though it were one while `normImputed` claimed a rule stood behind it.

## Rule strings, one column per decided value

Before this change the same idea had three shapes. `Event.fieldImputed` held a rule letter. Placement provenance was recorded nowhere at all: `placement_from_title` set a value and `resolve_cut_placements` silently overwrote it. And the 54 norms `_rescale_norms` had already rewritten were unmarked, so 54 decks carried a number the source never wrote with nothing saying so.

`Deck.placementImputed` and `Deck.normImputed` now follow `fieldImputed`'s shape, and the 54 are backfilled as `rescaled`. Rule names rather than booleans, and one column per decided value rather than one per feature, so "which of this deck's numbers did we decide, and under what rule?" answers in one query for every class of uncertainty, including ones not invented yet.

| `placementImputed` | n | How the placement was obtained |
| --- | --- | --- |
| null | 4540 | The source scored it |
| `title-range` | 16 | `05th-08th`, `3rd/4th`: best-of-range per ADR 0014 |
| `cohort-cut` | 8 | CanBrawl2's `Top 4`/`Top 8`, resolved to 1st/5th by its cohort |
| `title-single` | 3 | `1st - Robert L`, `121st` |
| `none` | 24 | No placement recoverable at all |

| `normImputed` | n | Where the norm came from |
| --- | --- | --- |
| null | 4485 | The source scored it |
| `rescaled` | 54 | Re-ranked against a corrected field (ADR 0015) |
| `minted` | 28 | Normalised here for the first time |
| `none` | 24 | No placement to normalise |

### `none` is a rule, not a null

The distinction carries the invariant. A null means the source's own number stands; `none` means a rule was looked for and none fit. Collapsing the two is what let 28 decks sit in an unnamed third state for as long as they did, because a null norm beside a real placement read exactly like a null norm beside none.

With `none` written down, the invariant is stateable and testable over the whole record: **every Deck either carries a norm or says outright that no rule could give it one.** Nothing sits between the two. `tests/test_build.py` asserts it against the built artifact and enumerates the 24 decks it admits, so the next class of uncertainty fails a test the day it lands instead of surfacing as a gap somebody notices in a chart months later. It skips rather than passes when the artifact is stale, since a stale bundle would grade the invariant against superseded build code (issue #55).

## Where it runs, and why not at ingestion

In `build.build_graph`, after the field sizes are derived and after the rescale. Not in `ingest.py`, for the reason ADR 0015 already gives: a value imputed there would be indistinguishable from source data in `ingest.json` and would freeze. Re-deriving each build means the rules stop firing on their own once the source is fixed, and a restated `eventSize` moves the denominator without anyone editing a stored number.

The placements themselves settle earlier, at load, because a title is parsed per deck and a cohort per event. Normalising cannot: it needs the event's field size, which is a fact about the cohort and is itself corrected against counted pilots. So the split is placement at load, norm at build, and the provenance column follows whichever pass wrote the value.

## The report generates itself

`reconciliation.json` hand-assembled an `imputed_fields` list for the field-size rules. Bolting a `minted_norms` beside it would have invited a third, so the report's `imputed_values` section is generated from the provenance columns instead: `build._imputed_values` enumerates the columns, groups by rule, and lists the keys. A new rule string on any of those columns reports itself the build it first fires, and the build's own CLI output prints the same grouping, so a new class of uncertainty announces itself where the developer is standing.

What the old list held that a rule name cannot is the counted contradiction (claimed field against pilots attended and deepest finish) that refuted the source's own number. That survives as `field_evidence`, renamed so the report holds one index of decided values and one record of evidence, rather than two shapes for one idea.

## `trends.py` reads the field off the node

The head-to-head timeline used to recover each event's field by inverting a norm, falling back to the deck count where no norm was invertible. That fallback disagreed with the field the build stores at 3 of 108 events (Area52IQ 24 against 7, DeckaDiceIQ 24 against 5, Pats Birthday Brawl 24 against 8), and was harmless only because those events had no norm and so drew no markers. Minting removes that protection: Pats self-heals, since its minted norms invert back to 24, but Area52IQ's one minted deck is a win, and 0 is uninvertible, so its point would have drawn labelled "1st of 7".

The inversion and the fallback are deleted and `Event.fieldSize` is read instead, which is the follow-on ADR 0015 named. It also retires a positional hazard: the recovery only worked while `count(DISTINCT f)` sat last in the projection, and reordering the `RETURN` would have moved all 108 events onto the deck-count fallback with no error raised anywhere.

Two measured claims elsewhere in `trends.py` were falsified by minting rather than by any change to their own code, and both are recorded where they sit. `HeadToHeadPoint` documented the inversion this change deleted. `pilot_performance_over_time` returns a played-but-wholly-unscored year as a cell of zero events rather than no cell, motivated by six drawable pilots holding such a year, in all six their first (issue #101); after minting there are none, because an unscored year was overwhelmingly one whose ranks sat in the titles unnormalised. The behaviour stays, on the grounds it shares with `meta_share` and `card_adoption` rather than on the measurement, and its docstring now says the population is empty so the next reader does not re-measure it as a defect.

## Consequences

Measured against a graph rebuilt from `main` on the same snapshots (both sides rebuilt, since the artifact on disk predated the `fieldSize` column entirely).

28 of 4591 decks move, and not one placement moves: minting only adds norms. `_ranked_deck_total` reads 4539 before and 4567 after, so the gem denominators move for the first time since ADR 0012. The population mean norm improves from 0.4792 to 0.4768, in the direction the excluded set predicts.

26 pilots' career means move and every one of them improves, the largest by 0.3021 (a pilot whose single ranked deck becomes two, one of them a win). 19 archetype means move and every one of them improves, the largest by 0.0453 (Sultai, 8 ranked decks to 9). Two pilots newly clear the head-to-head catalogue's two-year floor, at 238 to 240 offered; the archetype timeline's catalogue is unchanged at 121, with 13 archetypes gaining an event. All 108 field sizes are identical on both sides.

**9 of the 28 are minted against a field nobody counted**, and this is the first time that number reaches an average. 8 are Pats Birthday Brawl and 1 is Area52IQ, and at both the field is ADR 0015's `MIN_CUT_FIELD` floor of 24 rather than a measurement: Pats claims an `eventSize` of 8, counts 8 pilots and records a deepest finish of 5th, so Rule B reads it as a top-8 cut reported as a field and the floor decides the replacement. ADR 0015 says as much about its own rules ("an `A` in the report means the source's number is refuted, never that the new number is measured"), but until now those events held no norms, so the floor never reached a metric. It does now: all 8 of Pats' attendees read as top-18% finishes, and if the domain claim behind Rule B is wrong for that event, 8 flattering finishes enter every mean.

That is a reason to record the provenance, not a reason to withhold the norms. Excluding them leaves the same 8 finishes silently absent from every mean, which is the bias this ADR exists to remove, and the exclusion is invisible where the floor is not. The pair `normImputed = 'minted'` and `Event.fieldImputed = 'B'` makes the whole set one query, so if Rule B is ever revised these 9 are recoverable by name.

**The hidden-gem list does not move.** 36 cards before and 36 after, zero entering and zero leaving, largest mean shift 0.0184 (Nest Invader). ADR 0012's 0.33 band is therefore out of scope here: minting corrects an input to the band without disturbing the band.

The golden oracle grades 174 differences after the change and **170 before it**, since `baseline/subgraphs.json` was last captured at issue #105 and ADR 0015's 170 were never recaptured. The 4 this change adds are all in `gems_whole_meta` and all one card: Nest Invader picks up a 12th deck (`05th-08th - Jack C - 4C Tokens - CBR3`, newly ranked), which lands as a changed `mean_norm`, a new Deck node, a new CONTAINS edge, and the two count lines. The oracle is deliberately not recaptured, because doing so now would fold ADR 0015's 170 rows into this change and misattribute them. **Recaptured at issue #165**, which first re-derived the split by grading the same oracle at the revision before each change (0 before #140, 170 before this one, 174 after), so the attribution above is measured rather than asserted and no surplus row was hiding among them.

## What stays out of scope

**24 decks at 4 events carry no placement at all** and stay unranked: GGWAD 12 of 28, Area52IQ 6 of 7, DeckaDiceIQ 4 of 5, CFWAT25 2 of 6, under 24 distinct pilots. Lines through them still break. Their titles are why: `??st Andrew V - Mox Jund - CFWAT25` is the source's own explicit unknown and `Connor P - 4C Kiki Pod - Area52IQ` carries no placement token at all. There is nothing to recover, and `normImputed = 'none'` is the honest record of that rather than a gap. "Correct throughout" means every deck whose placement the project has decided, not every hole in every chart.

The inverse case is clean and stays clean: 0 decks carry a norm without a placement, which minting cannot introduce because it mints from a placement.

The head-to-head chart itself is not edited. Chifley C's TGC2024 point draws because the norm exists, not because the renderer learned to bridge a gap; a fix in `_head_to_head_figure` would have papered over the one visible symptom and left every biased mean in place.
