# Hidden gems are found inside their own archetype, and the list says how much of it is luck

A gem is a card that is rare inside one archetype and crowds that archetype's own best
decks by more than chance explains. Every term is measured within the archetype, and
nothing is compared across archetypes except the probability bar, which every drawn card
clears identically:

```
for each archetype with >= MIN_GEM_SLICE ranked decks:
    top cut = the archetype's best GEM_TOP_CUT of its ranked decks, by finish
    for each card in >= MIN_GEM_DECKS of its decks and <= MAX_GEM_SHARE of them:
        p = P(this many or more of the card's decks land in the cut), asked
            inside each event the card turned up at and convolved over them
        admit if p <= MAX_GEM_LUCK
```

| Constant | Value | Question it answers |
| --- | --- | --- |
| `MIN_GEM_DECKS` | 5 ranked decks | Is there enough evidence to trust this? |
| `MAX_GEM_SHARE` | 15% of the archetype | Is this still rare *there*? |
| `GEM_TOP_CUT` | the best 20% | Which of its decks count as the good ones? |
| `MAX_GEM_LUCK` | 0.010 | How often would chance alone do this? |

An archetype is a deck's **primary** tag and nothing else. `CONTEXT.md` fixes that rule
for aggregates ("counting every tag sums to roughly 160 percent of decks") and it binds
harder here than anywhere, because the archetype is now the unit every one of the four
constants is measured against. Over the ranked record it is 7332 deck-archetype pairs
against 4566 primaries, and getting it wrong dominated everything else in this ticket:

| | gems found |
|---|---|
| every tag, at a 33% cut and a 0.005 bar | 33 |
| **primary tags only**, same constants | **10** |
| primary tags, constants re-swept | 20 |

That last row is the list before the two corrections below take it to 7; it is here to
size the primary-tag effect, not to describe what ships.

Two mechanisms, and the first is not subtle. **Nine of the fifteen archetypes that
produced gems fell below `MIN_GEM_SLICE` once only primary tags counted**: Jeskai went
from 217 ranked decks to 26, Bant from 160 to 18, Temur from 76 to 20. Those were not
archetypes being measured inside, they were secondary labels pooling decks whose engine
is something else, and Bant alone contributed three gems including one resting on a
single pilot.

The second reaches the archetypes that survived: Lands lost only 16 of its 210 decks and
still lost four of its six gems. Pooling secondary tags makes a slice a **mixture** of
sub-archetypes, and the hypergeometric null assumes a card's decks are a random subset of
one homogeneous pool. A card that marks a sub-group then reads as concentrated wherever
that sub-group happens to sit in the pooled ranking. So the longer list was not merely
longer: it was scored against a broken null, which means its own expected-by-luck figure
was understated too, and no amount of tightening the bar would have found that out.

The sweep below then re-picked two of the four constants: with the pools correct, a
33% cut and a 1-in-200 bar were leaving real findings unread.

This replaces the absolute performance bar ADR 0012 fixed (`MAX_GEM_MEAN_NORM = 0.33`,
the top third of *every* ranked deck in the format) and the shrunk posterior ADR 0019
put beside it. Both are withdrawn; the rarity argument, `MIN_GEM_SLICE`, and the
pilot count survive.

## Why the absolute bar had to go

`gem_prob` shrank a card's observed mean toward its slice's mean and asked whether the
result cleared 0.33. A gem rested on a median of six decks against a fitted `k` of 60,
so the card supplied 8% to 20% of its own estimated level and its archetype supplied the
rest. The printed number therefore tracked the archetype, not the card:

| archetype | slice mean | best gem chance |
|---|---|---|
| Storm | 0.334 | 0.7467 |
| Bus Stop Grixis | 0.336 | 0.4946 |
| Walks | 0.405 | 0.1396 |
| Bant | 0.510 | 0.0001 |
| Mono Black | 0.603 | 0.0000 |

**39 of the 44 archetypes with gems had no card above 10%.** Decomposing the strongest
one shows the mechanism: Lurrus in Storm printed 74.7%, an average Storm card printed
45.8% before its own record was read, and the identical Lurrus record inside an
archetype averaging 0.45 prints 1.5%. Roughly 46 of those 75 points were "you are
looking at Storm". Measured inside the archetype, the archetype cancels.

## Four alternatives measured and rejected, so nobody repeats them

1. **Normalise each archetype internally, keep the shrunk posterior.** Ranking decks
   inside their own archetype and refitting gives a within sd of 0.2872 against a between
   sd of 0.0258, so `k` rises to 124 and **all 283 gems go to zero**. Removing the
   archetype offset also removes the archetype's contribution to how much cards differ,
   which doubles the shrinkage.
2. **Flip the scale direction.** Arithmetically invariant, verified to 4.4e-16.
3. **Widen the rarity ceiling to chase signal.** Excess over chance rises monotonically
   with the ceiling (0.83x at 10% of decks, 1.45x at 20%, 1.59x at 30%), and binning by
   how common a card is says why: cards in <=5% of an archetype's decks run at **0.68x**
   (z -4.5) and 5-10% at **0.99x**, while 10-20% runs at **2.09x** (z +11.1) and 20-40%
   at **5.45x**. All the signal is in cards that are not rare, so optimising the ceiling
   abandons the premise rather than serving it.
4. **Pilot endorsement.** "Do better pilots pick this card" looked strong (sd of z 1.727
   against 1.000 under chance, 132 cards at |z| >= 2 against 36 expected) and collapsed
   once each pilot was allowed to vote only once: **41 better-piloted against 39
   expected**. The apparent signal was one pilot's pet card counted five times.

Restricting the slice to above-median pilots before measuring enrichment does not help
either (0.73x, worse than the 0.83x over everyone), because the top cut is relative:
removing weak pilots moves the goalposts by as much as it removes noise.

**One negative result survives every control and explains the rest: 115 rare cards are
played by significantly *worse* pilots than chance, against 36 expected.** Rarity in this
corpus marks brewing and inexperience more than hidden tech, which is the finding this
whole rule is built around rather than against.

## Three constants are swept; the ceiling is a definition

144 combinations of top cut (10/20/25/33%), rarity ceiling (5/10/15%), deck floor
(5/6/8) and probability bar (0.05/0.01/0.005/0.001) were scored under a rule fixed
before the results were looked at: **maximise genuine finds (found minus expected by
luck), subject to the graph fitting under `RENDER_THRESHOLD` and at most half the list
being luck.** 95 produce any gems at all and 21 qualify; the top five, over primary
tags, scored under the shipped null including the pilot correction below:

| genuine | found | luck | nodes | top cut | ceiling | floor | max p | archetypes |
|---|---|---|---|---|---|---|---|---|
| **3.7** | **7** | **3.3** | **49** | **0.20** | **0.15** | **5** | **0.010** | **4** |
| 3.5 | 6 | 2.5 | 43 | 0.20 | 0.15 | 8 | 0.010 | 3 |
| 3.4 | 5 | 1.6 | 30 | 0.20 | 0.10 | 8 | 0.010 | 2 |
| 3.3 | 6 | 2.7 | 39 | 0.33 | 0.10 | 6 | 0.010 | 2 |
| 3.0 | 6 | 3.0 | 43 | 0.20 | 0.15 | 6 | 0.010 | 3 |

A 20% cut takes four of the top five and the 15% ceiling takes three, which is what
alternative 3 predicts: a tight rarity bound is what suppresses the signal, and a cut of
the best third dilutes what "best" means.

**The floor is 5 because the sweep says 5, and it says so by 0.2 genuine finds.** That
margin is small and it is not noise: the score is exact, the null being a convolution of
hypergeometric tails with nothing sampled, so two cells differing by a tenth of a find
differ by a tenth of a find. The rule was fixed before the results were read precisely so
that a narrow win still counts, and rounding the floor back up to 6 on a preference would
be the "manufacture a result rather than read one" this design exists to prevent (ADR
0012). It costs what it says: one more card admitted on five decks, and `MIN_GEM_SLICE`
falls from 40 to 34, widening the archetypes the hunt can ask about from 32 to 40.

**The ceiling did not win this grid; it is the top of it.** `SHARES` stops at 0.15 and
0.15 ships, so the score never had the chance to reject it, and widening the axis shows
the score climbing with no peak in sight: at a 20% cut and the shipped floor and bar,
0.20 scores 5.9 genuine finds, 0.25 scores 8.1 and 0.30 scores 8.6, against 0.15's
3.7. That is refused rather than unmeasured, and the reason is the premise, not the
corpus: a card in 30% of an archetype's decks is its standard build, so a ceiling picked
by the score would be answering a different question from the one this rule asks. So
three of the four constants are measurements and the ceiling is a definition, and it is
listed in the table above the way a swept result would be, which it is not.

The one thing the corpus does say about the ceiling is that widening it is not where the
signal is anyway. Binned by how much of its archetype a card holds, at the shipped cell:
cards in 5-10% of the decks run at **2.93x** their expected count (4 found against 1.4),
while 10-15% runs at **2.02x** (2 against 1.0) and 5% and under at 1.07x (1 against 0.9).
The productive band is the middle of the rule, not its top edge, which is the opposite of
what alternative 3's cross-ceiling monotonicity suggested and is worth re-reading before
anyone widens the axis on that argument. It also means the tighter cell (a 10% ceiling:
5 found, 2.3 luck, 2.7 genuine) costs 1.0 genuine finds for a list where every card is
in under one deck in ten, which is the trade to make if this list is ever accused of not
being about rare cards.

The sweep is checked in as `scripts/gem_sweep.py`, beside the other measurement that
backs a decision (`scripts/points_agreement.py`, issue #143), and reproduces this table
and the shipped list of 7. The winning cell is a property of the corpus rather than of
the rule, so re-run it whenever the artifact grows. It is checked in rather than
attached to the issue because the version attached to #184 rotted invisibly: it calls
`query._ranked_deck_slice`, which this ticket deleted, and it matches `HAS_ARCHETYPE`
without `{isPrimary: true}`, the bug that moved two of the four constants. In `scripts/`
it imports the shipped null and the shipped constants, so the next such change breaks it
loudly instead of leaving a plausible harness on a closed issue.

## The luck count is part of the answer, not a caveat on it

Screening every rare card of every archetype is thousands of chances for
coincidence (36,844 archetype-card pairs in the format, 17,639 of them inside an
archetype big enough to ask and 2,267 surviving the two rarity bounds, and it is the
2,267 that were actually tested), so a bar of
1-in-100 still lets cards through: at the chosen constants **3.3 of the 7 are expected
by chance alone**, and nothing distinguishes which. The bar is a dial for list length,
not a filter for truth, which is why the sweep optimised genuine finds rather than the
ratio, at a 20% cut and a 15% ceiling:

| min decks | max p | cards kept | expected by luck | ratio |
|---|---|---|---|---|
| 5 | 0.05 | 31 | 26.8 | 1.16x |
| 5 | 0.01 | **7** | **3.3** | **2.13x** |
| 5 | 0.005 | 1 | 1.4 | 0.71x |
| 5 | 0.001 | 0 | 0.2 | 0.00x |
| 6 | 0.01 | 6 | 3.0 | 2.01x |
| 8 | 0.01 | 6 | 2.5 | 2.41x |

The bar is now load-bearing in a way it was not before the pilot correction below. Loosen
it to 0.05 and the list is 31 cards of which 27 are expected coincidence, which is not a
finding. Tighten it to 0.005 and there is one card left, and to 0.001 and there are none:
charging a card for its pilots raises every tail, so almost nothing in this corpus reaches
those bars at all. 1-in-100 is the only setting on the axis that returns a list and a
reason to believe it, which is a narrower escape than the old numbers suggested.

So the count rides on the answer (`Subgraph.expected_by_luck`) rather than being left to
the reader, and the oracle grades it. It is summed over every card the rule *screened*,
not over the ones it kept: the rejects are most of the evidence about how often this bar
is cleared by accident. And it is each card's own chance of clearing, not the bar itself,
because a card in five decks has six possible outcomes and its smallest reachable tail
(0.0002) sits well under the bar, while a card in twenty has finer steps.

That number is load-bearing rather than decorative, because there is no validation route
behind it (below). Without it this design reproduces #176's defect at a smaller scale.

**The count used to be a floor stated on the page rather than in the code. It is now in
the code.** The null shuffles *decks*, so it treats a card's twelve decks as twelve
separate results. Often
they are not: a pilot's decks rise and fall together, the pilot ICC of `placementNorm`
inside an archetype is 0.213 (0.226 over all ranked decks pooled, which is the estimator
`PILOT_ICC` ships), and pilot volume tracks finish hard (a pilot with 8 or more
ranked decks averages 0.438, a one-deck pilot 0.624). Rerun with *pilots* shuffled instead
of decks, keeping each pilot's run of results together, three ways: resampling each card's
decks pilot-block by pilot-block gives 12.3, permuting pilot blocks of equal size gives
14.3 (sd 4.1), and permuting them within size bins gives 15.3 (sd 4.9). Measured against
the 20-card list the rule admitted before either correction below, that list sits at
P = 0.11 to 0.16 rather than under 0.001, and genuine finds fall from
13.0 to about 5 to 8. Two of the Lands gems (both baubles) land at 0.38 and 0.39.

The shuffle that shuffles decks is verified correct at what it measures: permuting
finishes within each archetype, which preserves the dependence between cards that the
analytic sum ignores, reproduces it exactly (7.1, sd 3.2, against the summed 7.0).

None of those three shuffles is shipped, and the reason is not that they are wrong. Each
needs resampling, so each would cost the exactness the FAQ promises the reader ("nothing
is resampled or randomised, so a rebuild of the same graph reports the same numbers") and
the oracle grades bit for bit. And each errs as far the other way: it credits the pilot
with everything, so a real card found early by a strong pilot is indistinguishable from
that pilot's pet card, which is the same wall alternative 4 hit. Collapsing to one result
per pilot is the same overcorrection in closed form, and it is not a hypothetical: run
that hypergeometric with pilots as the unit and **none** of the twelve cards the
unstratified-pilot rule admitted survives, including Lair of the Hydra at nine top-cut
decks from six different players. A rule that rejects that is not measuring what it says.

What is shipped is the correction between the two, and it is exact.
`query._pilot_deflated` charges each card a design effect
`1 + (mean decks per pilot - 1) * PILOT_ICC` and deflates its tail by it on the z scale,
the standard survey-statistics move for clustered sampling. `PILOT_ICC` is 0.226, the
one-way random-effects ICC of `placementNorm` by pilot over all 4,567 ranked decks and
1,075 pilots, measured off the graph and reported by the sweep. The properties that
matter here:

- **It is proportional, not binary.** A card whose decks are all different players has a
  mean of 1, a design effect of 1, and is returned untouched. Only repetition is charged,
  and it is charged at the rate the corpus actually exhibits rather than at the extreme.
  1,813 of the 2,267 screened pairs have at least one repeat pilot, at a mean of 1.67
  decks per pilot, so this is the common case and not an edge.
- **It is deterministic.** No RNG, no seed, no resampling. The FAQ's promise holds and
  the oracle still grades bit for bit, which is what ruled the three shuffles out.
- **It is honest about its own precision.** Deflating on the z scale is a normal
  approximation to a discrete tail, so it is right about direction and size and not about
  the fourth digit. It exists to stop a card resting on one player's habit, not to price
  one exactly. Rishadan Port lands at 0.0102 and Fiery Impulse at 0.0100, coin flips
  against the bar rather than clear rejections.

The effect on the shipped list is large and points the way the evidence pointed all
along: **twelve gems become seven, and genuine finds fall from 8.5 to 3.7**. Dropped are
Rishadan Port, Fiery Impulse, Mishra's Bauble, Collector Ouphe and Pyroclasm, all of them
already at the bottom of the list and all of them resting on two to four players.
Pyroclasm's three top-cut decks were two people; Collector Ouphe's four were two.

The genuine count falls further than the list does, and that is the finding rather than
an artifact. Expected-by-luck barely moves (3.5 to 3.3) while the count halves, because
clustering cuts both ways: repeated players inflate individual cards *and* make flukes
more common in general, since any card riding in a strong pilot's 60 clears the bar more
easily than an independence assumption predicts. Signal down, noise flat. The old 8.5 was
never there; it was this corpus counted generously.

Two things this does **not** fix, recorded so nobody re-derives them as news. It does not
absorb correlation between cards that travel together in one shell, though that is a
weaker objection than it looks: an expectation of a sum is the sum of expectations
whatever the dependence, so the 3.3 stays unbiased and only its spread widens, and where
the finding is a synergy the co-travel is signal rather than noise. And it cannot be
validated forward, because a gem that works stops being rare (below).

**Where it is printed is a separate question from whether it is kept, and the two were
decided differently.** It first shipped in the caption beside the table and was moved
into `faq-gems-certainty` during review, on the reader's objection that a count of false
positives raised next to the rows asks "which ones?" at the one place with no room to
answer it. It is a property of the list and of no row in it, so no asterisk, tint or
ordering can point at the cards it applies to. In the FAQ it sits inside the answer to
that question, next to the per-row reading that *can* be acted on: the odds column, and
what Decks, In top and Pilots each rest on.

This is not a softening. The count is still computed, still carried on the answer, still
graded by the oracle, and still stated on the page. Moving it back to the caption would
undo a decision made deliberately, so it is recorded here rather than left as a diff
nobody can explain.

## Temporal validation: built, run, and set aside by decision

A temporal holdout was built and run. It trains the rule on decks up to year *X* and
measures, in *X+1*, whether the flagged cards were **adopted** (their share of the
archetype rose) more than the rare cards the rule did not flag. Adoption rather than
persistence is the right criterion, because a gem that works stops being rare.

| split | gems | gem share change | control share change | z |
|---|---|---|---|---|
| train <=2024, adoption in 2025 | 2 | +20.6% | +0.1% | +4.63 |
| train <=2025, adoption in 2026 | 7 | -0.4% | -0.5% | +0.03 |

The first split is two cards (Elvish Spirit Guide 10% to 35% and Tinder Wall 8.8% to
25%, both in Initiative), which is an anecdote. The second has more cards and shows
nothing: 29% of flagged cards rose against 30% of unflagged ones. Neither settles
anything, because the training windows are small and 2026 is a part year.

**Set aside as a validation route by maintainer decision:** gems are transient by
nature, so a card that keeps looking like a gem is a card nobody acted on, and tracking
their temporal trace is not what this feature is for. Recorded with its numbers so it is
not re-run and reinterpreted from the same inconclusive data.

## The field size is not the problem; the length of the record is

`placementNorm = (placement - 1) / (fieldSize - 1)`, so winning a 20-player local and
winning a 200-player major both score 0 and the top cut cannot tell them apart. The tilt
is real: the median field behind a ranked deck is 70 and behind a top-quarter deck is
53, and 22.3% of top-quarter decks come from events under 32 players against 16.4% of
all decks. The race solves this with a majors cut (`MAJOR_FIELD_SIZE = 64`, ADR 0017),
and #176 confirmed the gem population is deliberately all events.

Measured at the chosen constants, both ways of applying a majors floor:

| | found | expected by luck | genuine | nodes |
|---|---|---|---|---|
| all events (shipped) | 7 | 3.3 | **3.7** | 49 |
| top cut restricted to majors | 1 | 2.5 | -1.5 | 7 |
| whole pool restricted to majors | 1 | 2.4 | -1.4 | 7 |

Both majors floors now score *negative*: they keep one card and expect two and a half by
chance, which is a list that is worse than nothing. The verdict is the same one the
original numbers gave and it is no longer close.

**Leave the floor out.** The luck expectation barely moves while genuine finds all but
vanish: it cuts signal and noise in the same proportion, so it is a loss of power bought
for nothing.

**But the reasoning that first accompanied this table was wrong, and it mattered.** It
read: "the hypergeometric null is exact whatever decks the cut is drawn from, so the
tilt shows up as a cut that is easier to land in rather than as a wrong probability."
The first clause is true of field size and false of the thing field size was standing
in for.

Field size is the denominator. The quantity that breaks the null is **how much of an
event's record exists at all**. 26 of the 107 events record under half their own field
and so publish only a top cut: SSWam holds 7 decks against a field of 88, so all 7 score
between 0.00 and 0.05 and the ~81 entrants who missed the bracket leave no trace. Every
deck the graph holds from such an event is a good finish by construction. Measured over
the offered archetypes:

| a deck from | lands in its archetype's top cut |
|---|---|
| a full-coverage event | 597 / 3399 = **17.6%** |
| a top-cut-only event | 114 / 162 = **70.4%** |

That is not a field-size error and ADR 0015's correction does not reach it, the field
being right and the record short. Correcting a field in fact **strengthens** the tilt,
since recognising a bracket as the top of a 24-player field is recognising those decks
as good: Pats Birthday Brawl's mean norm moves 0.375 to 0.114 under Rule B. So the
remedy is not more field-size work and not a majors floor, both of which were tried.

A card is therefore not exchangeable with another card of the same rarity, which is
precisely what the hypergeometric assumes. Simulating the unstratified screen under a
null that keeps each card's event footprint and randomises only which deck of its
archetype it sat in at each event puts the true expected-by-luck at **9.3 where the rule
reported 7.0**, and an exact conditional test refuses **13 of the 20** cards it admitted.

**So the null is asked inside the event.** A card's hits at one event are hypergeometric
against that archetype's decks *there*, and the whole is their convolution
(`query._gem_tails`). At SSWam, where every deck of an archetype is in the cut, the term
is forced and the event contributes nothing rather than free credit. A card earns its
odds only where some decks of its archetype did well, some did not, and the ones running
it are the ones that did.

What this costs is list length rather than evidence: **12 cards at 3.5 expected by luck,
against 20 at a true 9.3.** The honest reading goes from "20 cards, 9 of them accidents"
to "12 cards, 3 or 4 of them accidents". It reduces exactly to the old arithmetic wherever an
archetype sits at a single event, which is what the fixtures in `test_query.py` pin.

Those twelve are the count before the pilot correction above, which is the second and
independent break of the same exchangeability assumption and takes the list to seven.
Event stratification cannot absorb it: ADR 0004 gives a pilot one deck per event, so
inside a stratum every deck is already a different player and the correlation lives
entirely across events.

Two costs are worth naming. The stratified null discards all between-event variation,
including any a card genuinely caused; that is accepted, because the cut is drawn inside
the archetype and an event's level is a property of who turned up rather than of any
card. And its power varies with how many decks an archetype fields per event: on the
current corpus only 46% of a screened card's decks sit in strata that can vary at all,
and the null's spread across those cards runs 0.40 to 1.15 hits between the tenth and
ninetieth percentiles. Dropping the floor to five is what made that share as low as it
is, since a five-deck card is the likeliest to have every one of its strata forced. A
sparse archetype (mardu fields a median of one
deck per event) has less to say than a dense one and will lose cards to power rather
than to bias.

Norm provenance is not a factor: `placementNorm` is always the corrected value, with
`normImputed` flagging the 83 of 4567 ranked decks (1.8%) whose norm was minted (ADR
0016) or rescaled against a corrected field (ADR 0015) rather than taken from the
source.

## A pet card is not disqualified

A card carried by one pilot stays in the list, with its pilot count beside it, and the
FAQ says so. Pilot identity affects how a baseline is computed, never whether a card is
shown: one pilot's six decks are one pilot's opinion, so any null must resample at the
pilot level rather than the deck level (which is exactly what collapsed alternative 4).
This is the maintainer's decision, recorded so it is not silently reversed.

## What ADR 0012 keeps, and what it loses

**Keeps.** The two bounds are still asymmetric and only the ceiling is a share, for
ADR 0012's original reason: the floor asks "is there enough evidence", a property of
sample size that does not scale with the meta, and the ceiling asks "is this still
rare", which is meaningless except against the slice. `MIN_GEM_SLICE` is still the
crossover where the ceiling falls under the floor, recomputed at 34 from the new
constants, and an archetype under it still has no answer rather than an empty one.

**Loses.** The performance bar and its absolute reasoning are gone. The refusal is now
*silent in the query*: with no dropdown there is no user to refuse, so a small archetype
is skipped inside the query and `SliceTooSmall` is deleted rather than caught. It is not
silent on the page. Dropping the dropdown removed the user to refuse, not the reason for
refusing, and 84 of the format's 124 archetypes (22% of its ranked decks) are never
screened, so the caption names the population: "found in the best 20% of each archetype's
decks, over the archetypes with 34 or more ranked decks". Without that clause a reader
whose archetype is absent reads "no gems here" off a page that means "not enough decks to
tell", which is the exact distinction ADR 0012 raised `SliceTooSmall` for. And ADR 0012's
refusal of the unfiltered view is **reversed**: it refused because the whole-meta view
dragged in 308 decks over the 250-node limit, and at 7 gems drawing only their top-cut
decks it is 49 nodes. That is a measurement that changed, not a rule that was
overridden.

## One unfiltered picture, cut to the node budget

The archetype dropdown is gone. The tab draws `Archetype -> Card <- Deck` for every gem
in the format at once, decks linking out to Moxfield as everywhere else, and it draws at
build time rather than behind a Draw button, because a button with no choices in front
of it is a step that asks the reader for nothing.

Only each gem's **top-cut** decks are drawn. The decks outside the cut are counted (they
are the card's rarity) and not drawn: they are not what the claim rests on, and drawing
them would put the reader's eye on the decks the rule discounted.

**Every** one of them, with no per-gem cap. A cap of five was carried while the list was
long, on the reasoning that the deck layer collapses: an archetype's best decks nearly
all run nearly all of its gems, so the deck nodes share a neighbourhood, a force-directed
layout has no information to separate them by, and they settle on one another with their
labels on top. That collapse is real and nothing tuneable fixes it. `avoidOverlap`,
spring length and gravity can inflate the blob but cannot separate nodes the graph gives
no reason to separate.

The cap was not what fixed it either, which is why it is gone. Measured against the
current list, a cap of five drew 27 decks of which **all 27 had an identical neighbour
set** to some other deck; drawing every top-cut deck draws 38 of which **33 (87%)** do.
The tie rate is a property of how gems overlap inside an archetype, not of how many
decks are drawn, and capping made it worse rather than better, since the decks a cap
keeps are the archetype's very best and those are the ones that run everything. So the
cap bought 11 fewer nodes, no legibility, and a picture that disagreed with its own
table.

What it cost was worth more than that. `top_decks`, and so the table's **In top 20%**
column, is the true count, and a reader who counted deck nodes against the column found
a discrepancy that took a sentence of FAQ to explain away. The two are now the same set
by construction, on 7 of 7 cards, so the FAQ invites the count instead of excusing it.

A drawn deck is wired to every gem of its archetype it runs, not only to the one whose
cut brought it in. Otherwise the picture would show a deck that visibly does not run a
card it runs, and the overlaps between gems are precisely what the picture says that the
table cannot.

`MAX_GEM_NODES` is now the only cap on the picture, and it cuts **whole gems** off the
weak end rather than the evidence behind a drawn one: a shorter list of fully evidenced
findings beats a longer list whose deck layer is a sample the reader cannot see the edge
of. It does not bind today, at 49 nodes against 250.

Deck nodes are labelled `pilot · finish` ("Rob L · 1st") rather than by `d.name`, which
everywhere else in the app is the deck's whole Moxfield title. Thirty-eight decks around
seven cards at forty characters each draw an unreadable mat of overlapping text; the
picture's job is to show *which* good decks run a card, and who played it and how they
finished is that in four words. The title stays one click away on Moxfield, which is
where a reader who wants the list is going anyway.

A card that is a gem in two archetypes is **two nodes** (`card:{tag}:{canon}`), because
those are two findings resting on two sets of decks and one merged node could carry only
one archetype's numbers. No card on the shipped list of 7 is a gem twice, but the rule
screens each archetype independently and nothing stops one from being.

`MAX_GEM_LUCK` is a threshold, not a length, so it admits more cards from more decks on
every ingest and a fixed bar would eventually breach the canvas. The drawn list is
therefore the **strongest prefix** that fits `MAX_GEM_NODES`, and the luck count is then
read at the odds of the weakest drawn gem rather than at the bar, so it describes the
list on screen. Taken as a prefix rather than packed with whatever else would fit,
because the picture has to stay a set the reader can name. Today it does not bind: 49
nodes against 250.

## Consequences

`Node` gains `top_decks` and `gem_luck` and loses `gem_prob`; `mean_norm` goes with it,
since no query set it once the gem view stopped. `_hypergeometric_tails` becomes
`_hypergeometric_pmf` plus `_gem_tails`, which convolves one term per event, and
`_gem_slices` returns a `_Slice` carrying each ranked deck's event and each event's
`(decks, of them in the cut)`, so the strata and the cut are counted off the same rows.
`MAX_GEM_DECKS` is deleted. `Subgraph` gains `expected_by_luck`,
`None` on every other query. `HiddenGems` loses its `archetype` parameter, which
invalidates the two gem cases in the golden oracle: they became one, and the oracle was
**recaptured with `--force`** in its own commit, per `docs/development.md`. Every
unrelated case came back identical row for row. `gem_archetypes` falls from 51 entries
to 40: `MIN_GEM_SLICE` dropped from 50 to 34, which admits more archetypes, and counting
primary tags alone shrinks every archetype, which removes more.

The gem view is now order-exact in the oracle rather than order-insensitive: it ranks its
whole answer before drawing any of it, so an order that moved would be an answer that
moved.

The whole hunt is four queries and about 0.5s on the built graph, run once at app start.
No stored graph property changes, so no rebuild.
