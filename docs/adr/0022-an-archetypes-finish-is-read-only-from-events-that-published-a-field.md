# An archetype's finish is read only from events that published a field, its share from every deck

26 of the 107 events published a top-8 bracket rather than standings. ADR 0021 took them off every surface that reads a *pilot's* level and left the archetype surfaces carrying the same defect, filed as #189. This is that ticket, and it splits: the finish axis takes the filter, the share axis does not.

## The share axis keeps them

Excluding brackets moves 193 decks of 4,590. No archetype's meta share moves by more than **0.14pp**:

| archetype | share now | without brackets |
|---|---|---|
| Jund | 6.17% | 6.03% |
| Walks | 4.38% | 4.25% |
| Nadu | 2.00% | 2.07% |

A bracket does over-represent whatever won it, but the effect is an order of magnitude below what a reader can resolve on a chart, and those decks were genuinely registered and played. "Every deck the source shipped" is a defensible contract on its own terms, and filtering here would trade real sample for an invisible correction. `meta_share_over_time`, `card_adoption_over_time` and `LandscapeCell.n` / `share` are unchanged.

## The finish axis drops them

`archetype_landscape` plots share against mean `placementNorm`, and that mean was taken over decks selected on their own answer: at a bracket the only decks on record are the ones that cut. Over the 140 archetype-years holding 8 or more scored decks, **69 move** once the brackets come out, by up to **0.095** on a 0-to-1 axis, and all 69 move the same way, the archetype's finish getting worse.

| archetype-year | mean now | without brackets |
|---|---|---|
| Goblins 2023 | 0.528 (12 decks) | 0.623 (10) |
| Rogue 2026 | 0.646 (17) | 0.718 (15) |
| Time Vault 2025 | 0.487 (17) | 0.551 (15) |
| Academy 2025 | 0.353 (18) | 0.417 (14) |

A one-directional shift across half the archetypes it can measure is not noise. An archetype that happened to win a bracket was being drawn as a better-finishing archetype than it is.

`_within_archetype_sd` takes the filter with it, for the reason `_within_pilot_sd` does: the spread that widths a mean has to be the spread of the quantity the mean is drawn from. The per-year fits move from 0.305 / 0.293 / 0.295 / 0.296 to 0.297 / 0.290 / 0.291 / 0.292, which moves no bound a reader can see.

## The asymmetry is not new, which is what settles it

`LandscapeCell` already computed its two axes over different populations, and said so: `n` and `share` count every deck of the archetype, while `mean_norm` and `events` count the **scored** ones, "a deck the source never scored neither shifts the mean nor pads the count". Dropping the brackets from the finish side widens an existing condition by one clause. It does not introduce a split that was not already there.

What it does introduce is a gap a reader can notice: Grixis 2025 is 238 decks and a mean over 224 of them. So `LandscapeCell` now carries `scored`, and the hover states both counts beside each other:

```
Grixis · 11.36% · 238 / 2,095 decks · 0.53 (1 = 1st) · 224 scored at 35 events
```

Stating one number and quietly meaning another is the failure mode here; two numbers, side by side, is the fix.

## The timeline drops the whole event, not just the point's value

`archetype_timeline` plots a per-event mean, so at a bracket the point *is* the selected sample. The event yields **no point at all** rather than a break in the line: a break says the archetype turned up and the source scored none of it, and here the source scored a good deal of it and none of it is readable. That drops 167 of the 2,304 marks the catalogue's archetypes would otherwise draw, 7.2%.

`archetypes_with_history` follows it, as `pilots_with_history` follows the pilot chart. Its promise is that a pick never lands on a refusal, so it has to count the events the timeline can actually draw. On this artifact the catalogue still offers **121 of 126** archetypes, unchanged, because an archetype ranked only at brackets was already too thin for it.

## The 0.5 reference line stays

The field's mean `placementNorm` is 0.4767 with brackets counted and **0.4927** without, against a drawn reference of 0.5. That gap is 0.007, down from 0.023 before ADR 0021, and most of what remains is the residual incompleteness gradient that ADR 0021 measured and left alone (a fully published event averages 0.4945). 0.5 is what a random finisher's expected rank implies, the pilot charts already draw it, and 0.007 is far below what a reader can resolve on a chart. No change.

The landscape's caption counts dots above that line and is unchanged in form. The count itself will read lower now, which is the correction working: it was reading the brackets.

## What still keeps these events

**Hidden gems keep them and must**, for the reason ADR 0021 gives: the gem null is conditioned per event (`query._gem_tails`, ADR 0020), so a bracket's decks are scored against that bracket's own cut rate. That uses the information rather than discarding it, and is strictly better than a filter.

**Head-to-head is untouched.** Its exposure is a thinned set of meetings, not a distorted value, and dropping brackets would delete real meetings to compensate for absent ones (ADR 0021).

## Consequences

`_within_archetype_sd`, `_archetype_events` and `archetypes_with_history` now take the skip list; `archetype_landscape` and `archetype_timeline` read it once per call and thread it through, as `pilot_performance_over_time` does. `LandscapeCell` gains `scored`, and the landscape hover states it.

181 of the 4,566 scored decks carrying a primary archetype leave the finish side. Four archetypes, all in 2023 (`boros`, `jeskai_control`, `sultai`, `temur`), were scored **only** at brackets and now hold a share with no finish: 2023 returns 56 cells of which 52 carry a dot, where every other year still places all of them. None of the four falls inside a drawn top 25, so what the chart draws is unchanged at 25 dots in every year, and no year falls under `MIN_LANDSCAPE_ARCHETYPES`.

The archetype fixtures were describing events that could not exist, exactly as ADR 0021's pilot fixtures were: a 500-seat field holding eight decks. They now declare `_COVERED_FIELD` and are padded by `_cover_fields`, whose fillers carry no primary archetype, so they cover a field and join nothing. `test_app`'s two demo snapshots take the same treatment. The only fixture number that moved is a share's denominator.

The FAQ says the archetype charts leave these events off the vertical axis while keeping them on the horizontal, and the race entry no longer claims to be one of only two charts that leaves them out.
