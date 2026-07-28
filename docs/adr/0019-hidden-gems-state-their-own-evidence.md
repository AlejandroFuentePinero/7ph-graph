# Hidden gems state their own evidence: a chance, a pilot count, and no near misses

> **Partly superseded by [ADR 0020](0020-hidden-gems-are-found-inside-their-own-archetype.md).**
> `gem_prob` is **withdrawn**. Everything below about it is accurate and was still not
> enough: the chance it printed is dominated by the slice mean it shrinks toward, so it
> tracks how the archetype finishes rather than how the card does. 39 of the 44
> archetypes with gems could not put a single card over 10%, and an average Storm card
> scored 45.8% before its own record was read. The remedy is not a better estimator on
> that bar, it is not asking an absolute bar at all, so ADR 0020 asks the question
> inside the archetype and prints exact odds instead. What survives here, and is carried
> forward unchanged: the **pilot count** (`Node.pilots`, and its measurements), and the
> **rejection of the bootstrap hold-share**, which applies with equal force to the new
> rule since that lift is also computed on the sample that selected the card. The
> near-miss section is void with the band it ranked against. `_card_spread`,
> `_gem_chance` and `MAX_GEM_MEAN_NORM` are deleted.

The band ADR 0012 fixed is sound as a population and overclaims as a list. Membership is
a hard cut (`MAX_GEM_MEAN_NORM = 0.33`) applied to a mean a gem takes from a median of
**six** ranked decks, so a card near the line is admitted or refused on luck, and the tab
drew every gem identically with no number on it at all. This is ADR 0017's finding
carried to its third surface, and it takes ADR 0017's remedy: the estimator stays, the
claim changes.

Measured over the 51 slices `gem_archetypes` offers, which is exactly the set of views
the tab can produce, and checked against `hidden_gems_subgraph` for every one of them:
283 gems, **0 mismatches** on membership, deck counts and pilot counts. The band and the
pilot counts are rebuilt from the graph without the query's help; the chance is
re-derived through a different code path (`statistics` primitives and `NormalDist`
rather than hand-rolled sums and `erf`) and agrees to 4e-15, which catches a coding slip
on either side and not a wrong model, since both fit the same decomposition.

## Every gem carries a chance, and it is not the bootstrap the ticket first asked for

`Node` gains `gem_prob`: the chance the card's *true* mean finish clears the bar, given
how little evidence stands behind it. The observed mean is shrunk toward the slice's own
deck-level mean by a weight fitted from the record (`query._card_spread`, the same
one-way random-effects decomposition `trends._shrinkage` fits over pilots), and the
chance is how much of the resulting distribution sits on the good side of 0.33.

Issue #176 first prescribed a seeded bootstrap: resample a gem's own decks, report the
share of resamples that held the band. That is measured here and **rejected**, and the
gap is not marginal:

| over the same 283 gems | |
|---|---|
| bootstrap hold-share | mean **0.73**, 55 gems at 0.90 or better |
| printed chance | median **0.0007**, 30 gems reach 0.10, 16 reach 0.50 |
| per-gem ratio between the two | median **1078x** |

The hold-share is centred on the very sample whose luck selected the card, so it
re-confirms the luck. Printing it as "how reliably this is a gem" would have replaced one
overclaim with another, which is why the number the tab prints is the shrunk one. There
is no seed to hold, because nothing is drawn: the seeding requirement #176 wrote for the
bootstrap is met by there being nothing random left.

That is not a formality. The bootstrap above had to have each card's finishes **sorted**
before resampling to reproduce at all, because Ladybug's row order moves between calls
and an unsorted record hands the same seeded stream a different draw each run: unsorted,
the same artifact reported 54 to 57 gems at a hold-share of 0.90 across runs. That whole
failure mode is absent here.

What is not claimed, because it was checked: the chance is not bit-identical between two
calls on one artifact **by construction**. The fit sums each record in sorted order, but
`mean_norm` and the slice mean are engine `avg()` aggregates whose last bits move with
row order, and a sampled `mean_norm` differs by one unit in the last place between calls
on four slices of six. The shrinkage at `k` around 60 damps that far below the printed
resolution, so every gem's printed chance held across runs; the honest statement is that
there is no *sampling* variation left, and what remains is the same float-order noise the
oracle's own 1e-9 tolerance was written for.

**The fit is the format's, the mean it shrinks toward is the slice's.** Within-card sd
0.2994 against a between-card sd of 0.0385, so `k` is about 60: a card needs sixty decks
before its own record outweighs simply knowing it is a card, and no gem in the band has
more than 21. Fitting that per slice was tried first and fails outright: **22 of the 51
offered slices** produce no separable between-card term at all, which would have left
nearly half the tab unable to state a chance. The shrinkage target is still the slice's
mean, because the bar is absolute and a slice is not: in Storm, which averages 0.334, a
card at the bar is mostly its archetype, and the chances there are the tab's highest for
exactly that reason.

The cost is 0.32s per drawn slice (0.41s worst), almost all of it the one global query
the fit reads.

**The fit lands below the range #176's review swept, and that is the estimate, not a
choice.** The review's posterior table swept an assumed between-card sd from 0.134 down
to 0.065 (its split-half value) and reported median chances of 0.37 down to 0.06. The
one-way fit here returns 0.0385, below that floor and close to the review's own 5+ deck
ANOVA figure of 0.0355, so the printed median is 0.0007 rather than 0.06 and the column
is flatter than the review's table implies: 30% of gems print under its smallest step.
The value is fitted rather than assumed, by the same estimator the race already uses, and
nothing in the record supports raising it to make the column more talkative. The cost is
real and worth naming: the chance discriminates well at the top (0.75 down to 0.10 over
the first 30 gems) and barely at all in the tail, where it says only "not on this
evidence". That is the honest reading of a between-card spread this small, and it is the
finding rather than a defect of the column.

## Every gem carries its distinct pilots

A deck's finish is steered by whoever piloted it, and #175 established pilot level as the
strongest reliable signal in this data. Over the same 283 gems, a median **44%** of a
gem's edge over its slice is explained by its pilots' records alone, **49 gems (17%)**
rest on two pilots or fewer, and 11 on exactly one. Nothing on the tab hinted that a gem
can be one pilot's pet card, so `Node` gains `pilots` and the table prints it beside the
deck count.

A distinct-pilot floor is a real option and is **not** taken here: it would change the
population rather than the claim, which is the maintainer's decision and not this
ticket's. `MIN_GEM_DECKS`, `MAX_GEM_SHARE` and ADR 0012's two-bound reasoning are
untouched.

## The numbers go in a table above the picture

The graph draws which decks run which card and carries no number at all, so the evidence
rides in a table (`app._gem_table`): card, decks, pilots, finish, gem chance, ordered by
the chance. Above the graph rather than under it, unlike the race's leaderboard: the
graph frame fills the viewport, so a table below it is a screen away from the claim it
qualifies.

The column keeps three readings apart, which one number cannot. A chance the app's share
convention can write is written; a chance too small for it to write, where a quarter of
the gems sit and `numfmt.share` rounds to a flat "0%", prints as "&lt;0.01%", because a
card the band admitted is not impossible; and a chance that cannot be stated at all
prints a dash, since "we cannot say" and "no chance" are different answers. Which case a
chance falls in is decided by asking the convention what it would write, not by a
threshold of this table's own: the first draft carried the threshold, set it a step out,
and quietly downgraded twenty chances the convention could in fact have written.

The copy moves with it. The lede reads "Rare cards whose record suggests they
overperform" instead of "Under-the-radar cards", the table's caption states the drawn
slice's own median decks and pilots and that near the bar the band turns on a deck or
two, and the FAQ gains a second gem entry (`faq-gems-certainty`) beside the rule, the way
ADR 0017 paired the race's two.

## The near misses stay out

313 cards sit outside the band and reach it in 30% or more of resamples, a median of 4
per slice and never more than 21. Drawing them is affordable: the median slice would go
from 18 nodes to 33 and the largest from 105 to 203, all under `RENDER_THRESHOLD`. So the
node budget is **not** the reason, and the reason #176 offered does not survive
measurement.

They stay out because the statistic that promotes them is the one this ADR just rejected.
Under the number the tab prints, the near misses are median 0.0003 and **not one of them
reaches a coin flip**; their 30%-plus hold-share is the same sample-centred artefact that
made the gems read as 0.73. The band is a definition, not a ranking, and admitting a
second tier on a statistic the tab does not print would mean showing a reader two classes
of card sorted by a rule the surface cannot state.

What is worth recording against that: the strongest near misses **outrank most gems** on
the chance column (Conjurer's Bauble in Storm at 0.459 against a gem median of 0.0007),
because the chance orders cards on a scale the band cuts with a step. That is a real
argument for eventually drawing a wider set and letting the chance sort it, and it is now
a possible ticket rather than a guess, because the column that would do the sorting
exists. It is not taken here: it widens the drawn population, which is ADR 0012's
territory and the maintainer's call.

## The `[5, 5]` band at the smallest offered slices stands

ADR 0012 rejected `ceiling = max(floor, share)` for degenerating the band to `[5, 5]` in
small slices, and the shipped constants produce that degeneracy just above the crossover:
**10 of the 51 offered slices** have a ceiling under 6, so their band asks for a card in
exactly five decks. Eight of them produce gems: **11 gems, every one at exactly 5 decks**,
none with a chance above 0.084 and most under 0.01.

The position is that these slices stay, and the surface says what they are. Raising
`MIN_GEM_SLICE` above the arithmetic crossover would take ten archetypes off the dropdown
to suppress eleven cards that the chance column already reports as unsettled, and it
would be a change to ADR 0012's constants that this ticket is explicitly not authorised to
make. The band there is not wrong: it is the two bounds meeting, and a five-deck card in a
51-deck slice really is both as attested and as rare as that slice can offer. What was
wrong was printing it with nothing beside it.

## Consequences

`Node` gains `pilots` and `gem_prob`, both `None` on every node no gem query produced, so
the seam widens additively in the sense ADR 0014 records: no field changes type or
meaning, and a consumer that ignores both sees what it saw before. `hidden_gems_subgraph`
runs two more queries and one fit per call.

The golden oracle carries the two new fields on its 44 gem Card nodes. It was **spliced,
not recaptured**, the third path `docs/development.md` now records: the two gem cases'
node sets, their edges, every other case and both catalogues were asserted unchanged
against a live capture first, and only then were the two keys written in, so the diff is
44 rows gaining two keys and no reordering or float churn at all. `graph7ph baseline`
reports no regression.

No stored graph property changes, so no rebuild.
