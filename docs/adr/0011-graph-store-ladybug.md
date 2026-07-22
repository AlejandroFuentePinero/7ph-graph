# Ladybug as the graph store

Supersedes ADR 0001. That ADR chose Kùzu, and the choice is now void: Kùzu Inc.
archived the entire `kuzudb` GitHub org on 2025-10-10 (all 16 repos), final
release `kuzu 0.11.3`, and the company was acqui-hired by Apple (reported
February 2026). There is no successor product, no foundation transfer, and no
further releases, ever. We move to **Ladybug** (`ladybug` on PyPI,
github.com/LadybugDB/ladybug), the active MIT-licensed fork that continues Kùzu.

The evidence below was gathered from the PyPI JSON API, the GitHub REST and
GraphQL APIs, and official documentation, in July 2026. It is recorded here so a
future reader re-opening this decision does not have to re-derive it.

## The criteria

These come from our actual constraints, not from a generic feature comparison.

- **In-process embeddable.** The Hugging Face Space runs a single Gradio process
  and serves a prebuilt artifact. Anything needing a server or a sidecar is
  disqualified. This was ADR 0001's original reason for Kùzu and it still holds.
- **File-backed artifact** that can be built offline, uploaded, and opened
  read-only.
- **Maintained Python wheels for 3.12+.** This is the failure mode that forced
  the decision, so it is the primary filter.
- **Parameterized queries, bulk load, and read-only concurrent access** while a
  separate build process writes.
- **Not a single-vendor abandonment risk.**
- **Preserve the Cypher surface** if possible. The whole query library is written
  in Cypher, and Cypher is the intended target for the v2 agentic RAG (issue #13).

## Why Ladybug

- PyPI `ladybug` 0.18.2, released 2026-07-15. Wheels cp310 through cp314,
  `requires_python >=3.10,<3.15`. MIT, retaining the original Kùzu copyright.

  Note (issue #48, found while implementing the swap): that wheel coverage is
  Python coverage, and the macOS floor moved with it. Ladybug 0.18.2 publishes
  only `macosx_15_0` wheels where `kuzu` 0.7.1 published `macosx_11_0`, so macOS
  12, 13 and 14 lose a binary install and fall back to a C++ source build. That
  is the same failure shape this ADR's "Migration urgency" section names as the
  eventual cost of staying on Kùzu, arriving now on older macOS instead of later
  on Python 3.14. It does not affect the Space (Linux, cp312 manylinux, glibc
  2.27+) and did not affect the machine the migration was run on, so nothing in
  the build or the test suite detects it. It binds only if a contributor runs an
  older macOS; revisit the pin if that happens.
- 392 commits and 17 distinct contributors in the trailing 90 days; repo pushed
  2026-07-19.
- A 35-repo org with bindings for eight languages, a docs site, packaging,
  nightly builds, and a real release train (0.15.3 through 0.18.2 since April).
- Ships a cp312 manylinux wheel, exactly our Space target.
- Still embedded, serverless, file-backed, Cypher. Our schema and every query
  survive unchanged.

## The decision was verified before commitment

A throwaway spike built the real graph on Ladybug 0.18.2 and compared it against
Kùzu 0.7.1, so this is not a decision taken on documentation alone:

- All 18 build counts identical on the real snapshots; `reconciliation.json` and
  `ingest.json` byte-identical.
- 30 query specs across every entry point returned identical node and edge sets.
- The 18-statement schema DDL parsed verbatim, including the Kùzu-era
  reserved-word workarounds.
- Build time unchanged (around 25s); artifact size dropped from 57M to 19M.
- Kùzu's open defect #3295 (read-only alongside read-write) does not reproduce.
- Two Kùzu workarounds became unnecessary: parameterized `LIMIT` and the
  empty-list parameter. The all-null batch column was not a third: #52 (`0d9f5e7`)
  investigated the `priceUsd` padding and found it traces to the original scaffold
  commit, copy-paste rather than a workaround.

The one behavioural difference found was floating-point noise in `avg()`, of
magnitude 5.6e-17, which changed no result. It is documented in issues #45 and
#49.

## The residual risk, and its review trigger

Ladybug's principal maintainer authored roughly 80% of commits. The headline
contributor count is inflated by inherited Kùzu git history, so the genuinely
active base is smaller than it appears. Two independent investigations converged
on this.

**Re-check annually: Ladybug's release cadence and contributor concentration.**
That is the one signal worth watching, and the trigger for re-opening this ADR.

The mitigation is partial, not a single clean seam. `run_query` in
`graph7ph.query` dispatches the five subgraph specs (a `match` over
`PilotNeighbourhood`, `CardUsage`, `CardCooccurrence`, `HiddenGems` and
`PilotAffinity`), so those move at a known cost. The dropdown catalogues do not
route through it: `gem_archetypes`, `pilot_catalogue` and `card_catalogue` are
imported and called directly from `app.py` and `baseline.py`, and the Cypher is
written inline in each query function rather than behind an engine-neutral layer.
A later move to a different engine touches all of those, not just `run_query`.
**DuckDB is the designated fallback** if the maintainer risk materialises.
Choosing Ladybug now defers that cost rather than doubling it, and may avoid it
entirely.

## Alternatives rejected

| Candidate | Reason |
| --- | --- |
| DuckDB (plain SQL) | Strongest governance available: Dutch non-profit foundation, no VC, 341 contributors, monthly cadence plus an LTS line, cp310-cp314. Plain SQL genuinely covers our workload (no variable-length paths, max 3 hops, binary relations that are ordinary junction tables). Rejected because it costs a full rewrite of the query library and forecloses graph traversal for v2, for a risk reduction we can revisit later at the same price. Remains the designated fallback. |
| DuckDB + duckpgq | The SQL/PGQ extension would have kept graph-pattern syntax, but it does not support `OPTIONAL MATCH`, which our query library uses. That is a direct feature regression on the axis we promised not to regress. It also has no tagged releases, around 81% of commits from one researcher, a self-declared status as an ongoing research project, and DuckDB's own docs currently instruct pinning DuckDB back to 1.4.4 to use it. Adopting it would swap one single-party dependency for another. |
| bighorn (Kineviz) | Dead on arrival. Forked 2025-10-10, last push 2025-10-11, one day of work. No releases, no PyPI package, description still pointing at kuzudb.com. Kineviz themselves abandoned it and now work downstream of Ladybug. |
| RyuGraph | Another Kùzu fork, MIT, but lagging badly: last PyPI release 2025-12-06, repo last pushed 2026-01-20, no cp314 wheels. |
| Vela-Engineering/kuzu | One contributor, no PyPI package, commit-hash-suffixed tags. Would mean compiling C++ from source inside the Space. |
| pyoxigraph | Genuinely viable on the mechanics (SPARQL, cp314 wheels, read-only store, fast bulk load, healthy project). Rejected because it means remodelling the entire graph into RDF triples for no gain. |
| SurrealDB | Genuinely embeddable, but BSL-1.1 (not open source, vendor-controlled terms) and single-vendor VC-backed. Would repeat the Kùzu failure mode with a worse licence. |
| CozoDB | Abandoned. Last release 2023-12-11, no commits since 2024-12. |
| FalkorDB, Apache AGE, Memgraph, TerminusDB, ArcadeDB | All require a server process. Disqualified by the single-process Space constraint. |
| Neo4j | Embedded mode is a JVM API. The Python package is a Bolt network driver requiring a server. |
| Raphtory | GPL-3.0 and cp311 wheels only. |
| rustworkx, NetworkX, igraph | No query language and no persistence layer; the v2 RAG would become text-to-Python rather than text-to-Cypher. igraph is additionally GPL-2.0. |

## Why not simply upgrade Kùzu to 0.11.3

Worth recording, because it is counterintuitive. Every serious open corruption
bug in Kùzu (#6045, #6040, #5286) sits in the vector-index and full-text-search
extensions, which are 0.8.0+ features that do not exist in 0.7.1. The 0.7.1 core
storage engine is not implicated in any of them. Upgrading would cost a full
rebuild across five storage-format bumps (v34 to v39; 0.11.3 hard-refuses a v34
file) and gain nothing we need. There are no CVEs. The 307 open issues are frozen
permanently.

## Migration urgency

Low, and stating it here keeps the decision from being misread as an emergency.
We pin `kuzu==0.7.1` on Python 3.12, which has wheels on every platform we use.
The hard floor is Python 3.12's EOL on 2028-10-31. The first real break is moving
this project to Python 3.14 on macOS, where no Kùzu wheel exists at any version.
Kùzu declared no upper Python bound, so that break arrives as a failed C++ source
build rather than a clean resolution error.

## Consequences

ADR 0001's reasoning is preserved, not reversed: embedded, file-backed, Cypher,
in the same process as the app, with pyvis still rendering the subgraphs pulled
from the store. Only the implementation of that choice changes. The schema, the
query library, and the artifact contract carry over.

ADR 0002 describes derived relationships as "a growing library of parameterized
Cypher query functions". That wording was checked against this decision and
remains accurate, since Ladybug keeps the Cypher surface. ADR 0002 is left
unchanged.
