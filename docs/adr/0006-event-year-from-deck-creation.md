# Event year: derived from deck creation, and year is as far as it goes

The graph had no temporal dimension, so the metagame could not be sliced by time (archetype share by year, a pilot's record by year, card adoption over time). `Year` is now a dimension node linked to `Event` by `IN_YEAR`, alongside the existing dimensions (`Macro`, `Colour`, `CardType`):

```
CREATE NODE TABLE Year(year INT64, PRIMARY KEY(year))
CREATE REL TABLE IN_YEAR(FROM Event TO Year)
```

## createdAt is a proxy, not the event date

There is no event date in the source. `events.json` carries only `lastUpdated`, which is when the record was touched, not when the event happened. The only usable signal is deck `createdAt` (present on 4553/4553 decks), so an event's year is `min(createdAt)` across its decks.

This is a proxy: it dates the event by when its pilots registered their lists, which is not the same fact. It is stated here rather than buried in the build because every temporal query in this graph inherits the assumption. At year granularity it holds up on snapshot `20260713T232944Z`:

- **Unambiguous.** 0 of 107 events have decks whose `createdAt` spans two calendar years, so the proxy never has to choose. Measured over that one snapshot, which is not quite what the build sees: it derives years from the union of every snapshot (ADR 0003), where a deck dropped by a later fetch is retained with its old `createdAt`. The two are identical today, since only one snapshot exists, but the union is the surface that can straddle first.
- **Independently cross-checked.** Of the 37 events whose name encodes a year, 36 agree with `min(createdAt)`. The one apparent disagreement ("Super Series 27-10" reading as 2027 against `createdAt` 2023) is a false positive: "27-10" is a day-month. So 36/36 real agreement, checked against a signal the derivation never sees.
- **Distribution.** 2023: 8 events, 2024: 17, 2025: 51, 2026: 31.

The derivation is deterministic, so rebuilding the same snapshot yields the same Years.

## The year is the UTC year

All 4553 `createdAt` values carry a `+00:00` offset, so a Year is a UTC year. 7PH is an Australian format, and Sydney runs 10 or 11 hours ahead, so a deck registered after 13:00 UTC on 31 December is already 1 January locally. A Year is therefore the UTC year the lists were registered in, not the local calendar year the players would name, and the two can disagree for an event held across New Year. The straddle guard inherits the same frame: it detects straddles in UTC, so an event that crosses New Year locally but not in UTC passes it.

This is stated rather than fixed. Converting to Australian local time would be a second inference layered on the first (the source says nothing about where an event was held, and the format has interstate events across three offsets), and the 36/36 name cross-check below already confirms UTC years agree with what the events call themselves. If an event ever lands on New Year, this is the assumption to revisit.

## The build fails rather than guess

The proxy is only honest while an event's decks share one calendar year. If one ever straddles two, `min(createdAt)` would silently pick the earlier and the graph would assert a year it cannot support. The build raises `YearStraddle` instead, before anything is written, so the live graph is untouched and the CLI reports it the way it reports a `SchemaError`: an abort naming the offending events, not a traceback. Both mean the same thing, that the snapshot cannot honestly be built. The guard is not defensive padding against an impossible state: it is the exact condition the evidence above rests on, and it holds today by measurement, not by construction. Upstream could add a New Year event tomorrow. If it does, this ADR needs revisiting, and a hard failure is what forces that rather than a quietly wrong year.

## Month is not modelled

Month is deliberately absent: the data cannot support it. 6 of 107 events straddle a month boundary, so `min(createdAt)` would pick a month the event arguably was not in:

| Event | createdAt span |
| --- | --- |
| NHC25 | 2025-01-27 -> 2025-02-06 |
| NHC24 | 2024-01-28 -> 2024-02-06 |
| NHC26 | 2026-01-26 -> 2026-02-01 |
| PogNov25 | 2025-11-29 -> 2025-12-01 |
| PoGWinaDual30-06 | 2024-06-30 -> 2024-07-02 |
| LPMPerth | 2026-06-28 -> 2026-07-02 |

A Month node would need a tie-break rule (modal month rather than min) that is an inference the data cannot confirm. Year needs no such rule, which is what makes it the honest granularity. Year is where the proxy stops being a guess, so it is where the model stops.

## Why a node, when ADR 0002 kept Colour Identity a property

ADR 0002 rejected Colour Identity as a node "since it is derivable from the atomic colour edges". Year is also derived, so the distinction is worth stating rather than assuming.

Colour Identity was rejected because the atoms it derives from were already *in the graph*: the `Colour` nodes and `DECK_COLOUR` edges are there, so a node would have restated what a traversal already answers. Nothing in the graph carries a date, so Year is not restating anything: it is the only way in. And Year is a dimension to traverse and group by, like `Macro`, not a label to read off a hub. That is what earns it a node.

## Consequences

`Deck` gains a required `created_at`. It is not stored on the Deck node: the event is what gets dated, and per-deck creation would invite exactly the sub-year precision this ADR rejects.

**Amended by ADR 0013 (head-to-head timeline).** `createdAt` is now persisted as a `Deck` property after all, read only by the head-to-head timeline, whose x-axis needs a coordinate finer than `Year`. The event still gets its year from `min(createdAt)` and no query groups below year; ADR 0013 carries the argument (it dates the registration, not the event).

Year is distinct from **Era** (the period between two Points Versions) and the two must not be conflated. Era is a rules concept, bounded by points-list revisions and defining what was legal when a deck was built. Year is a calendar bucket derived from a proxy. An Era boundary can fall mid-year, so the same year can hold two Eras and neither aggregates into the other. Both terms are in `CONTEXT.md`.
