# Field size: corrected at build time, against what the data can be counted for

The source ships `eventSize` on every deck, and `placementNorm = (placement - 1) / (eventSize - 1)` is ranked against it. At 9 of 108 events that number is wrong, so the error lands on every metric that reads a normalised finish. Four pilots sat at a career mean of 1.000, dead last in field, and every one of them earned it by making a top 8: at `GGWARL`, `DLIQ`, `#BBO1` and `ProBox` the source claims a 5-person field while 7 or 8 pilots finished, so a tied-5th scores `(5-1)/(5-1) = 1.000`. The same defect runs the other way at `GGWAD`, which claims 16 against 28 registered pilots, and reads its last place as 1.000 where 16th of 28 is mid-field.

The build now derives a field size per event, corrects it where a count contradicts the source, and re-ranks that event's norms against the corrected field. `Event` carries `fieldSize` and `fieldImputed` (the rule letter, or null where the source's number stood).

## Three rules, and only the first is provable

**Rule A** fires when `eventSize` contradicts something countable: more distinct pilots attended than the claimed field, or someone finished at a rank beyond it. Impossible under any reading, so the *refutation* is arithmetic rather than judgement. The *replacement* is not, and the letter must not be read as claiming it is: because the floor applies to every rule, 5 of the 6 Rule A events take their new field from `MIN_CUT_FIELD` and not from any count (7 or 8 pilots against a claimed 5, all landing on 24), and only `GGWAD` is count-derived (28 pilots against a claimed 16). An `A` in the report means the source's number is refuted; a `B` means nothing refuted it and the domain rule decided anyway.

**Rule B** is domain knowledge, not evidence: per the repo owner, 7PH runs no 8-player events, so an `eventSize` of 8 or less is a top-8 cut reported as a field. The `max_placement < event_size` guard anchors it in what the standings show, and only partly. `MazeIQ` and `Pats Birthday Brawl` show a deepest finish of 5th against a claimed 8, the bracket signature; `DeckaDiceIQ` records only its winner, so nothing there corroborates or contradicts the 6 it claims and the domain rule decides it alone. It fires at 3 events.

**Rule C** is defensive. A null `eventSize` has never been observed, at 4592 of 4592 decks. It is recorded as a branch, not as evidence.

Only `eventType == 'Tournament'` is corrected, by whitelist. A `Teams` event's `eventSize` counts teams and not people (TMCTeams25 is 39 against 117 decks), so Rule A's contradiction is that event's normal shape, and an `eventType` nobody has classified is left alone rather than corrected on Tournament's assumptions. A structural detector was tried and rejected: decks-per-distinct-placement does not separate the two, because top-8 brackets with ties reach 4.00 and overlap TMCTeams25's 3.08. `eventType` is the only reliable discriminator, which makes this correction dependent on a field the source is free to restate.

Both fields it depends on are volatile under ADR 0003: neither `eventSize` nor `eventType` is in the ingestion gate's immutable projection, so a restated one moves an event in or out of correction, and re-ranks its norms, with no flag raised. That is the same trade the gate already makes for every field the source is entitled to restate, and it is the price of re-deriving each build rather than freezing an imputed value into `ingest.json`.

## The floor is a floor, not a constant

A corrected field is `max(counted, MIN_CUT_FIELD)` with `MIN_CUT_FIELD = 24`, rather than a bare 24. It stays right as more lists arrive: `MazeIQ` at a ninth deck still yields 24, a broken 30-pilot event yields 30, and the field is never set below something we counted. Changing the constant is a code change with an ADR, as ADR 0012's band is.

The result is robust to the choice. 16, 19, 24, 33 and 40 all move the same two cards across the hidden-gem band.

## Where it runs, and why not earlier

In `build.build_graph`, after `resolve_pilots` and after the duplicate drop. Rule A's floor asserts "at least N people attended", so it counts canonical pilot identities: a curated merge collapses two ids onto one person, and a duplicate registration is not a second attendee, both the direction that floor must not err in. Rule B reads the deepest placement, and placements settle at load (`placement_from_title` is why `Pats Birthday Brawl` has any, `resolve_cut_placements` rewrites CanBrawl2's nested tiers).

Not at ingestion. `ingest.py` reconciles snapshots and classifies fields as historical or volatile; an imputed value written there would be indistinguishable from source data in `ingest.json`, and would freeze. Re-deriving every build means the rules stop firing on their own once the source is fixed.

## Consequences

54 scored decks are re-ranked, 46 of them to a different value (the other 8 are winners, whose norm is 0.0 under any field). The other 99 events are byte-identical. No pilot keeps a career mean of 1.000 earned from a top-8 finish; the shallowest finish among those who keep 1.000 is 12th.

Norms are rescaled, never minted. A deck carrying a recovered placement but no source norm (`Pats Birthday Brawl` 8, `Area52IQ` 1) stays unranked, so `_ranked_deck_total` and the gem denominators do not move, and minting stays separable as its own change. **Superseded by ADR 0016**, which is that change: every deck whose rank is known now carries a norm minted against this `fieldSize`, and the 54 rescaled here are marked `normImputed = 'rescaled'` so the value this ADR rewrites is no longer indistinguishable from the source's own.

The hidden gems list grows from 34 to 36: Fallen Shinobi (23 ranked decks, mean 0.337 to 0.328) and Yawgmoth's Will (105 decks, 0.334 to 0.328), both crossing the 0.33 band of ADR 0012. `hidden_gems_subgraph` reads `placementNorm` and consults no flag, so `fieldImputed` does not gate it: a domain-rule correction reaches the gem list on the same footing as an arithmetic one. Every imputed event is listed in the reconciliation report each build so the assumption stays legible.

`trends.py` still recovers field size by inverting the norm, which now tracks the corrected field where there is one. Reading `Event.fieldSize` instead retires that recovery, and is the follow-on to issue #140. **Done in ADR 0016**, which had to: minting the norms of the three events whose recovery fell back to a deck count is what would have made them draw a win labelled against the wrong field.
