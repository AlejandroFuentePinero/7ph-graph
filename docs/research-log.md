# Research Log

Cross-session insights and handoffs that have no other structured home.

## 2026-07-14 — Title placement tokens agree with stored `placement` (backfill is safe)

- Checked the full `decks.json` snapshot (4553 decks): where a deck both has a stored `placement` and a leading placement token in its title (e.g. `05th/08th ...`, `13th ...`), they agree in **4501/4501** cases. **Zero contradictions** where both are present.
- The only discrepancies are the **51 decks with a null stored `placement`**; of those, the title still encodes a recoverable placement for **19** (the rest have `??`/`XX` placeholders or no token). 26 of the 51 nulls are the `nan`-pilot decks.
- Why it matters: a future placement-completeness / era-bucketing ticket can backfill the 19 missing `placement` values straight from the title token without fear of overwriting or conflicting with real data. The consistency was verified across the whole dataset, so that verification does not need repeating.
