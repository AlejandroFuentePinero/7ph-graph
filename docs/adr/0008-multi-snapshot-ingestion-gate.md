# Multi-snapshot ingestion: the gate folds every transition, retains old on a flag, and promotes one bundle

ADR 0003 promises the superset gate flags "a missing id or a changed fact for review rather than silently absorbing it". That promise held for a single fetch but broke once snapshots accumulated between builds, which is the normal case: `fetch` and `build` are decoupled and 7phstats lags upstream by roughly a week, so several snapshots routinely pile up before a build runs. This ADR restores the promise for a multi-snapshot build and settles two questions ADR 0003 left open: what a flag does, and what "atomic promotion" means.

## The gate folds across every adjacent transition, not just the last

The old build gated `union(s0..s_{n-1})` against `s_n`: one comparison, the newest fetch against everything held before it. With three snapshots `s0(pilot=alice)`, `s1(pilot=bob)`, `s2(pilot=bob)`, that compares `union(s0,s1)=bob` against `s2=bob`, matches, and promotes clean. The `alice -> bob` rewrite of an immutable fact, buried in the interior transition, is collapsed into the prior union and never seen. This is a structural property of a single-transition gate, not a data quirk: any change not present in the very last transition is invisible to it.

The gate now folds across the sequence, gating each snapshot against the union of everything held before it and accumulating flags (de-duplicated by kind, entity, and id). The prior side is the accumulated union, not the raw previous snapshot: because the union retains a dropped id at its old value, a fact that changes while its id is briefly absent (dropped by one fetch, back in the next) is still caught as `changed`, where a raw adjacent-pair comparison would see only a plain drop then a fresh addition and miss the rewrite. An immutable-fact rewrite in *any* snapshot is flagged, interior or not. The union it builds is unchanged: `union_snapshots` is an oldest-to-newest overwrite by stable id, so folding the whole sequence yields the same superset the old two-step path did. Only the flagging is stronger. On the current two-snapshot set the fold produces zero flags and a byte-identical union.

## A flagged immutable fact is retained old, not silently rewritten

ADR 0003 flagged a changed fact but still let the union take the newest value, so the live graph changed before a human read the flag, and any curation scoped to the old identity was stranded. The contract is now **retain-old**: an entity whose immutable projection changed is pinned in the union to its first-seen (pre-change) record until a human resolves it. We keep the fact as we first knew it and leave the flag standing, rather than guess-merging or blocking the build.

Retain-old, not block-until-acknowledged, because it matches how this project already curates: hard facts apply automatically and ambiguous identity decisions defer to the growing curation dictionary (ADR 0004, 0007) without ever guess-merging. A rewrite of an immutable fact is exactly such an ambiguity, so the build defers it: it keeps the old value, flags it, and stays green. Blocking would freeze every build behind a manual acknowledgement and needs an escape hatch that retain-old does not. Volatile fields (points, price, tags) ride along frozen on a contested record; that is acceptable because the record is under review anyway. A `dropped` id is *not* pinned: it is the benign windowing case ADR 0003 already handles, where the union retains the record from the last snapshot that held it and the build promotes.

Retain-old only changes graph content when a change is flagged, and the current snapshot set flags nothing, so today's graph is unchanged.

## Promotion is one atomic bundle: the reports live inside the graph directory

The build writes the graph and two sidecar reports (`reconciliation.json` from the pilot resolution, `ingest.json` from the gate). These used to be siblings promoted by three independent renames, so a crash between them could pair a new graph with stale reports. The reports now live *inside* the graph directory rather than beside it, so the single directory rename that promotes the graph promotes both reports with it, as one atomic unit. There is no interleaving to interrupt: after any crash, the live graph and its reports are the same generation, and the retained `.backup` directory carries its own matching reports for a self-consistent rollback. Kùzu ignores files in its database directory that are not its own, so the reports sit there without disturbing it.

## Consequences

- `reconciliation_path` and `ingest_report_path` now resolve *inside* the graph directory (`<db>/reconciliation.json`, `<db>/ingest.json`), not as siblings (`<db>.reconciliation.json`). Any tooling that read the sidecar paths must follow them in. The previous sibling files from an older build are stale and can be removed.
- The build stays green on a flagged immutable-fact rewrite, holding the old value; the flag in `ingest.json` is the signal to curate. Resolving it is a human step (correct the source or add a curation entry), after which the fold stops flagging it.
- The gate is still stateless over the snapshot set on disk: no last-seen ledger is persisted. Folding the ordered snapshots reconstructs every transition on each build, which is enough while the full snapshot history is retained.
