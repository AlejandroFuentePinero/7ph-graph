# The best-player race plots a running score, and every rank carries its interval

Issue #135 built the race as a rolling window: five sample points, each the shrunk mean of a pilot's finishes at majors in the 18 months ending there, drawn as eight lines over a faded layer of every other contender. It was tested, reviewed, and waiting to land when the shape it draws was measured directly.

The movement was noise. The chart now plots a **running** score instead, and the leaderboard beside it states the interval on every rank rather than presenting an ordering the evidence does not support.

## The rolling window drew reshuffling, not form

The test is a permutation. Take each contender's own major finishes, shuffle them across their own event dates, and rebuild the windows. That destroys every trace of *when* a pilot played well while preserving how well they played overall and which events they attended, so whatever movement survives is the window machinery reacting to sampling rather than to form.

| | mean within-pilot swing across the five windows |
|---|---|
| observed | 0.0915 |
| shuffled (400 permutations) | 0.0885, 90% range 0.0823 to 0.0947 |

The observed value sits inside the shuffled range. Real movement beyond noise is +0.003, about three percent of what the chart drew. The lede promised "how they rose and fell" and the picture could not deliver it.

The cause is not the window length, it is the record. There are 21 majors in the graph. The median plotted point rested on 4 majors and the floor allowed 2, and a rolling mean over four observations of a quantity this noisy is mostly noise. Widening the window trades the claim away entirely; narrowing it was already measured and rejected during #135's grilling (disjoint 6-month buckets collapse to a split-half rho of 0.19).

A caption cannot rescue a chart whose central visual claim is false, so the y quantity changed instead.

## A point is now everything up to its date

Each sample date counts every major the pilot had played by then, scored with the same empirical-Bayes shrinkage. The series is the accumulation of evidence about a pilot, not a claim about their form, and every reading it offers is one the data supports:

- A line rising is a thin record thickening under a pilot who was always this good. Shrinkage holds a short record near the field average until there is enough of it to say otherwise, so early points are pulled toward 0.577 and separate as the evidence arrives. The caption and the FAQ both say this, because the opposite reading ("the pilot improved") is the natural one and is wrong.
- The last point of every line **is** that pilot's career score, by construction, because the newest sample date is the newest event in the graph. The right edge of the chart is the leaderboard, line for line.
- The faded layer behind the eight now carries a reading of its own. Contenders above the lowest drawn line run **36, 33, 10, 5, 0** across the five dates: the field starts undecided and closes on an answer, and at the right edge nothing crosses the drawn eight.

That last property also resolves a real defect in the rolling version. Because the eight were picked on career score while the y axis was an 18-month slice, contenders outside the eight routinely held the highest points on the chart, which read as a bug and was in fact the chart disagreeing with its own legend. Under the running score the two cannot disagree at the edge.

The cost is honest and worth naming, and it is larger than a caption change. Issue #135 is titled "a temporal ranking chart of the top pilots over time" and asked for "who was on top in each era, who climbed, who faded". **The chart that ships cannot answer that, and neither could any chart built on this record.** There are 21 majors in the graph, a median of 4 behind a window point, and the permutation test above puts the answerable signal at three percent of what was drawn. The delivered chart answers a smaller question honestly ("how fast did the record decide?") in place of a larger one that had no answer. The alternative was not shipping the tab, and that was weighed. A reader should not take the tab's presence as evidence that "who was best when" is a question this data holds.

## The eight are a group, and the leaderboard now says so

The rank column was the second overclaim. Scores at the top of this board are separated by thousandths against a median of 8 majors per contender. Bootstrapping the standings, redrawing every contender's finishes with replacement and rescoring the whole field 1000 times:

- Only **4.9 of the top 8** survive a resample. On average three of the eight places belong to somebody else.
- **15 contenders** hold a one-in-ten claim to a top-eight place, including Richard O at 25% from a shown rank of 18.
- Rank 2 is the only firm place, at 89%. The other seven run 50% to 69%, and rank 7's honest interval is 1 to 40.

So `RaceCell` carries `rank_low` and `rank_high`, the 90% interval over those resamples, and the leaderboard renders it as a "Rank CI" column beside the rank. A rank of 4 that could be 17 is a different claim from a rank of 4, and a reader has to see the two together to make it.

The resampling redraws the **whole field** each time and re-estimates the shrinkage inside the loop, because a rank is a statement about a pilot against the others: holding the others still would measure something else. It is seeded (`RACE_RESAMPLE_SEED`), so a rebuild of the same artifact draws the same bounds and a moved bound means moved evidence. It assumes a pilot's finishes are draws from one fixed level, so genuine improvement is booked as noise and the interval is conservative; not conservative enough to turn a coin flip into a certainty.

The interval lives on the cell rather than being derived by a caller because it depends on the whole field's records, which no consumer holds. It is deliberately cut-independent: a `P(top 8)` column would have put the app's eight-line display cut inside the tool, which ADR 0013 keeps out of it.

## What was measured and left alone

Three changes were considered against the same evidence and rejected, which is most of why the two above are the right ones.

**A win bonus.** The metric is nearly blind to winning: `placementNorm` is uniform ordinal, so at the 306-player major first beats second by 0.0033. Three of the top eight have never won a major, and three of the four multiple-major winners sit outside it, which reads as broken to anyone who follows the scene. But adding a win-rate term does not predict future results any better: split each pilot's career chronologically and predict the later half from the earlier, and the score moves from r = 0.375 to 0.383 against a standard error of 0.094. The complaint is real and the fix does not work, so the metric stands and the FAQ carries the caveat.

**Every candidate statistic ties.** Predicting the later half of a career from the earlier (n=116, pilots with 6+ majors): mean of best 2 scores 0.391, the mean 0.375, the median 0.375, best single finish 0.357, top-10% rate 0.331. One cluster inside the standard error. Inverted standard deviation scores −0.024, so consistency in the sense of *low variance* carries no signal at all; what predicts is level, and every way of measuring level works about equally well. The mean is kept because nothing beats it and it is the one that uses every observation. That is the defence of the metric, and it is an empirical one rather than a philosophical preference for consistency.

**A higher career gate.** Raising `MIN_CAREER_MAJORS` from 5 to 10 moves top-8 stability from 4.9/8 to only 5.8/8 while the contender pool collapses from 139 to 38. It buys less than it costs.

Also settled and not revisited: **majors only**. Non-major results predict later major results about as well as major results do (r = 0.401 against 0.410), and including all events would lift split-half reliability from 0.476 to 0.598. That is a real gain and it is declined, because the race is a question about the biggest events and mixing a mostly-locals record with a majors-only one ranks two different measurements as one (the reasoning `MAJOR_FIELD_SIZE` already records). The field-size distribution is bimodal with a clean gap, non-majors topping out at 64 and majors starting at 70, so the cut slices a real seam rather than a continuum.

## The finding is not local to this chart

The permutation test above is cheap and answers a question every trend surface in this repo makes an implicit claim about: is the movement I am drawing real? It was run against the two other surfaces that rest on the same quantity, and both fail in the same way.

**`pilot_performance_over_time` is worse than the race was.** A pilot's mean finish per year, permuted the same way over 115 pilots with three or more qualifying years: observed swing 0.2376, shuffled 0.2388, so real movement is **−0.0012**. Not small. Zero. A drawn point rests on a median of 3 events and 62% of points rest on 3 or fewer, with `MIN_PILOT_YEAR_EVENTS = 2` admitting a two-event mean. That chart has been shipping and draws pure noise. Filed as #175.

**The hidden gem band has the threshold form of the same problem.** Membership is a hard cut (`MAX_GEM_MEAN_NORM = 0.33`) applied to a noisy mean, so a card near the line is admitted on luck. Bootstrapping each candidate card's decks 1000 times, over the 51 archetype slices `gem_archetypes` offers (the only gem views the app can produce): of the 283 gems, only **57** hold the band in 90% of resamples and **137** hold it in under 70%, while **298** cards outside reach it in 30% or more. A card's mean is known to about half the entire between-card spread (typical standard error 0.0514 against a spread of 0.1038; split-half r = 0.410 over the 1,839 cards with 8+ decks), and a gem rests on a median of **6** decks. The band as a population is sound and ADR 0012's two-bound reasoning stands; what overclaims is presenting a threshold crossing as a fact. Filed as #176.

## The remedy is not portable, only the test is

The first drafts of #175 and #176 both proposed this ADR's running score, on the reasoning that a shared defect wants a shared fix. That was tested against the artifact for the pilot chart and **rejected**, and the correction is worth recording because it is the obvious mistake to make next time.

A running score works here because the race has a **field**. Its reading is relative: contenders above the lowest drawn line collapse 36, 33, 10, 5, 0 across the five dates, so the picture visibly starts undecided and closes on an answer. A single pilot has nothing to converge against, so none of that reading survives the move. Simulated over the 240 pilots the performance chart draws, with `_shrinkage` re-fitted to that population, the median running line moves **0.051** on a fixed 0-to-1 axis, 119 of 240 move less than 0.05 across the whole career, and **125 of 240 pilots have only two qualifying years**, so the median case is a two-point, nearly flat line. It would replace a chart that at least reports a real per-year mean with one that says almost nothing, and it would undo the first-year-to-last span #101 restored.

So the permutation test travels and the remedy does not. The rule stated below is deliberately "either the quantity changes or the claim does": for the race the quantity was the thing that could not be defended, and for the two surfaces above it is the claim.

Neither is changed here. What this ADR adds beyond the race is the **practice**: before a trend surface ships, permute the observations within each subject's own record and check that the drawn movement beats the shuffle. Where it does not, either the quantity changes or the claim does. It is a few lines of code, it needs no new data, and it would have caught all three of these before they were drawn.

## Consequences

`RaceCell` loses `window_start`; `window_end`, `window_score`, `window_majors`, `window_rank` and `window_contenders` become `as_of`, `as_of_score`, `as_of_majors`, `as_of_rank` and `as_of_contenders`. The old names described a span and the values are no longer spans. `RACE_WINDOW_MONTHS` is gone, `MIN_WINDOW_MAJORS` becomes `MIN_SCORED_MAJORS`, and `_race_windows` becomes `_race_sample_dates`.

The app's copy changes with the quantity: the lede, the chart caption, the hover, the x axis title, and the FAQ, plus a second FAQ entry on how settled the order is. The tool gains one dependency on `random`, seeded, and about 0.4 seconds of startup work for the bootstrap.

The oracle is unaffected: the race is a `Series`, and `graph7ph baseline` grades subgraphs.
