# Every evidence guard, classified: what each one gates, and what its refusal looks like

The `meta_share` cell floor was removed because it was a floor built for estimates applied to a count, and because the value it withheld rendered as a different value (the amendment to ADR 0013). Both halves looked like patterns rather than one-offs, so issue #101 audited every guard in the package against the same two questions.

This ADR is the written answer. It exists because the ticket asked for the classification to be recorded rather than reasoned once in a session and lost, and because the next person tempted to add a floor should be able to read what the existing ones are for.

## The two tests

**(a) What does the guard gate?** An *estimate* (a mean, a rate, a ratio of rates) is a guess at a latent quantity from a few trials, so a thin sample can land anywhere by luck and a floor is doing real work. A *direct count over a known denominator* is exact at any size, so a floor on it withholds a fact. A floor on a count is the first defect, and it is what killed `MIN_CELL_DECKS`.

**(b) What does the refusal look like?** A guard may be perfectly classified and still be wrong, if the withheld value renders as a *different* value rather than as a refusal. A hole on a share chart reads as zero. A year dropped from a line asserts continuity across it. A row omitted from a tool result is indistinguishable from a zero to an agent. That is the second defect.

There is a third question that turned out to be the sharpest of the three, and it belongs in any future audit: **does the set a guard fires on correlate with what the surface exists to show?** `MIN_CELL_DECKS` failed exactly there. Its cells were bounded at 2.08% against an axis reaching 11.4%, so it could never withhold a spike, only a low point; and because the display cut ranks on the latest year, a drawn archetype's thin cells were necessarily its past. The floor deleted entry and growth, which is the half of the question the chart was built to ask.

## The verdict

The first pattern did not recur. **No surviving guard is a floor on a count.** `MIN_GEM_DECKS`, `MIN_PILOT_YEAR_EVENTS` and `MIN_QUALIFYING_YEARS` all gate a mean, and the lucky-draw failure mode is measurable rather than theoretical: against the 181 `(pilot, year)` cells holding six or more events, a two-event subsample misses the full-year mean by 0.136 on average and lands on the wrong side of the chart's own 0.5 reference line 22.5% of the time. `MIN_CELL_DECKS` was a misfiling, not the tip of a habit.

The second pattern did recur, in four places, all now fixed. Three of the four were not floors at all in the end; they were the *shape* of a refusal.

### Guard by guard

| Guard | Gates | Kind | Refusal | Verdict |
|---|---|---|---|---|
| `MIN_PILOT_YEAR_EVENTS` (trends) | a year's mean `placementNorm` | estimate | empty tick, broken line | **fixed**: the year is now stated, only the mean is refused |
| `MIN_QUALIFYING_YEARS` (trends) | whether a trajectory exists | estimate | `NotEnoughHistory` | **fixed**: was an untyped empty series |
| `MIN_SHARED_EVENTS` (trends) | whether a rivalry has a timeline | direct count | `NotEnoughHistory`, with the count | **fixed**: was an untyped empty series |
| `MIN_GEM_DECKS`, `MAX_GEM_SHARE`, `MAX_GEM_MEAN_NORM` (query) | a card's mean placement | estimate | absence from a selection | clean |
| `MIN_GEM_SLICE` (query) | whether a slice can be asked at all | estimate | `SliceTooSmall`, stated in the app | clean, and the precedent the trends seam now copies |
| `RENDER_THRESHOLD` (explore) | whether a subgraph is drawn | display budget | a named refine alert naming the flooding kind | clean |
| `_FUZZY_THRESHOLD`, `_FIRST_NAME_THRESHOLD`, `_TYPO_EDITS` (pilots) | whether two spellings are one pilot | identity | no merge, pair left undecided | clean as guards; see the residue below |
| `top_n` on co-occurrence (query) | which partners are drawn | display budget | a user-set top-N | clean; see the residue below |
| `pilots_with_history`, `gem_archetypes` | what is offered | catalogue | absence from a dropdown, disclosed in the tab copy | clean |

`MIN_SHARED_EVENTS` is the one that most looks like `MIN_CELL_DECKS` and is not. It does gate a direct observation, so the classification alone would condemn it. But it fires all-or-nothing on a whole pair, so unlike a cell floor it can never punch a hole in a series it is already drawing: every drawn rivalry keeps all of its meetings, including the first. And the set it fires on, pairs who met once, is by construction the set with no time axis at all, which is the opposite of the correlation that killed `MIN_CELL_DECKS`. It stays.

The general shape of that: **a floor on whether an answer exists is a different object from a floor on a cell inside an answer.** The first can only ever refuse the whole question, which the caller can see. The second withholds part of a picture that still draws, which is what the eye then fills in wrongly. ADR 0013's aggregates-carry-a-floor rule was never wrong; what it lacked was this second axis.

## What changed

**`pilot_performance_over_time` states the year and refuses only the mean.** It used to drop a below-floor year entirely. The chart spanned first-drawn-year to last-drawn-year, so of 90 refused cells, the 7 falling inside a span drew honestly as an empty tick, and the 83 falling at the ends were erased from the x-axis: 74 of 238 charts claimed a shorter career than the pilot had, 44 a later debut and 35 an earlier exit. The bias test fires hard here. 79 of the 90 below-floor cells are a pilot's first or last scored year, 88% against a 65% base rate, because a one-event year is overwhelmingly a year someone was arriving or leaving. That is `MIN_CELL_DECKS` again in a different measure: the guard deleted precisely the arrival and departure a career chart exists to show.

`PerformanceCell.mean_norm` is now `float | None`, and the series is rectangular over the years the pilot has a ranked deck in, the way `meta_share` and `card_adoption` are rectangular over the graph's years. A refused year arrives as "2026, one event, no mean". The floor itself did not move, and no point is drawn that was not drawn before; 83 erasures became 83 visible empty ticks.

**The trend seam refuses by name.** `Series(cells=[])` meant four different things: refused as too thin, never played, never met, and met once. `NotEnoughHistory` is raised instead, carrying the evidence it actually found, so the head-to-head note now says "share one event" or "have never met" rather than "fewer than two events" for both. This is `SliceTooSmall`'s pattern, which ADR 0012 already settled for the gem slice. It does not distinguish a pilot key the graph has never heard of, which still arrives as a pilot with no history; that is a caller error rather than an answer about the format, and no surface can reach it, since both trend dropdowns are built from the graph's own pilots.

**The meta-share matrix is rectangular over the archetypes, not over the join.** It took its archetype list from the rows the `isPrimary` join returned, so an archetype no deck ever led with had no row anywhere and no entry in the manual picker: 2 of 126 archetypes, Black Walks and Deadpool Walks, holding 53 decks between them as a secondary tag. Both the docstring and this ADR's predecessor claimed the matrix was rectangular over "every archetype in the graph"; it was rectangular over the archetypes the query happened to see. The list now comes from the `Archetype` nodes, and the two come back as real zeros.

That zero is worth being precise about, because it is a smaller claim than it looks. `meta_share` measures share *by primary archetype* for every archetype it reports, so a flat zero for Black Walks says "no deck led with it", not "no deck played it": 53 decks carry it as a secondary tag. The number is the same measure every other line on the chart carries, and the alternative was leaving the archetype off the picker entirely, which answered the same question with silence. What is now available and was not is the honest one: a reader can ask about Black Walks and be told it is never anyone's primary. If the carried-not-led distinction turns out to matter to a reader, the fix is a second measure, not a different zero.

**The performance tab copy said something false.** It told the reader "a year with only one event to average is left as a gap." True of 7 cells, false of 83. A reader told that thin years show as gaps, seeing no gap, correctly concluded there were no thin years. The copy did not mitigate the erasure, it manufactured the misreading.

## Left standing, on the record

Three things the audit surfaced that are real but are not this ticket, recorded so they are not rediscovered from scratch:

**The co-occurrence top-N cuts inside a tie.** At the default 15, the cut lands inside a tie for 3737 of 4642 cards and drops 100,222 partners holding the same shared-deck count as the last one kept, with the survivors chosen by alphabetical order of card name. It is not an evidence guard: the ranking key is a direct count, the cut is monotone (measured over all 4642 cards, there is no card where a dropped partner beats a drawn one), and the surface names itself a top-N with a user-set size. But an arbitrary alphabetical slice of a tied band is presented as "the strongest partners". Fixing it changes which nodes are drawn for 3737 cards, so it is an output change needing its own before-and-after.

**`_choose_display_name` discards every losing cluster.** 154 pilot ids recovered two or more distinct names from their own decks; 74 have a name that both lost the vote and fell outside the winner's cluster, and only 17 of those are caught by `multi_name_ids`, which requires the surname family to differ. The other 57 are reported nowhere. The thresholds are clean, and refusing to merge is the correct conservative call; the defect is that a disagreement about a person's own name is resolved silently rather than recorded as an open question. That is the identity-flavoured version of the same complaint and belongs with the curation dictionary work.

**`_pct_label` rounds up to "100%" without a guard.** It has a `<1%` case on the low side and nothing symmetric on the high side, so 8 of 47,864 adoption pairs render "100%" while not being 100%, for instance 207 of 208. One line, but it changes 8 drawn labels.

## Consequences

ADR 0013's evidence rule gains its second axis: classify the value, then check what the refusal looks like, then check whether the refused set correlates with the question. The three floors it pinned all survive unchanged. What changed is that two of them now refuse out loud.

`PerformanceCell.mean_norm` becoming `float | None` is a contract change for any agent reading the trend seam, and `pilot_performance_over_time` and `head_to_head_timeline` now raise where they returned empty. The app is the only current caller and is updated. No stored graph property changes, so no rebuild and no golden-oracle recapture.
