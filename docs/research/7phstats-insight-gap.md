# The insight gap against 7phstats.com

Research for issue #139. What questions about this format can our graph answer that 7phstats.com
does not, or does not answer honestly, and which of those are worth building.

## What this rests on

Two evidence bases, both read rather than assumed.

Theirs: the shipped 7phstats bundle (404 KB minified, beautified to 25,147 lines) plus the six
static JSON files the site serves (`decks.json` 4592 records, `cards_index.json` 4995 cards,
`archetypes.json` 124 records, `events.json` 107 records, `points_updates.json`,
`recommendations.json`). Every route was read off the route table and its computations read out of
the component bodies, not inferred from the rendered page. All twelve routes are covered.

Ours: the built graph at `data/graph`, measured 2026-07-25. 108 events (104 Tournament, 4 Teams),
4591 decks, 1083 pilots, 4995 cards, 126 archetypes of which 124 are ever primary, 338,080
`CONTAINS` edges, 84 cards with `points > 0`, 98 reserved cards. Decks per year 192 / 941 / 2095 /
1363 for 2023 to 2026, events per year 8 / 17 / 51 / 32. Every candidate below was run against that
artifact and the numbers quoted are measured, not estimated.

**The source data files we ingest are the same files 7phstats serves.** No candidate here rests on
data they lack. The difference is entirely graph shape and presentation: they hold the same
`eventSize` we recover algebraically, the same `reserved` flag we never read, the same `points`
value nothing on their site compares against anything. Where a candidate is genuinely unbuildable
it is unbuildable for both of us, and it sits below the cut line with the missing field named.

### Spot-checks against their numbers

Four of our numbers were checked against theirs. One agreed exactly, one disagreed for a reason that
turns out to be a gap in our ingestion, and two disagreed by definition. Each disagreement is a
finding rather than a discrepancy to reconcile away.

**The 8-point rule agrees exactly.** Decks running no reserved-list card, derived from
`Card.reserved` over both boards, come to 1137. Their `is8pt` flag is also 1137, and the two sets
are identical with no deck on either side alone. The accessibility bonus needs no extra ingestion,
which is what makes candidate 8 cheap.

**Deck point totals disagree on 9 percent of decks, and we are the ones who are wrong.** Their
`cards_index.json` carries two point values per card, `points` and `pointsCompanion`. They differ for
exactly two cards: Lurrus of the Dream-Den and Lutri, the Spellchaser, each 0 points in the deck and
**3 points as a companion**. We ingest `points` only, so `Card.points` is the default context and
every companion deck's total is 3 light. That single field accounts for almost all of the gap:
all-board agreement with their `pointsToday` rises from 91.00 to 98.54 percent once the surcharge is
applied. In our graph Lutri sits in 306 sideboards and Lurrus in 166 (plus 152 main-boards, where 0
is correct), and 622 decks run one of them. Any candidate about how a pilot spends seven points is
wrong for roughly a seventh of the corpus until `pointsCompanion` is ingested.

**Archetype counts disagree for 118 of 124 archetypes, by definition, not by error.** Their
`archetypes.json` `count` and `metaPctRecent` are computed over a rolling recent window, not the full
archive: the denominator implied by `count / metaPctRecent` is 942 decks, and a 120-day window is the
best fit (86 of 124 counts match exactly). Ours are all-time. So 4C Control is 8 decks to them and 36
to us, Gruul 1 and 28, Orzhov Taxes 16 and 50, and `avgPlacementNorm` differs beyond 0.005 for 97 of
124. Neither side is wrong, and their chart titles do say "rolling 4 months". The finding is that a
reader moving between the two sites gets two different numbers for the same archetype with nothing on
either surface reconciling them, which is the same defect the weak-answers section records inside
their own hero row.

**We hold one event that does not exist.** Their `events.json` has 107 events and our graph has 108.
The extra one is named `nan`. One record in `decks.json` (`FZP9B6alz0OoUVVe74nHeA`, 38th of a
38-player event, registered 2024-03-24) carries `"event": "nan"` and `"eventId": null`, and we ingest
it faithfully into an `Event` node with a null `eventId` sitting in year 2024. It is also why our
NHC26 holds 203 decks against their 204. Every event-count denominator in this document is therefore
one too many, including candidate 1's floors and candidate 6's coverage; the effect is small but it
is a real defect and it has its own follow-up below.

The one-line finding: their surfaces have real breadth (a powerful filter console, live
recomputation on a date slider, rolling trend charts) and almost no evidence discipline (no sample
size on any archetype tile, a hidden five-deck floor putting five-deck archetypes at the top of a
leaderboard, deck-counting rather than event-counting throughout, an absolute top-8 cut pooled
across events of 1 to 306 players, and no methodology page anywhere), so the gap worth attacking is
not "more charts" but "the same questions answered with the denominator on screen".

## Candidate insights, ranked

Ranked by reader value per unit of work. Eight above the line, sixteen kept below it. Every
candidate carries its reader question, its answer shape, the entities and edges it reads, what
7phstats does with it, its measured sample-size risk, and a rough size.

Two conventions apply throughout and are stated once. `Deck.placementNorm` is `(placement - 1) /
(eventSize - 1)`, so **lower is better**; the measured corpus baseline is 0.484 over 4539 ranked
decks, not 0.5, and any chart drawn on a higher-is-better axis must flip explicitly or it renders
upside down. And in a **grouped** projection the Ladybug engine silently corrupts any non-DISTINCT
aggregate that sits *after* a `count(DISTINCT ...)`: `avg()` comes back NaN and `count()` comes back
0, with no error raised. It is projection **order**, not the combination, and the number of groups is
irrelevant (one group returns None, four return NaN). `RETURN y.year, avg(x), count(DISTINCT e)` is
correct where `RETURN y.year, count(DISTINCT e), avg(x)` is not; two DISTINCT counts are fine in
either order, and an ungrouped aggregate is fine. Put the DISTINCT count last rather than splitting
the query.

### Above the line

#### 1. Archetype performance leaderboard, floored on events not decks

**Reader question.** Which decks are actually winning in this format, and how many separate
tournaments is that based on?

**Answer shape.** A ranked dot chart of archetypes by mean finish on the house higher-is-better
score, each row labelled with its distinct event count and its ranked deck count, the qualifying
floor stated on the surface, and the archetypes that missed it listed by name.

**Reads.** `Deck`, `Archetype`, `Event`. Edges `HAS_ARCHETYPE {isPrimary: true}`, `PLAYED_AT`.
Properties `Deck.placementNorm`, `Event.eventType`.

**Their version.** Covered badly. `/stats` panel "Top archetypes (by avg placement)" applies an
invisible `count >= 5` floor over decks, so the unfiltered number one is Red Esper at 5 decks and
number two Energy at 6. "Perf" is never defined on that route, and the site has no methodology page
at all. The same panel hard-filters `eventType === "Tournament"`, silently dropping 223 decks that
the overview pills beside it still count.

**Sample size.** Reproduced on our graph: unfloored, the top three are Red Esper (5 decks, 4
events, 0.914), Energy (5 decks, 4 events) and Delirium (6 decks, 5 events). A floor of 10 distinct
events keeps 70 of 124 ever-primary archetypes and excludes 54, which must be listed rather than
silently dropped; qualifying counts are 105 at 5 events, 70 at 10, 42 at 20. The median qualifying
row rests on 23 events and 38 decks, the thinnest admitted rows (Merfolk, Jeskai Breach) on exactly
10 events, so per-row event N goes on every row. Floored, the answer inverts: Storm 0.685 over 49
events, Bus Stop Grixis 0.653 over 19, Breachbond 0.615 over 25, Walks 0.579 over 72. The 4 Teams
events (223 decks) fold several pilots onto one placement and must be labelled or excluded.

**Size.** M.

This is top because it is the question every reader of a metagame site arrives with, it is the one
they answer publicly and wrongly, we do not answer it at all today (no format-level archetype
performance surface exists in our app), and the fix is our own house rule applied once: count
events, print the floor, name what it excluded.

#### 2. What got pointed, and what took its place

**Reader question.** Which cards fell out of favour between last year and this year, and which ones
moved in to replace them?

**Answer shape.** Two short ranked lists side by side, biggest drops and biggest gains in play
rate, each row carrying last year's rate, this year's rate, the change in percentage points, and
the card's current points cost and reserved-list status.

**Reads.** `Card`, `Deck`, `Event`, `Year`. Edges `CONTAINS` (both boards), `PLAYED_AT`, `IN_YEAR`.
Properties `Card.points`, `Card.reserved`.

**Their version.** Covered badly. `/card/:canon` shows one card's meta share over time, one card at
a time, and only if you already know which card to look up. There is no cross-card movement ranking
anywhere on the site, and the `/search` card table is raw inclusion count with no time dimension,
so its default is permanently the fetchland list. Our own Cards tab has the identical
one-card-at-a-time limit and caps the pair view at two lines.

**Sample size.** Rates are exact shares over known year denominators (2095 decks in 2025, 1363 in
2026), so no floor is needed on the rate itself. Eligibility floor is 100 decks in 2025, which
keeps 326 cards; below that a 10-deck swing reads as a 0.5pp move and the ranking fills with noise.
Measured fallers: Tamiyo 663 to 86 decks (31.6 to 6.3 percent, -25.3pp, 2 points), Mental Misstep
-24.8pp, Barrowgoyf -20.5pp, Ragavan -16.2pp. Risers: Wan Shi Tong +21.3pp, Quantum Riddler
+16.8pp, Consult the Star Charts +12.6pp. Two caveats belong on the surface, not in a footnote:
2026 is roughly half a year, so a card that arrived in June is understated by its own denominator,
and the year denominator is decks, so the largest single event supplies 14.5 percent of 2025 and
15.0 percent of 2026. The claim to print is that six of the eight biggest fallers carry points and
seven of the eight biggest risers carry none, not that fallers and pointed cards are the same set:
Troll of Khazad-dum (-12.3pp) and Molten Collapse (-9.6pp) are unpointed fallers and The One Ring
(+10.6pp) is a pointed riser.

**Size.** S.

#### 3. How much of the archive is illegal under today's points list

**Reader question.** If I copy a list that won a tournament last year, does it still fit inside the
points limit today?

**Answer shape.** A bar per registration month showing the share of that month's decks that cost
more than their own applicable limit (7 points, or 8 if the deck runs no reserved-list card), with
the deck count on every bar, plus a per-year cost distribution drawn against the 7 and 8 point
ceilings. The step where the number falls off a cliff is the last points change, visible without
being told.

**Reads.** `Deck`, `Card`, `Event`, `Year`. Edges `CONTAINS` (**both boards** for the cost sum and
for the reserved test), `PLAYED_AT`, `IN_YEAR`. Properties `Card.points`, `Card.reserved`,
`Deck.createdAt`.

The board scope was settled by measurement against their `pointsToday`, because it was initially
written here as main-board-only and that is wrong. Agreement rates across 4591 decks: all-board
91.00 percent, main-only 88.15 percent, all-board plus the companion surcharge below 98.54 percent,
main-only plus that surcharge 95.75 percent. Sideboard cards count toward the points total, so the
cost sum reads both boards.

**Their version.** Not covered, and it is the thing they most conspicuously do not compute. Their
deck records carry both `pointsAtCreation` and `pointsToday` and no route compares them.
`/deck/:deckId` prints the two integers side by side with no delta and no indication of which card
moved. `/event/:eventId` has a column literally headed "Total points spent" that multiplies deck
count by today's points. Neither `Card.points` nor `Card.reserved` is read by any surface we ship.

**Sample size.** A census, not a sample: all 4591 decks, all 84 pointed cards, singleton format so
the per-deck sum is exact arithmetic. 65.2 percent of all decks cost more than their own limit
today, which is the number the caption should lead with. Monthly N swings from 7 (2023-01) to 536
(2025-11); months under 20 decks are drawn hollow, because below that one deck moves the bar by 5
percentage points or more. The cliff is real and is not one card: 2025-11 is 429 of 536 (80.0
percent) and 2025-12 is 36 of 134 (26.9 percent), with Barrowgoyf (289 November decks), Deadpool
(224), Tamiyo (185) and Ragavan (163) vanishing while Force of Will and Mental Misstep persist. Two
honesty requirements. Present a cost distribution, never a legality verdict, because the 8-point
allowance is derived from "runs no reserved card" and 243 of the 469 decks pricing at exactly 8 in
2026 do run one. And print the method's own error bar: 22.8 percent of 2026 decks already exceed
their own limit under today's list, so part of the historical 82.6 percent (2023 to 2025) is
reconstruction noise, not drift. An event-weighted monthly variant does not work: events per month
are 1 to 3 for everything before September 2025 and four months have none at all.

**Size.** M.

#### 4. What actually plays with this card, and on how much evidence

**Reader question.** If I am building around this card, which cards genuinely belong with it, rather
than the ones everybody in the format plays anyway?

**Answer shape.** One card picked, its partners ranked by association strength rather than raw rate,
every row carrying the number of decks holding both cards, and a stated support floor. When nothing
clears the floor the surface says so instead of drawing a package.

**Reads.** `Deck`, `Card`. Edge `CONTAINS`. Nothing else, which is why it is cheap.

**Their version.** Covered, and on the measure itself covered better than ours, which is the
uncomfortable half of this entry. Their `/recommendations` scores co-occurrence with PPMI
(`score: "weighted_cooccurrence_ppmi"` in the file header), and PPMI corrects for partner popularity
where a raw rate does not. On seed `underworld breach` theirs returns Brain Freeze, Intuition,
Sevinne's Reclamation and Lion's Eye Diamond, the actual combo package. Ours returns Brainstorm,
Ponder, Gitaxian Probe and three fetchlands, because a raw rate ranks by how common the partner is.
Our `drop_lands` toggle is a patch over the wrong metric rather than a fix.

What they omit is the floor. Their file holds 446,746 edges and gives every one of the 4995 cards
exactly 100 partners regardless of evidence: median edges per card equals the maximum, 100. Support
across those edges is 204,044 on a single deck (45.7 percent), 49,807 on two, 40,779 on three or
four, 41,678 on five to nine, and only 110,438 (24.7 percent) on ten or more. So more than half their
recommendation graph is one- or two-deck coincidence rendered at full confidence:
`Ally Encampment -> Earth King's Lieutenant` scores a perfect 1.0 at a 100 percent rate, on 2 decks.

**Sample size.** The floor is the feature, and it was prototyped rather than assumed. Normalised PMI
over deck co-occurrence with a 20-deck support floor, one query, 0.2 seconds for a seed including
process start, no precompute. Seed `underworld breach` (201 decks) yields 146 partners: Brain Freeze
0.975 on 197 decks, Intuition 0.955 on 186, Sevinne's Reclamation 0.831 on 190, Lion's Eye Diamond
0.779 on 195, then Serenity 0.636 on 60 and Seal of Cleansing, Seal of Fire and Rip Apart, the
sideboard hate package their fixed top-8 cuts off. Not one land appears, with no `drop_lands` toggle.
Seed `black lotus` (112 decks) returns the Storm package, Yawgmoth's Will 0.894 on 88 decks. The
degradation is the honest part: seed `abbot of keral keep` sits in 8 decks of 4591 and returns
**nothing** at a 20-deck floor, where the surface should say there is not enough evidence. Dropped to
a floor of 5 it returns a plausible mono-red shell on 5 to 7 decks, showable only with every row
labelled. nPMI rather than raw PPMI because it is bounded to [-1, 1], which gives the reader a scale.

We currently fail this test on our own surface. Our shipped co-occurrence view on that same 8-deck
card draws Lightning Bolt at 100 percent, then Chain Lightning, Grim Lavamancer, Monastery Swiftspear
and **Mountain** at 88 percent, with no sample size anywhere. So this is a repair to our own view
first and an overtake second.

**Size.** S.

This ranks high for its size: it is the smallest change on the list, it corrects a surface we
already ship that is currently misleading, and it is the one place where the honest version beats
both sites at once. It does not conflict with the "package as component" candidate below the line,
which answers a different question (which cards move as a set) and would build on this measure.
See also the "not worth chasing" note on their recommendation engine: the ranked list is what to
skip, the measure is what to take.

#### 5. Archetype skeleton and flex slots

**Reader question.** If I sit down to build Grixis, which cards are non-negotiable, which are the
real choices, and which of the mandatory ones are just what every deck in the format plays anyway?

**Answer shape.** One archetype picked, one scatter: each main-board card plotted as adoption
inside the archetype against how much more often it appears here than in the format at large. Four
regions named in the caption: core identity, format tax, contested flex slots, one-off tail.
Beside it a band count ("33 cards in 90 to 100 percent of lists, 27 in 50 to 90, 13 in 20 to 50,
258 under 5") and the honesty line "386 decks, 155 pilots, 85 events".

**Reads.** `Deck`, `Card`, `Archetype`, `Pilot`, `Event`. Edges `HAS_ARCHETYPE {isPrimary: true}`,
`CONTAINS {board: 'Main'}`, `PILOTED_BY`, `PLAYED_AT`.

**Their version.** Not covered. Nothing on 7phstats aggregates cards within an archetype:
`/archetype/:tag` lists individual decks, and `/search`'s card table aggregates a filtered slice by
raw inclusion count with no format baseline, so its top rows are always the fetchland list. Neither
can tell a core card from a tax card. Our Hidden gems view is capped at 10 percent adoption by
`MAX_GEM_SHARE`, so it can say nothing about a card in 278 of 386 Grixis decks.

**Sample size.** Draw only for the 31 archetypes clearing both 40 primary decks and 20 distinct
pilots. The deck floor alone admits 34, but that includes Tokens (40 decks from 10 pilots) and
Breachbond (58 from 16), where the skeleton is a handful of people's repeated lists. Print decks,
pilots and events on every skeleton. Floor the plotted card set at 10 decks format-wide, because
below that the lift ratio is a small-denominator artefact: 50 of Grixis' 359 main-board cards fall
under it. The regions separate cleanly in the measurement: the tax sits at lift 1.06 to 1.86
(Mental Misstep, Polluted Delta, Brainstorm, Ponder) while the core sits at 4.3 to 6.9 (Kaito,
Cori-Steel Cutter, Psychic Frog, Molten Collapse), with a populated middle (Tasigur 0.39 adoption
at 5.19 lift, Sheoldred 0.46 at 2.60). One correction the caption must make: a chunk of the
high-lift core is colour-locked mana (Watery Grave 4.06, Gloomlake Verge 5.74, Darkslick Shores
5.32), which is lift trivially implied by Grixis being a UBR deck rather than a build choice, and
must be separated from the real identity cards.

**Size.** M.

#### 6. Event coverage: whole field or just the top tables

**Reader question.** For each tournament, do we have every deck that showed up, or only the ones
that made the cut?

**Answer shape.** One row per event, ordered by year, showing lists we hold against the recovered
field size as a filled coverage bar, with the events under half coverage called out by name and a
plain sentence saying what that does to every other number in the app.

**Reads.** `Event`, `Deck`, `Year`. Edges `PLAYED_AT`, `IN_YEAR`. Properties `Deck.placement`,
`Deck.placementNorm`, `Event.eventType`.

**Their version.** Covered badly. Their `/events` cards show a "N lists" pill beside a "players"
pill that is null for 71 of 107 events and falls back to a pilot count equal to the deck count, so
an event that had 81 players reads "8 lists, 8 pilots". Their placement badge takes the field size
as a prop at all 9 call sites and never reads it, so 5th of 19 and 5th of 306 render identically.
Their conversion factor is an absolute `placement <= 8` cut computed over exactly these events,
which is why 23 of 108 events return 1.000 for every card and every archetype.

**Sample size.** A census over all 108 events, so no floor. The field size is recoverable
algebraically as `1 + (placement - 1) / placementNorm` for 105 of 108 events, and it is perfectly
self-consistent: zero events produced conflicting estimates across their own decks. Three events
have no recoverable field (Area52IQ, DeckaDiceIQ, Pats Birthday Brawl) and must show as unknown,
not as zero. The split is stark: 72 events at 95 percent coverage or better, 19 under 50 percent,
worst being SSWam 7 lists of a field of 88, ANZSS10 8 of 81, ChromaticIQ 7 of 39, MazeWATrop 8 of
40. Nine events recover a field smaller than the lists we hold and need their own flagged category:
the 4 Teams events, where the recovery counts teams and not players (TMCTeams25 117 lists against a
field of 39), plus 5 ordinary Tournaments where the source field size is simply wrong (DLIQ 7
against 5, ProBox 7 against 5, #BBO1 7 against 5, GGWARL 8 against 5, GGWAD 28 against 16). One
event has the literal name `nan` with a null eventId and 1 deck and must be excluded as an
artefact, not shipped as the worst-covered event. That leaves 95 events with a clean plottable
figure.

**Size.** M.

Build note: another session has uncommitted work in `build.py` (issue #140) adding `Event.fieldSize`
and `Event.fieldImputed` as real node properties with a `MIN_CUT_FIELD = 24` correction rule. That
is not in HEAD and not in the shipped artifact (`TABLE_INFO('Event')` returns only event, eventId,
eventType). Once it lands this candidate should read `fieldSize` directly, and the five impossible
Tournament rows above are exactly what its correction rules fix.

#### 7. What a third colour costs, and whether it pays

**Reader question.** How much of a deck's cost is just the mana, and am I better off with a tight
two-colour deck or should I be splashing a third and fourth colour?

**Answer shape.** Two linked readouts on one card. A stacked bar per colour count (one to five)
splitting median deck cost into lands and everything else, so the manabase share is visible. On the
same x axis, the finish per colour count with deck and event counts labelled per point, drawn on
the house 0 to 1 score with the measured 0.484 baseline as the reference line.

**Reads.** `Deck`, `Card`, `CardType`, `Colour`, `Event`, `Year`. Edges `CONTAINS {board: 'Main'}`
(to sum `Card.priceUsd`), `HAS_TYPE` (to split `Lands` from the rest), `DECK_COLOUR` (degree gives
the colour count), `PLAYED_AT`, `IN_YEAR`.

**Their version.** Not covered. Colour appears on every one of their routes only as an input or a
decoration: WUBRG pip toggles in the filter panels, mana pips on deck rows, and "Colour" as a
group-by in the custom chart builder. No route reports colour count against anything and none ever
splits a deck's price into manabase and spells. It is also entirely untouched on our side:
`DECK_COLOUR`, `CARD_COLOUR`, `HAS_TYPE`, the `Colour` and `CardType` nodes and `Deck.colourIdentity`
are traversed by nothing we ship.

**Sample size.** 2026: 1352 decks with at least one `DECK_COLOUR` edge over 32 events. The price
half is the load-bearing claim and it is enormous: median main-board price runs $448, $659, $2,382,
$4,064, $4,444 across one to five colours, and the land share of that price runs 33, 46, 83, 77, 58
percent, so the manabase is the dominant line item the moment a deck goes past two colours.
Overall land share is a median 72 percent of main-board price (quartiles 41 and 84). The finish
half survives a four-year robustness check the original candidate never ran: the one-to-four
ordering repeats in every year (2023 0.475/0.466/0.527/0.574, 2024 0.415/0.472/0.515/0.544, 2025
0.492/0.482/0.516/0.548, 2026 0.472/0.513/0.520/0.569, in flipped higher-is-better form). Only the
five-colour bucket is erratic (0.661, 0.507, 0.555, 0.420) and thin (43 decks over 14 events in
2026), and it must carry its event count and a note rather than be dropped, because a colour-count
axis missing its top rung invites the reader to extrapolate. Read colour count from `DECK_COLOUR`
degree, not by parsing `Deck.colourIdentity`: 30 decks carry the literal string `unknown` with no
`DECK_COLOUR` edge, they post the best mean of the lot (0.409 raw), and a naive count would put a
data hole at the top of the chart. The confound to print: colour count is largely archetype
identity, so this reads "the decks people take to four colours tend to place higher", never a
return on adding a colour. It holds inside 5 of 7 macros and reverses only for prison.

**Size.** M.

#### 8. The eight-point trade: what giving up the reserved list actually costs

**Reader question.** If I build without any reserved-list cards to get the extra point, how much
money do I save and how much worse do I finish?

**Answer shape.** A two-column comparison, reserved-free against reserved-running, each column
showing median deck price, mean finish, deck count and distinct event count, with a price-controlled
panel underneath and the archetype mix as the lead rather than a footnote.

**Reads.** `Deck`, `Card`, `Event`, `Year`, `Archetype`. Edges `CONTAINS {board: 'Main'}` (to read
`Card.reserved` and `Card.priceUsd`), `PLAYED_AT`, `IN_YEAR`, `HAS_ARCHETYPE {isPrimary: true}`,
plus `HAS_MACRO` for the confound check.

**Their version.** Covered badly. They store `is8pt` as a flag and expose it as a "Point legality:
All / 7pt / 8pt" filter on `/stats`, `/search` and `/downloads`, plus a three-row "Top 8pt" panel on
`/event/:eventId`. It is a filter and a flavour panel, never a comparison. No route puts 7-point and
8-point decks side by side against a result or a price, and `/deck/:deckId` shows "RL n" as
unexpanded jargon without displaying `is8pt` at all.

**Sample size.** Both arms are fat and stable across four years. 2026: 986 reserved-running decks
over 32 events at 0.546 against 377 reserved-free over 31 events at 0.454, with median main-board
prices of $3,020 and $513. The gap holds in every year (reserved-free 0.411/0.409/0.461/0.454
against reserved-running 0.551/0.534/0.536/0.546) and inside all seven macros. The finding that must
reshape the view: 375 of the 377 reserved-free decks cost under $1,000, so "reserved-free versus
reserved-running" and "cheap versus expensive" are almost the same partition, and the reader cannot
be allowed to read the finish gap as the cost of taking the eighth point. Controlling for price
halves it: inside the under-$1,000 band, reserved-free 0.454 (n=375, 31 events) against
reserved-running 0.499 (n=42, 21 events). That 42-deck control arm is the thinnest number in the
view and its N must be printed beside it; I keep it rather than drop it because it is the single
number that stops the wrong conclusion. The honest lead is the archetype mix: Blue Tron is 37 of 37
reserved-free, Blue Moon 64 of 68, Flood Moon 33 of 37, and those three alone are 134 of the 377, so
the trade is not a knob a deck turns, it is an archetype the deck already is. Archetypes under 20
decks in 2026 get no row (90 of the 111 present, covering 504 decks).

**Size.** M.

### Below the line: buildable, lower priority

Kept, not deleted. Each is verified and shippable; they lost on value per unit of work, not on
evidence. Note that **Event circuits** is the only genuinely traversal-native candidate in the whole
set, which is the open direction the research log flagged on 2026-07-15; it sits here on size, not
on merit.

#### Event circuits: which tournaments share a crowd

- **Reader question**: are there separate scenes in this format, and which events do the same people keep showing up to?
- **Answer shape**: an event-to-event map, edges where entrant sets overlap, events coloured by circuit, with a per-circuit panel of which archetypes it over- and under-plays against the format.
- **Reads**: `Pilot`, `Deck`, `Event`, `Archetype`; `PILOTED_BY`, `PLAYED_AT`, `HAS_ARCHETYPE {isPrimary: true}`.
- **Their version**: not covered. `/events` is a flat searchable list in file order with no relationship between events at all, and no route on their site draws any pilot-to-pilot or event-to-event structure.
- **Sample size**: raw shared-pilot counts are a hairball (2218 edges, modularity 0.204); Jaccard >= 0.15 gives 90 attached events, 559 edges, modularity 0.384, and clusters that are name-coherent with no name parsing. 18 events fall below the floor and must be drawn unattached, not dropped; the `nan` event must be excluded. The promised "top three archetypes per circuit" panel is degenerate (the three biggest circuits all return Grixis, Jund, Walks) and must be rebuilt as over/under-representation: PoG over-indexes Storm 1.91x, the Canberra block under-indexes Flood Moon 0.56x. 55 low-confidence pilots (505 decks) may each be two humans merged, which inflates overlap.
- **Size**: L.

#### Card packages that travel as a unit

- **Reader question**: which cards always come as a set rather than one at a time, and which decks are quietly running somebody else's package?
- **Answer shape**: a graph of packages, cards joined when each is in at least 75 percent of the other's decks, drawn as connected components with each unit's deck count and the archetypes it crosses.
- **Reads**: `Card`, `Deck`, `Archetype`, `Pilot`, `Event`; `CONTAINS {board: 'Main'}`, `HAS_ARCHETYPE {isPrimary: true}`, `PILOTED_BY`, `PLAYED_AT`.
- **Their version**: covered badly. `/recommendations` computes co-occurrence PPMI but renders a flat ranked list, so 10 of the top 15 results for their own sample deck are fetchlands and duals. Our Co-occurrence view has the same one-directional weakness and surfaces staples unless the reader manually ticks "filter out lands".
- **Sample size**: ranked as a list the 0.75 mutual rule is a disaster (the top 20 pairs are the fetchland clique); drawn as components it self-resolves, 353 qualifying pairs into 46 components of which exactly one is the format tax. Floor at 15 decks and 10 distinct pilots: the thinnest surviving components still rest on 10 to 14 pilots over 9 to 16 events, but the 10-card Hardened Scales component is 21 decks from only 4 pilots and must be withheld or drawn as thin. State the all-members deck count separately from the pairwise counts: the Tron and Eldrazi component is 67 decks with every member against pairwise counts near 250.
- **Size**: L.

#### Which archetype names are actually the same deck

- **Reader question**: half these archetype names sound like variants of each other, so which pairs are genuinely one deck under two labels?
- **Answer shape**: a similarity map over the archetypes with enough decks, each pair scored on shared core cards, each edge inspectable down to the shared and the separating cards.
- **Reads**: `Archetype`, `Deck`, `Card`, `Pilot`; `HAS_ARCHETYPE {isPrimary: true}`, `CONTAINS {board: 'Main'}`, `PILOTED_BY`.
- **Their version**: not covered, and they have the opposite problem: their three archetype surfaces disagree by construction (primary tag on `/archetypes`, all tags on `/archetype/:tag` and `/stats`), so the same archetype shows three deck counts across three pages. Our Meta tab draws 126 archetypes as independent lines and never says two of them are the same deck, which is why the default cut is a 15-line rainbow.
- **Sample size**: gate at 40 primary decks, 20 pilots and a 50-percent core of at least 35 cards, which admits 29 archetypes and leaves 93 off the map. Rogue must be excluded outright (13-card core, only 1 card of it not a format staple). Top pairs: Breach and Lurrus Breach 0.67 over 41 shared cards, Bus Stop Grixis and Grixis 0.60, Boros and Boros Moonshine 0.53, with 28 of 561 pairs at a genuine zero. The staple-floor worry is measurable and small: stripping the 20 cards above 40 percent format adoption lowers every score by 0.10 to 0.12 and leaves the top five unchanged, so report the stripped score alongside the raw one.
- **Size**: M.

#### Does this archetype have two builds inside it

- **Reader question**: when people say they are on Jund, are they all playing the same deck, or are there two versions hiding behind one name?
- **Answer shape**: the card pairs inside an archetype that never appear together, drawn as either-or choices with each camp's signature cards, deck count and colour identity, and a plain "this archetype is one deck" where nothing clears the bar.
- **Reads**: `Deck`, `Card`, `Archetype`, `Event`, `Year`, `Pilot`; `HAS_ARCHETYPE {isPrimary: true}`, `CONTAINS {board: 'Main'}`, `PLAYED_AT`, `IN_YEAR`, `PILOTED_BY`.
- **Their version**: not covered anywhere on either site. `/archetype/:tag` treats an archetype as one homogeneous population and averages a performance number over it, so a two-build archetype with one strong half renders as one mediocre line.
- **Sample size**: gate on the **year-restricted** expected co-occurrence, not the raw one, and require expected 8 or more. That gate is what makes the view honest: Jund's 37 raw exclusion pairs fall to 12 once each pair is scored only over the years both cards were present, so without it two thirds of the "build split" is card rotation. Print observed 0 against the year-restricted expectation and the shared years ("expected 16.9, observed 0, 2024 to 2026, 270 decks"). 19 of 31 floor-clearing archetypes yield at least one surviving pair, so the one-deck fallback fires for 12, which is a useful answer rather than an empty view. Do not say "two builds" unless the exclusion graph is one component: Jund's is three.
- **Size**: L.

#### What is this deck actually built like

- **Reader question**: this list is filed under one archetype name, but which decks in the format does its card base really resemble?
- **Answer shape**: one deck picked, a short ranked bar of how much of each archetype's core it contains, its own label highlighted, with the cards missing from its own core and borrowed from the runner-up listed by name.
- **Reads**: `Deck`, `Card`, `Archetype`, `Pilot`; `CONTAINS {board: 'Main'}`, `HAS_ARCHETYPE {isPrimary: true}`, `PILOTED_BY`.
- **Their version**: covered badly. `/deck/:deckId` shows the list, the pills and a mana curve, and their own design notes admit it offers no diff against the archetype's typical list. We have no deck subject view at all: our four subject views are pilot, card and archetype, so a specific decklist is unreachable in our app today.
- **Sample size**: compare against 29 archetypes (40 decks, 20 pilots, 35-card core), not all 124, and say so on the surface. 3008 decks get a profile; only 64 of them (2.1 percent) have a better-fitting archetype by more than 15 points, so this must be built as a profile every deck can draw, not a misfit hunt. It is readable rather than flat: own-label fit median 0.83 against runner-up 0.57, median gap 0.26, and the gap widens to 0.31 when format staples are stripped, so the fit is not staple-driven.
- **Size**: M.

#### Inside one archetype, which staples separate the good finishes from the bad

- **Reader question**: everyone playing this deck runs roughly the same cards, so which of the flexible slots show up in the lists that finish well?
- **Answer shape**: cards ranked against that archetype's own average finish as a diverging bar, each row carrying how many of the archetype's decks run it and across how many events.
- **Reads**: `Deck`, `Archetype`, `Card`, `Event`, `Year`; `HAS_ARCHETYPE {isPrimary: true}`, `CONTAINS {board: 'Main'}`, `PLAYED_AT`, `IN_YEAR`, `Deck.placementNorm`.
- **Their version**: covered badly. Their `/card/:canon` "Perf" is computed over every deck in the archive that ever ran the card with no floor at all (1587 of 4995 cards appear in exactly one deck and still get a three-decimal Perf), and it returns a literal 0.000 both for "no scored decks" and for "finished last". Neither their card page nor their search table is sliced by archetype, so a card's number is really its archetypes' number. It extends rather than repeats our Hidden gems view, which is capped at 10 percent adoption.
- **Sample size**: floor at 30 decks and 10 distinct events for the card inside the chosen archetype, against that archetype's own baseline (Grixis 0.4425 over 386 ranked decks), which leaves 88 of 474 cards. Most archetypes must be refused by name: only 10 carry 100 or more ranked primary decks, and the qualifying-card count collapses past the top dozen (Jund 105, Walks 97, Lands 96, Grixis 88, down to Academy 4 and Tinker 3). It survives its own degeneracy test: the top rows are basics, but snow and regular basics coexist as competing choices in the same archetype (Island 284 decks 0.523 against Snow-Covered Island 107 at 0.636), so that is the finding rather than filler. Print per-card year skew, because the recency confound is card-specific: Gloomlake Verge sits 85 percent in 2026 and its 0.602 is substantially recency, while Snow-Covered Swamp peaks in 2025 and its 0.663 is not.
- **Size**: M.

#### The budget build: cheapest version of the deck I want to play

- **Reader question**: I want to play this archetype but I do not own duals, how cheap can I go before the results fall off?
- **Answer shape**: pooled within-archetype price terciles as the headline, with a per-archetype price strip and median underneath as the decoration.
- **Reads**: `Deck`, `Card`, `Archetype`, `Event`, `Year`; `CONTAINS {board: 'Main'}` to sum `Card.priceUsd`, `HAS_ARCHETYPE {isPrimary: true}`, `PLAYED_AT`, `IN_YEAR`.
- **Their version**: covered badly. `/archetypes` and `/search` both offer a "Perf/Price" sort ranking on `valuePerUsd`, a percentile-times-percentile composite never rendered on the row and defined only in a hover title on a different page, so the reader gets an order with no visible justification and no price distribution.
- **Sample size**: the presentation must be inverted from the obvious one. Pooled terciles cut inside each archetype are clean and monotone (cheap 0.499 over 277 decks and 29 events, middle 0.540, expensive 0.599 over 277 decks and 32 events, roughly 4 sigma, and the expensive third wins in 16 of 21 archetypes), and because the cut is within-archetype it is not an archetype-mix artefact. Per-archetype thirds rest on 6 to 38 decks over 4 to 17 events and cannot be ranked against each other: only Grixis, Lands, Blue Moon, Jund and Oracle clear 10 events per third. Several archetypes have no room to answer the question at all (Blue Tron spans $319 to $551, Walks $6,205 to $8,164) and the view must say "not much" where that is the truth.
- **Size**: M.

#### What the rest of your budget buys once you commit to a big card

- **Reader question**: if I spend most of my points on one expensive card, what do the decks that do that play with the rest, and what do they never get to play?
- **Answer shape**: a pointed card picked, one side the pointed cards that appear alongside it far above the field rate, the other the popular pointed cards that essentially never appear with it, every row carrying its deck count.
- **Reads**: `Deck`, `Card`, `Archetype`; `CONTAINS {board: 'Main'}` traversed twice to read `Card.points` on both ends, `HAS_ARCHETYPE {isPrimary: true}` for the archetype-capture disclosure only.
- **Their version**: not covered. `/recommendations` does co-occurrence over the whole archive but is explicitly point-blind: it prints "Pts 0" per card, offers a points filter, and keeps no running point total, so it never warns that a recommendation would break the budget. Our own co-occurrence view is equally point-blind, reading `CONTAINS` and `Card.type` only.
- **Sample size**: seed floor of 50 decks keeps 70 of the 84 pointed cards and kills the rows resting on single decks (Mox Sapphire has 18 decks and its top-lift row rests on 4). 50 rather than 100, because 100 drops Time Vault (54), Library of Alexandria (57) and Mox Ruby (71), exactly the commitment cards the question is about. Partner rows need their own 40-deck field floor or the lift denominator is unstable. Do not sell it as discovery: 200 of 203 Time Walk decks, 102 of 132 Thassa's Oracle decks and 83 of 112 Black Lotus decks are one primary archetype, so the honest framing is "here is the package that comes with this card". Point costs are today's values applied to a historical archive and the caption must say so.
- **Size**: M.

#### Your first tournament is your worst one

- **Reader question**: do people finish better the more events they have played, and how much worse is a debut than a regular's result?
- **Answer shape**: a step chart over how many events the pilot had already played when they registered this deck (1st through 6th+), with the deck count on every bar and the survivorship confound stated in one sentence underneath.
- **Reads**: `Pilot`, `Deck`, `Event`; `PILOTED_BY`, `PLAYED_AT`, `Deck.createdAt` to order a pilot's events, `Deck.placementNorm`.
- **Their version**: not covered. `/pilot/:pilot` shows one pilot's rolling line but never positions a result in that pilot's own career, and no route aggregates across pilots. Our own performance chart needs two qualifying years and only 238 of 1083 pilots can draw it; this is the format-level version for everybody, including the 476 one-event pilots ours can never show.
- **Sample size**: buckets are fat and no floor is needed (1065 / 597 / 425 / 333 / 269 / 1850 decks, standard errors 0.007 to 0.017, spread 0.165 raw). It survives an event-centred control the original candidate did not run: debut +0.091 above its own event's mean, 6th-plus -0.063 below. Three things go on the surface. Bucket 6+ is 1850 decks from only 223 pilots, so the pilot count belongs on that bar. The survivorship number belongs beside the chart with its own N (467 one-and-done pilots average 0.630 against 0.539 for the 598 who returned). And the reference line is the format mean 0.484, not a 0.5 coin flip, because `placementNorm` is a finishing rank with no match record behind it.
- **Size**: M.

#### What this pilot plays that nobody else on the deck plays

- **Reader question**: when this player brings this deck, what cards do they run that the rest of the field playing it does not?
- **Answer shape**: a two-sided list for one pilot-and-archetype pair, the cards they run far above the field rate and the field staples they refuse, every row reading both numerators and both denominators, with board as a control.
- **Reads**: `Pilot`, `Deck`, `Archetype`, `Card`; `PILOTED_BY`, `HAS_ARCHETYPE {isPrimary: true}`, `CONTAINS` with board in `{'Main', 'Side'}`.
- **Their version**: not covered. `/deck/:deckId` shows one list with no comparison (their own noted gap), and `/recommendations` produces a global staples list. Our Cards tab never conditions on a pilot and our pilot views exclude cards entirely, so nothing anywhere connects a pilot to their card choices.
- **Sample size**: 204 pilot-and-archetype cells qualify at 4 or more lists inside an archetype holding 30 or more, covering 162 pilots and 42 archetypes; a floor of 3 gives 335 cells and 231 pilots. Below the floor the view must refuse by name, because a one-list cell renders confident rows off a single registration ("1 of 1 against 6 of 385"). Worked example: William L on Lands, 31 lists against 163, runs Sakura-Tribe Scout 20/31 against 22/163 and plays zero Savannah and zero Swords to Plowshares where a third of the field does. Each of a pilot's lists is a separate event registration (measured: distinct events equals list count in every qualifying cell), but they are one builder iterating on one shell, so this describes how they build and never claims the choices win. Note the board values are capitalised `Main` and `Side`; filtering on `'main'` returns an empty table silently.
- **Size**: M.

#### Specialists versus deck-hoppers

- **Reader question**: do players who stick to one deck finish better than players who bring something different every time?
- **Answer shape**: a scatter of the format's regulars, concentration against mean finish, marker sized by events entered, with the two group means and the caveat in the caption.
- **Reads**: `Pilot`, `Deck`, `Archetype`, `Event`; `PILOTED_BY`, `HAS_ARCHETYPE {isPrimary: true}`, `PLAYED_AT`, `Deck.placementNorm`.
- **Their version**: not covered. Nothing on their site measures a pilot's breadth: `/pilot/:pilot` renders an uncapped row of archetype chips (27 for one pilot) with no summary and no comparison. Our own affinity graph lists "specialist is eyeballed, not measured" as its own weakness; this is that measurement.
- **Sample size**: ship at 5 events and 4 ranked lists (274 pilots), not 8 and 6 (160), because the effect is the same size at both floors and only the larger slice has the power to show it. The raw gap does not clear noise on its own (r = -0.129, permutation p = 0.116) and the caption must not claim it does. What is real is that it survives controlling for experience (partial r -0.164 at p = 0.045 on 160 pilots, -0.160 at p = 0.009 on 274, -0.163 at p = 0.099 on 99: same sign and size at every floor) and that it is not explained by specialists picking stronger decks (concentration correlates -0.030, p = 0.61, with the finish expected from a pilot's archetype mix). It is a weak effect, roughly 2.5 percent of the variance, and only worth shipping if the surface says so in those words. 476 of 1083 pilots have exactly one deck and can never appear.
- **Size**: M.

#### Who brought it first, and how it spread

- **Reader question**: when a new deck showed up in the format, who registered it first and how did it spread from event to event?
- **Answer shape**: for one emergent archetype, each event it has appeared at in order, with the pilots bringing it and how many were new to it, plus the first handful of pilots named with their debut event.
- **Reads**: `Archetype`, `Deck`, `Pilot`, `Event`, `Card`; `HAS_ARCHETYPE {isPrimary: true}`, `PILOTED_BY`, `PLAYED_AT`, `Deck.createdAt`, `CONTAINS {board: 'Main'}` for the tag-split check.
- **Their version**: covered badly. Their `/event/:eventId` "New Cards" table finds first appearances at an event but is card-level, uses the same registration-timestamp chronology without saying so, and is degenerate (median 12 new cards per event, one event yielding 478). Nothing on their site is archetype-level or pilot-level.
- **Sample size**: offer 7 archetypes, not 9. Green Eldrazi and Esper Midrange must be withdrawn or flagged: their main-deck card profiles sit at 0.723 and 0.687 similarity to an existing, still-active tag against a format-wide median of 0.133 and a 99th percentile of 0.483, so they are tag splits rather than new decks (Jeskai Midrange at 0.560 needs the same flag). For every archetype except Flood Moon the median event contributes 1 pilot and 30 of Nadu's 47 events are single-pilot, so the "new to it" column reads 1 on most rows and cannot be dressed as a diffusion curve. Flood Moon is the only subject with real mass (44 events, 50 pilots, 93 decks, median 2 pilots per event) and the only measurable single-circuit origin, so it should be the default. Say "first registered", never "invented": debuts are simultaneous three-pilot events for both Flood Moon and Eldrazi.
- **Size**: M.

#### New card takeover, event by event

- **Reader question**: when a new card shows up, how fast does it spread, and how many tournaments did it take to become a staple?
- **Answer shape**: one dot per event on a date axis, y the share of that event's field running the card, every dot labelled with its field size, the debut event marked, and a caption stating how many events it took to first clear 10 percent of a field.
- **Reads**: `Card`, `Deck`, `Event`; `CONTAINS {board: 'Main'}`, `PLAYED_AT`, `Deck.createdAt` for event ordering.
- **Their version**: covered badly. `/card/:canon` draws a weekly 120-day trailing average, so adjacent points share 119 of 120 days and the line is heavily autocorrelated; per-point N is hover-only, the unit is the deck so one 306-deck event can carry a whole window, and nothing marks the card's debut. Our Cards tab adoption chart is year-grain only, so a card that arrived in March and peaked in June is one flat 2026 dot.
- **Sample size**: every dot is one event and N is that event's field size, measured 1 to 304 (median 28). 29 of 108 events hold under 10 decks, where the share can only move in steps of 1/n, so those dots are drawn small and demoted rather than dropped, and a 10-percent-crossing claim requires a 20-deck field. Drawable set is 253 cards debuting 2024 or later with 8 or more events and 30 or more decks, median runway 26 events; only 8 debut in 2026 with at most 32 events of runway and that must be on the selector. Event position is `min(createdAt)`; only 32 of 108 events span more than one calendar day (longest 12), so the ordering is cleaner than feared. Debut means first seen in our corpus, which starts January 2023 with 8 events, so pre-2024 debuts are excluded rather than shown.
- **Size**: M.

#### Is this deck still the same deck?

- **Reader question**: the archetype has kept its name for three years, but is it still the same deck, and which cards came in and went out?
- **Answer shape**: one row per year for the chosen archetype, showing what the year's core kept, gained and dropped with the card names listed, plus one overlap number and the year's deck count.
- **Reads**: `Archetype`, `Deck`, `Card`, `Event`, `Year`; `HAS_ARCHETYPE {isPrimary: true}`, `CONTAINS {board: 'Main'}`, `PLAYED_AT`, `IN_YEAR`.
- **Their version**: not covered. `/archetype/:tag` shows performance and meta share over time and a deck list and never touches card composition; their "New Cards" table is per event and answers first-ever appearance, not composition change. Our Meta tab has no card dimension and our Cards tab no archetype dimension.
- **Sample size**: only 19 of 126 archetypes reach 20 primary decks in two adjacent years, so the dropdown offers 19 and prints that 107 were excluded. The 20-deck floor follows from the core definition (a card in 50 percent or more of that year's decks): below it a single registration moves membership by 5 percentage points and flips cards in and out. 2023 clears 20 decks for none of them, so the honest span is 2024 to 2026, three rows and two transitions, and 2026 is a partial year. Measured: Grixis 28/238/116 decks with overlap 0.67 then 0.74, Jund 69/140/61 with 0.61 then 0.84, Walks 48/106/43 with 0.52 then 0.71. The 50-percent core threshold is a choice and not data, and must be stated on the surface.
- **Size**: M.

#### Everywhere, or just big at one tournament?

- **Reader question**: was that archetype genuinely all over the format last year, or did it just show up in numbers at a couple of large events?
- **Answer shape**: for one year, event presence plotted against the presence expected if that archetype's decks were spread across events in proportion to field size, ranked by the residual.
- **Reads**: `Archetype`, `Deck`, `Event`, `Year`; `HAS_ARCHETYPE {isPrimary: true}`, `PLAYED_AT`, `IN_YEAR`.
- **Their version**: not covered. No page on 7phstats counts events for anything: `/`, `/archetypes`, `/archetype/:tag` and `/stats` all use deck counts as the denominator, so a single 306-deck event silently supplies a quarter of a six-month window. Our own Meta tab has the same gap and its weakness list already admits it.
- **Sample size**: the naive shape does not work and must be replaced. Deck share tops out around 8.5 percent while event presence runs to 88 percent, so "above the diagonal" is meaningless. The residual against proportional spread does carry signal (2026 Jund 17 events against 21.8 expected, Blue Tron 23 against 17.5; 2025 Initiative 16 against 23.4) but at 3 to 8 events on a 32 to 51 event base, roughly 2 sigma, so rank and label, never declare. A 10-deck archetype floor keeps 38 of 111 archetypes in 2026 and 51 in 2025. 2023 must be dropped from the selector entirely, not merely flagged: 8 events means presence takes 9 values, only 4 archetypes clear 10 decks, and two events supply 69 percent of the year.
- **Size**: M.

#### Do people who turn up stick around?

- **Reader question**: of the players who showed up for the first time in a given year, how many are still registering decks now?
- **Answer shape**: a retention table, one row per starting year, plus a per-year split of the active player base into first-timers and returners.
- **Reads**: `Pilot`, `Deck`, `Event`, `Year`; `PILOTED_BY`, `PLAYED_AT`, `IN_YEAR`, `Pilot.lowConfidence`.
- **Their version**: not covered anywhere. `/pilot/:pilot` is a single-player page with no roll-up, and there is no player-population, growth or retention surface on any of their twelve routes. Our Pilots tab is entirely subject-entry and nothing aggregates across the 1083.
- **Sample size**: cohorts are 116 / 321 / 393 / 253 and go on the row labels. Retention runs 2023 at 78 / 74 / 66 percent, 2024 at 66 then 49, 2025 at 38 into a half year, so the 2026 column is a floor and that belongs on the column header. The 2023 cohort is left-censored and must be greyed or excluded: the corpus begins January 2023, so all 116 are "first seen" rather than new, and it rests on 8 events against 17, 51 and 32 later. Retention means "registered at least one deck that year", and 476 of 1083 pilots have exactly one deck in the whole corpus, so "still playing" can be a single appearance. The active-base split is the genuinely new part: first-timers fall from 100 percent to 78, 57 and 40 percent, so the scene is measurably shifting from recruiting to retaining. Surface the 55 lowConfidence pilots (5.1 percent) here, since a name collision merges two humans into one retained player.
- **Size**: S.

## Below the cut line: needs data we do not ingest

Not ranked with the buildable candidates. Each names the missing field in one line.

- **What the last points change did to the field** (a before-and-after strip of adoption and finishes around an update). Missing: `points_updates.json`'s `effectiveAt` timestamps and per-card `delta`, never ingested; there is no Points Version node and no points-history edge, and `Card.points` is a single current scalar. Confirmed against the live artifact: `MATCH (p:PointsVersion)` fails with "Table PointsVersion does not exist".
- **What replaced a card after it got pointed** (the substitution that followed a change). Missing: the same points history. `Deck.createdAt` is present and non-null on all 4591 decks, so if `points_updates.json` were ingested the sub-year axis under ADR 0013 would already exist. This is the highest-value item on this list, not a dead end.
- **What a deck cost in points when it was registered.** Missing: `pointsAtCreation`, which their `decks.json` carries per deck and we do not ingest. Everything we can say about points drift has to be reconstructed by pricing old lists at today's values, which is candidate 3 above and carries a measured 22.8 percent method error. Worth stating how large the underlying effect is, measured directly from their file: **3202 of 4592 decks (69.7 percent) cost a different number of points today than when they were registered**, and the drift is overwhelmingly upward (895 decks at +1, 666 at +2, 607 at +3, 443 at +4, a tail out to +14, and only 41 decks cheaper). This is one field, already on disk in a file we already download.
- **Who this player teams up with** (team-mate structure at the 4 Teams events). Missing: team membership. There is no Team node, no team property on `Deck`, and the team-event deck names carry placement, pilot and deck but never a team ("01st James W - Grixis - TMCTeams25"). Even with perfect recovery the yield is thin: 119 of the 165 pilots who have ever played a team event have played exactly one.
- **Anything resting on a win rate, a match record or a head-to-head result.** Missing: match-level results, which the source does not publish at all. Every performance number in this document is a within-event finishing rank, never a rate of wins.
- **Anything resting on an event date.** Missing: event dates. `Deck.createdAt` is a registration timestamp used as a proxy under ADR 0013, and 32 of 108 events span more than one calendar day.
- **Card copies per deck.** Missing: quantities. `CONTAINS` carries a board and no count, and their `cards_index.json` drops quantities too. Immaterial in a singleton format except for basic lands, where it understates deck price by cents.

Two survivors carry a `buildable: false` flag from verification that their own measured evidence
contradicts (both were computed end to end on our graph and name no missing field). They are kept
above, in the buildable set: **Do people who turn up stick around?** and **Inside one archetype,
which staples separate the good finishes from the bad**.

## Weak answers

Places they cover something and we could state it far more clearly. These are framing wins, not new
candidates; each points at the ranked candidate that would carry it.

**"Perf" is never defined on any page a newcomer lands on.** The entire methodology copy in their
bundle is one visible line on the event detail page ("Conversion Factor = (% in Top Cut) / (% in
Starting Field)") plus seven hover-only `title` attributes, two of which sit on a page other than
the one that sorts by the number. A reader sees "Perf 0.914" with no way to learn it is `1 -
mean((placement - 1) / (eventSize - 1))`. The better answer is that a metric is defined where it is
drawn: candidate 1 states its floor and its baseline on the surface, and our FAQ tab already carries
the definition but is linked from no chart.

**Conversion factor is a ratio of ratios over an absolute top-8 cut.** Event sizes run 1 to 306;
the median event's top 8 covers 23 percent of its field, but 22 of 108 events have top 8 covering
half or more, and 23 events have every deck at `placement <= 8`, so every conversion factor on those
pages is exactly 1.000 with nothing saying so. Three events have `K = 0` and render every value as a
dash. The better answer is candidate 6: recover the field size (exact for 105 of 108 events) and
show coverage, then either normalise the cut by field size or state plainly that the metric cannot
be pooled across these events.

**A placement is shown without its denominator, everywhere, on purpose.** Their placement badge
takes `placementTotal` as a prop at all 9 call sites and never reads it, so 5th of 19 and 5th of 306
render identically. We have the same defect: our pilot neighbourhood graph draws a bare ordinal and
only the head-to-head hover carries a field size. Candidate 6 recovers the denominator for 105 of
108 events and settles what noun it takes, which is the open question ADR 0014 left undecided.

**Sample size lives in hover tooltips or nowhere.** No archetype tile on `/archetypes` shows an N; a
tile reading "Perf 0.914" could be 3 decks or 300, and on their default 180-day window 32 of the 80
tiles that clear their invisible three-deck floor clear it with 3, 4 or 5 decks. Their rolling
charts label N for the latest point only. Every candidate above prints its N and its event count on
the surface, which is by itself the differentiator.

**Today's points sit beside a four-year history with no reconciliation.** Their card page renders a
current points badge above a performance line spanning several point changes, and their event table
multiplies deck counts by today's points under the heading "Total points spent". The better answer
is candidate 3, which makes the drift the subject rather than an unstated assumption, and candidate
2, which shows what moved after it.

**A rolling four-month weekly window reads as a trend it cannot support.** Adjacent points share 119
of 120 days, so the line is heavily autocorrelated, and weeks with zero decks are drawn as a flat
segment on the plot floor, which reads as catastrophic performance rather than absence. The "new card
takeover, event by event" candidate below the line replaces the smoothing with one dot per event and
the field size on every dot, which is the honest unit.

**The same archetype is counted three different ways on three of their pages** (primary tag on
`/archetypes`, all `engineTags` on `/archetype/:tag` and `/stats`), so its deck count and its
performance differ across pages with nothing reconciling them. Candidate 1 fixes the convention by
stating it (primary only, and that secondary-tagged decks read as absent), and the "same deck under
two names" candidate is the structural version of the same problem on our side, where Black Walks
and Deadpool Walks hold 53 decks between them and draw as zero lines on our Meta tab.

## Coverage note

Terse inventory of their twelve routes, enough to justify the "not covered" claims above.

| Route | What it answers | Nearest candidate | Covered? |
| --- | --- | --- | --- |
| `/` | 12 archetype tiles (Perf / Meta / Conv) over a 180-day window, 5 recent events | 1 | Partly, deck-counted, no N, no definitions |
| `/stats` | Filter console, deck-count sparkline, placement histogram, top archetypes and top pilots | 1 | Partly, hidden 5-deck floor, Teams silently dropped |
| `/archetypes` | 124 archetype tiles, three KPIs each, date slider | 1, "same deck" | Partly, no sample size on any tile |
| `/archetype/:tag` | One archetype: KPIs, two rolling charts, hot deck, histogram, deck list | 4, 16 | Partly, no card composition, static and live KPIs mixed |
| `/card/:canon` | One card: hero stats, two rolling charts, common archetypes, deck list | 14, 2 | Partly, no floor, "Perf 0.000" means two things |
| `/deck/:deckId` | One deck list, pilot, event, result, mana curve | "what is this deck built like" | Partly, no comparison to anything |
| `/pilot/:pilot` | One pilot: performance line, placement histogram, archetype chips, deck list | 11, "specialists" | Partly, no event count, no cross-pilot roll-up |
| `/events` | Flat searchable event list, top-4 preview per card | 5, "circuits" | Partly, date pill is a scrape timestamp |
| `/event/:eventId` | One event: top decks, treemap, three sortable analytics tables | 5 | Partly, conversion factor degenerate on 23 events |
| `/search` | Deck slice by any filter, or card frequency over that slice | 4, 2 | Partly, no time dimension on the card table |
| `/recommendations` | Paste a list, get co-occurring cards | "card packages" | Partly, staples list, point-blind |
| `/downloads` | CSV or ZIP export of a filtered deck slice | n/a | Orphaned, nothing links to it |

Not present at any route: archetype card composition, archetype-to-archetype comparison, pilot-to-pilot
or event-to-event structure, any event-count denominator, deck price decomposition, colour-count
analysis, points drift, player retention, event coverage, and any methodology, glossary or about page.

## Not worth chasing

Things they do that we should not copy.

- **The custom chart builder** (`/stats` custom mode: 4 entities by 3 visuals by up to 8 metrics).
  Nothing guides the reader toward a pairing that means anything and nothing warns when a pairing is
  degenerate. Axes render bare and untitled, so a shared chart does not say what it plots. Our house
  position is bounded insight cards, not a build-your-own console.
- **A recommendation engine as a ranked list against a pasted decklist.** Theirs returns 10
  fetchlands and duals in the top 15 for their own sample deck, because lands are hard-coded to full
  colour fit and sit in 60 to 74 percent of every deck, and 45.7 percent of its 446,746 edges rest on
  a single shared deck. Skip the surface, not the maths: their PPMI scoring is better than our raw
  rate and candidate 4 takes it and adds the floor they omit. The package-as-component framing
  (below the line) is the other version worth having; the flat ranked list is not.
- **`spiceScore`.** A mean cosine distance to the 20 nearest decks in an embedding space, printed to
  three decimals with no scale and no baseline, defined only in a hover title on a different page.
  No graph equivalent and no reader value at that level of opacity.
- **`valuePerUsd` as a sort mode.** "Perf/Price" ranks on a percentile-times-percentile composite the
  row never displays, so the reader gets an order with no visible justification. If price is worth
  showing, show the price (candidate 7, and the budget build below the line).
- **Conversion factor as shipped.** An absolute `placement <= 8` cut with no field-size guard,
  returning 1.000 for everything at 23 of 108 events and a dash at 3 more, with roughly half the rows
  of a typical event's card table resting on a single deck. Do not port the metric; port the question
  and give it a denominator.
- **Rolling 4-month weekly windows.** 119 of 120 days shared between adjacent points, zero-data weeks
  drawn on the plot floor as if they were bad results, and no per-point N at rest.
- **Uncapped and silently capped lists.** `/card/polluted-delta` renders 3398 deck rows in one DOM
  list; elsewhere lists are cut at 60, 200 or 300 rows with no "showing 60 of 382" framing. Both
  failures are the same one: the reader is not told what they are looking at.
- **A twelfth route nothing links to.** Their `/downloads` path string appears exactly once in the
  whole bundle, in the route table.

## Killed in verification

Tested against the graph and did not survive. Compact, one line each.

- **What the last points update actually did / did to the decks that got hit / what the last points change did to the field.** Three framings of one thing: no points-history node, edge or timestamp in the schema. Moved to the cut line above.
- **What replaced a card after it got pointed.** Same missing history; also 3295 of 4591 decks exceed 7 points at today's values, so no before-and-after population can be cut. Moved to the cut line.
- **The points ladder: what an unspent point costs you.** Non-monotone (4-point decks finish as well as 7-point decks), year-unstable (the gap is 0.012 in 2025, the fattest year), and confounded: 75 of the 128 underspenders in 2026 are reserved-free budget decks. Stratified by each deck's own cap, both arms fall inside noise.
- **Point-hungry decks and the ones that leave points unspent.** A chart of two confounds over one null: the top of the ranking is decks playing under an 8-point cap, not decks spending more, and once each deck is measured against its own cap 20 of 21 archetypes sit within 0.2 points of it. One genuine row survives (Blue Moon leaving 2.22 of its 8 unspent) and belongs inside candidate 8.
- **Is it the deck or the driver.** The proposed null destroys pilot skill as well as deck-specific skill. Under the correct null (shuffle each pilot's own placements across their own decks) the observed spread of 0.161 does not clear a p95 of 0.164, and 125 of the 196 cells belong to pilots with only one qualifying archetype, so the two are not separable in principle.
- **Who this player teams up with.** No team membership anywhere in the data, and team-event deck names carry placement, pilot and deck but never a team. Moved to the cut line.

## Follow-up

Two data defects surfaced by the spot-checks. Neither is a candidate and both are cheap, but the
first silently corrupts any points-spend answer, so it should land before candidate 3 or 8.

- [ ] #143 Ingest `pointsCompanion`: `Card.points` holds the default context only, so 622 companion
      decks price 3 points light and our totals disagree with the source on 9 percent of decks
- [ ] #144 Drop or quarantine the `nan` event: one source deck carries `"event": "nan"` with a null
      `eventId`, giving us a 108th `Event` node that is not an event

One candidate is filed and being built. The rest stay in this document until promoted.

- [ ] #145 **Archetype tab: metagame landscape and archetype head-to-head** (candidate 1, reshaped)

Candidate 1 was filed as a ranked leaderboard and reshaped on review into the Pilots tab's structure
applied to archetypes: a metagame landscape scatter (meta share against finish, per year) and an
archetype head-to-head timeline over shared events. The ranked-dot-chart framing in candidate 1 above
is superseded by the issue; the evidence under it still holds.

Two things measured while specifying it, both of which belong here. The scatter earns its shape:
Storm took 2.4 percent of the 2025 meta with a 0.774 finish over 28 events, the best in the year by a
distance and invisible in anything sorted by share. And the head-to-head has one honest break from
its pilot original: a pilot brings one deck to an event but an archetype brings several, so each
point is a mean over a median of 3 decks rather than a single result, and roughly 40 percent of
shared events have one side down to one deck.

Closed without building, recorded here rather than lost:

- #146 (candidate 3, points legality) dropped on review.
- #147 (candidate 4, card association) closed as a rework of the existing co-occurrence view rather
  than a new insight. The evidence defect it named is real and still stands in candidate 4.

Not filed, in rank order: candidate 2 (what got pointed and what took its place), candidate 5
(archetype skeleton and flex slots), candidate 6 (event coverage), candidate 7 (what a third colour
costs), candidate 8 (the eight-point trade).

The sixteen below-the-line candidates stay in this document until one of them is promoted. Event
coverage should be re-read against issue #140 before it is opened, since that work adds
`Event.fieldSize` as a stored property and changes what the candidate has to recover for itself.
