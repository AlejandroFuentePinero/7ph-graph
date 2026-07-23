# Archetype merge heuristics: which are timeless (issues #24, #99)

Whether one archetype tag is a *version* of another (Bus Stop Grixis of Grixis,
Green Eldrazi of Eldrazi) or an archetype in its own right. What survived
testing, what did not, and why. Measured on snapshot `20260718T085655Z`: 4,592
decks, 124 tags.

The test for "timeless" is narrow: a heuristic passes only if it carries no
parameter fitted to the current card pool, so that a rotation cannot silently
change what it decides.

**Status: nothing here is applied.** #24 closed as grilled and #99 closed as
deferred without landing: archetypes reach the graph exactly as the source tags
them. This is kept as the *failure record*, so the heuristics under "Fails" are
not retried from scratch. Two corrections found after it was written are at the
bottom.

The scripts that produced these measurements live in `scratch/archetype/`, which
is gitignored and local to one machine, so treat any reference to them as
possibly absent.

## Passes

### 1. Declared containment (card-free, the strongest signal found)

The source hangs a parent tag at weight 5 under the specific tag at weight 95.
1,388 decks carry exactly that `(95, 5)` pattern. So when >=95% of tag B's
decks also carry tag A, **the source is declaring B a specialisation of A**.

This reads nothing from the card pool, so no rotation can move it. It produces
25 candidate pairs, and it is the only method that finds all of:

- Bus Stop Grixis -> Grixis (99%), the paradigm case every card method missed
- Blue Tron -> Tron, Black Walks -> Walks, Deadpool Walks -> Walks, which have
  no card evidence and no shared name token with their parent

It generates candidates. It does not decide them: Orzhov Taxes -> Orzhov is
100% contained and is still arguably its own archetype.

### 2. Shared defining cards (positive evidence for a merge)

A card defines a group when it is non-land, present in >=80% of each tag in the
group, and >=70% of all decks playing it are in that group, among cards played
by <=15% of the format.

The discriminator is the **count** of shared defining cards, which is not a
tuned parameter. One shared card is a shared enabler: Goblins and Twin both
play Kiki-Jiki, Lands and Breachbond both play Crucible of Worlds, Storm and
Iron Man both play Black Lotus. None are merges. Several shared cards is a
shared identity:

| group | shared | decks |
|---|--:|--:|
| Scales + Hardened Scales | 7, incl. the card *Hardened Scales* | 25 |
| Storm + Gluten Free Storm | 5, incl. Tendrils of Agony | 95 |
| Eldrazi + Green Eldrazi | 4 | 88 |
| Breach + Breachbond + Lurrus Breach + Jeskai Breach | Underworld Breach, Intuition | 186 |

Breachbond shares no name token with Breach, so name matching cannot find it.

### 3. Colour identity differs -> hold

A format rule, not a measurement. Red Esper is WUBR and Esper is WUB.

Must compare colour *distributions*, not the dominant value: tags spread over
many identities, and comparing dominants wrongly held Scales vs Hardened
Scales, which share four colour identities and seven defining cards.

### 4. Companion differs -> hold

Lurrus contracts the deck to permanents of mana value 2 or less. That is a
deckbuilding constraint, not a version. Companions sit in the sideboard, so
they are invisible if only maindecks are loaded.

Lutri is excluded: its condition is that the deck is singleton, which every 7PH
deck already satisfies, so it constrains nothing.

### 5. Upstream aliases (already applied, and useful precedent)

9 archetypes carry aliases and the build already folds them. Worth recording
because the source **already folds colour-prefixed variants into the bare
archetype name**: `grixis_madness` and `rakdos_madness` both alias to Madness,
`boros_legends` to Legends, `dimir_flood_moon` to Flood Moon. Green Eldrazi ->
Eldrazi is the rule the source applies elsewhere, not a new invention.

## Fails

Recorded so they are not retried.

| heuristic | why it fails |
|---|---|
| Jaccard threshold (0.40/0.45/0.50) | frozen number fitted to one pool; 69 of 110 tags sit below 0.45 cohesion and are structurally unmergeable; 10 of 11 links recoverable by string match, so it adds almost nothing over names |
| Relative threshold vs min(cohesion) | Rogue at 0.116 cohesion links to everything, collapsing into a 3,316-deck group |
| Permutation test | significance scales with n, so it gets *less* useful as data accumulates; calls Bus Stop Grixis distinct from Grixis at z=120.9 |
| Lands-vs-spells retention | correct on every control and on Orzhov Taxes / Golgari Cradle, but carries hardcoded 0.85 and 0.70 cut points and puts the paradigm case in the ambiguous band |
| Core retention | confounded by era. Bus Stop Grixis is 75% a 2024 tag, mainline Grixis 75% 2025-26, so it "drops" a third of the core purely because those cards were not printed yet. Year-matching is too coarse to fix it |
| Card lift | the points system caps format play rates near 9%, so any card at 80% presence scores about 12x whether it is Underworld Breach or Mox Opal |
| "Child has its own engine" veto | precision is too low for every known engine: Blood Moon 13% (488 decks play it), Stormwing Entity 19%, Clarion Conqueror 71% at 72% presence. No card is exclusively Orzhov Taxes's |
| Points spent | 1 of 82 tags has a near-fixed spend; format sd 2.37 on mean 9.13. No separation |
| Macro x colour as a universal partition | 159 cells at 0.410 mean cohesion vs 124 cells at 0.449 for the status quo, and it shatters Lands into 39 cells |

## Corrections (found while signing decisions for #99)

### The companion check must read the sideboard

`proposal.py` scores a companion as present from maindeck-or-sideboard, so bare
Breach read as "has Lurrus" off 28 *maindeck* copies and the hold rule above
never fired for Lurrus Breach. A companion is a sideboard card; a maindeck
Lurrus is an ordinary creature and signs no contract. Read the board and the
contract is unmistakable: 56 of 57 Lurrus Breach decks have Lurrus in the side
and none run Teferi, Time Raveler or Leyline Binding, which the 27 non-Lurrus
Breach decks run in 22 and 20 respectively (both are permanents above mana value
2, which the contract forbids). Fix this before trusting the auto-hold tier.

### Parent standalone rate tells an archetype from a colour bucket

Containment says the source declares B a kind of A. It says nothing about
whether A is an archetype or just a colour label, and merging into a colour
label produces a bucket named after a colour pair. The tell is the share of
decks carrying the parent tag where that tag is the deck's *primary*:

| parent | stands alone |
|---|--:|
| Storm | 85% |
| Grixis | 72% |
| Affinity | 59% |
| Eldrazi | 58% |
| Scales | 54% |
| Breach, Nadu | 45% |
| Temur | 26% |
| Orzhov | 16% |
| Rakdos | 8% |

The three merges the card evidence auto-confirmed (Storm, Eldrazi, Scales) all
have parents standing alone 54% or more; the three lowest were all in the
undecided pile. Rakdos at 8% resolves to 13 decks that are eight unrelated
builds sharing only their colours. This reads no card data, so no rotation can
disturb it, which makes it a candidate for a sixth question.

## The shape this implies

Containment generates candidates and the two format-rule vetoes reject
automatically. Nothing available decides the remainder, because the residual
judgment is about deck provenance rather than deck contents, and provenance is
not in the cards. So the remainder is a signed decision, surfaced with its
evidence, in the pattern ADR 0005 and ADR 0009 already establish.
