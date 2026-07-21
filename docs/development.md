# Development

## Refreshing the data

`fetch` then `build` is the whole refresh:

```sh
uv run graph7ph fetch   # download 7phstats data into snapshots/<timestamp>/
uv run graph7ph build   # load the latest snapshot into data/graph
```

Each fetch is kept as an append-only snapshot, the build unions every snapshot
and gates the newest against what the graph already holds, and the new artifact
is promoted only if it validates, with the previous one retained at
`data/graph.backup` for an instant rollback (ADR 0003). A build that flags
dropped ids or changed historical facts says so and writes the detail to
`data/graph/ingest.json`.

Restart any `graph7ph app` that was already running: it keeps serving the old
data, silently. Promotion renames the live directory, so the running app's open
files still point at the previous artifact, and the dropdown catalogues are read
once at startup, so new pilots and cards would be missing from them regardless.
The deploy path handles this on its own, since the Space restarts on upload.

Fetch and build are the only steps that talk upstream. Any credential they need
belongs to this pipeline environment (a local `.env`, which is gitignored, or the
CI secret store later): it is never read by the app and never deployed with it.

## Tests

```sh
uv run pytest
```

## No-regression gate

`baseline/subgraphs.json` records what every query entry point answers, plus the
18 table counts and the dropdown catalogues, captured from the built graph. It is
the oracle the Ladybug migration is graded against (issue #45):

```sh
uv run graph7ph baseline                    # grade the built graph, non-zero on any difference
uv run graph7ph baseline --capture          # rewrite the baseline, but only if it still matches
uv run graph7ph baseline --capture --force  # rewrite it even though it differs
```

`--capture` grades against the existing baseline first and refuses to overwrite
one that differs, printing the number of diff lines and telling you to pass
`--force`. That is deliberate: the oracle goes red *wholesale* the moment a
`fetch` moves the data, and its diff is too large to read, so a blind recapture
would rubber-stamp any real regression riding along with the data move (issue
#67). `--force` recaptures anyway, and still prints the count it overrode. With
no baseline on disk, `--capture` just writes. A baseline that exists but cannot
be graded (a corrupt oracle) is a refusal too, not a clean slate: pass `--force`
to replace it.

Both forms refuse outright on an artifact the working tree has moved past. The
gate re-runs the queries live, so a query change is graded honestly against any
artifact, but ingest, build, schema and curation changes live *inside* the
artifact: change one, skip the rebuild, and a gate that graded anyway would
report "no regression" about code it never ran (issue #55). Each build stamps
`data/graph/provenance.json` with a digest of the sources it was built from, and
the gate compares that against the sources standing here. Rebuild to clear it.

Rows are compared under each query's own rule: order-exact where the query sorts
before emitting, order-insensitive for the two that do not, and floats within a
tolerance, because aggregation order changes the last bits of a mean between
engines.
