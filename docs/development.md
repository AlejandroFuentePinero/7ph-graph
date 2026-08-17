# Development

## Refreshing the data

`fetch` then `build` is the whole refresh:

```sh
uv run graph7ph fetch   # download 7phstats data into snapshots/<timestamp>/
uv run graph7ph build   # load the latest snapshot into data/graph
```

Each fetch is kept as an append-only snapshot, and the build folds the whole
sequence: each snapshot is gated against the accumulated union of every snapshot
before it, not just the previous one, so a rewrite buried in an interior snapshot
is caught rather than collapsed into the prior union (ADR 0008). The new artifact
is promoted only if it validates, with the previous one retained at
`data/graph.backup` for an instant rollback (ADR 0003). A build that flags dropped
ids or changed historical facts says so and writes the detail to
`data/graph/ingest.json`; a flagged immutable fact is held at its pre-change value
until a human resolves it, so the flag is an action to take, not a notice.

Then file the curation review for the round, which is where the identity work
starts (issue #227). `curation-report` is a pure read of the promoted bundle and
`gh` stays out of the build, so filing is this line rather than a build side
effect:

```sh
brief=$(uv run graph7ph curation-report) &&
  case $brief in
    "Curation review"*)
      gh issue create --title "$(head -1 <<<"$brief")" \
        --body "$brief" --label ready-for-human ;;
    *) head -1 <<<"$brief" ;;
  esac
```

The brief ranks on what changed: holds the data has now settled (decide each and
retire its `[[hold]]`), candidates this ingestion introduced, holds whose
evidence moved, then the unchanged tail and the counts. A round with none of the
first three says so in one line, and there is nothing to file, which is what the
`case` guards: the brief opens with `Curation review` only when one of the three
has something in it, and otherwise the line prints the reason rather than filing
a round nobody has to work. The command refuses to report on a bundle that is
missing, built from other sources, or older than the newest snapshot, so a brief
always describes the corpus it names.

Hold the brief before filing rather than piping it: a refusal is a sentence on
stderr and an empty stdout, and a pipe would hand that empty stdout to `gh` and
file it as the round's review. The title is the brief's own first line, which
names the snapshot the body reports on, so the issue cannot be titled after a
different one than it describes.

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
uv run playwright install chromium   # once, for the browser suite below
```

Rebuild before running the suite after any ingest, build, schema or curation
edit. Eleven tests grade the real bundle through the `live_graph` fixture, and it
*skips* rather than fails on a bundle that is missing or built from other sources
(issue #55), so on a stale bundle they pass green having graded nothing. Among
them is `test_build.py::test_nothing_in_either_identity_queue_is_left_unexamined`,
the invariant that every identity-queue entry is curated or held (issue #227): it
can only redden on the machine that ran the ingestion, and only if the suite runs
after the rebuild. CI never builds, so it always skips there.

`tests/test_graph_desktop.py` measures what the graph document actually paints at
desktop width, on a real Chromium, and holds the three regressions ADR 0018 names.
`playwright` is an ordinary dev dependency, but the browser it drives is a download
rather than a wheel, so `uv sync` cannot bring it. Until that install has run, the
suite skips itself and says so; CI runs the same install before `pytest`, so the
gate holds there whether or not a given checkout has one (issue #197).

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
before emitting, order-insensitive for the one that does not, and floats within a
tolerance, because aggregation order changes the last bits of a mean between
engines. A case also carries any whole-of-answer number its query sets, which
today is the gem list's expected-by-luck count: a claim the surface prints, and
one that can move while every drawn row holds.

A case's *parameters* are part of the oracle, so a query whose signature changes
invalidates its cases rather than regrading them: `compare` says "spec changed"
and a case the new signature cannot express reads as "in the baseline but not in
the cases being run". The gem query lost its archetype parameter at #184, which
turned its two cases into one and forced a `--force` recapture; expect the same
whenever a query's spec changes shape.

A `--force` recapture rewrites the file wholesale, in the engine's row order and
with the last bits of every float redrawn, so its diff is mostly churn and cannot
be read line by line. Review it by comparing the old and new JSON case by case
with the node and edge lists sorted: that separates the lines the change actually
moved from the reordering, and the count it leaves should match the grade printed
just before the capture. Recapture in its own commit with nothing else in the
diff, and say in the message which lines are real, or the reordering becomes a
place a regression can hide (issues #67, #165).

A change that only *adds a field* to some nodes needs neither form. Splice it
instead: take a live capture, assert every existing value, node set, edge list,
count and catalogue equal (floats within `baseline.TOLERANCE`) and that the only
difference anywhere is the new key, then write that key in, in the field order
`dataclasses.asdict` emits, so a later `--force` moves nothing. Those assertions
are the safeguard; without them a splice is a blind edit to the oracle. What comes
out is a diff of exactly the rows that gained the field, no reordering and no
float churn, which is why a splice can ride in the same commit as the change that
adds the field where a `--force` recapture cannot: the reviewer can read every
line of it. First used for the two gem node fields at issue #176 (ADR 0019).
