# An unpublished finish is drawn at the best it could have been, where the event published enough to bound it

The two rivalry charts break their line over an event one side attended and the source never scored. ADR 0013 chose that break deliberately, as the honest rendering of "they turned up and nothing about their finish is known". It is the right reading of a solo line and the wrong one of a comparison: on a chart captioned "shared events", a lone marker with the other line stopping dead beside it reads as a defect in the data rather than as an answer, which is how it was reported.

This ADR says the finish is not unknown. Where an event published part of its field, the decks it left out sit in the slots below the last published one, so their finish is **bounded**, and the bound is what the rivalry charts now draw.

## The record bounds the tail, and only at some events

The whole population is 24 decks over 4 events. It is small enough to state entirely:

| event | field | published | the unpublished sit in | bound (score) | interval width |
| --- | --- | --- | --- | --- | --- |
| `GGWAD` | 28 | slots 1..16 | 17..28 | **0.407** | 40.7% of the axis |
| `CFWAT25` | 19 | 1, 3, 5, 13 | 14..19 | 0.278 | 27.8% |
| `Area52IQ` | 24 | 1 only | 2..24 | 0.957 | 95.7% |
| `DeckaDiceIQ` | 24 | 1 only | 2..24 | 0.957 | 95.7% |

The bound is the norm of the first unpublished slot: `last published slot / (fieldSize - 1)`.

**That slot takes two floors, and neither is enough alone.** The obvious reading is the worst placement on record, but a tie band is stored at its best end (ADR 0014), so sixteen placed decks can carry a worst label of 13 and a bound counted off the label would sit three slots high, drawing the tail better than the record allows. The other reading is the number of decks placed, which fails for the opposite reason: the corpus holds a subset of most fields (63 of 107 events, `Area52IQ` holding 7 decks of 24), so the count falls short of a label already in hand. Each is a floor on the last published slot on its own, so `_tail_bounds` takes the further of the two. `GGWAD` published `1,2,3,3,5,5,5,5,9..16` and both give 16, which is why the two readings were indistinguishable when this was written.

Even the further of the two is only a floor: an event may have published past every deck the corpus holds, so the bound errs toward the top of the axis. That understates how far down the tail begins, which is the direction the safety argument below wants, since a bound too high can only understate a gap.

**Three of the four are brackets, and a bracket's bound is worthless.** `Area52IQ` published one placement of 24, so every deck it left out is bounded only by "not the winner" and the bound scores 0.957, near the top of the axis. Drawing a pilot who busted out at 0.957 is a worse lie than the hole it replaces. So the bound is gated on the event having published at least `MIN_FIELD_COVERAGE` of its field, which is exactly the cut `_cut_only_events` already makes: `GGWAD` at 57.1% is the one event that yields a bound today, and the three brackets yield none and keep their break.

That the gate reuses an existing threshold is not a convenience. The quantity it governs is the same one: how much of a field has to be on record before anything can be read off what is missing from it.

## The best end, not the middle, and the reason is a guarantee rather than a taste

The bound could be drawn anywhere in the interval. It is drawn at the **best end**, the best the deck could possibly have finished, for two reasons.

The first is that ADR 0014 already resolves a placement range that way: `placement_from_title` reads a tie band at its best end, so `05th-08th` is a 5th. This is that convention applied to a new class of range, which is the extension ADR 0016 anticipated when it said a future class of recovered placement is normalised the build it lands.

The second is that the best end makes every drawn comparison safe in one direction, and this one is a theorem rather than a measurement. The bound is the norm of the **first unpublished slot**, so it is strictly worse than every finish the event published; a scored side's value is a published finish, or a mean of several, so it can never sit below the bound. The bounded side therefore loses on the chart, and since its true finish is at or below where it is drawn, it loses in the record too. It holds because placement and norm agree by construction (`placementNorm == (placement - 1) / (fieldSize - 1)`, ADR 0013 measured it bit-exact on 4,540 of 4,540 decks), which is what makes "the last published slot" and "the worst published finish" the same fact.

Checked against the artifact anyway, over every one-sided point either chart draws: **0** would show the wrong winner. At `GGWAD` the margin is visible in the numbers, every published finish being 16th or better so that every scored side scores 0.444 or above, against a bound of 0.407.

So the band between the lines is a **lower bound on the gap between the drawn means**. The chart can understate a lead and can never overstate one, which is the direction `beats_a_coin` already takes with a tie.

That guarantee is stated in the chart's own currency, the mean over a side's **published** decks, the only mean this app has ever drawn, and it does not reach the archetypes' full fields. Where the scored side itself had decks cut, its mean rests on the published ones alone: 24 of the 72 counted bounded meetings are like this (`bus_stop_grixis`, `omnath`, `reanimator` and `walks`, six meetings each at `GGWAD`), and at 12 of them the cut decks sit low enough that the record leaves open which archetype's whole field finished better. The drawn winner cannot flip on the record as it stands, since every published finish beats the bound; what fuller publication could still do is move the scored side's own mean, which is how every mean in this app already behaves rather than a leak this ADR opened. It is recorded here because the counting section below leans on "settled", and settled means settled between the numbers the chart draws.

A midpoint has neither property. It would be a second, contradictory convention for the same shape of range, and it would assert a value with no guarantee attached: at `Area52IQ` the midpoint scores 0.478, mid-field, invented from a single data point.

## What it repairs

Measured over the pairs the chart draws under the floors this ADR ends on:

| chart | pairs carrying a broken point | repaired | still break |
| --- | --- | --- | --- |
| archetype timeline | 87 of 4,891 drawable | **72** | 15 |
| pilot head-to-head | 214 of 39,919 drawable | **161** | 53 |

**The 15 that stay are the pairs neither side was scored at**, and they are exactly the pairs among the six archetypes `GGWAD` scored none of: `boros`, `breachbond`, `goblins`, `hardened_scales`, `rogue` and `shops`, which is 15 pairs of 6. Two sides drawn at one bound would assert a tie the record does not hold, since their decks have distinct real finishes somewhere in one interval and which is better is precisely what is missing. Every pair where **one** side was scored is repaired, all 72 of them, because ADR 0022 already drops brackets from this chart so `GGWAD` is the only event in play.

The pilot chart keeps brackets (ADR 0022: "dropping brackets would delete real meetings to compensate for absent ones"), so its 53 are its bracket meetings (7 pairs) plus its own both-unscored points (46).

## The bound is carried, never stored, and enters no mean

`_tail_bounds` derives it per draw in `trends.py` and the cells carry it beside the value. It is not minted onto the `Deck` in the build, which is where ADR 0016 puts a decided norm, and the difference is deliberate: a minted `placementNorm` flows into every mean in the app (meta share, the landscape's finish axis, pilot performance, the leaderboard), and this is a bound rather than a measurement. Those 24 decks keep `normImputed = 'none'`, so ADR 0016's invariant is untouched: every Deck still either carries a norm or says outright that no rule could give it one.

It follows that no mean and no share moves anywhere in the app.

## The headline counts the meeting, because the record settles it

This shipped the other way, and the surface argued with itself. `comparable_points` counted the events both sides were **scored** at, so a caret stood on the plot at a meeting the caption did not count: `artifact` versus `breachbond` printed "6 of 6 shared events" and cleared `beats_a_coin`, reading as a certified sweep, where the record is 6-1 of 7. Treating "no number for this side" as "this meeting did not happen" drops a meeting the record settles.

**A bounded meeting is settled, and by the same theorem that makes it safe to draw.** The bound is worse than every finish its event published, so the scored side's mean cannot sit below it: that side won the meeting as the chart draws it. That is a fact about the record as it stands rather than about a fuller one: at the 24 meetings whose scored side was itself partially published (the scope note above), publication of the rest of the field could lift that side's own mean past the bound, exactly as any mean in this app moves when its inputs do. The count is over the drawn means, which is what the reader is looking at and what the floor and the gate read. The count is therefore taken over `drawn_finish` rather than over the raw means, which makes the denominator exactly the set of points the chart draws a comparison at. A meeting neither side was scored at stays out, and that is precisely the meeting the chart breaks over. Every drawn comparison is counted and every counted meeting is drawn, which is the property that was missing rather than the number.

Measured over the whole surface: the denominator moves on **72** pairs, the named leader on **11**, and the `beats_a_coin` verdict on **4**. `artifact` versus `breachbond` now reads "6 of 7", hedged.

**The sign test survives the change, which is not obvious, and not by the argument first written here.** A bounded meeting is a certainty rather than a coin flip once you condition on which side got cut, so counting it as a Bernoulli trial needs an argument. The tempting one, that under the null *which* side the event left unscored is itself a fair coin, is false whenever the two sides fielded different numbers of decks: the side with more decks is harder to cut in full, and 38 of the 72 counted bounded meetings are between unequal counts, up to 96/4 where `walks` fielded 4 decks against a single `boros` (hypergeometric over which 12 of `GGWAD`'s 28 slots went unpublished). What holds is narrower and enough. Every pair carries at most one bounded meeting, because one event bounds anything, and the two-sided tail probability the gate stands on is invariant to the bias of a single trial: with the other n-1 meetings fair, the chance that *either* side reaches k of n is the same whatever that one trial's p, by the symmetry of the fair binomial over the rest. So no certified headline is looser than `beats_a_coin` assumes, including the one that leans on a bounded meeting (`boros` versus `walks`, 12 of 16). What the bias does weaken is the directional evidence inside such a pair, whose one-sided p is larger than the fair count reads. The argument breaks at two bounded meetings in one pair, so a second partially published event entering the corpus reopens it.

**The floor moves with it, because it reads the same function.** `MIN_ARCHETYPE_EVENTS` counts `comparable_points`, so a pair whose second meeting is bounded now clears it: **3** pairs that were refused now draw, 4,891 rather than 4,888. That is the same correction rather than a side effect, since refusing a pair whose record settles two meetings was the identical mistake as not counting one of them. The constant's own reasoning is unharmed: it exists so a chart holding one drawable point and a gap cannot clear the floor, and a both-unscored meeting is still exactly that.

`drawn_finish` moved from the app into `trends.py` to make this possible, beside `comparable_points` and `beats_a_coin`, for the reason those two are already there: what is drawn and what is counted have to be one rule. It sat in the app for as long as the two were allowed to disagree.

## One rule, asked of the cell, on both charts

The fix above is only worth having if it cannot be undone by the next reader, so the rule is not a function the callers remember to call. Both rivalry cells answer `drawn()`, returning `((a, a_is_bound), (b, b_is_bound))`, and every reader asks the cell: the two figures for their y values and their marker symbols, `_has_bounded_point` for whether a legend is owed, `comparable_points` for the denominator and both floors.

That replaces a `sides` callback the caption used to pass in to pull four raw values off a cell and re-apply the rule itself. It was a seam things drifted through, and they did: the caption had no idea whether the figure had drawn a b side at all, which is how 12 solo timelines came to offer a legend for a caret nobody could see. A reader that asks the cell cannot get a different answer than the figure got.

**The pilot floor moved onto the same count**, and it had been the odd one out on a premise the corpus contradicts. `MIN_SHARED_EVENTS` counted events both pilots *entered*, justified in its own comment by "a pilot brings one deck to an event, so a shared event is a comparison by construction". A pilot can turn up and go unscored, so it is not: **10** pairs shared two events, could be compared at one, and drew a rivalry over time holding a single comparison and a gap, which is the shape that floor exists to refuse. It now counts `comparable_points` like the archetype floor, and the refusal names comparisons rather than attendances. 39,919 pairs draw where 39,929 did.

So the invariant holds across both charts and every reader of either: **a meeting is drawn exactly when the record settles it, counted exactly when it is drawn, and floors on the same count it states.**

## The mark is not the imputed asterisk

A bounded point is drawn as an open downward triangle at the bound, and its hover reads `≤ 0.41 (1 = 1st)` with "no finish published" where the deck count or the field ratio would sit.

It does **not** reuse `numfmt.IMPUTED_MARK`. The asterisk has a precise meaning ADR 0016 spent a column per value establishing: a number this project worked out in place of one the source did not record. A bound is a different claim, an inequality rather than a value, and marking both with `*` would blur a distinction that ADR exists to hold. The glyph carries the inequality instead, `≤` states it in the readout, and one legend line under the plot says what the triangle means, drawn only when one is on screen, which is the rule `_head_to_head_caption` already follows for the asterisk.

**The number inside the inequality still carries its own provenance, and that is not this decision coming back.** What is settled above is that `*` must not mean *being* a bound. Where the number came from is the orthogonal question `*` answers everywhere else, and a bound divides by the field, so it is the project's arithmetic exactly when the field size is. At `GGWAD` the 28 is Rule A's, which is why the scored side of that meeting already reads `9 / 28*`; the bounded side beside it read a bare `≤ 0.41` under a legend saying marked numbers are ours, so it claimed the source had published it. It now reads `≤ 0.41*`. The two marks are orthogonal by construction: ▽ and ≤ say this is a bound, `*` says the number behind it is ours.

## The solo line is out of scope and keeps its break

`archetype_timeline(a)` with one archetype still breaks over an attendance it was never scored at, on the 6 archetypes `archetypes_with_history` names. The guarantee above is about a comparison, and a solo line has none to get wrong; the picker's count already states the evidence behind the line, and the break against the 0.5 reference is a claim worth deciding separately rather than inheriting.

The bound those 6 would draw exists and is the same 0.407, so this is a decision not to draw it rather than an inability to. It carries no ticket; this section is the record until one is opened.

The decision has to be taken where the point is built, not where it is drawn. `mean_b` is None at every point of a solo series because there is no second archetype, not because a side went unscored, so a rule reading "bound the side with no mean" bounds a side that does not exist. It shipped that way: every point of every solo line carried a `bound_b`, no figure drew it (there is no b trace to draw), and `_has_bounded_point` read it anyway, so 12 of the 121 solo timelines offered the caret's legend under a plot with no caret on it. `archetype_timeline` now bounds the b side only when a b archetype was asked for.
