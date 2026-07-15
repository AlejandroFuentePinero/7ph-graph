# Pilot identity: upstream id as key, display name derived

We key each Pilot node on the upstream `pilot` field. Although it is often a pseudonym or handle, it is a stable identity: the same string always refers to the same source record. The real name is recovered from the deck title into a separate Display Name (majority vote per pilot, with fuzzy consolidation of spelling variants).

We deliberately do NOT key pilots on the recovered name. Title names carry typos and spelling drift, which would split one player into several nodes, and they are not unique, which would merge two different players into one.

## Same-event collisions: the id is not one-person-per-id

The upstream id is stable but not reliably one person. The source does not deduplicate ids across people who share a handle or name, so a single id can hold several decks at the same event (62 (pilot, event) pairs collide, up to 8 deep). By our domain (a Deck is one pilot's single entry at one event), those are distinct people sharing an id, not one person with several lists.

So resolution enforces one deck per (pilot, event): within each event a pilot's decks are ordered (best placement first, deck id to break ties) and dealt one per identity, spinning the id into `<name> 1`, `<name> 2`, ... Identity 1 keeps a full one-per-event record; only genuine same-event duplicates overflow. The whole split family is marked low confidence and surfaced in the reconciliation report.

The cross-event assignment is an inference we cannot confirm: we do not know which `Dan S` at one event is the same person as which `Dan S` at another, so non-colliding decks all pool onto identity 1 and overflow decks pool by placement rank. It is deterministic and honest (low confidence), not correct. We rejected minting a fresh singleton per overflow deck: it would fragment one likely-person's history into dozens of nodes and reuse a name like `Dan S 2` for unrelated people across events.

## Consequences

A small reconciliation report is produced at ingestion for the cases the data cannot resolve on its own: a handful of variant clusters to canonicalise, roughly two candidate under-merges (one display name spread across two pilot ids), 26 decks filed under a null pilot id (re-keyed as low-confidence per-name pilots rather than kept as one bogus node), and the same-event splits above. These are flagged for human review, never merged automatically.
