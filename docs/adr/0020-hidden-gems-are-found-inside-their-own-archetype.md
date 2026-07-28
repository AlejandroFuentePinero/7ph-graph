# Hidden gems are found inside their own archetype, and the list says how much of it is luck

A gem is a card that is rare inside one archetype and crowds that archetype's own best
decks by more than chance explains. Every term is measured within the archetype, and
nothing is compared across archetypes except the probability bar, which every drawn card
clears identically:

```
for each archetype with >= MIN_GEM_SLICE ranked decks:
    top cut = the archetype's best GEM_TOP_CUT of its ranked decks, by finish
    for each card in >= MIN_GEM_DECKS of its decks and <= MAX_GEM_SHARE of them:
        p = hypergeometric P(this many or more of the card's decks land in the cut)
        admit if p <= MAX_GEM_LUCK
```

| Constant | Value | Question it answers |
| --- | --- | --- |
| `MIN_GEM_DECKS` | 6 ranked decks | Is there enough evidence to trust this? |
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

Two mechanisms, and the first is not subtle. **Nine of the fifteen archetypes that
produced gems fell below `MIN_GEM_SLICE` once only primary tags counted**: Jeskai went
from 217 ranked decks to under 40, Bant from 160, Temur from 76. Those were not
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

## Every constant is swept, not chosen

135 combinations of top cut (10/20/25/33%), rarity ceiling (5/10/15%), deck floor
(5/6/8) and probability bar (0.05/0.01/0.005/0.001) were scored under a rule fixed
before the results were looked at: **maximise genuine finds (found minus expected by
luck), subject to the graph fitting under `RENDER_THRESHOLD` and at most half the list
being luck.** 125 produce any gems at all and 69 qualify; the top five, over primary
tags, against the artifact this ticket was built on:

| genuine | found | luck | nodes | top cut | ceiling | floor | max p | archetypes |
|---|---|---|---|---|---|---|---|---|
| **13.0** | **20** | **7.0** | **102** | **0.20** | **0.15** | **6** | **0.010** | **7** |
| 11.5 | 17 | 5.5 | 89 | 0.20 | 0.15 | 8 | 0.010 | 6 |
| 11.5 | 16 | 4.5 | 78 | 0.20 | 0.10 | 6 | 0.010 | 5 |
| 11.3 | 21 | 9.7 | 108 | 0.20 | 0.15 | 5 | 0.010 | 8 |
| 10.8 | 17 | 6.2 | 112 | 0.33 | 0.15 | 6 | 0.010 | 7 |

A 20% cut takes nine of the top ten cells and the 15% ceiling takes seven, which is
what alternative 3 predicts: a tight rarity bound is what suppresses the signal, and a
cut of the best third dilutes what "best" means. The runner-up raises the floor to 8
and gives up 1.5 genuine finds to remove 1.5 expected false ones, which is a wash by
this rule and a real trade if the list ever needs to be shorter.

The sweep is checked in as `scripts/gem_sweep.py`, beside the other measurement that
backs a decision (`scripts/points_agreement.py`, issue #143), and reproduces this table
and the shipped list of 20. The winning cell is a property of the corpus rather than of
the rule, so re-run it whenever the artifact grows. It is checked in rather than
attached to the issue because the version attached to #184 rotted invisibly: it calls
`query._ranked_deck_slice`, which this ticket deleted, and it matches `HAS_ARCHETYPE`
without `{isPrimary: true}`, the bug that moved two of the four constants. In `scripts/`
it imports the shipped null and the shipped constants, so the next such change breaks it
loudly instead of leaving a plausible harness on a closed issue.

## The luck count is part of the answer, not a caveat on it

Screening every rare card of every archetype is tens of thousands of chances for
coincidence (36,844 archetype-card pairs before the two rarity bounds), so a bar of
1-in-100 still lets cards through: at the chosen constants **7.0 of the 20 are expected
by chance alone**, and nothing distinguishes which. The bar is a dial for list length,
not a filter for truth, which is why the sweep optimised genuine finds rather than the
ratio, at a 20% cut and a 15% ceiling:

| min decks | max p | cards kept | expected by luck | ratio |
|---|---|---|---|---|
| 5 | 0.05 | 62 | 53.3 | 1.16x |
| 6 | 0.05 | 48 | 40.8 | 1.18x |
| 6 | 0.01 | **20** | **7.0** | **2.84x** |
| 6 | 0.005 | 14 | 3.5 | 4.00x |
| 6 | 0.001 | 5 | 0.7 | 7.33x |
| 8 | 0.01 | 17 | 5.5 | 3.11x |

Tightening the bar buys a cleaner list and a shorter one at almost exactly the rate that
leaves genuine finds flat, which is the shape of a threshold that is sorting by evidence
rather than finding a natural break.

So the count rides on the answer (`Subgraph.expected_by_luck`) rather than being left to
the reader, and the oracle grades it. It is summed over every card the rule *screened*,
not over the ones it kept: the rejects are most of the evidence about how often this bar
is cleared by accident. And it is each card's own chance of clearing, not the bar itself,
because a card in six decks has seven possible outcomes and its smallest reachable tail
(0.0008) sits well under the bar, while a card in twenty has finer steps.

That number is load-bearing rather than decorative, because there is no validation route
behind it (below). Without it this design reproduces #176's defect at a smaller scale.

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

## The field-size question: leave it, measured

`placementNorm = (placement - 1) / (fieldSize - 1)`, so winning a 20-player local and
winning a 200-player major both score 0 and the top cut cannot tell them apart. The tilt
is real: the median field behind a ranked deck is 70 and behind a top-quarter deck is
53, and 22.3% of top-quarter decks come from events under 32 players against 16.4% of
all decks. The race solves this with a majors cut (`MAJOR_FIELD_SIZE = 64`, ADR 0017),
and #176 confirmed the gem population is deliberately all events.

Measured at the chosen constants, both ways of applying a majors floor:

| | found | expected by luck | genuine | nodes |
|---|---|---|---|---|
| all events (shipped) | 20 | 7.0 | **13.0** | 102 |
| top cut restricted to majors | 8 | 6.3 | 1.7 | 37 |
| whole pool restricted to majors | 7 | 6.6 | 0.4 | 38 |

**Leave it.** The luck expectation barely moves while genuine finds all but vanish: the
floor cuts signal and noise in the same proportion, so it is a loss of power bought for
nothing. A smaller top cut of the same events is still a top cut, and the hypergeometric
null is exact whatever decks the cut is drawn from, so the tilt shows up as a cut that
is easier to land in rather than as a wrong probability.

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
crossover where the ceiling falls under the floor, recomputed at 40 from the new
constants, and an archetype under it still has no answer rather than an empty one.

**Loses.** The performance bar and its absolute reasoning are gone. The refusal is now
*silent*: with no dropdown there is no user to refuse, so a small archetype is skipped
inside the query and `SliceTooSmall` is deleted rather than caught. And ADR 0012's
refusal of the unfiltered view is **reversed**: it refused because the whole-meta view
dragged in 308 decks over the 250-node limit, and at 20 gems drawing only their top-cut
decks it is 102 nodes. That is a measurement that changed, not a rule that was
overridden.

## One unfiltered picture, cut to the node budget

The archetype dropdown is gone. The tab draws `Archetype -> Card <- Deck` for every gem
in the format at once, decks linking out to Moxfield as everywhere else, and it draws at
build time rather than behind a Draw button, because a button with no choices in front
of it is a step that asks the reader for nothing.

Only each gem's **top-cut** decks are drawn. The decks outside the cut are counted (they
are the card's rarity) and not drawn: they are not what the claim rests on, and drawing
them would put the reader's eye on the decks the rule discounted.

And of those, only each gem's best `MAX_GEM_DECKS` (5). The deck layer is where this
view collapses, and not for want of physics tuning: an archetype's best decks nearly all
run nearly all of its gems, so the deck nodes share a neighbourhood, a force-directed
layout has no information to separate them by, and they settle on one another with their
labels on top. Drawing every top-cut deck put **64 of 75 deck nodes in a structural tie**
with some other deck, and gave Initiative alone 41 edges between 6 cards and 10 decks, a
bipartite core at 68% of complete. Nothing tuneable fixes that. `avoidOverlap`, spring
length and gravity can inflate the blob but cannot separate nodes the graph gives no
reason to separate. Drawing five of each gem's best takes the picture to 82 nodes, 127
edges and 44 ties, and Initiative to 7 decks.

What is sampled is **decks, not edges**. A deck is drawn because it is among some gem's
best five, and once drawn it is wired to every gem of its archetype it runs, including
one whose own best five it missed. Otherwise the picture would show a deck that visibly
does not run a card it runs, and the overlaps between gems are precisely what the
picture says that the table cannot. This costs no node: every drawn deck is already
inside its archetype's cut, so a gem it runs holds it by construction. Three of the
twenty gems therefore draw six or seven decks rather than five.

The screen counts every deck; only the drawing is sampled. `top_decks` on the card node,
and so the table's **In top 20%** column, stays the true count, and the FAQ says plainly
that the picture draws a few of each. A reader who counts deck nodes and compares them
with the column would otherwise find a discrepancy nothing on the page explains.

Deck nodes are labelled `pilot · finish` ("Rob L · 1st") rather than by `d.name`, which
everywhere else in the app is the deck's whole Moxfield title. Seventy-five decks around
twenty cards at forty characters each drew an unreadable mat of overlapping text; the
picture's job is to show *which* good decks run a card, and who played it and how they
finished is that in four words. The title stays one click away on Moxfield, which is
where a reader who wants the list is going anyway.

A card that is a gem in two archetypes is **two nodes** (`card:{tag}:{canon}`), because
those are two findings resting on two sets of decks and one merged node could carry only
one archetype's numbers. No card on the shipped list of 20 is a gem twice, but the rule
screens each archetype independently and nothing stops one from being.

`MAX_GEM_LUCK` is a threshold, not a length, so it admits more cards from more decks on
every ingest and a fixed bar would eventually breach the canvas. The drawn list is
therefore the **strongest prefix** that fits `MAX_GEM_NODES`, and the luck count is then
read at the odds of the weakest drawn gem rather than at the bar, so it describes the
list on screen. Taken as a prefix rather than packed with whatever else would fit,
because the picture has to stay a set the reader can name. Today it does not bind: 82
nodes against 250.

## Consequences

`Node` gains `top_decks` and `gem_luck` and loses `gem_prob`; `mean_norm` goes with it,
since no query set it once the gem view stopped. `Subgraph` gains `expected_by_luck`,
`None` on every other query. `HiddenGems` loses its `archetype` parameter, which
invalidates the two gem cases in the golden oracle: they became one, and the oracle was
**recaptured with `--force`** in its own commit, per `docs/development.md`. Every
unrelated case came back identical row for row. `gem_archetypes` falls from 51 entries
to 32: `MIN_GEM_SLICE` dropped from 50 to 40, which admits more archetypes, and counting
primary tags alone shrinks every archetype, which removes more.

The gem view is now order-exact in the oracle rather than order-insensitive: it ranks its
whole answer before drawing any of it, so an order that moved would be an answer that
moved.

The whole hunt is four queries and about 0.5s on the built graph, run once at app start.
No stored graph property changes, so no rebuild.
