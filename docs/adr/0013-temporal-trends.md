# Temporal trends: a Series return type, a decoupled trend tab, and four agent tools

ADR 0006 gave the graph a `Year` dimension but nothing traversed it. This ADR decides what does. Four analytics were grilled as candidate trends: archetype share (the meta), card adoption, pilot performance, and pilot head-to-head. All four are kept, but as a new kind of result that the existing graph seam does not carry.

## A trend is a Series, not a Subgraph

ADR 0002 says query functions return a `Subgraph` the renderer draws. A trend is a series (a value per year, or a value per event), which is neither a node nor an edge. Rather than stretch `Subgraph` to fake it, or model year-buckets as nodes and shares as edges (the "make the groupby look like a graph" dishonesty ADR 0002 rejected for issue #6), trends get their own return type, a tabular `Series`, and their own seam. They do **not** flow through `run_query`, whose contract stays `Subgraph`-returning. Two result kinds, two seams, neither overloaded.

## The graph-incidental test, reweighed for a rendered/agent tool

ADR 0002 cut issue #6 partly because it "reduced to a two-hop bipartite aggregation... the graph was incidental." Three of the four trends are the same shape: archetype share, card adoption, and pilot performance are each a `GROUP BY Event.year` over a traversal that already exists. The year axis is an aggregation key, not a path. Head-to-head is the exception: it is shared-event overlap, genuine shared-neighbour structure, and so the only graph-native one, yet its data is the thinnest.

If "graph-native" were the sole bar, applied literally it would cut all four, because the one that passes it (head-to-head) has almost no data to trend and the three with data all fail it. But #6 rested on **two** grounds, and ADR 0002 states "their combination is what made it a cut rather than a rework." The incidental-ness alone did not kill #6; low yield did the other half. #6's deeper failure was as a *tool*: an agent spends a call to learn nothing. The right bar for a rendered chart and a future RAG tool is therefore **graph-native OR an honest high-yield number**, not graph-native alone.

Under that bar, the per-analytic verdict is:

- **Archetype share over time** — a groupby, but high yield and clean (941 / 2095 / 1325 decks in the fat years). A strong tool. Kept, and built first.
- **Card adoption over time** — a groupby, decent yield with a thin fringe the floor handles. Kept.
- **Pilot performance over time** — a groupby, and the yield is structurally weak (830 of 1298 pilots appear in a single year), so it is kept only *scoped* to pilots with real history (see the floor below).
- **Head-to-head over time** — graph-native but near-zero yield as an aggregate (two pilots may share one or two events a year). Kept, but not as an aggregated trend: as a timeline of the individual shared events.

None is cut. The reframe is that #6's bar was about tool quality, and a rendered series a human reads (or an agent reads with the sample size in hand) does not carry #6's "wastes a call on nothing" failure mode.

## Rendered and agent-facing are the same layer

ADR 0002 said the query functions become the future RAG's tools. Trends keep that: one aggregation layer with two consumers. A new **trend tab in the app**, decoupled from the vis.js node-edge renderer, draws the `Series` as line and bar charts for humans. The identical functions are the v2 agent's tools, which read the `Series` as numbers. The vis.js renderer is never taught about charts; the trend tab never touches the graph renderer.

This is why the year-filter that issue #26's follow-up dropped stays dropped without loss: the agent consumes numbers and never wanted the year-scoped *picture*, and for the human the new trend tab is the temporal surface. A trend series subsumes a year filter on the *data*; it never had to subsume it on the *render*, because the render moved to a different tab.

## Four distinct tools, not one parameterized trend

The agent surface is four functions, one well-posed question each, matching the one-function-per-query pattern ADR 0002 already commits to. They do not share a signature: `meta_share_over_time` takes no required argument, `card_adoption_over_time` takes a card, `pilot_performance_over_time` takes a pilot, `head_to_head_timeline` takes two. A single `trend(metric, dimension, filter)` tool would force the agent to learn which combinations are legal and would return a different shape per metric anyway, so it buys nothing and hands the agent a muddier model. The three year-based tools may share a thin internal `_by_year` helper; that is an implementation detail the agent never sees. Shared helper, yes; shared tool surface, no. The abstraction rejected here is a speculative trend *engine*, not four honest functions.

## Head-to-head dates the registration, not the event (amends ADR 0006)

Head-to-head is drawn as two lines, `placementNorm` on the y-axis (the only quantity comparable across events of different sizes; each point is labelled with the raw finish and field size for readability), a point per shared event, coloured per player, with an optional time-range slice on the x-axis. That x-axis needs a coordinate finer than `Year`, or two events shared in one year collapse onto the same x.

ADR 0006 deliberately refused sub-year precision and did not store per-deck `createdAt`, "since per-deck creation would invite exactly the sub-year precision this ADR rejects." This ADR **amends that consequence**: `createdAt` is persisted as a `Deck` property, and the head-to-head axis reads it.

The amendment is narrow and does not reopen 0006's core claim. ADR 0006 refused to *date the event* below year granularity, because `min(createdAt)` across an event's decks would assert a year, and eventually a month, the data cannot back. Head-to-head does not date the event. It plots when each pilot *registered their list*, which is exactly what `createdAt` records, as a hard per-deck fact with no proxy and no inference. The event stays year-only; the *registration* carries a date because the source gives it one. Every other trend still groups by the `Year` node via `IN_YEAR`; only head-to-head reads the per-deck date.

The timeline is drawn on registration dates rather than event dates, but the two sit close in practice: lists are scribed shortly after their event, so the temporal distance between registration and event is small and the date axis faithfully reflects the real ordering and spacing of a rivalry. This is what makes the registration-date axis good enough to stand in for an event-date axis the source never provides, without the timeline having to claim it holds an event date.

`Date` is a `Deck` property, not a node. `Year` earns a node because it is a low-cardinality dimension the three group-by trends aggregate over, exactly ADR 0006's "a dimension to traverse and group by, like Macro." A registration date is the opposite: a high-cardinality continuous coordinate, one value per deck, that nothing groups by and the timeline merely reads off the hub. Modelling it as a node would restate ADR 0002's rejected Colour-Identity case and bloat the node table with the sub-year precision 0006 warned against.

## Minimum evidence: a floor where a value is an aggregate, an annotation where it is an observation

Following ADR 0005 (refuse rather than report noise) and ADR 0012 (an absolute count as the trust floor, since evidence is sample size and does not scale with the meta). A chart is more dangerous than a number here: a line through thin points reads as a real trend. The rule is placed by value type:

- **Aggregates carry an absolute-count floor.** For `meta_share`, the floor is per-`(archetype, year)` cell on that archetype's deck count in that year; a cell below it renders as a gap, not a zero, distinguishing "share is near zero" from "too thin to say." For `pilot_performance`, a year cell needs enough decks to compute an honest mean, and the pilot needs at least two qualifying years or the answer is "not enough history," never a lone dot. Thin *years* are not dropped (192 decks is thin but honest); each tool returns the year's total N so a coarse year is visible.
- **Direct observations carry no floor but always the base N.** A card's adoption count is the signal, not noise, so `card_adoption` returns raw count, share, and the year total rather than suppressing low counts. A single head-to-head point is one real registration, so it needs no within-point floor; the *pair* needs at least two shared events or it is a dot, not a timeline.

Every tool returns the evidence count alongside the value, because the v2 agent must see the sample size to reason honestly.

Lines connect points and nothing more. No trend *direction* is inferred: with four year-buckets, two of them thin (192 and 941 decks) next to two fat (2095, 1325), a computed slope would weight a 192-deck year equally with a 2095-deck year and manufacture a direction the data cannot support. Direction is left to the reader, human or agent, who has the per-cell N in hand.

The exact floor values (the meta cell floor, the pilot per-year floor) are not pinned here. As with `MIN_GEM_DECKS`, the ADR records the rule and the tracer picks the number against real counts.

For `meta_share`, returning all ~125 archetypes as lines is an unreadable hairball. The tool returns the full `(archetype, year, share, n)` matrix; the **trend tab** applies a pooled cumulative-share cut (25 / 50 / 75 percent, default 50) plus a manual archetype panel, purely as display legibility. The cut is computed once over the pooled all-year population, giving a fixed set of lines across the x-axis (a per-year set would make lines enter and exit as archetypes cross the threshold). The cut is deck-weighted, so recent fat years dominate the selection; the manual panel is the escape hatch for an archetype that was large only in an early year. The agent always receives the full matrix, never a silently truncated one.

## Consequences

`Deck` gains a stored `createdAt`, which forces a golden-oracle re-capture wherever deck properties are pinned. Trends do not render through the existing renderer or `run_query`; the trend tab and the `Series` type are new surfaces built alongside them, first proven end to end by the `meta_share` tracer, then reused by the two year-siblings, with head-to-head built last because it carries this ADR's 0006 amendment and the only non-year, non-aggregate shape.

The `Year` node, unused until now, gains its first real consumers (the three group-by trends traverse `IN_YEAR`), so the dimension survives. That settles the premise of #70 (the `YearStraddle` build guard was blocked on whether the dimension would be deleted): it will not be, and #70 becomes live work rather than moot.
