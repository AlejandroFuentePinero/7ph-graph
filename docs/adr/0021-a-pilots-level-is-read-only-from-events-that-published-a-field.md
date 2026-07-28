# A pilot's level is read only from events that published a field, not a bracket

Two of the 21 majors in the graph never published standings. `SSWam` holds 7 finishes against a field of 88 and `ANZSS10` holds 8 against 81, and neither records a finish deeper than 5th. What is on record at both is a top-8 bracket; the rest of the field registered and vanished.

That makes attendance itself worth points. Cut, and the record gains a near-perfect finish; bust, and there is nothing to gain a bad one from, because the source never wrote the row. The best-player race and the pilot performance chart both average those finishes into a claim about how good a pilot is, and both now refuse to.

## What a cut-only finish is worth

| | mean flipped score |
|---|---|
| finish at a major that published its field | 0.506 |
| finish at one of the two brackets | **0.967** |

Nearly double, and bounded below by whatever the cut was. This is not a heavy tail or a strong field, it is the definition of the sample: the only rows that exist are the good ones.

Ten of the 139 contenders held one. Removing the two events moves Luke M 1, who held both, from rank 5 to rank 10, and lifts Matthew G 1 from 9 into the drawn eight. One swap in the eight lines the chart draws, from 15 finishes out of 2,387.

## The threshold is a seam, not a judgement

`MIN_FIELD_COVERAGE = 0.5`: an event is scored when at least half its field has a finish on record.

Coverage is bimodal on this corpus and the constant sits in the empty band between the modes. The 26 published brackets top out at **42.1%**; real records start at **78.9%**. One event lies between them, `GGWAD` at 57.1% (16 finishes of a 28 field), and it is a thinned standings rather than a bracket, so keeping it is the right call and the threshold makes it.

That makes the live gap 42.1% to 57.1%, so the cut has about **8 points of slack either side** and every event keeps its side anywhere inside it. Past 0.58 GGWAD flips from kept to dropped; that is the nearest real edge, and it is 8 points away, not 15. The wider 42.1-to-78.9 span is the gap between the *modes*, not the gap the constant actually has to clear.

The alternative detector, "deepest recorded placement far shallower than the field", was considered and is the more literal statement of the defect. It picks out exactly the same 26 events here, needs its own threshold, and reads a placement where the other reads a count. Coverage was taken because it measures the quantity the bias actually depends on: what fraction of the field we hold.

Dropping the event whole, rather than discounting the finishes in it, is forced. No number of good finishes on record tells you how many bad ones were withheld, so there is nothing to weight by.

## The same rule reaches the pilot chart, and it mattered more there

The first scoping of this work fixed the race alone and documented the pilot chart's exposure as a known bias. That was measured rather than assumed, and the measurement refused it.

Excluding brackets from `pilot_performance_over_time` costs 181 decks of 4,567 and 8 pilots of 240, so cost was never the reason to leave it. And the distortion is worse there, because the race has two defences the chart does not:

| | race | pilot chart |
|---|---|---|
| evidence behind a drawn point | median 8 majors | median 3 events |
| shrinkage toward the field | yes | none |
| effect of one bracket finish | thousandths | up to 0.32 on a 0-to-1 axis |

Of the 602 year points both versions draw, **83 move**, with a median move of 0.045 and a maximum of 0.32 (RapidRedStag's 2025 falls from 0.381 over 6 events to 0.703 over 3). Every large move goes the same way: the line gets worse once the brackets come out. A one-directional shift across a tenth of the points is not noise, and a pilot whose year was half bracket appearances was being drawn as having had a strong year.

`_within_pilot_sd` takes the filter too, since the spread that widths a point has to be the spread of the quantity the point is drawn from.

## What keeps these events, and why that is not an inconsistency

**Hidden gems keep them and must.** The gem null is convolved per event (`query._gem_tails`, ADR 0020), so a bracket's decks are compared against that bracket's own cut rate rather than against the field's. The information is used rather than discarded, which is strictly better than a filter, and applying one here would throw away the 27-event stratification that ADR 0020 exists to provide.

**The archetype surfaces keep them for now**, and carry a different bias from the same cause: a bracket contributes only winning decks. Measured, that splits cleanly. Meta share moves by at most 0.14pp for any archetype, which is invisible and not worth changing. The landscape's *finish* axis moves for 49 of 92 archetypes, by up to 0.073, always in the same direction. Filed as #189 rather than fixed here, because the share/finish asymmetry is a design question and it interacts with the reference line that surface is drawn against.

That interaction is worth stating, since it will surprise whoever picks up #189: the field's mean `placementNorm` is **0.4767** with brackets counted and **0.4927** without. Most of the gap between the drawn 0.5 reference and the true field mean was the brackets, not a real asymmetry in the scale. That gap is now 0.007, and the reference line **stays at 0.5**: it is what a random finisher's expected rank actually implies, and the residual is far below what a reader can resolve on the chart.

## Two more places the angle reaches, both measured and both left alone

The general defect is "the results we hold were selected, and the selection correlates with doing well". Two other surfaces sit on that angle. Both were measured, and neither is being changed. Recording why, so the question is not reopened from scratch.

**Head-to-head needs no fix, and filtering it would make it worse.** `head_to_head_timeline` plots raw per-event placements for two pilots, not a mean, so there is no level being claimed and nothing for a selected sample to bias. Every point it draws is a true observation: at a bracket both pilots cut, so both placements are real and the comparison between them holds. What the selection costs is *meetings*, not values. Where A cut and B busted, B has no row and the meeting never appears, and those are exactly the events where A beat B most decisively, so the chart under-represents lopsided results in both directions. The remedy is not available: dropping bracket events would delete real meetings to compensate for absent ones, which trades a thin sample for a thinner one. Left as a caption-level caveat at most.

**Incompleteness still skews upward among the events that pass, at about a fifth the strength.** A fully published event must average 0.5 by construction, since the score is a rank over a field size. What the corpus holds:

| share of the field on record | mean `placementNorm` | events |
|---|---|---|
| 95 to 100% | 0.4945 | 62 |
| 85 to 95% | 0.4766 | 12 |
| 50 to 85% | 0.4472 | 3 |
| the brackets, now excluded | 0.0905 | 26 |

A clean monotone gradient: the less complete an event's record, the better the results it published. Missing decklists are not missing at random, they are missing from the bottom, which is the same mechanism the brackets exhibit in the limit. It is left alone because it is a fifth the size and reaches 15 events and 639 decks, and because there is no seam to cut on: unlike the bracket case, coverage above the threshold is a continuum with no gap in it, so any further cut would be an arbitrary line rather than a measured one. Worth revisiting only if the gradient steepens as more partial events arrive.

## The fixtures were describing events that could not exist

Twenty-three tests failed on the first run of this rule, and every one of them was a fixture declaring a 500-player field holding a single deck. That is the bracket signature at its most extreme, so the rule was reading them correctly; they had simply never been asked to be plausible before.

`_cover_fields` now fills each declared field with one-off filler pilots. Each filler has a single mid-table finish at a single event, so it clears no gate anywhere: not `MIN_CAREER_MAJORS`, not `MIN_PILOT_YEAR_EVENTS`, not `MIN_SPREAD_FINISHES`. Nothing any test asserts on moved. `_RACE_MAJOR_FIELD` drops from 100 to 66, still over `MAJOR_FIELD_SIZE`, because every seat now costs a deck at build time and the section has fifteen tests in it.

The lasting benefit is that a race fixture is now a description of a possible tournament. `_publish_only_a_bracket` builds the opposite case on purpose, by stripping the fillers' placements while leaving their lists, which is exactly the shape the source ships.

## Consequences

`trends.MIN_FIELD_COVERAGE` and `trends._cut_only_events` are new; `MAJOR_FIELD_SIZE` is no longer sufficient on its own to decide what the race scores. `RaceCell.major_events` reads **19** rather than 21, and the contender field falls from 139 to 137, so two pilots lose their place by dropping under `MIN_CAREER_MAJORS`. `pilots_with_history` offers 232 pilots rather than 240.

Figures restated across `trends.py`: the shrinkage `mu` moves 0.5771 to 0.5729 and `k` 4.95 to 5.07; `_within_pilot_sd` is fitted on 257 pilots and 3,003 finishes for a pooled sd of 0.266, where issue #175 measured 272 and 3,209 at 0.268. The interval half-widths it implies are unchanged at two decimals. Top-8 stability under the bootstrap is unmoved at 4.9 of 8, and the count of contenders holding a one-in-ten claim on a top-eight place goes from 15 to 16.

The app's race caption now says the events "published who finished where" rather than naming the field size alone, and the race FAQ loses a claim that had become false: it said this was the only chart that leaves events out and that a pilot's own performance chart counts every event with a recorded finish. Both halves are now wrong, so the entry states the two reasons separately and says why the gems keep what the other two drop.

The oracle is unaffected, as ADR 0017 records: the race and the pilot chart are `Series`, and `graph7ph baseline` grades subgraphs.
