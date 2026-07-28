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

## 2026-07-22 — `gr.Plot` surfaces no hover/click to Python; series-highlight needs the iframe+JS route

- The Trends tab (ADR 0013, #78) renders its Plotly charts through Gradio's `gr.Plot`, which exposes **only a `change` event** to Python: no hover, click, or select. So the "hover a series, dim the others" interaction cannot be wired through Gradio at all, and even if it could, a server round-trip per hover would lag. The only real path is client-side JS bound to `plotly_hover`/`plotly_unhover` on the Plotly div, which `gr.Plot` gives no access to.
- The way to get it is to stop using `gr.Plot` and instead embed a Plotly HTML document in a `gr.HTML` iframe (the same `_embed` pattern the app already uses for pyvis) with a small JS handler. Rejected for #78 because it ships plotly.js (~3.5 MB inlined per render, or a CDN dependency the offline/CSP setup avoids), and it is a foundational rendering change all four trend tabs inherit. Built-in Plotly legend double-click-to-isolate covers the "focus one series" need for free in the meantime.
- Why it matters: #79/#80/#81 reuse this exact `_trend_figure`/`gr.Plot` surface. Anyone asked for chart interactivity there will re-hit this wall and re-weigh the same payload trade-off unless they read this first.

[handoff] If hover-highlight is ever wanted, do it once in `_trend_figure`'s render path (iframe + plotly.js + JS), not per tab.

## 2026-07-23 - A trend defect has two layers, and fixing one does not fix the other

ADR 0013 gives the trend tools one aggregation layer with two consumers: the tool returns a `Series` an agent reads as numbers, and the trend tab draws it. That split means a guard's *data* behaviour and its *rendered* behaviour can diverge badly, and #101 showed the divergence is not a corner case.

- **A measured claim in a ticket can be right about the data and wrong about the picture.** #101 correctly measured that 7 of 238 drawable pilots have a below-floor year inside their drawn span, then inferred the chart drew a straight line across it. It did not: `_performance_figure` already spanned the drawn years and paired missing ones with `None`, so plotly broke the line. Meanwhile the real defect was the 83 refused cells falling *outside* the span, erased from the x-axis entirely across 74 pilots, which the ticket never mentioned. Fixing what the ticket described would have been close to a no-op.
- **It happened twice in one session.** After the tool was fixed to return a refused year as a cell (`mean_norm=None` plus the event count), the chart still drew that year and a year the pilot sat out as the same blank tick, throwing away the very count that made the refusal legible. The tool was honest and the picture was not.

Why it matters: the same `Series` plus `gr.Plot` surface backs all four trends, so any future claim of the form "this trend hides X" has to be checked at both layers before it is acted on. Read the figure function, not only the tool, and confirm what a reader actually sees. `.venv/bin/python` plus `fig.data[0]` and `fig.layout.annotations` reads a built trace back without launching the app, which is how both of the above were settled.

## 2026-07-25 - The golden oracle is red on `main` until #140's field-size correction is signed off

Issue #140 (field-size correction, ADR 0015) re-ranks 54 decks' `placementNorm`, which moves two cards across the hidden-gem band. `uv run graph7ph baseline` therefore reports **170 differences against `baseline/subgraphs.json`, all inside `gems_whole_meta`, with zero removals**: Yawgmoth's Will and Fallen Shinobi plus the decks and edges they drag in, and slightly better means on the gems already there. This is the intended change, not a regression.

- **The gate stays red until the oracle is recaptured, and that blocks deploys, not just CI.** `scripts/deploy_space.sh` refuses on a failed baseline gate, and CI only runs pytest, so nothing else flags it. Anyone who builds and tries to deploy will hit a refusal that looks like a regression and is not.
- **The usual remedy does not scale here.** [[oracle-node-property-recapture-noop]] says to hand-edit `subgraphs.json` rather than `--capture`, because a recapture rewrites the file in a new engine row order and buries a one-node change in a ~3000-line reorder diff. That advice was written for a change touching one or two nodes. At 170 entries hand-editing is not viable, so this is the first case that genuinely forces `baseline --capture --force` and eats the reorder churn.
- Why it matters: the choice is not "which is cleaner" but "which is possible", and the reorder diff that results is expected noise rather than evidence of a second change. Re-deriving this costs a full build plus a grade.

Resolved by #165 (2026-07-26): #140 was signed off, #164 added 4 more differences on top of the 170, and the oracle was recaptured at `7cf02bc`. A non-zero grade on a clean tree is a real regression again. The entry below records how the 174 were split before the recapture, which is the method to reuse if the debt ever reaccumulates.

## 2026-07-26 - Attributing a debt-carrying oracle, and reviewing a wholesale recapture

The entry above left `main` grading 170 differences; #164 took it to 174. #165 cleared it, and the two techniques that made that safe generalise to any oracle debt.

- **Attribute by grading the same oracle at the revision before each suspected change, not by trusting what the ADRs claimed.** Build at each revision and call `baseline.check` directly, since the CLI refuses a bundle whose stamp is not the working tree's. #165 measured 0 at `9eb4a31` (pre-#140), 170 at `f936bdc` (pre-#164) and 174 at `7cf02bc`. **The zero is the load-bearing measurement**: it proves nothing earlier contributes, which no amount of reading the two ADRs can establish. Grading an old-code graph with today's harness is only honest once `git diff <rev> HEAD -- src/graph7ph/baseline.py src/graph7ph/query.py` comes back empty or purely additive, as it did here. A build is ~25s, so the whole exercise is minutes; the worktree and `PYTHONPATH` setup is in [[graph-diff-against-main]], and skipping the `PYTHONPATH` silently grades your own code twice.
- **Round the floats before diffing two grade outputs as text.** The grade lines carry full float repr, and two builds agree on a `mean_norm` only to engine noise (~1e-17, well under the harness's 1e-9 tolerance), so an exact string comparison invents differences the harness itself calls equal. #165's raw set-difference read 12 head-only and 8 before-only lines; after `re.sub(r"\d+\.\d+", lambda m: f"{float(m.group()):.12g}", line)` the true answer was 6 and 2, netting the +4 that was actually there.
- **A wholesale recapture is reviewable, just not line by line.** The 2026-07-21 entry above says never to recapture wholesale, and that still holds *for adding a case*, where splicing works. When the blast radius genuinely forces `--force`, review the result by comparing the two JSON files case by case with the node and edge lists sorted, which separates the real lines from the row-order churn. #165's `+4228 / -3275` decomposed into: `counts` and `catalogues` byte-identical, 13 of 17 cases byte-identical, the two order-insensitive pilot cases reordered, `gems_one_archetype` moving two floats by ~5e-17, and only `gems_whole_meta` carrying the 174 real lines. Stating that decomposition is what stops a reviewer waving 7500 lines through.

Why it matters: the 2026-07-21 entry warns that a real regression committed inside recapture churn would be invisible and the gate would then be green against a baseline encoding the bug. That risk is the reason a recapture wants its own ticket with nothing else in the diff, and the per-case comparison is the check that discharges it.

## 2026-07-27 - #167 leaves nine stale "of 108 events" claims outside `build.py`

[handoff] Returning the orphan deck to CBR3 (#167) dissolves the phantom `nan` Event, so **the graph holds 107 events, not 108**. This repo writes counted claims into docstrings and comments as load-bearing evidence, and nine of them still say 108. `build.py`'s own four were re-measured and fixed in #167; these were deliberately left out rather than widen a curation diff into three unrelated modules.

The exact sites:

- `ingest.py:139` - 6 of 108 events sit within a week of a New Year
- `trends.py:237` - own `eventSize` at 99 of 108 events, corrected field at the 9
- `trends.py:239` - 36 of 108 events carry a published entrant count
- `trends.py:243` - exceeds the decks-at-event count at 58 of 108
- `trends.py:255` - all 108 events carry a field size
- `trends.py:298` - 21 of 108 spread over more than one date
- `trends.py:839` - disagreed with the stored field at 3 of 108 events
- `app.py:1420` - pilots who entered at 10 of 108 events
- `app.py:1540` - Grixis and Lands at 59 of its 108 events

**Do not just decrement the denominators.** Some of these are fractions whose *numerator* may or may not have counted the phantom event, and the two cases need different edits. `build.py`'s four all turned out to be denominator-only (the year dicts still agree, the field rules still fire at 9, no event's decks disagree on size or type), but that was established by measurement, not assumed, and it does not transfer: the phantom carried one deck, one date, a null `eventId` and a bogus `eventSize` of 38, so it plausibly sat inside some of these numerator sets and outside others. `trends.py:237` is the clearest example, since its 99 and 9 sum to 108 and only one of the two can absorb the loss.

Why it matters: a wrong count in this repo reads as a measurement that no longer reproduces, which is exactly the signal the counted-claim convention exists to give. Re-deriving the list costs a grep; re-deriving which are fractions and which are denominators costs a build plus a query per site.

Resolved the same day. **The warning was worth heeding: two of the nine moved their numerator, and one moved it by two.** Each claim was re-measured against the pre-#167 build first, and only a measurement that reproduced the documented number was trusted to produce the new one, which caught two misreadings of what a claim was even counting before either could be written down.

- `trends.py:243`, the field exceeding the decks-at-event count, went **58 of 108 to 56 of 107**. Two events left the set, not one: the phantom disappears, and CBR3's claimed 63 stops exceeding its deck count once that count reaches 63.
- `trends.py:239`, the field falling back to the last recorded placement, went **71 of 71 to 70 of 70**. The phantom was inside that set, its bogus `eventSize` of 38 being its own placement copied into the size field, so it matched the rule it was evidence for.
- The other seven moved denominator only: `ingest.py:139` (6), `trends.py:237` (98 of 107, the 9 corrected unchanged), `trends.py:255` (all), `trends.py:298` (21), `trends.py:839` (3), `app.py:1420` (10), `app.py:1540` (59).

Two claims did not mean what they looked like, and guessing would have written a wrong number into a docstring that reads as measured fact. `app.py:1540`'s "Grixis and Lands, 59 of its 108 events" counts events where both are the **primary** tag (85 and 67 events respectively as primary, 91 and 69 if any engine tag counts, 59 where both are primary). And `trends.py:239`'s "71 of 71" has a denominator of 71 where 72 events carry no `players` field, because one of those 72 records no placement at all and so cannot be evidence either way.

## 2026-07-27 - A gated statistic can be less honest than the plain one it replaces

#175 gated three surfaces that were reading a mean `placementNorm` as a settled value. Two of the three gates shipped as specified. The landscape's did not, and the reason generalises past that chart.

Its AC asked the caption to count only the dots whose interval clears the 0.5 line: "N of 25 clearly above". Built that way it printed **"1 of 25 clearly above"** beside a picture in which twenty dots plainly sit above the line. The number was correct and the sentence was unreadable, because a reader resolves a caption that contradicts the chart by disbelieving the caption, and then disbelieves the honest parts too. It now reports both counts, the plain one leading and the gated one qualifying. The gate and the evidence are unchanged; only which number leads moved.

The transferable rule: **a gate that removes an overclaim can install a different one if its number visibly disagrees with what the surface shows.** Report the number the reader can see, and let the gate qualify it. Check a gated claim against the drawn artifact before shipping it, not only against the arithmetic.

**#176 (the hidden gem band) is the open sibling of exactly this class** and will face the same choice, on the same kind of surface. Its threshold crossing is a coin flip for most of its members, so the obvious remedy is to gate the band's membership claim. Weigh reporting both there too.

Two smaller things the ADR does not carry. The permutation practice ADR 0017 sets is worth running against the *built* surface and not only the plotted quantity: it is what surfaced that 98.7% of the pilot chart's adjacent-year pairs overlap, which is the finding drawn rather than a number in a ticket. And a normal-approximation sign test needs its continuity correction at these sample sizes: without it the gate certified 52 distinct counts at n <= 60 whose exact binomial p is 0.10 or worse, "3 of 3" among them, every disagreement falling the unsafe way.

## 2026-07-27 - The landscape's y-axis squash was a height problem, not a range problem

#175 closed with an open note: ranging the landscape's y-axis over the whiskers rather than the dots drops the dots' own span from 62-79% of the frame to 28-56%, "worth revisiting if the crowding proves worse in use than the honesty is worth". It did prove worse in use. The lever was not the range.

The frame itself was the problem. At Plotly's 450px default the chart's own furniture (the share range filter's band, the tick labels, the axis title) claimed 166px and left the dots 276, so 28-56% was 28-56% of a frame that had already been eaten. Raising the figure to 640 and letting Plotly's autoexpand size the bottom margin leaves 492px, and the whisker-inclusive range now reads without giving anything back.

Why it matters: the recorded remedy on #175 points at the y-range, and acting on it would trade away the honest range (a clipped interval misstates the evidence) to buy height that was already there for the taking. **The range decision stands. Do not reopen it for crowding.**

[handoff] Uncommitted at the time of writing, on top of `34819bf`: `_LANDSCAPE_HEIGHT`, plus a fixed-pixel range-filter band in `app.py`. Prune this line once it lands.

## 2026-07-28 - Card choice barely moves a deck's finish, which bounds what this corpus can ever support

The 2026-07-27 entry above predicted #176 would face the same gate-versus-report choice as the
landscape. It did, and it shipped the qualification. What working it also turned up is larger
than that ticket, and it constrains any future feature that tries to say a *card* is good.

Fitting a one-way random-effects model over every card in the graph: within-card sd **0.2994**
against a between-card sd of **0.0385**. Refit inside archetypes, so the archetype's own level
cannot inflate the spread, the gap widens: within **0.2872**, between **0.0258**, eleven times
more noise than signal. That sets `k` around 124, meaning a card needs roughly 124 decks before
its own record outweighs simply knowing it is a card. The best-attested rare card anywhere in
the corpus has 21.

**Consequence: no estimator recovers per-card performance here, and five separate routes were
measured to confirm it** (absolute bar, within-archetype normalisation, scale flipping,
top-deck enrichment, pilot endorsement). Every one landed at or below chance. Restricting to
above-median pilots first makes it slightly worse, not better, because a top-quarter cut is
relative: removing weak pilots moves the goalposts by the same amount it removes noise. Full
numbers on #184.

**The sibling constraint, which explains the rest:** rarity in this data is anti-correlated
with quality. 115 rare cards are played by significantly *worse* pilots than chance against 36
expected, and cards in <=5% of an archetype's decks turn up in its best decks at **0.68x** the
chance rate. A rare card here usually marks someone brewing, not someone who found tech. This
is the other side of #175's finding that pilot level is the strongest reliable signal.

What does remain sayable is a **counting fact plus its luck expectation**: "this card is in 6
of its archetype's 7 top-third decks, and about 8 of the 31 cards shown would look this good by
chance". That claims concentration, not quality, and it is verifiable rather than inferred.
Anything phrased as "this card performs well" is not supportable on this corpus and should be
challenged at design time rather than at review.

[handoff] #176 is complete but **uncommitted**, awaiting maintainer approval, with
`docs/adr/0019-...md` untracked. Note before committing: ADR 0019's shrunk posterior is already
superseded by #184, which deletes `gem_prob` and `_card_spread`. Shipping it is still right (it
makes the live tab honest today), but it lands knowing part of it is scheduled for removal.
Prune this line once #176 lands.
