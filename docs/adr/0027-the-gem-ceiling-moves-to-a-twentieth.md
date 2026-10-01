# The gem ceiling moves to a twentieth, by the rule ADR 0026 fixed

ADR 0026 settled what happens when the shipped gem cell drifts past the sweep's own
qualification bar: it is retired, and the sweep names its successor. The 2026-10-01
ingestion round re-ran `scripts/gem_sweep.py` (issue #254), over 134 archetypes and
5,533 primary-tagged ranked decks, and the cell ADR 0026 shipped had drifted the same
way:

| | genuine | found | luck | nodes | archetypes |
|---|---|---|---|---|---|
| 0.20 / 0.10 / 8 / 0.010 (was shipped) | 1.9 | 4 | 2.1 | 27 | 2 |
| **0.20 / 0.05 / 8 / 0.010 (ships now)** | **2.2** | **3** | **0.8** | **20** | **2** |
| 0.20 / 0.05 / 6 / 0.010 | 1.9 | 3 | 1.1 | 20 | 2 |
| 0.20 / 0.05 / 5 / 0.010 | 1.7 | 3 | 1.3 | 20 | 2 |

At 2.1 luck in a list of 4 the shipped cell is past the half-luck floor, so holding
it is not a choice ADR 0026 leaves open, and **the ceiling moves** to the cell the
sweep ranks first. Three things are worth recording about this particular move:

- **The ceiling is still inside its definition.** ADR 0020 ruled `MAX_GEM_SHARE` a
  definition whose top is 0.15, and 0.05 sits well inside that axis, so tightening it
  is a measurement within the definition. It does cut the band ADR 0020 found most
  productive on the original corpus (cards in 5-10% of an archetype ran at 2.93x their
  expected count there, against 1.07x for 5% and under). That reading was of a corpus
  of 4,100 decks; on this one the band above 5% is where the luck now sits, and a rule
  fixed in advance is read on the corpus that exists rather than on the one it was
  first measured against.
- **0.05 was the bottom of the swept axis, so the axis was widened before the move
  was taken.** `SHARES` now carries 0.03 and 0.04 as well. No cell below 0.05 scores
  above 0.8 genuine on this corpus, against 0.05's 2.2, so 0.05 is a peak and not a
  truncation. The 0.15 top stays where ADR 0020 put it, for the reason it gave.
- **The win is narrow and counts.** 2.2 against 1.9 is three tenths of a find, and the
  rule was fixed before the results were read precisely so that a narrow win is a win
  (ADR 0020). The floor stays at 8: at a 0.05 ceiling, 6 and 5 score 1.9 and 1.7.

The field-size question stays closed at the new cell, on a weaker margin than
before. Restricting the cut or the pool to majors no longer scores negative (1 found
against 0.5 expected by luck, either way), but it costs 1.7 of the 2.2 genuine finds
and leaves a list of one card in one archetype, which is not a better answer to the
question the tab asks. The verdict ADR 0020 and ADR 0026 recorded stands; the number
behind it is worth re-reading at the next sweep.

## Consequences

`MAX_GEM_SHARE` 0.10 to 0.05 in `query.py`; the floor, the cut and the bar do not move.
`MIN_GEM_SLICE` derives from 80 to 160, so `gem_archetypes` falls from 18 entries to 6
and the caption's population clause now excludes 128 of the 134 archetypes holding a
ranked primary-tagged deck. The drawn list goes from 4 gems to 3: Lair of the Hydra
(Lands, 22 of 257 decks, 8.6%) falls over the new ceiling, and Mana Vault and Urza's
Bauble in Lands and Fiery Impulse in Blue Moon stay, at 0.8 expected by luck in a list
of 3 and 20 nodes. Every card on that list is a Main deck card, so the FAQ's board
answer no longer points at a Side-only example.

The hand fixtures in `test_query.py` are re-derived at 200-deck slices (the 100-deck
slices ADR 0026 set fall under the new `MIN_GEM_SLICE` and would silently stop
screening anything), with every worked example recomputed rather than transcribed:
8 of 8 in a cut of 40 of 200 is C(40,8)/C(200,8) = 1.3958e-6, and a card of that shape
clears the bar by chance 0.00894 of the time. The population fixture puts each of its
five slices at its own event, because 839 decks at the fixture's one 500-player event
trips the field correction ADR 0015 makes and flattens every norm. The golden oracle
moves (gem rows, `expected_by_luck`, and the `gem_archetypes` catalogue) and is
recaptured with `--force` in its own commit per `docs/development.md`. The FAQ figures
that quote the list (the luck share, the board example, the population clause) are
re-derived from the live bundle.
