# Archetypes are served as families, and the owner rules the exceptions by the engine a code is built on

Every surface that says "archetype" now serves the archetype's family: the code
upstream tags a deck with, together with the versions of it upstream tags as codes of
their own (issue #381). This supersedes two older positions. #374 kept versions of a
deck "as tagged" and folded only second spellings of one deck. #99 deferred version
merging because no heuristic free of a fitted parameter could tell a version from an
archetype (`docs/archetype-merge-heuristics.md`). Families need no such heuristic.
Upstream says it in its own tags: it gives most versions their parent as a second tag
at low weight, so a code joins a parent when 95% of the decks it is primary on carry
that parent, on 5 decks or more. Where the tags cannot say it, or say it wrong, the
owner rules (issue #382).

## The test behind a ruling

A code joins the family whose engine its decks are built on, and the piece the name
points at decides. It is a test of codes, not of single decks. A deck that runs an
engine inside another archetype stays where upstream filed it, since upstream's
classification is trusted and nothing here re-classifies a deck. The 18 rulings
applied it in these forms:

- **A combo decides over its shell.** Jeskai Breach runs the Breach combo in a Jeskai
  shell, so it is Breach. Twin Moon runs the Splinter Twin combo, which 1% of Blue Moon
  decks run, so it stays apart from Blue Moon.
- **Shared fuel is not a family.** Breachbond borrows Fastbond, Zuran Orb and Crucible
  from Lands but wins with the Breach combo, so it is Breach. Shops and Bots both run
  Mishra's Workshop, and Shops stays apart because 20 of its 45 decks are a coherent
  prison deck that filing under Bots would hide.
- **A colour tag that rarely stands alone names colours, not a deck.** Bare Bant,
  Simic and Orzhov are primary on 6 to 10% of the decks carrying them, so Warps,
  Amulet Titan, Orzhov Taxes and Orzhov Soul Sisters, each its own engine, stay apart.
  Where the bare code is itself one coherent deck (Esper, Mardu, Rakdos and Jund, whose
  decks share 0.40 to 0.49 of their cards, against 0.20 to 0.25 for Bant, Simic and
  Orzhov), it takes its versions.
- **A support package is not an engine.** Flood Moon is the Dimir tempo deck with a
  Back to Basics lock that many archetypes run, so it is Dimir.
- **The rule's own filing can be wrong.** Golgari Cradle, Temur Prowess and Boros
  Equipment carry their colour family's tag on every deck, but are built on
  Cradlehoof's, Izzet Prowess's and Hammer's engines. A ruling files them there,
  against the rule.
- **A ruling moves a code whole.** Icetill is 9 Jund decks running Icetill Explorer
  and 6 Gruul and Sultai decks nearer Delirium. Filed under Jund, the 6 go with it.
- **A family keeps upstream's name.** No ruling renames one. Temur Prowess joins Izzet
  Prowess, a name upstream already gives 49 green-splash prowess decks.

| Code | Family | Against the rule |
|---|---|---|
| Jeskai Breach, Breachbond | Breach | |
| Breakfast | Nadu | |
| Flood Moon | Dimir | |
| Jeskai Ascendancy | Ascendancy | |
| Car | Fantasticar | |
| Ketramose | Esper | |
| Reckless Red | Mono Red | |
| Bomberman | Iron Man | |
| Icetill | Jund | |
| Boros Equipment | Hammer | yes, from Boros |
| Temur Prowess | Izzet Prowess | yes, from Temur |
| Golgari Cradle | Cradlehoof | yes, from Golgari |
| Iron Man, Orzhov Taxes, Orzhov Soul Sisters, Warps, Amulet Titan | kept apart from the family their tags name | |

Left to the rule on an explicit decision, with no entry: Blue Tron into Tron; Red
Esper, Esper Control and Esper Midrange into Esper; Mardu Lurrus into Mardu; Rakdos
Eclipse into Rakdos; Bus Stop Grixis and Grixis Oko into Grixis; Lurrus Breach into
Breach; Amber Ascendancy into Ascendancy; Trackworks Golgari in Golgari (it runs
Gaea's Cradle but no Craterhoof); Twin Moon and Shops apart. Each ruling's evidence is
on #382 and its reason beside it in `curation/pilots.toml`.

A ruling names upstream's codes and no deck, so every deck a later snapshot files under
one of them lands in the family at build. One whose code upstream stops using steps
aside, and the build says so. A code upstream invents later is judged by the rule
alone: a small version, or one carrying a misleading colour tag, stands apart or in
the wrong family until a sweep like #382's finds it.

## How ADR 0023 and ADR 0020 read now

**ADR 0023**, every archetype figure counts the primary tag, now reads through the
family. A deck's primary code is served as its family, so its share, finishes, card
usage and pilot affinity are the family's. A deck carrying two codes of one family
carries one archetype.

**ADR 0020**, hidden gems are found inside their own archetype, now finds them inside
the family: a slice is the family's primary decks, and a card common across the family
is not rare in it. Under Izzet Prowess, Tropical Island went from 13% to 20% of the
decks, over the ceiling, which is the definition working: a card most of the deck
plays is not hidden.

ADR 0020's half-luck bar is now **60%**, by the owner's ruling. The format is noisy, and
a list may be more than half luck so long as it says so. The bar is a general
acceptance of uncertainty for the system, not a setting for the list it first
admitted. Within one session it first admitted an 11-card list at 0.15/5 (57% luck),
which fell to 8 cards at 80% once Temur Prowess joined Izzet Prowess, and the owner kept
60% all the same. Under it, ADR 0026 retires the 0.05/8 cell ADR 0027 shipped, which
reads 71% luck under families, and ships the sweep's top cell, 0.20/0.04/8/0.010: Urza's
Bauble in Lands and Fiery Impulse in Blue Moon, 0.93 expected by luck.

## Consequences

- `MAX_LUCK_FRACTION` is 0.6 in `scripts/gem_sweep.py`, and the gem test reads it from
  there. `MAX_GEM_SHARE` is 0.04, so `MIN_GEM_SLICE` derives to 200 decks and Initiative
  (167) leaves the archetypes a gem can come from. The gem fixtures in `test_query.py`
  are re-derived at 230- and 250-deck slices.
- The picker offers 96 archetypes, against 135 on the frozen main, and the build serves
  25 families grouping 41 codes.
- Families crowd the 2025 landscape on a phone, so the page now tries a name above and
  below its dot when neither side is free. On a phone that brought about 2 more names
  into each year, and the floor of a quarter holds without moving.
- The #380 comparison checks every family against its members. It flags two items it
  cannot sort, both checked by hand and posted on #382: Initiative's gem catalogue
  change, which comes from the ceiling and not a family, and Esper's finish point for
  Ketramose's one event, which has no member line to add up.
