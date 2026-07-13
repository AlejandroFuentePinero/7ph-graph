# Data sources and ingestion: 7phstats primary, our store is the system of record

7phstats (static JSON served at `/data/*.json`) is the primary source. It is a heavily enriched derivative of the Moxfield `7PointEventResults` account and the only source of archetype, macro, and points intelligence. It already carries decklists and, in deck titles, the real pilot names. Scryfall is a future card-attribute enrichment (colours, images, oracle text) joined on canonical card name; it is not used in v1. Moxfield is a sanctioned secondary source, accessed with a user-agent credential under non-commercial, one-request-per-second terms, held for freshness of the newest events, filling attribution gaps, and independence; it is not wired into v1.

Ingestion is a full rebuild, but our store is the system of record, not a mirror. Each fetch is saved as an append-only timestamped snapshot, and the build unions all snapshots by stable id, so data that leaves the source (for example if it starts windowing) is never lost. A gate asserts each new snapshot is a superset of what we already hold: snapshots are schema-validated, and each entity's immutable projection (its historical facts, such as a deck's pilot, event, placement, and decklist) is hashed so a missing id or a changed fact is flagged for review rather than silently absorbed. Volatile fields (points, price, tags) are taken latest. Promotion of the rebuilt store is atomic, with the previous version retained for rollback.

## Consequences

- The project is permanently non-commercial for as long as it uses Moxfield data.
- 7phstats lags the upstream Moxfield account by roughly a week for the newest events. Accepted for v1; a Moxfield leading-edge fetch is a later option if day-one freshness becomes a requirement.
