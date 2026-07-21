# Research Log

Cross-session insights and handoffs that have no other structured home.

## 2026-07-14 - Title placement tokens agree with stored `placement` (backfill is safe)

- Checked the full `decks.json` snapshot (4553 decks): where a deck both has a stored `placement` and a leading placement token in its title (e.g. `05th/08th ...`, `13th ...`), they agree in **4501/4501** cases. **Zero contradictions** where both are present.
- The only discrepancies are the **51 decks with a null stored `placement`**; of those, the title still encodes a recoverable placement for **19** (the rest have `??`/`XX` placeholders or no token). 26 of the 51 nulls are the `nan`-pilot decks.
- Why it matters: a future placement-completeness / era-bucketing ticket can backfill the 19 missing `placement` values straight from the title token without fear of overwriting or conflicting with real data. The consistency was verified across the whole dataset, so that verification does not need repeating.

## 2026-07-15 - The analytics layer is mostly bipartite groupbys, not graph-native

- Dropping archetype-unique-cards (see ADR 0002) surfaced a broader pattern: card usage, co-occurrence, and adoption are all two-hop bipartite aggregations (deck-card projections / groupbys). None uses paths, shared-neighbour structure, communities, or traversal beyond two hops. The graph store is incidental to them.
- This is in tension with ADR 0001, which chose Kùzu specifically for multi-hop traversal and neighbourhood rendering over relational stores. Right now only the pilot-network / head-to-head views are genuinely graph-shaped; the rest would run as well on a dataframe.
- Why it matters: before building the next analytic, ask whether it earns the graph or is another groupby in a graph costume. The unexplored, genuinely graph-native directions are traversal-based: pilot communities via shared decks/archetypes, archetype-similarity clusters from shared card neighbourhoods, cards that bridge two archetypes, multi-hop paths. That is where the store's cost is actually justified.

[handoff] Open direction, not a decision. When picking the next feature, weigh a traversal-based insight against yet another bipartite view.

## 2026-07-15 - The explorer's view-tuning arc is closed

- Everything after issue 7 closed (`bd9fe84..HEAD`, plus the hidden-gem band) was one arc: not new plumbing, but **deciding what each analytic view actually means**. Pilot identity got same-event splitting (ADR 0004); affinity gained a macro tier and head-to-head; card usage was recast as adoption rate, then re-rendered as uniform dots; co-occurrence was reworked to top-N by rate with a two-card intersection; archetype-unique-cards was **cut** rather than fixed (ADR 0002); hidden gems got a fixed, documented band (ADR 0012). Each view was taken one at a time until its definition was defensible.
- **This arc is deliberately finished, not abandoned.** Hidden gems was the last view to tune. Do not reopen view-by-view fine-tuning on a hunch: if a view's definition is questioned again, it needs a reason that its ADR does not already answer.
- The v1 epic (#1) stays open on work that is *not* view definition: deployment to a Hugging Face Space (#8), applying human pilot-identity decisions (#9), and preserving analytic metrics in query results so the v2 tool layer is not foreclosed (#12).
- Why it matters: a cold reader sees an open epic with 28 user stories and several open issues, and cannot tell which parts are settled. The views are settled. The remainder is packaging, curation, and the v2 seam.

[handoff] The bipartite-vs-traversal question above is now the live one. With view tuning closed, the next feature is a genuine choice between the open v1 remainder (#8/#9/#12) and a first traversal-native analytic; it is no longer competing with "one more pass on an existing view".

## 2026-07-20 - The golden-subgraph gate cannot be verified by its own unit tests

- `compare()` in `graph7ph/baseline.py` has 24 unit tests over synthetic subgraphs, and every one of them passed while the real behaviour was badly wrong. A version that matched rows on their raw float values reported **36 differences, listing 17 cards as both added and removed**, where the truth was 2. The synthetic fixtures had too few rows and no engine-scale float noise to expose it. Only re-running a mutation battery against the built graph caught it.
- The battery: copy `baseline/subgraphs.json`, apply one mutation, run `uv run graph7ph baseline --baseline <mutated>`. Shuffling the rows of `gems_whole_meta` or `pilot_many_events` must **pass** (they are the order-insensitive queries), and so must shifting every `mean_norm` by 5.6e-17. Reversing `cooc_pair_shared_decks` rows, removing one gem node, deleting a whole case, and shifting `mean_norm` by 8.6e-4 must each **fail**, and the removal must name the card that moved. Measured results are tabulated in the issue #45 comment thread.
- Why it matters: issues #47 through #50 are all graded by this gate, and a gate that under-reports is indistinguishable from a clean migration. Anyone editing `compare`, `_identity`, or `_same` should re-run the battery, because a green `uv run pytest` is not evidence the gate still works.

[handoff] The battery lives only in session scratch, not in the repo, because it needs the real built artifact that tests cannot reach. Rebuild it from the list above rather than trusting the unit tests.

## 2026-07-21 - `baseline --capture` is non-deterministic; never re-capture wholesale to add a case

- Adding one case to `CASES` and running `uv run graph7ph baseline --capture` rewrote **6116 lines** (3108 insertions, 3008 deletions) for a case worth 100. The gate stayed green throughout: none of it was a regression. Two independent sources of churn, both benign, both invisible in a diff that size.
- **Row order.** Confirmed by capturing twice in a row: the two files are not byte-identical. Six lists reorder, `pilot_many_events` and `pilot_head_to_head` in both nodes and edges, plus `gems_whole_meta.edges` and `gems_one_archetype.edges`. These are the `ORDER_INSENSITIVE` queries, so `compare` tolerates it by design, but the captured JSON is written in row order and therefore differs on every capture. Two captures of an unchanged graph can never be diffed byte for byte.
- **Float drift.** `gems_whole_meta` and `gems_one_archetype` returned identical rows under `_identity` with `mean_norm` moved in the last bits. Two consecutive `--capture` runs on the same Ladybug 0.18.2 binary differ by at most **5.55e-17**, which is 1 ulp at that magnitude and matches the **5.6e-17** the `TOLERANCE` comment already cites. The documented figure is correct and needs no revision. A capture compared against an older committed one can sit **1.11e-16** apart (2 ulp, one drifting up and the other down), so that is the number to expect from an arbitrary pair of captures, but it is the same phenomenon and not a larger noise floor.
- **What to do instead.** To add a case, capture into a scratch file, then splice only the new entry into the committed baseline, keyed by case name and ordered to match `CASES`. Issue #54 landed as a pure 100-line addition that way.
- Why it matters: the module docstring's promise is that "a capture is plain JSON, so the baseline is reviewable in a diff rather than an opaque pickle". A wholesale re-capture destroys exactly that, and the danger is not the noise itself but that a reviewer facing 6000 lines waves it through. A real regression committed inside that churn would be invisible, and the gate would then be green against a baseline that already encodes the bug.

## 2026-07-21 - Only a scan returns insertion order; an aggregation's row order is arbitrary

- Two shapes of query, two different contracts. A plain scan (`MATCH (c:Card) RETURN c.name`) hands rows back in the order the loader wrote them, and does so deterministically: 100 reads of the fixture graph gave **1 distinct order** for both `Card` and `Pilot`. An aggregating query (anything with a `WITH ... count(...)`) hands them back in hash order, which is unrelated to load order and **not stable between two reads on the same connection over the same graph**: 300 reads of `gem_archetypes` on 10 builds gave **7 distinct orders**.
- **What that decides.** Whether a fixture can defend an `ORDER BY` at all. Issue #56's premise, that a catalogue's `ORDER BY` goes untested because the fixture is already alphabetical and so an unordered query matches by luck, is correct for `pilot_catalogue` and `card_catalogue` and **wrong for `gem_archetypes`**. Reordering a fixture cannot defend an aggregation, because load order was never what determined the output order. The only lever there is arity: with 2 qualifying archetypes, dropping the `ORDER BY` still went green in **94 of 120** reads; at 4, in **0 of 300**. That defence is probabilistic where the other two are causal, and it weakens if those four archetypes are ever renamed.
- **A single mutation run is not evidence.** The 2-archetype version was declared verified on one red run, passed `/tdd` and `/spec-review`, and was independently re-confirmed by a review sub-agent that also ran each mutant once. It was escaping about a quarter of the time. Against a query whose row order is arbitrary, a mutant must be run to a rate, not to a verdict; the same applies to the #45 battery and to anything grading the `ORDER_INSENSITIVE` queries.
- Why it matters: this is the third finding of one family, after "the golden-subgraph gate cannot be verified by its own unit tests" (2026-07-20) and "`baseline --capture` is non-deterministic" (2026-07-21). The pattern is that a green run against this engine's row order proves less than it appears to. Before writing any test that asserts on row order, check which of the two shapes the query is.

## 2026-07-21 - A test can kill its own mutant and leave the line beside it uncovered

- Issue #58 asked for two guards in `baseline.py` to be covered, each confirmed by re-injecting one mutant. Three tests were written that way and each killed its guard. A review then found **three further mutants still alive, all inside the lines those tests had just been written against**: inverting the `'baseline' if want is None else 'capture'` ternary, swapping `want`/`got` in the "spec changed" message, and deleting the `continue` after it. Each left the suite green.
- The cause every time was an assertion weaker than the guard it pinned. `!= []` and `any(name in d for d in diffs)` are satisfied by any diff about the right case, so neither can see a report that names the wrong side. And the instinct to isolate a guard by making everything else identical (both sides given the same empty rows, so only the spec differed) is exactly what disarms the `continue` beneath it: with no rows left to grade, falling through costs nothing and the mutant survives.
- **What to do.** Mutate the whole branch, not the predicate: the condition, the message it emits with each interpolation swapped in turn, and the control flow that follows. Assert on the message text rather than on the diff being non-empty, and give the two sides different rows so that a missing `continue` surfaces as a second diff a `len(diffs) == 1` can catch.
- Why it matters: this is a **scope** failure where the entry above records a **frequency** one ("a mutant must be run to a rate, not to a verdict"). Both are ways a green mutation run claims more coverage than it has, and both got through `/tdd` and `/spec-review`. The concrete cost here is a gate that reports the right regression against the wrong side, sending whoever grades the migration to re-capture an oracle that was already correct.

## 2026-07-21 - Confirmed audit findings that were never ticketed

A full post-migration audit of the Kùzu to Ladybug arc (#45-#65) confirmed 30 findings under 2-of-3 adversarial verification. Five became tickets (#66-#73). The rest are recorded here because they are real, were verified, and otherwise live only in a discarded session transcript.

- **`requirements.txt:9`'s version-coupling promise is enforced by nothing.** It states the pins "must move with uv.lock" because a Ladybug release can change the on-disk storage format. No test, preflight or CI compares them. A three-line test against `importlib.metadata.version("ladybug")` closes both skew directions.
- **The Space's transitive dependency set is unpinned.** `requirements.txt` names three packages; `uv.lock` locks ~50. Everything gradio pulls in is re-resolved by pip on the Space at image-build time against PyPI as it stands that day. The deployed environment is not reproducible from anything in this repo.
- **Nothing gates the Space's import closure.** One new `from graph7ph.models import ...` in `query.py` pulls pydantic into the app's import path and kills Space boot with all tests green. The AST-walk technique already exists at `tests/test_provenance.py:50`, just pointed elsewhere.
- **`provenance.py:63`'s `source_digest` excludes `snapshots/`.** It folds in the six `BUILD_INPUTS` modules plus `curation/pilots.toml`, but not the snapshot data that `ingest()` unions. So a fetched-but-unbuilt snapshot ships silently past #63's staleness preflight. The sorted snapshot directory names alone would catch it.
- **`scripts/deploy_space.sh:54` discards `find`'s exit status.** `if [ -n "$(find "$DB" -name '*.wal')" ]` uses only the substitution's output, so a `find` that errors having printed nothing is indistinguishable from a clean "no WAL". The guard reports "settled" in both cases.
- **`deploy_space.sh:88` leaves `huggingface_hub` unpinned** (`uvx --from huggingface_hub`), while depending on two specific behaviours of it: `delete_patterns="*"` clearing the previous deploy, and `.gitattributes` being spared so LFS tracking survives.
- **`provenance.py:35` does not record the engine version that wrote the bundle.** `uv lock --upgrade` inside pyproject's `>=0.18,<0.19` range can ship an artifact the Space's pinned 0.18.2 may not open.
- **`app.py:40`'s view dispatch duplicates five string literals with no cross-check** and falls through to `None` silently. Renaming a `_VIEWS` key kills a whole feature with 285 tests passing.
- **`query.py:403`, single-seed co-occurrence tie-break: ~20% mutant escape rate**, no dedicated test. The two-seed twin beside it is killed 5/5.
- **`tests/test_deploy.py:135` never asserts what was staged.** Removing `requirements.txt` from the `cp` leaves all six deploy tests green.
- **`app.py:201`, Hidden gems cannot render in its default state** (342 nodes against a 250 limit) and the control that fixes it is labelled "optional".
- **`app.py:163`, ~80% of head-to-head pairs return empty** behind a generic "Nothing matched", and head-to-head is the app's initial view.
- **`render.py:48` re-transmits ~0.77 MB of inlined vis.js on every Explore click**, regardless of result size, on a free-tier Space.
- **`fetch.py:20-27`: four of six fetched files (12.5 MB, 65% of each snapshot) are read by nothing**, and a 404 on `recommendations.json` kills all data intake with a bare traceback.

[handoff] None of these is ticketed. Triage before the next deploy-touching change; the `source_digest` and `requirements.txt` items are the two that can ship a broken Space silently.

## 2026-07-21 - Audit measurements survive independent checking; audit readings of prose do not

Five tickets (#66-#70) were written from a post-migration audit, then each was handed to a fresh agent that had only the issue text and the repo. **Two came back "cannot implement faithfully", and every one of the five contained factual errors.** The errors were not randomly distributed.

- **Every quantitative claim held, exactly.** 4592 decks, 2278 deck-id inversions (49.6%), 505 decks on 55 low-confidence pilots (11.0%), the 6116-line capture churn, `mktemp -d` at `:90`. Independent re-derivation matched in each case.
- **Claims about what a comment, doc or ADR *says* failed repeatedly.** A cited coverage gap in `tests/test_deploy.py:143-146` turned out to say close to the opposite of the claim. A README citation pointed at text that `9f01450` had moved to `docs/deploy.md`. An ADR consequence proposed for addition was already at ADR 0011:65. A "~24s" runtime was 1.1s. A row count of "~8937" was ~8562.
- **The thread-safety citation was wrong for the fourth consecutive time**, in the ticket written to end the cycle of wrong thread-safety claims. Each version cited a real line in `ladybug/connection.py` that does not execute here, because the pybind branch always wins and the C-API `else:` is dead. See [[kuzu-gotchas]] and #73.
- **Two tickets' central acceptance criteria were unimplementable.** One asked for added/removed/differing diff counts that `check()`'s `list[str]` return cannot produce. One asked for a header comment edit to text that does not exist.

**What to do.** Treat an audit's numbers as evidence and its readings as hypotheses. Before a finding about prose enters a ticket, open the file and read the surrounding lines: the failure mode is a confident paraphrase of something adjacent to, but not identical to, what is written. And cold-read any ticket set before declaring it ready, because the author cannot see their own inherited assumptions.

Why it matters: this is the fifth entry in one family. The others are about tests proving less than they appear (2026-07-20 golden-gate, and three on 2026-07-21). This one is about prose proving less than it appears, and it has the same root: **this project reasons well and does not make its reasoning falsifiable.** The audit's own convergence diagnosis said so about the 20-issue migration arc, and then the audit did it too.

## 2026-07-21 - What the post-migration audit could not establish

Recorded so that future confidence claims about the Kùzu to Ladybug migration do not overreach. The audit (#45-#65, 223 agents) confirmed 30 findings, but these areas were reasoned about rather than exercised:

- **Nothing was ever run against a real Hugging Face Space.** Every deploy finding comes from reading `scripts/deploy_space.sh` and the staged tree, never from an actual `upload_folder` plus a Space boot. No one has confirmed that a venv built from `requirements.txt` alone can `import app` on Linux/cp312. The AST walk in `tests/test_provenance.py` proves the import closure, not that the three pinned wheels resolve and install.
- **~400 lines of `pilots.py` fuzzy matching were never audited**, by either the audit or the follow-up cold reads: `name_relation`, `_edits`, `_similar`, `_split_event_collisions`, `_join_identical_names`, `_collapse_identical`. This is the largest unreviewed surface in the repo and it is a `BUILD_INPUT`. #74 came out of a structural check of one of these, not a real review of any.
- **`baseline/subgraphs.json` was never read whole.** Shape and counts were spot-checked. Whether any individual golden subgraph encodes a pre-existing wrong answer is unknowable without a second oracle.
- **Every measurement holds for N=2 frozen snapshots.** Retain-old never firing, zero dead curation entries, zero orphan deck ids: none has been observed under data movement, which is exactly the condition under which the #68 and #67 risks fire.
- **Ladybug's compiled `Connection::query` is not readable.** Only the `.so` ships, so the parameterless thread-safety question cannot be closed from this repo by anyone. See [[kuzu-gotchas]] and #73.

[handoff] The first item is the cheapest to close and the most load-bearing: one real deploy to a scratch Space would settle it.
