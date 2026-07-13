# Pilot identity: upstream id as key, display name derived

We key each Pilot node on the upstream `pilot` field. Although it is often a pseudonym or handle, it is a stable identity that the source has already deduplicated across events. The real name is recovered from the deck title into a separate Display Name (majority vote per pilot, with fuzzy consolidation of spelling variants).

We deliberately do NOT key pilots on the recovered name. Title names carry typos and spelling drift, which would split one player into several nodes, and they are not unique, which would merge two different players into one.

## Consequences

A small reconciliation report is produced at ingestion for the cases the data cannot resolve on its own: a handful of variant clusters to canonicalise, roughly two candidate under-merges (one display name spread across two pilot ids), and 26 decks filed under a null pilot id, which are re-keyed as low-confidence per-name pilots rather than kept as one bogus node. These are flagged for human review, never merged automatically.
