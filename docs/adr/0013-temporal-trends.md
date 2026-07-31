# Temporal trends: a Series return type, a decoupled trend tab, and four agent tools

> Amended by issue #145: a fifth tool, `archetype_landscape`, joins the four ([amendment](#amendment-a-fifth-tool-archetype_landscape-and-a-floor-on-the-field-rather-than-the-value-issue-145)).

> **Amended by [ADR 0022](0022-an-archetypes-finish-is-read-only-from-events-that-published-a-field.md).**
> 26 of the 107 events published a top-8 bracket rather than standings, and the two
> archetype surfaces below now read no finish from one. Every figure here counting an
> archetype's events or points is therefore stated against the wider pool: Grixis
> attended **74** events and Jund **61**, sharing **55**, not 85 and 73 sharing 62;
> **87%** of `(archetype, event)` points rest on one to three ranked decks, not 88%;
> and **102** of the 121 headlines are splits a fair coin produces easily, not 100. The
> landscape's caption counts move with it, from 17-to-20 above the line and 1-to-3
> settled to **14-to-19** and **0-to-2**. The reasoning is unaffected, and the share
> axis is untouched.

ADR 0006 gave the graph a `Year` dimension but nothing traversed it. This ADR decides what does. Four analytics were grilled as candidate trends: archetype share (the meta), card adoption, pilot performance, and pilot head-to-head. All four are kept, but as a new kind of result that the existing graph seam does not carry.

## A trend is a Series, not a Subgraph

ADR 0002 says query functions return a `Subgraph` the renderer draws. A trend is a series (a value per year, or a value per event), which is neither a node nor an edge. Rather than stretch `Subgraph` to fake it, or model year-buckets as nodes and shares as edges (the "make the groupby look like a graph" dishonesty ADR 0002 rejected for issue #6), trends get their own return type, a tabular `Series`, and their own seam. They do **not** flow through `run_query`, whose contract stays `Subgraph`-returning. Two result kinds, two seams, neither overloaded.

## The graph-incidental test, reweighed for a rendered/agent tool

ADR 0002 cut issue #6 partly because it "reduced to a two-hop bipartite aggregation... the graph was incidental." Three of the four trends are the same shape: archetype share, card adoption, and pilot performance are each a `GROUP BY Event.year` over a traversal that already exists. The year axis is an aggregation key, not a path. Head-to-head is the exception: it is shared-event overlap, genuine shared-neighbour structure, and so the only graph-native one, yet its data is the thinnest.

If "graph-native" were the sole bar, applied literally it would cut all four, because the one that passes it (head-to-head) has almost no data to trend and the three with data all fail it. But #6 rested on **two** grounds, and ADR 0002 states "their combination is what made it a cut rather than a rework." The incidental-ness alone did not kill #6; low yield did the other half. #6's deeper failure was as a *tool*: an agent spends a call to learn nothing. The right bar for a rendered chart and a future RAG tool is therefore **graph-native OR an honest high-yield number**, not graph-native alone.

Under that bar, the per-analytic verdict is:

- **Archetype share over time**: a groupby, but high yield and clean (941 / 2095 / 1363 decks in the fat years, as the graph stood when #101 remeasured them). A strong tool. Kept, and built first.
- **Card adoption over time** — a groupby, decent yield with a thin fringe the floor handles. Kept.
- **Pilot performance over time**: a groupby, and the yield is structurally weak (607 of 1083 pilots appear in a single year), so it is kept only *scoped* to pilots with real history (see the floor below).
- **Head-to-head over time** — graph-native but near-zero yield as an aggregate (two pilots may share one or two events a year). Kept, but not as an aggregated trend: as a timeline of the individual shared events.

None is cut. The reframe is that #6's bar was about tool quality, and a rendered series a human reads (or an agent reads with the sample size in hand) does not carry #6's "wastes a call on nothing" failure mode.

## Rendered and agent-facing are the same layer

ADR 0002 said the query functions become the future RAG's tools. Trends keep that: one aggregation layer with two consumers. A new **trend tab in the app**, decoupled from the vis.js node-edge renderer, draws the `Series` as line and bar charts for humans. The identical functions are the v2 agent's tools, which read the `Series` as numbers. The vis.js renderer is never taught about charts; the trend tab never touches the graph renderer.

This is why the year-filter that issue #26's follow-up dropped stays dropped without loss: the agent consumes numbers and never wanted the year-scoped *picture*, and for the human the new trend tab is the temporal surface. A trend series subsumes a year filter on the *data*; it never had to subsume it on the *render*, because the render moved to a different tab.

## Four distinct tools, not one parameterized trend

The agent surface is four functions, one well-posed question each, matching the one-function-per-query pattern ADR 0002 already commits to. They do not share a signature: `meta_share_over_time` takes no required argument, `card_adoption_over_time` takes a card, `pilot_performance_over_time` takes a pilot, `head_to_head_timeline` takes two. A single `trend(metric, dimension, filter)` tool would force the agent to learn which combinations are legal and would return a different shape per metric anyway, so it buys nothing and hands the agent a muddier model. The three year-based tools may share a thin internal `_by_year` helper; that is an implementation detail the agent never sees. Shared helper, yes; shared tool surface, no. The abstraction rejected here is a speculative trend *engine*, not four honest functions.

## Head-to-head dates the registration, not the event (amends ADR 0006)

Head-to-head is drawn as two lines, `placementNorm` on the y-axis (the only quantity comparable across events of different sizes; each point is labelled with the raw finish and field size for readability), drawn inverted to the shared higher-is-better score (1 a win, 0 last) exactly as the pilot-performance chart below is, while the tool returns the raw norm; a point per shared event, coloured per player, with an optional time-range slice on the x-axis. That x-axis needs a coordinate finer than `Year`, or two events shared in one year collapse onto the same x.

ADR 0006 deliberately refused sub-year precision and did not store per-deck `createdAt`, "since per-deck creation would invite exactly the sub-year precision this ADR rejects." This ADR **amends that consequence**: `createdAt` is persisted as a `Deck` property, and the head-to-head axis reads it.

The amendment is narrow and does not reopen 0006's core claim. ADR 0006 refused to *date the event* below year granularity, because `min(createdAt)` across an event's decks would assert a year, and eventually a month, the data cannot back. Head-to-head does not date the event. It plots when each pilot *registered their list*, which is exactly what `createdAt` records, as a hard per-deck fact with no proxy and no inference. The event stays year-only; the *registration* carries a date because the source gives it one. Every other trend still groups by the `Year` node via `IN_YEAR`; only head-to-head reads the per-deck date.

The timeline is drawn on registration dates rather than event dates, but the two sit close in practice: lists are scribed shortly after their event, so the temporal distance between registration and event is small and the date axis faithfully reflects the real ordering and spacing of a rivalry. This is what makes the registration-date axis good enough to stand in for an event-date axis the source never provides, without the timeline having to claim it holds an event date.

`Date` is a `Deck` property, not a node. `Year` earns a node because it is a low-cardinality dimension the three group-by trends aggregate over, exactly ADR 0006's "a dimension to traverse and group by, like Macro." A registration date is the opposite: a high-cardinality continuous coordinate, one value per deck, that nothing groups by and the timeline merely reads off the hub. Modelling it as a node would restate ADR 0002's rejected Colour-Identity case and bloat the node table with the sub-year precision 0006 warned against.

### The field size beside each point is the source's own, recovered by algebra (issue #103)

> **Mechanism superseded by [ADR 0016](0016-placement-provenance-and-minted-norms.md).**
> The inversion and the `else deck_count` fallback below are deleted: `field_size` is
> read off the stored `Event.fieldSize`, and after the field correction
> ([ADR 0015](0015-field-size-correction.md)) that value is the build-corrected field
> rather than the source's own at the corrected events. The measurements below stand
> as the evidence that settled #103.

Each point is labelled with a raw finish over a field size, and the code called that field size "the entrant count". Issue #103 measured it and settles what it is. It is the source's own `eventSize`, the denominator the source ranked the finish against. The repo never ingests that field, so the value is recovered rather than read, but the recovery is exact rather than approximate: `placementNorm == (placement - 1) / (eventSize - 1)` holds bit-exactly for 4540 of 4540 source decks carrying both fields, so inverting the norm is the algebraic inverse of a definition and not an estimate of one. The recovered value agrees with the source for 105 of 105 events that can yield it and 4404 of 4404 decks, to a worst residual of 5.7e-14.

It is not an entrant count. Where the source publishes one, the two agree exactly: 36 of 108 events carry a `players` field, and `eventSize` equals it in 36 of 36. On the other 71 it equals the last recorded placement, 71 of 71. It sits below the number of distinct pilots who entered at 10 of 108 events.

Four of those ten are `eventType='Teams'`, and this ADR states that case explicitly because settling it is what issue #103 was opened for. At a teams event the recovered value counts **teams** while the deck count counts **lists**: TMCTeams25 recovers 39 against 117 decks and 115 pilots, POGTeams26 24 against 70 decks, PoGTeams2024 5 against 24, HighlanderWorldsTeams26 3 against 12. This is why #101's two verifiers reached opposite conclusions, one reading the gap as a fabricated denominator and the other as a correct one. Neither number is wrong, because the deck count is not a rival witness of the same quantity: one counts the units the source ranked, the other counts the lists registered, and at these four events they are different things by construction.

Reconciling the field size against the deck count would have been the wrong repair anyway, and not only at teams events. `eventSize` exceeds the deck count at 58 of 108 events, because a top-cut event records only its top finishers: SSWam holds 7 decks out of a field of 88. The norm's own denominator is the only number the plotted y is coherent against, so the deck count could not stand in for it.

The `else deck_count` fallback arm is a different population and is recorded here as one. It covers the 3 of 108 events where no deck carries a positive norm, reaching 30 of 134,806 tool rows and 0 of 268,281 drawn markers, because every deck at those events has a null `placementNorm` and so no marker is ever drawn for one. Its stored value disagrees with the source's `eventSize` at 2 of the 3.

**Open, and deliberately not decided here: what noun the hover should carry.** The head-to-head hover renders a bare fraction with no unit at all, while the sibling adoption hover appends "decks", and 15,543 of 268,281 drawn markers carry a denominator that is not people (15,032 at the four teams events, 511 where the source folded several decks onto one ranking slot). The wordings were costed, from naming the event in the hover to a generic "6 of the 39 the finish was ranked against", and no single noun is true across both shapes. The number is settled on fact; the wording is a decision still to take.

## Minimum evidence: a floor where a value is an aggregate, an annotation where it is an observation

Following ADR 0005 (refuse rather than report noise) and ADR 0012 (an absolute count as the trust floor, since evidence is sample size and does not scale with the meta). A chart is more dangerous than a number here: a line through thin points reads as a real trend. The rule is placed by value type:

- **Aggregates carry an absolute-count floor.** An aggregate estimates a latent quantity from few trials, so a thin cell can land anywhere by luck. For `pilot_performance`, a year cell needs enough events to compute an honest mean, and the pilot needs at least two qualifying years or the answer is "not enough history," never a lone dot. Thin *years* are not dropped (192 decks is thin but honest); each tool returns the sample its value rests on, the year's total N for the two share trends and the year's event count for `pilot_performance`, so a coarse cell is visible as coarse.
- **Direct observations carry no floor but always the base N.** A count over a known denominator is exact whatever its size, so a low count is signal, not noise. `card_adoption` returns raw count, share, and the year total rather than suppressing low counts. `meta_share` is the same object and is governed the same way (see the amendment below). A single head-to-head point is one real registration, so it needs no within-point floor; the *pair* needs at least two shared events or it is a dot, not a timeline.
- **An absence is a zero, not a gap.** A year an archetype was not played is a real zero share, the same reading `card_adoption` gives a year a card sat out. So both matrices are rectangular, a cell per subject per year of the graph, and a line drops to zero across an absent year rather than jumping it and reading as continuous presence.

Every tool returns the evidence count alongside the value, because the v2 agent must see the sample size to reason honestly.

Lines connect points and nothing more. No trend *direction* is inferred: with four year-buckets, two of them thin (192 and 941 decks) next to two fat (2095, 1325), a computed slope would weight a 192-deck year equally with a 2095-deck year and manufacture a direction the data cannot support. Direction is left to the reader, human or agent, who has the per-cell N in hand.

The exact floor values are not pinned here. As with `MIN_GEM_DECKS`, the ADR records the rule and the tracer picks the number against real counts.

The `pilot_performance` tracer pins its per-year floor at **`MIN_PILOT_YEAR_EVENTS = 2`**, counting **events, not decks**. An event is one independent tournament finish, so a list a pilot reused across events is the several finishes it is, while two decks at one event would not be two trials; the event is the honest unit for a mean of finishes. In the current graph the two coincide (ADR 0004 folds a pilot to one deck per event, so events equal decks in every one of the 1833 `(pilot, year)` cells), but the unit is chosen for where they could diverge. Of those 1833 cells, 947 hold a single event, where a "mean" is really that one finish, so the floor gaps them; the 886 with two or more events are kept, and the trend tab labels each point with the event count it averages, so a thin two-event mean carries its own sample size rather than being silently trusted or silently dropped. That keeps the 238 pilots who clear two qualifying years. It is a distinct floor over a distinct population from `MIN_GEM_DECKS`: that one governs a mean over the decks running a card, this a pilot's own mean over its own finishes. Both survive the amendment above, because both guard a mean rather than a count.

The trend tab draws this one inverted to a higher-is-better score (1 a win, 0 last) so a rising line reads as improving, with a reference line at 0.5, a random finisher's expected normalised rank, so a point above it is a season that beat the field. The tool's `mean_norm` stays raw `placementNorm` (0 a win), the graph-wide convention the agent reads; only the chart flips it for the eye.

For `meta_share`, returning all ~126 archetypes as lines is an unreadable hairball. The tool returns the full `(archetype, year, share, n)` matrix; the **trend tab** applies a cumulative-share cut (25 / 50 / 75 percent, default 50) plus a manual archetype panel, purely as display legibility. The cut ranks archetypes by their deck count in the **latest year the data holds**, read from the series rather than pinned to a year, so it follows the graph forward as new events land. The question the chart answers is "what is the meta now, and how did it get here", so today's leaders are the lines worth tracing back; ranking on the pooled all-year population instead lets an archetype with a fat past but no present crowd out a live one. The set is still computed once, giving a fixed set of lines across the x-axis (a per-year set would make lines enter and exit as archetypes cross the threshold); the manual panel is the escape hatch for an archetype that was large only in an early year. The agent always receives the full matrix, never a silently truncated one.

### Amendment: `meta_share` carries no cell floor

This ADR originally filed `meta_share` under aggregates and pinned a per-`(archetype, year)` floor at `MIN_CELL_DECKS = 5`, gapping a cell of one to four decks. That floor is removed and the tool is refiled as a direct observation. The rule above did not change; its application to `meta_share` was wrong.

An archetype's share is a count of decks matching a predicate over every deck that year, the same construction as `card_adoption`, which this ADR already exempts. Only the predicate differs (carries a label, versus runs a card). A share cannot be wrong the way a mean can: two decks of 941 is 0.21 percent, exactly, always. `MIN_CELL_DECKS` took its value and its wording from `MIN_GEM_DECKS`, which guards `MAX_GEM_MEAN_NORM`, a *mean* over the decks running a card, where ADR 0012's own worked example is a two-deck card averaging 0.128. That lucky-draw failure mode has no counterpart in a share, so the lineage does not transfer.

The floor's cost was not neutral. A sub-floor cell is bounded by four decks over the year total, at most 2.08 percent against an axis reaching 11.4 percent, so it could never withhold a misleading *spike*; it could only ever withhold a low point. And because the display cut draws only archetypes that are large in the **latest** year, a drawn archetype's thin cells are necessarily its **past**: all fifteen gaps in the top-75-percent view fell in 2023 or 2024, none in 2025 or 2026. The floor systematically deleted the years an archetype entered or left the format, which is the "how did it get here" half of the question the cut was built to ask, and the eye reads the resulting hole as a zero, so the chart substituted a wrong value for a correct one.

The two other arguments for the floor are answered elsewhere. Legibility (a hairball of thin lines) is the display cut's job, and the cut and the floor were introduced together as two solutions to one problem. Evidence is carried, not enforced: every cell already ships its `n` and its `year_total`, which the chart puts in the hover, so a reader weighs a 0.21 percent point against the two decks behind it rather than being denied the point. The tuning statistic behind the number (172 of 372 cells hold one to four decks) was measured over the whole matrix, while the floor only ever fired on the five to thirty-one lines the cut selects.

The consequence is that `SeriesCell.share` is always a `float`, never withheld, and the trend tab draws a point in every year. `drawable_tags` is deleted with the floor: its only purpose was to hide archetypes that would draw a line of gaps, and no such archetype exists now, so the manual panel offers every archetype in the graph. The floors that guard genuine aggregates are untouched: `MIN_GEM_DECKS` (ADR 0012) and `MIN_PILOT_YEAR_EVENTS` above both stand.

### Amendment: a refused cell is stated, not dropped (ADR 0014)

Issue #101 audited every guard in the package against this rule and found it had a missing half. Classifying the value is necessary and was not sufficient: a guard can be correctly placed on a genuine estimate and still be wrong, if what it withholds *renders as a different value* rather than as a refusal. ADR 0014 records the audit and the guard-by-guard verdict; the parts that change this ADR's own text are:

- **No other floor on a count was found.** The three floors above all guard means, measured rather than assumed. `MIN_CELL_DECKS` was a misfiling, not a habit.
- **`pilot_performance_over_time` no longer drops a thin year.** It returns the year with `mean_norm` set to `None` and its real event count, so the series is rectangular over the years the pilot played, the way the two share trends are rectangular over the graph's years. Dropping the row made a refused year identical to a year the pilot did not play, and because a thin year is overwhelmingly a pilot's first or last, the chart's span erased 83 of the 90 refused cells from the axis entirely and claimed a shorter career than 74 of 238 pilots had. `PerformanceCell.mean_norm` is therefore `float | None`.
- **"Not enough history" is raised, not returned empty.** `pilot_performance_over_time` and `head_to_head_timeline` raise `NotEnoughHistory`, carrying the evidence they found, rather than returning `Series(cells=[])` for four distinct facts. This is `SliceTooSmall`'s pattern from ADR 0012.
- **The meta-share matrix is rectangular over the `Archetype` nodes**, not over the rows the primary-archetype join returned. Two archetypes no deck ever led with had no row anywhere, so "every archetype gets a cell in every year" was true only of the archetypes the query happened to see.

`MIN_PILOT_YEAR_EVENTS`, `MIN_QUALIFYING_YEARS` and `MIN_SHARED_EVENTS` keep their values. Only the shape of their refusals changed.

## Amendment: emphasis, not colour-by-position, above eight lines (issue #116)

> Revised by issue #117 once the model was built and read: the context layer keeps its
> colour faded rather than receding to grey, hue runs past eight as a tracing cue, and
> emphasis applies at every width. See the
> [#117 amendment](#amendment-emphasis-is-faded-colour-at-every-width-not-grey-above-eight-issue-117).

The trend tab draws the `>8`-series charts with **emphasis**: every line recedes to grey as context, and one is raised in the accent colour on **legend click-to-isolate**. This reverses the render's colour-by-position behaviour for those charts. This amendment named two such charts, the meta and the card-adoption default cuts (~15 lines each); the [issue #126 amendment below](#amendment-two-views-per-subject-tab-emphasis-is-meta-share-only-issue-126) caps adoption at two series, so meta share is now the only chart emphasis governs.

ADR-0013's prose never fixed a colour rule for the group-by line charts; the colour-by-position behaviour was the render's default, and this amendment records the decision to replace it. That default, as `app.py` described it, gave a trace "its alphabetical position within the current selection, not a property of the archetype: all 14 archetypes drawn at the Top 50% cut take a different colour at Top 75%", drawing hues from a 32-entry palette and recycling past 32. It failed the reader three ways. At ~15 lines no palette keeps the hues distinguishable, so the chart is the rainbow issue #85 was opened to fix. Because colour tracked selection position rather than the entity, widening the cut repainted every survivor, so a line's colour was unstable across two views of the same data and carried no identity worth reading. And hue-only identity is invisible to a colour-blind reader. Emphasis is honest about all three: it never claims fifteen distinguishable hues, it shows the whole meta as one grey shape, and it isolates the one line the reader asks for.

The reversal is scoped to the `>8` branch. At `≤8` series the render keeps a direct colour per entity, assigned in fixed order **by entity, never by rank, never cycled** (the palette seam from issue #109), which is itself the fix to the repaint-on-recut defect for the small case; emphasis is the same rule's answer to the case where eight distinguishable hues run out. Head-to-head (two lines coloured per player) and pilot performance (single series) are `≤8` and unaffected. The isolate is Plotly-native legend interaction, not point-level hover, which `gr.Plot` cannot provide (issue #78). The mark semantics this ADR fixed are untouched: the thin dashed join still asserts no trend between years, the hollow markers still read as observations, and no slope is inferred.

This ratifies what issue #85 and `docs/design/v1-visual-direction.md` (§5-6, §9) proposed and held as "proposed, not final" pending this sign-off, and it unblocks the emphasis build. The tool surface is unchanged: every `Series` tool still returns the full matrix with each cell's `n` and `year_total`, and emphasis is a property of the trend tab's render, not of the data the agent reads.

## Amendment: emphasis is faded colour at every width, not grey above eight (issue #117)

Building the emphasis model put it in front of a reader, and three of the decisions the
[#116 amendment](#amendment-emphasis-not-colour-by-position-above-eight-lines-issue-116)
made are reversed by what that showed. The model itself stands: two layers, one line
raised off a legend click, no point-level hover, mark semantics untouched.

**The context layer keeps its colour, faded; it does not recede to one grey.** A field
of fourteen identical grey lines is untraceable: the reader cannot follow one archetype
across the years, which is most of what the meta chart is for, and the isolate is no
substitute because it answers only about the line already named. So each archetype
draws in its own hue at `_CONTEXT_ALPHA` (0.20), and the raise is that same hue at full
strength rather than one accent for every line. The legend swatch then matches the line
it raises, and two lines raised together stay distinct.

The faded layer drops its observation markers and draws as line only. The marker's fill
is the opaque surface, chosen so two overlapping rings do not cross into mud, which was
right for a handful of full-strength lines; at 20% opacity the ring is invisible and all
that is left is the occlusion, thirty-one archetypes' worth of discs chopping the very
lines the fade exists to keep traceable. The raised line keeps its markers, so the
observation read the ADR fixed is intact wherever a reader is actually reading closely.

**Hue therefore runs past eight, on an extended scale, and §5's "never past eight" is
narrowed rather than broken.** The scale (`palette.EXTENDED`) opens on the signed eight
in slot order, then adds twenty-four hues chosen by farthest-point selection **as
drawn** — each candidate scored on its distance from every hue already in the scale
after compositing onto the surface at the emphasis opacity, not on its hex. That
distinction is load-bearing. Fading costs a colour four fifths of its separation, so
hues that look unrelated at full strength can be one colour on the canvas: the first cut
of this scale, built by rotating the signed eight into lightness rings, put two of the
Top 75% lines **4.6 RGB units apart on screen** while every hex differed, and its tests
passed. Candidates are held to the signed set's own band (saturation .45-.70, lightness
.45-.68, contrast >= 3:1) so the extension reads as the same family rather than the neon
an unconstrained separation search reaches for.

The ceiling is worth recording, because it bounds what any palette here can do. At the
emphasis opacity the **signed eight themselves** sit 7.6 units apart at their closest,
against 38.1 at full strength. The fade, not the extension, is what spends the colour
budget. The extension is therefore built to reach that same 7.6 and no worse, and that
is exactly what `test_palette` asserts: the floor is the signed eight's own closest
pair, faded the same way, so the guard needs no invented constant and states the honest
claim — extending the scale may not crowd the chart worse than the signed set already
does. Raising the opacity is the only lever that buys real separation (0.35 would give
13.3), which is a legibility-versus-recession trade to make by eye, not by argument.

What §5 protects survives intact: **hue past the eighth never carries identity.** It is
a tracing cue on a faded line, and `assign` still refuses a ninth *direct* colour. A
raised line does draw its extended hue at full strength — that is what makes the raise
read — but identity there comes from the legend and the hover, with a line or two raised
at a time, never fifteen. Because the scale opens on the signed eight and the cut hands
its tags over strongest-first, an archetype holds one colour across every cut, which is
the repaint-on-recut defect the #116 amendment was mostly about.

**Emphasis is how the chart reads at every width, not a mode above eight lines.** A
threshold made the same archetype read two ways either side of it, and moving the cut
became a change of chart rather than a change of how many lines were drawn.

Two defaults follow, both about what a reader meets before they click. A cut opens with
its **leading three** raised (`_OPEN_RAISED`), taken off the rank order the cut already
carries, so a cold start is a chart with a reading in it rather than a field of context
that says nothing until clicked. The manual panel opens with **every** line raised,
because each was named by the reader and a leading-few rule there would ask them to
choose the same archetypes twice. The click goes both ways in both panels, and the faded
layer is always on the canvas, so no click can lose a line rather than recede it.

One implementation constraint is worth recording because it looks like a bug in the
build. The isolate the #116 amendment specified, Plotly's `legend.itemclick`
`"toggleothers"`, is unusable here: its handler branches on the *clicked* trace's own
visibility, and a `legendonly` one takes the branch that turns **every** trace in the
legend on. Since a raise starts hidden, that is the reader's first click, and it would
draw the whole cut at full strength, the rainbow this model exists to retire.
`"toggle"` is used instead, and `itemdoubleclick` is switched off because it defaults to
`toggleothers`. The cost is that a second click raises a second line rather than
swapping; with per-entity hue restored, two raised lines are still distinguishable, so
the cost is small and the "isolate" of the #116 wording becomes *raise*.

`docs/design/v1-visual-direction.md` §5-6 is superseded on these points and carries a
pointer to here.

## Amendment: two views per subject tab, emphasis is meta-share-only (issue #126)

Two things this ADR assumed have changed. The pipeline decision it fixed (a `Series` is not a `Subgraph`, two seams, neither overloaded) is untouched and this amendment preserves it; only two consequences of how the trends were *packaged* are reconciled.

**The card-adoption cut is capped at two series, so it takes direct colour, not emphasis.** The emphasis amendment above named card adoption as a `>8`-series chart alongside the meta. Issue #126 removes the adoption chart's free multi-card overlay (which could stack arbitrarily many cards) and makes the co-occurrence pair (the subject card and at most one second card) the only multi-card compare. Adoption therefore never draws more than two lines, well inside the `≤8` branch, so it takes a direct colour per card by entity (the §5 palette seam), never emphasis. **Meta share becomes the only chart the emphasis model governs.** Issue #117 has already been narrowed to meta-share-only to match. This is a conscious reduction of an existing capability, recorded so it is not re-added as if it were an oversight.

**The "decoupled trend tab" packaging is retired; the pipeline decoupling is not.** This ADR is written around a separate *trend tab* in the app, decoupled from the graph renderer, as the human-facing surface for the `Series` type ("a decoupled trend tab" in the title, and the "Rendered and agent-facing are the same layer" section). The subject-first regrouping (#119) retired that separate tab, and #126 fuses each subject's graph and its trend under one view, one Draw fanning out to both a subgraph query and a series query. The two-seam pipeline decoupling that mattered (trends do not flow through `run_query`, the renderer is never taught about charts) still holds exactly. What is stale is only the *tab* as the packaging: the temporal surface now sits beside the graph under one subject, not in a tab of its own. Read every "trend tab" in the prose above as "the trend render," a property of where a `Series` is drawn, not a navigation location.

## Consequences

`Deck` gains a stored `createdAt`, which forces a golden-oracle re-capture wherever deck properties are pinned. Trends do not render through the existing renderer or `run_query`; the trend tab and the `Series` type are new surfaces built alongside them, first proven end to end by the `meta_share` tracer, then reused by the two year-siblings, with head-to-head built last because it carries this ADR's 0006 amendment and the only non-year, non-aggregate shape.

The `Year` node, unused until now, gains its first real consumers (the three group-by trends traverse `IN_YEAR`), so the dimension survives. That settles the premise of #70 (the `YearStraddle` build guard was blocked on whether the dimension would be deleted): it will not be, and #70 becomes live work rather than moot.

## Amendment: a fifth tool, `archetype_landscape`, and a floor on the field rather than the value (issue #145)

The agent surface is five functions, not four. `archetype_landscape(year)` returns one year's metagame as `LandscapeCell`s, pairing each archetype's meta share with the mean finish of its scored decks. It is added rather than folded into `meta_share_over_time` for the reason the section above gives for keeping the four apart: it is a different question with a different signature (a year, not nothing) and a different shape (one row per archetype, not a rectangular matrix over years), and a `trend(metric, dimension)` tool that carried both would hand the agent a muddier model, not a smaller one. It reuses the year plumbing the three group-by trends share, which is exactly the "shared helper, yes; shared tool surface, no" line already drawn here.

It groups by the primary archetype alone, as `meta_share` does: the source multi-tags at 1.6 tags per deck and the weights are lopsided (median primary 100, median secondary 5), so counting every tag would credit a deck to an archetype on 5 percent weight and sum the year's shares to about 160 percent of its decks.

**The floor is on the field, not on the value.** `MIN_LANDSCAPE_ARCHETYPES = 3` refuses a year that cannot make a field: a dot reads as popular or niche, and winning or losing, only against other dots, so a pair is a comparison rather than a landscape. It is a `NotEnoughHistory`, carrying the count it found, per the "raised, not returned empty" amendment above.

There is deliberately **no per-archetype event floor** beside it, though the y value is a mean of finishes and the rule above would otherwise pin one there, as `MIN_PILOT_YEAR_EVENTS` does for a pilot's own mean. The two thick years and the two thin ones reject it for different reasons, and the year selector offers all four, so both cases are live.

In 2025 and 2026 what the chart draws already carries the reliability: it shows only the year's 25 most-played archetypes, and inside that cut the minimum is 11 distinct events in both years, while across their top 50 exactly one archetype (2025's `grixis_oko`, 16 decks over 4 events) sits under 5. In 2023 and 2024 the cut does not do that work: the drawn top 25's minimum is 5 events in 2024 and **1** in 2023. A floor there would not rescue the year, only empty it, since 2023 holds 8 events in total and a floor of 5 would leave about 9 dots.

So the guard on a thin dot is the evidence carried beside it, exactly as the `meta_share` amendment argues: every cell ships its distinct event count, the marker size draws it (a one-event dot is the smallest ring on the chart, against a 28px maximum), the hover states it, and the caption states the whole year's events and decks, so 2023 announces its 8 events and 192 decks on the surface rather than presenting itself as a peer of 2025's 51 and 2,095.

**Display cut, as with `meta_share`.** The tool returns every archetype the year held; the tab draws the top 25 by share, recomputed for the selected year. Recomputed, not fixed: `latest_year_share_cut` deliberately computes one set from the latest year so its lines span the whole x axis, which a single-year scatter has no need of, so the two cuts are siblings and not one shared function. The surface states the cut and the size of the field it came from, so a bounded chart is never read as the whole meta.

## Amendment: a sixth tool, `archetype_timeline`, dating the event rather than the registration (issue #151)

The agent surface is six functions, not five. `archetype_timeline(a, b=None)` returns an archetype's finish over the events it attended as `ArchetypeTimelinePoint`s, and with a second archetype the same over the events both attended. It is the head-to-head's question one scale up, and it is a separate function for the reason the section above gives for keeping the others apart: a different signature (one archetype, or two, where `head_to_head_timeline` requires two pilots) over a different subject and a different cell. It groups by the primary archetype alone, as `meta_share` and `archetype_landscape` do.

**A point is an aggregate here, where the head-to-head's is an observation, and that changes what the surface must carry.** A pilot brings one deck to an event, so their point is one real result and the "direct observations carry no floor" arm of the rule above applies to it directly. An archetype brings several, so each point is a mean of whichever of its decks the source scored, and the means are thin: measured over the whole graph, 88 percent of `(archetype, event)` points rest on one to three ranked decks and the median is one. The rule above then puts an annotation on every point rather than a floor under it, since the point is still a real event that was attended: the decks behind each point ride in the cell, in the marker size, and in the hover per side, and the caption states that a point typically averages one to three decks. The head-to-head's caption line, "each point is one real result, not an average", is exactly false here and is not reused.

**The floor is on the pair (or the run), counted in comparable events.** `MIN_ARCHETYPE_EVENTS = 2` refuses a single archetype ranked at fewer than two events, and a pair ranked at fewer than two events *in common*, as a `NotEnoughHistory` carrying the count it found. It parts from `MIN_SHARED_EVENTS` beside it in counting **comparable** events rather than attended ones, which follows from the paragraph above: a pilot's shared event is a comparison by construction, while an archetype can attend a shared event with nothing scored on one side, and counting those would clear the floor on a chart holding one drawable point and a gap. Refusal is a common path rather than an edge case: over the 105 archetypes with five or more ranked events, the median pair of the 5460 shares four events, 9 percent share none and 21 percent one or fewer.

**With two archetypes the view narrows to the shared events, and the surface says so.** Grixis attended 85 events and Jund 73 but shared 62, so adding Jund visibly reshapes the Grixis line. That is chosen over keeping each line's full history so that every drawn point has a counterpart and the band between the lines is continuous, and the caption states the restriction as a definition so the shift does not read as a glitch.

> **"Every drawn point has a counterpart" was never true, and [ADR 0024](0024-an-unpublished-finish-is-drawn-at-the-best-it-could-have-been.md) makes it true only where one side was scored.** The floor paragraph already grants that "an archetype can attend a shared event with nothing scored on one side"; narrowing to *attended* events does not narrow to *scored* ones, so one of those attendances was drawn as a point with a break on one side and the band broke over it. Measured on the current artifact: 84 of the 4,888 pairs the surface will draw hold such a point, and all 84 trace to `GGWAD`, the one event that published a thinned standings (16 finishes of 28) rather than a bracket and so is kept by `MIN_FIELD_COVERAGE`.
>
> ADR 0024 draws the unscored side at the best it could have finished, so 69 of the 84 now have the counterpart and the continuous band this paragraph promised. The other 15 are the pairs where **neither** side was scored, which nothing can bound into a comparison, and they keep the break. So the guarantee this paragraph stated unconditionally holds conditionally: a shared event with one side scored now carries a counterpart, and one with neither still breaks. A solo line keeps its break outright, which ADR 0024 leaves out of scope on purpose. The headline's denominator is the comparable count and now matches the marks on the axis, a bounded meeting being one the record settles: `breachbond` and `jund` draw 18 points under "11 of 18 shared events".

**This one dates the event, which the 0006 amendment above deliberately did not (amends ADR 0006 again).** The head-to-head amendment was narrow precisely because "head-to-head does not date the event. It plots when each pilot registered their list." That argument does not carry here: both sides of a shared event must sit at one x or the band between them is drawn against a lie, and an archetype's point is not one registration but a mean over several decks that were not registered together. So the point's x is the earliest `createdAt` across the **event's whole field**, which is a proxy for the event and is named as one. Of 108 events, 37 have every deck registered on one day and 21 spread over more than a day, up to 12 apart (`ANZSS0703`, 69 decks), so the proxy is good to within days at almost every event and to under a fortnight at the worst.

This does not reopen ADR 0006's core claim, which is about the granularity the graph **stores**: the `Event` node still carries no date and is still placed no more precisely than its `Year`, and every aggregate still groups by `IN_YEAR`. What is dated here is a mark on a chart axis, derived per draw from the per-deck `createdAt` the amendment above already persisted, and the same derivation `head_to_head_timeline` has been making since it shipped (it takes `min(createdAt)` across the field for the same reason: to put both pilots' points at one x). This amendment records that the derivation exists, names it as an event proxy rather than a registration, and measures how far it can be wrong.

**Nearly the whole catalogue is offered, with its count in the label.** The selector rule is drawability alone (at least `MIN_ARCHETYPE_EVENTS` scored events, 121 of 126 archetypes today), because this plot is the escape hatch for everything the landscape's top-25 display cut hides: filtering it to the archetypes with a comfortable history would leave `golgari_cradle` (12 decks over 9 events) unreachable anywhere on the tab. Each label carries its scored-event count, so thinness is visible before the pick rather than as a refusal after it, which is the same "evidence beside the value, not a cut" line the landscape amendment takes.

## Amendment: a seventh tool, `player_leaderboard`, and its own ADR (issue #135)

The agent surface is seven functions. `player_leaderboard()` takes no argument, like `meta_share_over_time`, and returns the whole field of contenders as `RaceCell`s: one cell per contender per sample date, carrying both the pilot's career standing and what their record said at that date.

It follows this ADR's rules rather than bending them. It is a `Series`, not a `Subgraph`. It returns the **whole** field and the whole span, with the eight lines the chart draws and the fifty rows the leaderboard lists left to the app as display cuts, which is the `meta_share` rule. Its floors sit under aggregates and its evidence rides beside every value: `MIN_SCORED_MAJORS` withholds a score with too little behind it while the cell still states the sample that refused it, per the "a refused cell is stated, not dropped" amendment above.

Two things about it are novel enough to need their own decision record, so they live in **ADR 0017**: the y quantity is a running score rather than a rolling window, because the rolling version's movement measured as noise; and every rank carries a resampled interval, because the ordering the scores imply is not one this record supports. ADR 0017 also records the metric's own justification, which alternatives were measured against it, and which were rejected.

**ADR 0017 carries a standing practice for every trend built under this ADR, not just for the race.** Before a trend surface ships, permute the observations within each subject's own record and check that the movement the chart draws beats the shuffle. Where it does not, either the quantity changes or the claim does. Two existing surfaces fail it, both measured and filed: `pilot_performance_over_time` draws movement that is indistinguishable from noise (#175), and the hidden gem band's threshold crossing is a coin flip for most of its members (#176). The floors this ADR sets under aggregates guard against a mean over too little; they do not guard against a *trajectory* over too little, which is what the permutation test is for.

**The test travels; the race's remedy does not.** Both tickets originally proposed adopting ADR 0017's running score, and for `pilot_performance_over_time` that was simulated and rejected: the race's running score reads relatively, against a field, and a single pilot has no field, so the median line moves 0.051 on a fixed 0-to-1 axis and 125 of the 240 drawn pilots have only two points. On both surfaces the fix is on the claim and on the uncertainty carried beside each value, not on the quantity. ADR 0017 records the measurement.

## Amendment: every drawn mean carries its interval, from one pooled spread (issue #175)

The amendment above left the pilot chart's remedy open, saying only that the fix is on the claim and on the uncertainty carried beside each value. This records what was built, across the three surfaces that draw a mean `placementNorm`: the pilot chart, the landscape, and the archetype timeline's headline. They share the defect and now share the machinery, so the decision is one and not three.

**The interval is a field-pooled spread, not a resample of the point's own finishes, and that is measured rather than preferred.** #175 originally prescribed bootstrapping each year's own finishes, mirroring ADR 0017's rank interval. Simulated against the artifact's own noise (residuals of every ranked finish against its pilot's career mean, 4,000 trials per sample size), a nominal 90% bootstrap interval covers **50.6%** at two events and **62.1%** at three, and 62% of drawn points rest on three or fewer. The cause is structural: two finishes admit only three distinct resampled means, so the interval is built from a spread the sample cannot show, and no bootstrap variant escapes it. `_pooled_sd` and `_interval` replace it with one within-record spread estimated over the whole field and `mean ± 1.645 · sd / √n`, which covers 90.3 / 90.4 / 90.9 / 90.0% at n = 2 / 3 / 4 / 6 on the same simulation. **Any method proposed to replace it has to clear that simulation first.** This parts from ADR 0017, whose resampled rank interval stands on the race's own populations, and the parting is deliberate: the race's points rest on a median of four majors of a field, where these rest on two or three finishes of one subject.

The pooled form also settles the seed question ADR 0017 needed. Nothing is resampled, so the bounds are deterministic by construction. One hazard survives: Ladybug returns the same query's rows in different orders between calls and float addition is not associative, so each record is sorted before it is pooled. Without that, the same artifact reports bounds that differ in the last decimals, and "a moved number means moved evidence" fails quietly.

**`n` is the point's distinct scored events on every surface, and the spread is pooled over what there is a spread of.** For the landscape that pairs a deck-level sd with an event-level count, which is the conservative direction and is chosen as such: several decks of one archetype at one event met one field, so they are one trial, and where the two disagree the wider interval is the honest one. The fit itself is per surface and per population: within-pilot over careers of five or more ranked finishes (272 pilots, 3,209 finishes, sd 0.268), within-archetype over the year's scored decks (0.305 / 0.293 / 0.295 / 0.296 for 2023 to 2026, which the deck-norm scale pins near the 0.289 a uniform 0-to-1 spread gives, so the per-year fit is chosen for being where the query already is, not for accuracy it buys).

**Wide and overlapping is the finding drawn, not a failure of the drawing.** Re-running this ADR's permutation practice against what was built: observed mean swing 0.2376 against a shuffled 0.2404 (400 permutations, seed 20260727, 90% range 0.2213 to 0.2571), so real movement beyond noise is **-0.0028**. The filing measured -0.0012 off its own 400 draws, and the two agree to within the shuffle's own spread, which is the point: the finding replicates, and neither number is distinguishable from zero movement. Of the 382 consecutive-year pairs the chart draws, **377 (98.7%)** have overlapping intervals at a median width of 0.441. On the landscape, 17 to 20 of each year's drawn 25 sit above the 0.5 line but only 1 to 3 have an interval clearing it, so the caption reports both counts. On the timeline, 100 of the 121 headlines the selector can print are splits a fair coin produces at least a tenth of the time, so `beats_a_coin` gates the reading while the count stays printed: the count is a fact about the record, the lead is the claim.

**The landscape reports both counts, which amends this ticket's own AC after seeing it drawn.** #175 asked for the gated count alone ("N of 25 clearly above the 0.5 line", counting only dots whose interval clears it). Built and reviewed on the real chart, that reads as "1 of 25 clearly above" beside a picture in which twenty dots plainly sit above the line, and the caption loses an argument with the thing it is captioning. A reader resolves that contradiction by disbelieving the caption, which costs more honesty than the gate buys. Both numbers are reported instead: the plain count leads and agrees with the eye, the settled count follows as the qualifier. The gate itself is unchanged and so is the evidence; only which number leads has moved. This is the ticket's own standing practice applied to its own remedy, that a claim is checked against what the surface actually shows.

**Three presentation decisions, all taken on the drawn chart rather than from the ticket.**

*The labels move and the axis becomes a control.* They overprinted once the bars claimed the space above each dot, so they sit beside the rings and alternate sides along the share axis (`_label_sides`). That clears colliding adjacent pairs and no more, which a real year exceeds: 2026 stacks twenty of its drawn twenty-five between 1% and 3% of share. So the landscape also takes a share range filter, the control the rivalry charts already carry over time, shared as `_range_filter` rather than copied. The reader opens the crowded end instead of being handed a subset of the names, and the label-fewer fallback this ADR's figure docstring named in advance stays available and unchosen.

*The 0.5 line gets brighter, and deliberately stays colourless.* Three captions ask a reader to read a mark against it, and it was the faintest thing on the chart at the muted axis grey. The obvious remedy, the app's accent, was built first and **rejected on the drawn charts**: the rivalry pair draws its second archetype in the palette's slot-2 orange, so an accent line reads there as a third series, and no hue escapes the problem because the meta chart draws all eight slots at once. §5-6 settles it rather than taste, since colour names entities, a line at 0.5 is no entity, and `palette.py` refuses a ninth hue outright. The neutral is therefore the correct instrument and brightness is the only lever: it moves up the neutral ramp to `text-dim` at 85%, drawn once for all three charts (`_midpoint_line`) rather than three times. **Do not re-propose a colour for it.**

*The gate's arithmetic carries a continuity correction.* The pinned form, `|N - M/2| > 1.645·√M / 2`, is a normal cut standing in for a discrete count, and every disagreement it has with the exact binomial falls the unsafe way. It certified 52 distinct counts at M up to 60 whose exact two-sided p is 0.10 or worse, "3 of 3" among them, which `MIN_ARCHETYPE_EVENTS = 2` makes drawable and a coin produces a quarter of the time. Subtracting the half-step certifies nothing at p >= 0.10 on that sweep and moves the corpus figure from 93 hedged headlines to 100 of 121. The pin's formula is amended on measurement, in the direction the ticket exists to protect.

**The connecting line stays, dashed.** Point 3 of the ticket asked whether it still earns its place now that thin-and-dashed is measured as insufficient on its own. It does, and the reason is that it was never carrying the load alone: 125 of the 240 drawn pilots have exactly two qualifying years, where a line and no line read as the same object, and the intervals now draw the overlap the line was suspected of hiding. Dropping it would cost the eye its reading order across a four-point career and buy nothing measurable. The per-point event labels move from above the marker to beside it, since the upper whisker now occupies the space over every point and a label printed into it reads as a value on the scale.

**The seam widens by two fields, and nothing narrows.** `PerformanceCell` and `LandscapeCell` each gain `mean_low` and `mean_high`, which is a contract change for any agent reading the trend seam in the sense ADR 0014 records, but an additive one: no field changes type, no existing field changes meaning, and a reader that ignores both sees exactly what it saw before. Both are `None` wherever `mean_norm` is, so "too thin to say" needs no new spelling. No stored graph property changes, so no rebuild and no golden-oracle recapture.

**The population stays every placed event, and the caption now says so.** This chart and the race disagree by a median of 10 places of 139 over the race's contenders, which lands on pilots a reader can see, so the pilot caption names its population rather than leaving the two numbers to be read as one. The race remains the single majors-only surface, for the reason ADR 0017 gives: ranking pilots against each other requires measuring them on the same kind of event.
