# A gem cell that fails its own bar is retired, and the sweep names its successor

ADR 0020 made the four gem constants a measurement of the corpus and told the
maintainer to re-run `scripts/gem_sweep.py` whenever the artifact grows, because the
winning cell is a property of the corpus rather than of the rule. The 2026-09-01
ingestion round re-ran it (issue #247), over 127 archetypes and 4,901 primary-tagged
ranked decks, and the cell that shipped stopped qualifying under the sweep's own rule:

| | genuine | found | luck | nodes | archetypes |
|---|---|---|---|---|---|
| 0.20 / 0.15 / 5 / 0.010 (was shipped) | 2.3 | 6 | 3.7 | 38 | 3 |
| **0.20 / 0.10 / 8 / 0.010 (ships now)** | **3.0** | **5** | **2.0** | **32** | **2** |

The qualification rule was fixed before any result was read (ADR 0020): a cell counts
only if no more than half its list is expected by luck, and the half is a floor on
being a finding at all, not a target. At 3.7 luck in a list of 6 the old cell is past
that floor, so the choice was between holding it and accepting the luck share, or
moving to the cell the sweep now ranks first. **It moves.** Holding was rejected
because a list more than half of which is expected coincidence is not a finding under
the rule this feature is built on, and the FAQ would have had to keep saying so above
the table it describes.

Two things make this the small move it looks like rather than a re-litigation of ADR
0020:

- **The ceiling stays inside its definition.** ADR 0020 ruled `MAX_GEM_SHARE` a
  definition whose top is 0.15, refusing the wider cells the score prefers because a
  card in 30% of an archetype's decks is a staple. 0.10 sits inside that axis, so
  tightening it is a measurement within the definition, and ADR 0020 had already named
  the 10% cell as the trade to make "if this list is ever accused of not being about
  rare cards". On this corpus it is also simply the winning cell.
- **The floor moves the way ADR 0020 said a floor moves.** The floor was 5 because the
  sweep said 5, by 0.2 genuine finds, under a rule fixed in advance precisely so a
  narrow win counts. The same rule read on the grown corpus says 8, and refusing that
  reading would be the "manufacture a result rather than read one" the design exists
  to prevent. 8 sat at the top of the swept axis, so the axis was widened before the
  move was taken rather than the edge win silently accepted: floor 10 scores 2.5
  genuine against 8's 3.0 (and 12 scores 1.9), so 8 is a peak, not a truncation, and
  `FLOORS` now carries 10 so the shipped value stays bracketed.

The field-size question stays closed at the new cell: restricting the cut or the pool
to majors still scores negative (1 or 0 found against 1.3 expected by luck), the same
verdict ADR 0020 recorded.

## Consequences

`MIN_GEM_DECKS` 5 to 8 and `MAX_GEM_SHARE` 0.15 to 0.10 in `query.py`; the cut and the
bar do not move. `MIN_GEM_SLICE` derives from 34 to 80, so `gem_archetypes` falls from
40 entries to 16 and the caption's population clause now excludes 111 of the 127
archetypes holding a ranked primary-tagged deck. The drawn list goes from 6 gems to 5: Night Scythe (Tinker, 4 of
its 5 decks in the cut) falls under the new floor, and the rest survive, 4 in Lands
and 1 in Initiative, at 2.0 expected by luck in a list of 5 and 32 nodes.

The hand fixtures in `test_query.py` are re-derived at 100-deck slices (the old
60-deck slices fall under the new `MIN_GEM_SLICE` and would silently stop screening
anything), with every worked example recomputed rather than transcribed, and a new
live test pins the qualification bar itself
(`test_the_drawn_list_qualifies_under_the_sweeps_own_bar`), so the next time the
corpus drifts out from under the constants the suite says so on the machine that
ingested rather than waiting for a maintainer to re-run the sweep by hand. The golden
oracle moves (gem rows, `expected_by_luck`, and the `gem_archetypes` catalogue) and is
recaptured with `--force` in its own commit per `docs/development.md`. The FAQ figures
that quote the list (the luck share, the side-only card, the archetype spread) are
re-derived from the live bundle.
