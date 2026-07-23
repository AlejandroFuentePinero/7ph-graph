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

## The metric side: numbers with no guard at all (issue #103)

Everything above audits a threshold that decides whether a value is shown, which is the smaller half of the package, so issue #103 audited the other half against two questions of the same shape as the first pair, asked of a value rather than of a guard. A metric with no guard on it cannot be misclassified, cannot refuse in the wrong shape, and cannot correlate its refusals with the question, because it never refuses. It can still be wrong, and it can be right while its surface says it is something else.

### The two tests

**(a) Does the number mean what the surface says it means?** Every metric is computed over some population and labelled with another. Where the two differ the difference is either a deliberate choice, which should be stated, or an error. A share of decks that folds a duplicate registration is computed over 1363 registrations and labelled "decks", and that is honest under ADR 0004's definition of a deck. A denominator recovered from a tournament's own ranking is computed over ranked positions and labelled "entrant count", and that is not.

**(b) Can it be wrong without anything looking wrong?** A metric that fails loudly is a smaller problem than one that renders a plausible number. This is #101's second test read forward: there, a refusal rendered as a value; here, a value renders with nothing to distinguish it from the right one. A field size recovered one entrant short draws `1/5` instead of `1/6`, in range, correctly typed, with no exception and no tell.

### The verdict

**No drawn number was found to be wrong, with two exceptions, and both are read off a deck's own title rather than computed.** The metrics themselves are exact where they can be checked against the source. `field_size` in the head-to-head timeline equals the source's own `eventSize` for 105 of 105 events that can yield it and 4404 of 4404 decks, to a worst residual of 5.7e-14, and the source's `placementNorm` is bit-exactly `(placement-1)/(eventSize-1)` for 4540 of 4540 decks carrying both, so the recovery is the algebraic inverse of a definition rather than an estimate of it. `placementNorm` itself is copied verbatim and never recomputed, so no graph-side deck movement can shift it: 4591 of 4591 stored norms match the source. Adoption rates, co-occurrence rates, meta shares and affinity weights are direct counts over stated denominators, checked exhaustively rather than sampled (0 of 11,084 co-occurrence terms disagree with a recomputation from the raw snapshot; 0 of 5729 affinity weights disagree with an independent traversal; 0 of 4412 board-scoped adoption pairs exceed 100 percent, the signature a mixed ratio would leave).

The two exceptions were the recovered placements, and both are now corrected. 16 decks carried a tied bracket's worst rank where the source, in 573 of 573 cases where it scores the same shape of title, carries the best rank; 8 decks at one event carried a cut's boundary where the same series' other events number the identical cohorts from the best rank. Both came from `placement_from_title`, and both sat in the same integer column as 4540 source-supplied placements, unmarked. `placement_from_title` now reads an explicit range's best end, moving the 16 (14 from 8th to 5th, 2 from 4th to 3rd), and `resolve_cut_placements` reads each "Top N" cut off its event's cohort, moving the 8 at CanBrawl2 (four "Top 4" to 1st, four "Top 8" to 5th). 24 placements changed in total, all of them decks the source left unscored, so no `placementNorm` and no aggregate moved.

**What did recur, in twenty-two of the fifty-five findings, is the label.** A correct number described as a different quantity is this audit's `MIN_CELL_DECKS`: a defect that leaves the output intact and the reader wrong. The pattern has three shapes.

*The name of the population is false.* `field_size` is called "the entrant count" in two docstrings and glossed as "5th of a 143-entrant field" in the app. It is the denominator the source ranked against: equal to the source's own entrant count where the source publishes one (36 of 108 events carry a `players` field, and `eventSize` equals it in 36 of 36), and equal to the last recorded placement on the other 71 of 71, which is below the number of distinct pilots who entered at 10 events and is a team count at 4.

*The unit is not the unit beside it.* The build prints "75 candidate(s) to review, 211 already curated". The 75 is pairs. The 211 is 186 folded pilot ids plus 25 applied rejections, two units summed, and no true quantity equals it: the dictionary took 243 rows off the review list (318 candidates with it disabled, 75 with it applied) and records 209 decisions.

*The picture asserts what the number does not.* A pilot's affinity view draws macros and archetypes on one radius scale, correctly, and then leaves the pilot itself off that scale, so in 787 of 5729 cases a part is drawn larger than the whole it belongs to. A card's usage view hangs an archetype-wide adoption rate under a macro that may hold one deck of that archetype: 31,746 of 47,864 drawn percents differ from the rate conditional on the macro above them, and the tier above teaches the reader that the conditional reading is right, because 0 of 11,203 card-to-macro edges disagree with it.

**One ordering defect was fixed here, one arithmetic residue survives.** The duplicate drop used to run before the year-straddle guard, so a duplicate registration could silence an abort: rewriting the one real duplicate loser's date across a New Year made the pre-drop call raise and the post-drop call return a confident 2026. `_event_years` now runs on the pre-drop population, so the guard sees every deck it should; today the two orderings agree on 108 of 108 events, so the fix moved no year. The residue that survives is `_pct_label`, which still rounds up to "100%" with no symmetric guard, recorded above at 8 adoption labels; the shared rate-edge path makes it 29 of 122,404 drawn labels, and its fix changes drawn values so it waits on a signed pass.

### Metric by metric

| Metric | Computed over | Labelled as | Fails | Verdict |
|---|---|---|---|---|
| `field_size` (h2h timeline) | the source's own `eventSize`, recovered from the norm (105 of 105 events, 4404 of 4404 decks) | "the entrant count", "a 143-entrant field" | quietly: 0 of 4567 placed decks render `placement > field_size` | **label-only**: the value is the only denominator the plotted y is coherent against; the noun is false at 10 of 108 events |
| `field_size` deck-count fallback | the 3 events with no positively normed deck | same label | quietly, but reaches 30 of 134,806 rows and 0 of 268,281 markers | **label-only**: the stated trigger ("no placed deck at all") describes 0 of the 3 events that fire it |
| `placement` (recovered from a title) | 27 of 4592 decks the source left unscored | an exact finish, drawn as a bare ordinal | quietly: a plausible ordinal in range, unmarked | **fixed**: tied brackets now read best-of (matching the source 573 of 573 times) and "Top N" cuts read their cohort's best rank; 24 decks moved, all source-unscored |
| `placementNorm` | copied verbatim; divisor is the source's `eventSize` | "the tournament size the score is normalised against" | loudly, if at all | **clean**: 4591 of 4591 match the source; the duplicate drop moves 0 |
| `meta_share` | decks whose primary archetype is X, over every deck that year | "each archetype's share of the meta, per year" | quietly: classification is present-tense and applied retroactively | **label-only**: 17 of 504 cells move between two fetches, 0 of 56 at the default cut |
| `card_adoption` (board-scoped) | decks running the card in the selected board, over the whole slice | "(X% of meta)", board named nowhere in the drawing | quietly: 3757 of 9990 board-set draws render a lone `0%` dot | **label-only**: the numerator is board-scoped, the label is not |
| adoption rate, macro to archetype | the archetype's own decks, graph-wide | a percent hanging under one macro | quietly: 31,746 of 47,864 differ from the conditional rate | **label-only**: correct number, wrong-looking placement, terms discarded at render |
| co-occurrence, single seed | same-board pairings over the seed's both-board decks | "the percent of the seed's own decks that also run the partner" | quietly | **label-only**: 2899 of 6000 drawn edges contradict that sentence, 425 by 25pp or more |
| co-occurrence, two seeds | both-board deck-level membership throughout | "the deck count on the hub is the denominator every percent is read against" | quietly downward: a numerator-only error moves 7092 of 11,084 labels, 0 impossible renders | **label-only**: true of 9780 of 9780 hub edges, false of 1299 of 1312 seed edges |
| affinity node weight | distinct events the pilot registered the node at | a radius, no number drawn | cannot: exact counts, 0 of 5729 wrong | **clean**, but the pilot hub is left off its own scale (787 of 5729 nodes draw larger than it) |
| affinity macro row | a cover, not a partition | a tree | quietly | **label-only**: children total 1.85x their parent over 1144 of 1904 rows, by the documented multi-label model |
| `_pct_label` | a rounded share | "100%" | quietly | **real residue**: 29 of 122,404 drawn labels read 100% at 99.50 to 99.52 percent |
| card-usage tier order | `round(...)`, then archetype size, then name | "strongest adoption first" | invisibly: 9169 of 9169 misordered pairs carry identical labels | **label-only**: no surface renders the order as a rank |
| `latest_year_share_cut` | the strongest until cumulative share reaches the cut | a top-N by share | quietly, when the cut lands in a tie | **real**: inert on this snapshot, decided the drawn set on 11 of 41 of this year's own states |
| trace colour | alphabetical rank within the current selection | "one trace per archetype, coloured apart" | quietly past 32 traces | **label-only**: 0 of 12 historical default configurations wrap; 14 of 14 archetypes change colour between two cuts |

### Build, ingestion and curation guards

#101 stopped at the query and trend layer. These are the guards below it, classified the same way, in the same columns.

| Guard | Gates | Kind | Refusal | Verdict |
|---|---|---|---|---|
| `YearStraddle` (build) | whether an event's year can be derived at all | derivation precondition | `YearStraddle` before any write, naming each event and its years; "Build aborted, live graph untouched" | **clean and loud**: 0 of 108 events straddle, and where an independent signal exists (36 of 108 event codes carry a year) it agrees 36 of 36. Blind by construction to a whole event displaced across a New Year, which 72 of 108 events have no signal to detect |
| dedup before `_event_years` (build) | which population the straddle guard sees | ordering, not a decision | none | **fixed**: `_event_years` now runs above the duplicate drop, so the guard reads the pre-drop population. Both orderings agree on 108 of 108 events today, so the fix moved no year; it closes the path where a duplicate loser dated across a boundary turned an abort into a silent year |
| duplicate-registration drop (pilots) | one deck per pilot, event, name and 75-card signature | de-duplication | a substitution, logged in `reconciliation.json`, one CLI line, invisible in the app | **clean and deterministic**: 1 of 4592, tie-broken on placement then deck id. Suppressing it would mint 1 phantom pilot and 2 low-confidence flags |
| multi-snapshot gate (`gate_sequence`) | whether an immutable fact was rewritten anywhere in the sequence | rewrite detection over a direct comparison | `flag` in `ingest.json`, retain-old pin, one CLI line | **clean, unexercised by data**: 2 snapshots is 1 transition, 0 interior. Output is byte-identical to the pre-fold path, same union digest |
| `_deck_hash` immutable/volatile split, classification half | which deck fields a rewrite may flag | classification | silence, by design (ADR 0003) | **honest, and the loudest silent mover found**: 723 of 4553 decks were rewritten in 5 days, all volatile; 16 changed primary archetype, moving 17 of 504 meta-share cells, one across zero |
| `_deck_hash`, the `createdAt` half | nothing: the field is not in the projection | classification | none | **misfiled**: the sole input to the year dimension, to the straddle guard, to the head-to-head x-axis and to career threading is treated as volatile. 0 of 4553 differ today |
| `len(worst) <= 3` (models) | whether a leading digit run is read as a rank | parse plausibility | `placement = None`, indistinguishable from "the source gave none" | **inert and correctly placed**: 27 of 4592 reach it, all pass, 0 of 4592 titles open on four digits, and 999 against a maximum rank of 306 is 3.26x headroom. Its stated hazard (a year) occurs 0 times; the real one (a numeric handle) occurs 30 times and never reaches the function |
| `_canon` bounds check (models) | whether a card id resolves inside its own file's catalogue | integrity | `ValueError` to `SchemaError` to "Build aborted, live graph untouched" | **inert, keep**: 0 of 673,442 checked ids out of range, both indexes exactly dense. The lower bound is the load-bearing half: an unguarded negative id resolves silently to the last card |
| `_ids` arity and distinctness (curation) | that a merge, reject or split names two or more distinct ids | authoring well-formedness, not evidence | `CurationError`, uncaught, exit 1 | **fixed**: it used to test count alone, so a repeated id passed and became a permanent no-op; it now tests distinctness (`len(set(ids))`). 0 of 209 entries fire it today, so the tightening moved nothing |
| `[[name]]`-on-a-merged-id check (curation) | one cross-kind authoring contradiction | contradiction detection | `CurationError` naming both ids | **clean, and the precedent the other two contradictions need** |
| reject vs merge (curation) | nothing: no such check exists | absent | none | **real gap**: 3 of 34 reject pairs name two ids the dictionary itself merges, and the merge wins structurally rather than by recency |
| duplicate `[[name]]` key (curation) | nothing: last write wins | absent | none | **real gap**: 25 blocks load as 24 pins, and one rendered label depends on block order |
| `dead_entries` (curation) | decisions keyed on an id absent from the snapshot | reporting | a list, rendered nowhere | **honest but narrower than its own purpose**: 25 of 34 reject pairs fire, 9 fire nothing, the report says 0, because absence is tested against raw upstream ids |
| `curated` counter (curation) | nothing; it is a progress figure | count | none | **real**: 186 ids plus 25 pairs, printed beside a count of 75 pairs, against a same-unit truth of 243 |

### The two carried questions, and where they landed

The audit carried two questions to the end. The first, which rank a collapsed bracket should read, was a fact and is settled below. The second, the noun the head-to-head hover should carry, is a choice and stays open.

**Whether a bracketed finish collapsed into an exact one is honest.** It now is, after both recoveries were corrected. The dishonest case was the cut, where finishers were ordered by the organiser but that order went unpublished, so a "Top 8" deck drawn as an ordinal asserted a rank nobody assigned it: at CanBrawl2 that drew the event's four winners as 4th. `resolve_cut_placements` reads each cut off its cohort instead (the same series numbers the identical 4 + 4 + 9.. grid 1,1,1,1,5,5,5,5 at CB4 and four other events, against 0 of 107 numbered events carrying any tie at 4th), so the four "Top 4" decks read 1st and the four "Top 8" read 5th, and CanBrawl2 leaves the 2 of 108 events that drew nobody at first. That change touched 8 values and 0 baseline rows.

What remains collapsed is the genuine tie, and collapsing it is honest, which is the second half of the audit's question answered for this metric. When four decks tie for the 5th-8th bracket, all four finished 5th; the source numbers every one of them 5th (573 of 573), and drawing them 5th states a fact rather than hiding one. A tied 5th reads the same as a solo 5th because both are 5th; the only thing not shown is that others shared the rank, which is an addition to the finish, not a correction of it. So no bracketed finish now misstates a rank: the cut, which did, is derived from its cohort, and the tie, which does not, is left as the shared rank the source itself records. Surfacing the tie itself (a "5th-8th" label over a bare "5th") is a legibility feature, not a correctness fix, and is out of this ticket's scope.

**The head-to-head hover carries no noun.** It renders `%{customdata[0]}/%{customdata[1]}`, so a reader of `6/39` at a teams event supplies "players" where 115 pilots and 117 decks were present. 15,543 of 268,281 drawn markers carry a denominator that is not people: 15,032 at the four teams events, 511 where the source folded several decks onto one placement slot. The sibling adoption hover appends "decks", so the omission is local, not a convention. Three remedies were sized and none is obviously right, which is why this is a decision and not a fix. Prepending the event name changes the text on all 268,281 markers and 0 values, and resolves the 4 teams events by name alone, since every one contains "Teams", but leaves the 511 untouched. Appending a bare "entrants" is one line and makes the hover newly and explicitly false for the same 15,543, which is strictly worse than doing nothing. A per-event noun is correct on all 268,281 but needs a fourth data field plus a rule classifying each event, and the 511 folded markers are neither teams nor entrants but placement slots. Whichever is chosen, the tab copy that calls the pair "its position and the tournament size" moves with it: "its position" is false for 15,032 of 15,032 team markers, every one of which shares its placement with between 1 and 11 other people.

### Left standing, on the record

Three more things the audit surfaced that are real but are not this ticket, recorded so they are not rediscovered from scratch:

**Card ids are positional and not stable across fetches.** 4728 of 4967 shared positions resolve to a different canon between the two snapshots, because the catalogue is canon-sorted and 28 canons were inserted. Nothing downstream is affected, because ids are resolved to canons before anything is keyed on them, and the gate returns promote with 0 flags over both files. The module docstring calling a list position an id is the only thing that invites the assumption.

**One event is a placeholder.** 1 of 108 `Event` nodes is keyed `nan` with a null id, present in both snapshots. It inflates one pilot's affinity view by one distinct showing-up, 6 of 11,870 drawn affinity values.

**The reconciliation report is a report of the undecided, and says so.** It names 0 of the 238 applied curation entries individually, which is its documented population (ADR 0005 line 3), not a gap. What it does not surface is the 23 entries that fire nothing: 9 reject pairs and 14 name pins that restate the vote the resolver would have reached anyway.

### Consequences

ADR 0013's evidence rule gains a third axis and #101's pair gains a mirror. A guard is classified by what it gates and by what its refusal looks like. A metric is classified by the population it is computed over against the population its surface names, and by whether a wrong value would render as a plausible one. Most of this package's metrics pass the first test only because someone chose the harder denominator on purpose, and fail the second by construction, because a count in range is indistinguishable from the right count in range.

No stored graph property changes as a result of the label work, so no rebuild and no golden-oracle recapture. The placement recoveries are the exception and are the reason the first open question above is open: they are stored, they are inside the ingest hash's historical projection, and correcting them is an output-changing curation change under the repo's own gating rule.
