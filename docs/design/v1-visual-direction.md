# 7PH Explorer — v1 visual direction

Status: **agreed (dark-first)**. Parent ticket: #85. This is the implementation
source of truth for the v1 presentation overhaul — the exact tokens, scales, and
rules every surface follows. The `.html` beside this file is the visual companion
(type specimens, palette swatches, the chart before/after); open it in a browser.

The rule of precedence: this document, then the code. Where a value here and a
value in the code disagree, this document is the intent and the code is the bug.

---

## 1. Theme: dark-only

One `theme=` at the `gr.Blocks` level; the app no longer inherits the browser's
light/dark preference. Committing to a known background retires the compromises
that existed only because the background was unknown:

- The Plotly `#9ca3af` text is re-derived against the dark surface. The `_PALETTE`
  mid-luminance band is removed outright (issue #117): it existed to keep ~15 cycled
  hues legible on an unknown background, and emphasis (§6) retires the cycle itself.
- The pyvis details panel's `color:#333` on white and the inline-styled `<div>`
  state messages move onto theme tokens.

A light theme is explicitly out of scope for v1 (a proper light+dark pair is a
later issue, not a split-the-difference default).

_Amended 2026-11-15 (#327): dark is asked for in the page as it is served, and the
page is dark before its script runs. Until then the page committed to dark by
reloading itself at an address carrying a `__theme=dark` marker, so every visit
downloaded it twice (two downloads of 228KB, about 1.5s more from the live site) with
a blank flash between, and a page with scripts off painted no ground at all. Now the
page's own element carries the theme as an attribute and the served head carries the
ground and ink tokens: one download, the address the visitor arrived at, and the same
dark result under a light or a dark operating system setting. See "The shell" at the
foot of this document._

## 2. Design tokens

Defined once as CSS custom properties; every surface reads them by role.

| Role | Token | Hex | Notes |
|---|---|---|---|
| Ground | `--bg` | `#131110` | warm near-black, biased toward the accent |
| Surface | `--surface` | `#1c1917` | cards, chart surface, graph ground |
| Surface raised | `--surface-2` | `#24201d` | wells, insets |
| Border | `--border` | `#37312b` | hairline dividers |
| Text | `--text` | `#f2ede6` | primary ink |
| Text dim | `--text-dim` | `#b4aca2` | ledes, secondary |
| Text muted | `--text-mute` | `#8a8178` | captions, axis/tick labels |
| Accent | `--accent` | `#e26a2c` | the app's existing orange — primary action, active state |
| Accent bright | `--accent-bright` | `#f4823f` | on-surface emphasis, links, the raised chart line |

All text roles clear WCAG AA on `--bg`. No hardcoded colour may assume a
background the app does not control.

_Decided 2026-07-29 (#172): the same applies to the colours the app never wrote
but ships anyway. Gradio's own `--color-accent` (`#3b82f6`) and
`--checkbox-label-background-fill` (`#52525b`) painted 50 surfaces the app never
chose, among them the 2px bar under the selected tab, the largest blue object on
the page and one the #118 pass could not see, since it walks text nodes and a
pseudo-element bar is not one. Both are bound to the app's tokens in
`dark_theme()`, on two points that are decided rather than open:_

- _**Gradio's control accent is `--accent-bright`, not `--accent`**, decided on
  the measurement and not on the role wording. `--accent` was built and measured:
  it leaves the picked chip's label at **4.40** on the chip's own composited
  ground, below AA and the only new failure either candidate produced, where
  `--accent-bright` gives **7.27** on `--bg` and **5.64** on the chip. It also
  matches the split this codebase already runs (`--accent` is a fill or an edge,
  `--accent-bright` is ink on a surface), the call #163 made for the range slider._
- _**A picked radio or checkbox chip takes the quiet accent square `.clear-btn`
  already uses**, not a hue of its own. Why the chip needed redesigning at all,
  since it reads as scope creep otherwise: Gradio's picked chip and unpicked chip
  were byte-identical (the same `#52525b` fill, the same ink, a 0px border) and
  the blue dot was the only cue that one was picked, so retiring the blue without
  the chip treatment would have left the control with no selected state._
- _**The dot and the tick keep `--accent`**, the one place the control accent above
  must not reach. Gradio draws both as a hardcoded `#fff` glyph on the selected fill,
  and white on `--accent-bright` is **2.59**, under the **3:1** WCAG 1.4.11 asks of a
  non-text control and under the Gradio blue being retired (**3.68**), which is the
  one way this change could have gone backwards; `--accent` gives **3.32** and passes.
  Caught in review, same day. Two things generalise. The `--accent` / `--accent-bright`
  split decides it on its own (a fill, not ink), so the split is load-bearing rather
  than descriptive. And the #118 pass measures text nodes, so a glyph, a bar, or a
  border is invisible to it: **when a decision moves a colour that is not text, it is
  measured by hand or it is not measured.**_

**Measured by the #118 acceptance pass**, which walks every rendered text node
rather than reading the table above. The ink roles against the three grounds:

| Ink | on `--bg` | on `--surface` | on `--surface-2` |
|---|---|---|---|
| `--text` | 16.17 | 15.02 | 13.88 |
| `--text-dim` | 8.40 | 7.80 | 7.21 |
| `--text-mute` | 4.92 | 4.57 | **4.23** |
| `--accent` | 5.68 | 5.27 | 4.87 |
| `--accent-bright` | 7.27 | 6.76 | 6.24 |

The muted role is the tight one, and on the raised well it misses AA: **no
caption, label, or tick may be set in `--text-mute` on `--surface-2`**. That is
the whole reason Gradio's floating "Plot" chip is retired in the chrome cleanup
below rather than recoloured, and it is the constraint to check before putting
new text on a well.

The picked chip decided above composites a fourth ground the app never declares
as a token: `--accent` at 14% over `--surface`, `#38241a`. Measured on it,
`--accent-bright` is **5.64**, `--accent` **4.40**, `--text-mute` **3.83**. Only
the first clears AA, which is what settled the chip's ink.

## 3. Type system

**Revised by #132** (superseding the original zero-webfont decision). The type is
now two self-hosted faces, a display serif + a grotesque sans, the same two-face
shape Claude's own presentation system uses (a serif statement over a grotesque
support), so copy reads as designed rather than as a neutral system-sans delivery
vehicle:

| Slot | Face | Echoes | Used for | Licence |
|---|---|---|---|---|
| Display | **Fraunces** (variable) | Copernicus / Tiempos | page / section / insight titles | OFL |
| Body / UI / numeric | **Hanken Grotesk** | Styrene | ledes, body, captions, control labels, tabular figures | OFL |

Why open echoes and not the literal Claude faces: Styrene and Copernicus/Tiempos
are commercially licensed (and Copernicus is proprietary to Anthropic), so they
cannot be self-hosted and redistributed on the public Space. Fraunces and Hanken
Grotesk are the closest OFL match to that pairing and, being warm and
high-contrast, harmonise with the warm near-black ground (§2) already committed
to. Numerics ride Hanken Grotesk's `tabular-nums` rather than a third face, keeping
the system to the same two families Anthropic's does.

**Loading.** Self-hosted woff2, latin-subset, base64-embedded as `@font-face` data
URIs inside the injected stylesheet: no external request, no Space static-file
config, no CSP surprise, deploys as clean as the system-sans it replaces. The
faces are named once (a `FONTS` mapping beside `TOKENS`), so a face swap is one
edit, not a sweep. `font-display: swap` and a system-sans fallback stack keep the
first paint legible before the woff2 lands.

Eight roles (sizes lift slightly for the display face's presence):

| Role | Face | Size | Weight | Tracking | Line-height | Colour |
|---|---|---|---|---|---|---|
| Page title | Fraunces | 34 | 600 | -0.01em | 1.1 | text |
| Section heading | Fraunces | 22 | 600 | -0.005em | 1.2 | text |
| Insight title | Fraunces | 20 | 600 | none | 1.25 | text |
| Lede | Hanken Grotesk | 17 | 400 | none | 1.5 | text-dim |
| Control label | Hanken Grotesk | 12 | 600 | 0.06em, uppercase | none | text-mute |
| Body | Hanken Grotesk | 15 | 400 | none | 1.6 | text |
| Caption | Hanken Grotesk | 13 | 400 | none | 1.5 | text-mute |
| Numeric readout | Hanken Grotesk | 14 | 550 | tabular-nums | none | text |
| Subject line | Fraunces | 18 | 600 | none | 1.3 | text-dim |

_Amended 2026-11-15 (#327): the **page title** role is no longer on the page. The
full-width title row is gone, and the page's top-level heading is the short name in
the header, set in Fraunces 600 at 20 so it sits on the tab strip's row. No tab opens
with a **section heading** either, which reverses #113 (see §14). Nothing on the
page uses either role now, so the stylesheet no longer carries a rule for them._

_Added 2026-11-16 (#329): three roles for the pilot and card pages, defined under
"The pages (#329)". The **page name** is Fraunces 600 at 30 (24 at 640px and under),
-0.01em, in text: the subject's name over its page. The **tile figure** is Fraunces
600 at 26 (22), in text, proportional figures as a standalone number takes. The
**caption** on those two tabs is Hanken Grotesk 400 at 14, line-height 1.5, in text,
where the caption role above is 13 in text-mute and was drawn in the accent inside a
card; the other tabs keep that role until their own tickets._ _Amended 2026-11-16
(#333): inside a card that role is in the text colour on every tab, as the field-standing
line is. The accent is a link's._

The **subject line** (§14) is the one role added by #132: it states the subject once
above the cards, in the display face at 18 (between the insight title and the lede),
so it reads as the heading of the answer, not another control label.

- **Reading measure** bounded to ~62ch; no paragraph runs the full width of a
  wide monitor. _Exception, decided 2026-07-26: the **FAQ** answers. Its boxes are
  full-width, one question per row, and the answer fills its box, so on a wide monitor
  those lines do run past the measure. Two attempts to bring them onto it were reverted
  at the maintainer's call: capping the card left one narrow column with the rest of the
  screen empty, and flowing the cards into as many measure-wide columns as fit broke the
  one-question-per-row reading order. The layout is the decision and #85's criterion 4 is
  answered against it, so treat the FAQ measure as settled rather than outstanding._
  _Amended 2026-11-15 (#327): the page itself is now one column capped at 1,280px and
  centred, so a box is never wider than a 1440 window made it. At 1920 the content was
  1,856px wide and the longest FAQ paragraph averaged 273 characters a line; it is
  1,216px and 176 at every width from 1440 up. The FAQ's own layout is unchanged: one
  box per row, the answer filling its box._
- **Figures**: `font-variant-numeric: tabular-nums` (Hanken Grotesk carries tabular
  figures) only where digits align in a column (axis ticks, table rows). Standalone
  large numbers (coverage stats, hero) use proportional figures, since tabular makes
  a `121` look loose at display size.
- The old "Result title" role is renamed **Insight title** (§12): a plot's title
  inside its bounded card, no longer echoing the subject (§14).

## 4. One numeric convention

The same idea is written one way everywhere — every chart title, hover, legend,
axis, and readout.

| Quantity | Convention | Example |
|---|---|---|
| Share | trimmed two-decimal percent | `6.73%`, `0.12%` |
| Count + sample size | `count / total unit`, thousands-comma'd | `134 / 2,000 decks` |
| Score (inverted finish) | two decimals, with the sense once | `0.62 (1 = 1st)` |
| Imputed value | a trailing `*` on the decided number | `3 / 24*`, `0.62* (1 = 1st)` |
| Change in a share | signed, in the share's own form | `+2.31%`, `−0.4%` |

Retires the current split (`n=12` in one chart, `12/2000 decks` in another).

_Added 2026-11-16 (#329): the **change in a share** is the difference of two shares
as they are written, so the three numbers a reader sees add up (31.82% and +2.3% make
34.12%). It is always shown beside the share it started from ("from 31.82%"), which
is what makes it read as the gap between two shares and not as a rate of growth. Not
"pts": in this format a point is what a card costs._

**The imputed mark** (#166, added 2026-07-28). Every value the project decided
rather than the source supplying it carries the rule that decided it in the graph
(`Event.fieldImputed`, `Deck.normImputed`, `Deck.placementImputed`), and until #166
no surface read one. The mark closes that: one glyph, one meaning, on the number
whose provenance is in question and not on the unit or the sense beside it, with a
single legend line per plot rather than one per point (§14). Each number is marked
on its own rule, because they are decided one at a time: a placement read off a deck
title can sit against a field the source counted. The rule name stays off the
screen; a reader needs to know the number is ours, and "Rule B" answers a question
only the record can hold.

## 5. Categorical palette (charts and graph share it)

One eight-hue set serves **both** chart series and the graph's node kinds — a
Deck is the same blue as a dot or a line. Validated colour-blind-safe on the dark
surface (`#1c1917`) with `scripts/validate_palette.js` from the `dataviz` skill:
worst adjacent CVD ΔE **8.4**, all eight clear **3:1** contrast.

| Slot | Hue | Hex (dark) |
|---|---|---|
| 1 | blue | `#3987e5` |
| 2 | orange | `#d95926` |
| 3 | aqua | `#199e70` |
| 4 | yellow | `#c98500` |
| 5 | magenta | `#d55181` |
| 6 | green | `#008300` |
| 7 | violet | `#9085e9` |
| 8 | red | `#e66767` |

Re-validate after any change:

```sh
node scripts/validate_palette.js "#3987e5,#d95926,#199e70,#c98500,#d55181,#008300,#9085e9,#e66767" --mode dark --surface "#1c1917"
```

**Assignment rules (non-negotiable):**

- Assigned in **fixed order, by entity, never cycled**. A filter that changes the
  series count must **not repaint the survivors** — colour follows the entity,
  never its rank. (This reverses today's ADR-0013 colour-by-position; see §9.)
- Colour tops out at eight distinguishable series. Past eight, do **not** generate
  more hues — switch to emphasis (§6). _Narrowed by ADR-0013's #117 amendment: past
  eight, hue may continue on `palette.EXTENDED` as a **tracing** cue on faded lines,
  never as direct colour and never as identity. `assign` still refuses a ninth direct
  hue._
- **The mechanism**, which is the part that was missing and is why the rule above kept
  being broken. _Decided 2026-07-29 (#172): hue is assigned once over a **stable entity
  universe** and that map is **filtered** for the series actually drawn. Assigning over
  the drawn subset re-ranks it, and a re-rank is a repaint. That is exactly what the
  Meta tab's manual pick panel did: measured, dropping the middle of three picks moved
  a survivor from `#199e70` to `#d95926`, and re-adding the removed pick did not undo
  it, leaving the two archetypes permanently swapped._
- **The universe each chart assigns over** is named at the caller. _Decided 2026-07-29:
  the Meta trend's is every archetype in **latest-year rank order**, not
  alphabetical order, because the tracing scale the #117 amendment above allows stays
  distinct only for its first **32** slots and the widest cut draws 31 lines: rank order
  is what keeps those 31 inside that prefix. The consequence, accepted: a hand-picked
  pair no longer draws slots 1 and 2, it draws the hues those two archetypes carry
  everywhere else, which is the point of the change. It also ends the state where the
  cut chart and the manual chart painted one archetype two colours on a single scroll
  (measured, Oracle `#d55181` against `#3987e5`)._
- **Stability never costs distinctness**, which is the rule that ranks the two when a
  universe is longer than the scale. _Decided 2026-07-29 (#172), after the first
  hand-picked trio tried on the running app drew two of its three lines in one hue: the
  tracing scale is 32 hues and the universe is 126 archetypes, so four of them share
  every hue, and a filtered universe map is not safe to draw as it stands (Artifact at
  universe 82 and Breakfast at 114 both took `#25d0d0`). The universe hue is the
  default and a line moves to the first free hue only where another line on the same
  chart already holds it. Two lines the same colour is the one thing hue must never do,
  since the legend can name them and the chart still cannot tell them apart, so it
  outranks stability where the two conflict. Placed in two passes, and the order is the
  rule rather than an implementation detail: every line that can hold its own universe
  hue is given it **first**, and only the collided lines are then placed into what is
  genuinely left over. Resolving in one walk instead reads "first free hue" against a
  set that is still being built, so a hue counts as free only until the walk reaches the
  archetype that owns it, and adding a chip repaints a line already on the chart — the
  §5 failure this rule exists to close, reintroduced by the fix for it (caught in review,
  same day). The residue is narrow and is the smaller failure: a **displaced** line has
  no hue of its own to hold, so it moves as the leftovers change. The cut charts never
  reach it at all, their 31 lines rank into the distinct first 32 slots and nothing is
  displaced at any of the three cuts._
- **The exception**, decided rather than fixed. _Decided 2026-07-29 (#172): the two-line
  **head-to-head** and **archetype-timeline** charts colour by **position** (slots 1 and
  2), not by entity, and that is correct. The reason lived only in the code (the comment
  in `_head_to_head_figure`) and nowhere in this document: two distinct pilot ids can
  share a display label, so assigning by entity would dedup them onto one slot and
  collapse the two lines into one. The failure mode this section exists to prevent does
  not arise there either, since removing one of the two hides the chart rather than
  repainting a survivor. A later pass must not "fix" this into a bug._
  _Amended 2026-11-16 (#332): the share chart on an archetype's page joins them. It
  draws the page's one or two archetypes in slots 1 and 2, keyed by tag, so each is
  the hue the finishes chart under it draws it in._
- Two charts #172 checked and **closed with a reason rather than a change**: the card
  **adoption** chart's repaint is already dead (#126 capped it at the subject plus one
  card, so there is no non-tail pick to remove), and the **player leaderboard** chart has
  no control that varies its entity set at all.

## 6. Charts

- **Title** leaves the Plotly figure and becomes a page heading (result/section
  role, §3), not Plotly's font inside an image.
- **Gridlines**: solid hairline `#2c2c2a`, recessive, never dashed.
- **Axis / ticks**: muted `#898781`; ticks tabular and thousands-comma'd.
- **Marks**: keep the ADR-0013 read — thin dashed join (asserts no trend between
  years) + hollow observation markers — with a **2px surface ring** on markers so
  overlaps do not muddy. _Amended 2026-07-29 (#172): the surface ring stands in for
  "hollow" only where the points sit on bare surface. Over a translucent fill it stops
  being one: measured, the disc interior read (28,25,23) against a band of (32,44,59),
  a 36/255 channel delta, on 6 of 6 band-facing markers on the pilot rivalry chart, 66
  of 67 on the archetype pair, and 58 of 74 on the **solo** archetype timeline (whose
  fill-to-axis has the same defect and which the parked item never mentioned). On those
  charts the marker fill is now fully transparent and the ring reads against whatever it
  covers. The scope rule, which is what a later pass needs rather than the change
  itself: **occlusion is preferred over transparency only where nothing is painted
  beneath.** That is why the player leaderboard chart keeps the opaque fill (32 of its 36
  markers overlap, and nothing is drawn under them) and the rivalry charts do not (0 of
  8 markers overlap on the pilot chart, so the fill was buying nothing there).
  _Amended 2026-11-16 (#336): a band is painted under that chart now, beneath 12 of
  its 37 markers. It keeps the opaque fill: 35 of the 37 overlap another, and the
  band is faint enough (the accent at 0.10) that the fill is within 22 of 255 of it
  on every channel, against the 36 measured on the rivalry band._ The cost
  accepted: on the archetype timeline at phone width 75 of 116 discs overlap, and
  crossing rings read muddier than occluding ones. Taken because that chart's density is
  a separately acknowledged problem, and because the two rivalry charts must not drift
  apart in how the band reads._
- **Legend / emphasis**:
  - ≤ 4 series: direct end-labels.
  - 5–8 series: legend present, one colour per entity.
  - **> 8 series (the meta/adoption default cut): emphasis.** All lines recede to
    grey; one is raised in `--accent-bright` on **legend click-to-isolate**. This
    is Plotly-native and does **not** depend on point-level hover, which `gr.Plot`
    cannot provide (#78). _Superseded by ADR-0013's #117 amendment: lines recede in
    their own hue faded, the raise is that hue at full strength, emphasis applies at
    every width, and the click raises rather than isolates (Plotly's `toggleothers`
    turns the whole legend on from a hidden start)._
- **Legend placement**: never in a column beside the plot. Plotly's default puts it
  to the right *inside the figure's width*, so it takes its space from the plot: at a
  phone's 390px the meta chart's fourteen names claimed about half of it. A figure has
  one layout and no media query reaches inside it, so the legend goes where it costs no
  width at any size: horizontal, **below** the plot for the wrapping many-series charts
  (meta share, card adoption), **above** for the two-entry rivalry charts, whose range
  slider already owns the space underneath. A chart legend carries **no fill and no
  outline**: a hairline accent box round the strip was tried and dropped at the
  maintainer's call (2026-07-26), so do not add one back.
- **Chart chrome is set in the app's body face**, never left to Plotly's default
  `"Open Sans", verdana, arial`. `gr.Plot` draws into the page's own DOM, so the
  injected `@font-face` covers the SVG and naming the stack on the figure is enough.
- **Range slider** (head-to-head): moves out of the figure into a control row
  above the chart; the in-figure "◀ drag ▶" annotation is dropped.
- Single-series charts (pilot performance) carry no legend box — the title names
  the one line.

## 7. Graph

- **Ground**: `--surface` `#1c1917` — retires the white vis.js slab.
- **Nodes**: uniform dots, name beside the node, quantities on edges (the signed
  viz rule, unchanged). Weighted views size the dot within a bounded range.
- **Node colour**: drawn from the eight-slot set. Because any two kinds can be
  adjacent, kind-colour is a **secondary** cue — the node label plus an on-screen
  colour key carry identity, never hue alone. (Exact kind→slot assignment is
  finalised in the graph child issue.)
- **Grouped views** (head-to-head, co-occurrence): tint by group using slots
  1–3, the trio that stays distinct under adjacency.
- **Edges**: hairline on `--text-dim`; tinted to the group in grouped views,
  neutral otherwise. Labelled quantities stay on the edge. The neutral hairline
  was `--border` until it was measured against the ground it is drawn on rather
  than the page ground: 1.36:1 on `--surface`, a line inferred from the gap
  between two nodes rather than seen. `--text-dim` is 7.80:1, well past the 3:1
  WCAG asks of a graphical object that carries meaning, which an edge does here.
- **Height**: responsive, replacing the fixed 760/700px; details panel visible
  without scrolling.
- **Details panel**: field labels and hierarchy, the Moxfield link as an
  affordance, on the dark palette.

_Amended 2026-11-16 (#330): the colour key and the details are no longer inside the
graph's frame. They are a panel beside it, with the page's list. See "The graph
beside its panel (#330)"._

_Amended 2026-11-16 (#331): the dots are no longer uniform. The node a graph was
asked about is drawn larger, and a hovered or selected node dims what is outside its
neighbourhood. See "The subject, and a node in focus (#331)"._

## 8. States, first contact, provenance

- **One treatment for all five states**: nothing picked, running, empty result,
  a refused trend, too-large-to-draw (`_refine_alert`). Progress feedback on every
  query-running action. Add the missing Trends empty states.
- **Cold start** lands on a drawn default or 2–3 one-click examples (which one is
  a child-level decision), never an empty canvas. _Amended 2026-11-15 (#328): Cards
  takes the examples, three chips under its controls. Pilots takes neither: see
  "The controls (#328)"._
- **Provenance / credit** on screen: a coverage row (108 events, 1,083 pilots,
  4,591 decks, 4,995 cards, 2023–2026) and which snapshot the artifact was built
  from; a real favicon and social preview. _Amended 2026-07-26: the links to the
  repo, to 7phstats upstream, and to the licence were dropped from the footer at the
  maintainer's request during the v1 walkthrough, in favour of a contact line (who to
  tell when a hand-curated archetype is wrong). That credit survives in `README.md`,
  which a Space visitor never sees; #85's criterion 13 is closed on this decision
  rather than on the links being present._ _Amended 2026-11-15 (#327): how fresh the
  data is no longer sits only in the footer. The header states it on every tab
  ("162 events, updated 6 Nov 2026"), and the footer writes the date the same way._
- **Value provenance** is disclosed where a reader sees the value itself, by §4's
  mark. Shipped on the **head-to-head hover**, which is where #166's two cases meet:
  Pats Birthday Brawl's `3 / 24*` against SSWam's counted `5 / 88`, and a minted
  norm's `0.62*` against a source-scored `0.62`. _Decided 2026-07-28: the aggregate
  charts are deliberately left unmarked, and the reason is what the mark means rather
  than how rare the case is. On a surface drawing a raw value the mark says "this
  number is ours". On a mean it can only say "some of the decks behind this number
  are", a different claim in the same glyph. Measured on the artifact, of 3,268 drawn
  archetype-event points 90 rest wholly on decided norms and 22 on a mix, so both
  claims are live and no reader can tell which one an asterisk is making. Aggregate
  disclosure needs its own form (a count of the decided decks beside the deck count
  the hover already carries), not this one. The 83 decided norms of 4,567 ranked
  decks, 1.8%, stay queryable meanwhile._

## 9. ADR impact

The emphasis model (§5–6) reverses **ADR-0013**'s rule that trend charts colour by
position-in-selection and lean on the legend for identity. Emphasis is more honest
at ~15 lines, but it is a deliberate change to a signed decision — it needs domain
sign-off and an ADR amendment before the meta/adoption charts are rebuilt. Until
then, treat §5–6's emphasis as proposed, not final.

## 10. Terminology

On-screen terms match `CONTEXT.md`'s vocabulary (Pilot, Deck, Board, Archetype,
Macro, Year, Placement, Points); nothing from its `_Avoid_` list appears; labels
and buttons are written in reader language, not internal parameter names.

## 11. Information architecture (subject-first)

The app is organised by **subject**, not by output modality. It once split
Explore (graph views) from Trends (chart views) — a split by rendering pipeline,
not by what a visitor wants to know, which scattered each subject across both tabs
(Pilot head-to-head appeared in both). The subject-first regrouping (#119) put
every view under three subject tabs behind a view-picker, and #126 takes the next
step: where a subject's graph and its temporal trend share **exactly the same
filters**, one Draw renders both, stacked, so the structural answer (graph) and the
temporal answer (trend) sit together under one control set. Pilots and Cards each
collapse to **two views**, one Draw per view rendering all of that view's plots.
_Amended 2026-11-15 (#328): "one Draw" below now reads "one pick". Picking the
subject draws the view, and the pilot head-to-head alone keeps a Draw. See "The
controls (#328)"._

| Tab | Views (one Draw per view; a view renders all its plots) |
|---|---|
| **Pilots** | **Pilot overview**: neighbourhood (solo) + archetype affinity + performance over time (one pilot); **Head-to-head**: neighbourhood pair + head-to-head timeline (two pilots, second required) |
| **Cards** | **Card overview**: usage + adoption over time (one card + board); **Co-occurrence**: co-occurrence graph + adoption over time (card + second card + top-N + drop-lands, board-agnostic) |
| **Meta** | meta share over time |
| **Archetypes** | the metagame landscape: meta share against finish for one year (entered by year) |
| **Hidden gems** | rare cards crowding their own archetype's best decks (no controls) |
| **Player leaderboard** | who the leading pilots are and how long the record took to say so (#135; no controls) |

_Amended 2026-11-16 (#332): the Meta row is the **Metagame** tab and holds the
landscape under the share chart, and Archetypes is one archetype's page. See "The
field and the archetype (#332)"._

- **Two archetype tabs, two reader questions.** Meta and Archetypes (#145) are both
  about archetypes, so each states the question that is its own: Meta answers "who is
  played, over time", Archetypes answers "who wins". Each tab's lede carries its
  question, since the titles alone do not separate them. Archetypes is single-view and
  has no Draw button, following the Meta precedent: it draws Plotly aggregates, which
  are cheap enough to render on open and re-render on its year selector, where the
  Pilots tab's Draw exists for the pyvis graphs. _Amended 2026-11-15 (#328): the
  pyvis graphs draw on a pick too. The one Draw left on Pilots is the head-to-head's,
  and it is there for the second pilot, not for the graph._
  _Reversed 2026-11-16 (#332): the two tabs are no longer split by question ("who is
  played" against "who wins"). They are cut by level: Metagame is the whole field,
  share and finish both, and Archetypes is one archetype, share and finish both. See
  "The field and the archetype (#332)"._
- **Subject selected once.** The subject (a pilot, a card) is chosen at the tab
  level and reused across that tab's views, so a visitor picks a pilot once and
  moves between its views without re-selecting. Head-to-head, which needs two
  pilots, takes the second in its own control.
- **One Draw, both modalities.** Within a view, one Draw fans out to a subgraph
  query and a series query; the graph and chart pipelines stay separate under the
  hood (ADR-0013), only the presentation is combined. Plots stack vertically,
  graph(s) first then trend, each with its own heading and short explanation, the
  methodology caveats (#101) demoted into a details panel per §8, not deleted.
- **States compose.** Within one view a graph can hit "too large → refine" while a
  trend hits "not enough history → refused" (or vice versa); the plot that can draw
  draws, the other shows its own note in place, and no combination breaks a sibling.
- **One deliberate reduction.** The old Adoption-over-time view's free "compare with
  other cards" multi-select (arbitrarily many overlaid cards) is dropped; the
  co-occurrence pair (subject + one second card) becomes the only multi-card
  compare. This is a conscious reduction against #119's "preserve every view"
  principle, accepted for the cleaner two-view shape, not an oversight to re-add.

The **Adoption over time** trend is the same in both card views: Card overview scopes
it to the chosen board, Co-occurrence draws it board-agnostic (across both boards),
with no board qualifier text since there is no board control there. It plots both
cards when a second is chosen in Co-occurrence, the subject alone otherwise.
_Amended 2026-11-16 (#329): with a second card it also plots the decks running both,
as a third line. See "The pages (#329)"._

Placement note: *hidden gems* is entered by archetype and outputs cards. It once
sat under **Meta** (beside meta share) only to keep Meta from being a single-view
tab, but #125 reversed that trade-off: gems is now its own top-level tab, and Meta
holds meta share over time alone. The structure is seven tabs, **Pilots (2), Cards
(2), Meta (1: meta share), Archetypes (1: the landscape, #145), Hidden gems (1),
Player leaderboard (1: the field-wide board, #135)**, plus the FAQ tab (#133), which
carries no plot. The gems view itself is unchanged by the move (its query, archetype
entry, and the `SliceTooSmall` refusal per ADR 0012 are intact); only its placement
changed. _Amended 2026-11-16 (#332): still seven tabs, now **Metagame (1: meta share
and the landscape), Archetypes (1: one archetype's page)** in place of Meta and the
landscape's Archetypes._

_Decided 2026-07-29 (#172): the tab strip **scrolls sideways** on a narrow screen,
and every one of the seven tabs renders on it. Measured at 390px the seven tabs need
607px against 326px of container, so Gradio sliced four of them behind a "..."
overflow menu, and a tab selected from that menu left **no** button on the strip
carrying `.selected` or `aria-selected` at all: four of seven tabs could not show
"you are here" on the device the PRD names primary. The strip is the app's top-level
control, so that is the same defect §12 already fixed one level down when it stopped
clipping the fourteenth legend entry ("on that chart the legend is the control, so a
clipped entry is an archetype the reader cannot raise"). Wrapping the strip so all
seven sit at rest was built, measured and **rejected**: it costs 39px off the top of
every phone page, permanently, in a ragged three-row block of chrome, and needs a
media query to keep desktop unchanged. Scrolling costs nothing (measured: the first
lede sits at the same pixel at every width from 320 to 1440) and needs no breakpoint,
so an eighth tab adds scroll width rather than a row. This is §6's own legend argument
on the other axis: put the element where it costs no space at any size. The cost
accepted: the tabs past the edge need a swipe, with 80% of the next tab cut at that
edge as the affordance that they are there._

_Amended 2026-11-15 (#327): the strip is part of the header. From 1100px up it sits
on one row between the name and the freshness line, with all seven tabs on it and
nothing to scroll. Below that it takes the row under them at the full width of the
column, and scrolls sideways as decided above. Three numbers in that decision moved.
A tab is 44px tall at every width (it was 31px), which is the least a finger can be
asked to hit. The first lede is no longer at one pixel at every width, since the
header is one row or two. And the phone's page margin went from 32px to 16px, which
at 390px put the strip's edge in the gap after the fourth tab with nothing of the
fifth showing, so a tab's side padding is 12px there (16px elsewhere) and the edge
cuts the fifth tab's label again. The cut is still what says there is more._

_Amended 2026-11-16 (#332): the first tab is "Metagame", 33px wider than "Meta". The
header is one row from 1140px up, and a phone's tabs keep the 16px a side they have
elsewhere, the edge at 390px cutting the fourth tab's label. See "The field and the
archetype (#332)"._

---

## 12. Insight-card system (#132)

A view is N answers, not one continuous sheet. Each plot (a graph or a trend) is
bounded in its own card, so the eye reads discrete insights rather than one long
dark scroll. This retires the hairline-only result framing from #110 (§ result),
which set a plot off with a top rule alone.

- **Card.** Fill `--surface` on the `--bg` ground (the fill is what makes the card
  read as a raised object, not the rule), 1px `--border`, `border-radius: 12px`,
  padding ~`1rem 1.25rem`. Adjacent cards are held apart by a real gap (a
  `1.25rem` bottom margin), never by a shared rule, so the boundary between two
  answers is space, not a line. _Amended 2026-11-15 (#327): at 640px and under the
  padding is `0.75rem 0.75rem 1rem`, which with the page's narrower margin there
  takes a chart's plot area from 217px to 265px of a 390px screen._
- **Card head.** Inside the card, top: the **insight title** (§3), then a
  **one-line caption** (§3 caption role) naming the filters and how much came back.
  The subject is not repeated here (§14): it is stated once for the whole result set,
  above the cards. _Amended 2026-11-16 (#329): on Pilots and Cards the caption says
  what the picture holds in a sentence and no longer counts nodes or strings the
  filters on dots. See "The pages (#329)" for the caption rule._
- **Plot region, sized to content.** Retire the fixed `min(78vh,860px)` slab. Size
  each plot to how much it has to show, within a bounded band:
  - graph (pyvis iframe): every graph plot shares **one frame**, so a single uniform
    canvas reads across the tabs rather than each plot jumping to its own
    node-count-scaled height. The frame is `clamp(420px, 72vh, 760px)`: the ceiling is
    the size the pilot neighbourhood renders well at, so a desktop is unchanged in
    practice; the floor keeps a dense graph legible (the reason the earlier fixed slab
    was too small for complex graphs); and the viewport share in between is what §7
    always asked for and this section originally contradicted by pinning 760px flat,
    which on a phone was most of the screen. The pyvis document inside is a full-height
    flex column, so it takes whatever the frame gives it;
  - trend (Plotly): renders at its own natural figure height inside the card, which
    is already content-sized (a chart was never the screen-tall slab the graph was).
    _Amended 2026-07-26: a chart that carries its legend below the plot (§6) is the one
    exception, and takes a stated height instead. Plotly lays a legend out inside the
    figure's own box, so a figure left at its natural height puts that legend past its
    bottom edge and clips it: measured as the last two of fourteen archetypes missing at
    phone width, and on that chart the legend is the control, so a clipped entry is an
    archetype the reader cannot raise. The height is reserved only when a legend will
    actually be drawn, so a single-series chart keeps its natural height as before._
  The details panel stays visible inside the graph card without scrolling (§7).
  _Amended 2026-11-16 (#330): the frame is `clamp(525px, 78vh, 840px)` and the graph
  shares it with a panel. See "The graph beside its panel (#330)"._
- **Empty until drawn.** A view's cards live in a results stack that is **hidden
  until a Draw fills it** (§14), so the view opens as its controls over empty ground,
  not a row of identical "nothing yet" prompt cards. The guidance lives in the
  subject control's help text (§14). _Reversed 2026-11-15 (#328): a view is no
  longer empty until a Draw. The stack is still hidden until there is something to
  put in it, and there are still no prompt cards, but what fills it is the pick
  itself, and the ground under the Cards controls is not empty: it offers three
  cards to try. The search field says what it holds ("Search 5,514 cards"), which
  is the guidance. See "The controls (#328)"._
- **A card Group carries its own `visible` toggle.** When a card wraps children that
  toggle independently (a heading + plot + refusal note), hiding all the children still
  leaves the bordered, padded card Group drawn as an empty box. So each card that can
  refuse (the performance and head-to-head-timeline cards) is toggled as a whole, shown
  only when it has content: a partly shown results stack (e.g. head-to-head self-vs-self,
  where only the neighbourhood note shows) must never leave an empty card below.

## 13. Control-panel treatment (#132)

The controls for a view read as one distinct input surface, obviously the place
you drive from, set apart from the answers below. People do not read, so where to
click is carried by the surface, not by a label.

- **Panel.** The subject -> view -> filters -> Draw controls sit together in one
  `.control-panel`: fill `--surface-2` (the raised well token, one step lighter
  than the cards it sits above so it reads as more prominent, not less), 1px
  `--border`, `border-radius: 12px`, padding. It is visually separate from the
  result cards by fill and by space. _Amended 2026-11-16 (#332): Metagame has two.
  The landscape's year is in a panel of its own directly over the landscape, since
  it governs nothing above it. See "The field and the archetype (#332)"._ _Amended 2026-11-15 (#327): at 640px and under
  the padding is `0.75rem`, the inset the cards take there, so the panel's contents
  and the cards' stay on one edge._ _Amended 2026-11-15 (#328): the panel is one row
  of controls, and tighter for it: padding `0.6rem 1rem 0.8rem`, and at 640px and
  under `0.5rem 0.75rem 0.75rem`, the sides still the cards' inset. A control in it
  has no surface of its own: its label sits on the panel, over a field, a row of
  chips or a button 38px tall._
- **Subject stays primary.** The shared subject keeps its accent left-edge
  (`.primary-control`), now inside the panel, so the one control everything hangs
  off still reads first. _Amended 2026-11-15 (#328): the subject reads first by
  being first, and the widest thing in its row. The accent edge had not been drawn
  since the move to Gradio 6 (the field kept the edge's inset and lost the edge), and
  the row does not bring it back._
- **Draw** is the one bold action (`button_primary`, the amber accent), the
  brightest thing in the panel. _Reversed 2026-11-15 (#328): Draw is no longer the
  one bold action, because on four of the five views that had one there is no Draw.
  A pick draws. The button is kept only on the pilot head-to-head, which needs two
  pilots and so cannot draw on the first, and there it is the width of its label
  and takes no press until both are chosen. See "The controls (#328)"._
- **No control below its plot.** Every plot-affecting control lives in the panel,
  above the plots. Meta's "focus on specific archetypes" multiselect, today
  stranded *under* the cut chart, moves up into the panel with the cut control.

## 14. Copy rules (#132)

Copy is short, whole, and said once. This supersedes #113's multi-line tab ledes
and its on-surface methodology blocks.

- **Tab intro**: one short descriptive sentence, no more. (The current three-line
  Pilots lede becomes one.) _Amended 2026-11-15 (#327): the intro is the first thing
  under the header. The section heading #113 put above it on every tab repeated the
  label of the tab the strip already shows as open, so it is gone, on all seven._
- **No per-plot description paragraph** and **no on-surface methodology.** The
  `<details> How this is measured` blocks leave the plots entirely; the methodology
  they hold moves to the FAQ / Methodology tab (#133). A plot carries only its
  title and one-line caption. _Amended 2026-11-16 (#333): the words "How this is
  measured" are back on a chart, as a small link at the end of its title's line that
  opens the chart's FAQ entry. The methodology itself stays in the FAQ._
- **Subject once.** The chosen subject is stated a single time for the whole
  result (a subject line above the cards), never echoed in each card title. An
  insight is titled by its **plot type only**: `Neighbourhood`, not
  `Neighbourhood: Brennan C`. _Amended 2026-11-16 (#329): a pilot's or a card's page
  states its subject as the page head, the name in the display face over a descriptor
  and four tiles, where it was the one-line subject line. The pilot head-to-head
  keeps the subject line, since it names a pair. See "The pages (#329)"._
- **Empty-state guidance lives in the control, not a card.** Before a Draw there
  are no prompt cards: the results stack is hidden until drawn (§12), and the
  panel's own label and Draw button are the invitation. This supersedes #113's
  per-plot empty notes. Post-Draw failure states (a refused pilot, a too-thin pair)
  still speak in their own card as a one-line note in the interface's voice
  (composes with #114). Earlier revisions of this section put a "pick and Draw"
  sentence in the subject dropdown's `info`; #156 removed it under the next rule.
  _Amended 2026-11-15 (#328): with no Draw, the invitation is the search field's
  own placeholder ("Search 1,365 pilots") and, on Cards, three cards to try. Still
  no prompt cards, and the failure states are unchanged, after a pick where they
  were after a Draw._
- **Help text earns its place or is deleted** (#156). A control's `info` exists to
  change the reader's choice: what the control governs, when a pick is required.
  It never describes app behaviour ("it draws straight away", "then Draw to see
  its plots" where a Draw button is right there), and it never restates the label
  above it. Most controls need none.
- **One caption, one claim** (#156). The headline stays; the sample tail is at
  most one qualifier. Anything else that is genuinely load-bearing (how a point is
  averaged, what a shared-event restriction does) belongs in the FAQ, where this
  section already sent the methodology blocks. A qualifier the picture already
  carries (a legend entry, an axis title) is not load-bearing anywhere.
- **A control's scope is stated once** (#156), on whichever side reads more
  naturally, never from both. The Archetypes year says "Landscape only"; the
  timeline's caption says "every year in the data". Neither repeats the other.
  _Amended 2026-11-15 (#328): the year says it in its label, "Year (landscape
  only)". As a help line under the chips it made the year the one control in the
  row a line taller than the rest._
  _Amended 2026-11-16 (#332): the year is labelled "Year" and states no scope. It
  sits directly over the landscape, under the share charts, and its place says what
  it governs. The timeline's caption still says "every year in the data", now
  against the tiles over it, which are the latest year's._
- **Plain English, short and direct** (#156). Every string reads at a glance:
  ordinary words, one idea per sentence, no jargon the surface has not already
  earned. The detail lives in the FAQ, expanded just enough to answer the question
  and no further.
- **One quantity, one description** (#156). The same measure is named and
  explained the same way everywhere it appears (control, caption, FAQ), and that
  explanation matches what the code actually computes. A deck the source gave a
  placement is a **scored** deck on every surface; an event that published a
  bracket instead of its standings is defined once, in `faq-finish`, and the
  answers that depend on it point there rather than re-explaining it.
- **Measure** stays bounded to ~62ch (§3) so prose does not fragment into short
  lines inside a wide container.

## 15. Signature (#132): tried and dropped

The signature explored was the **7-point mark**: seven pips standing for 7PH's
defining mechanic, a deck built against a 7-point budget. Prototyped in the header
and before every insight title, it read on the page as an ambiguous "dot dot dot"
rather than a recognised budget, so it was **removed** at the user's call (the
frontend-design brief's own instruction to prototype a signature and confirm before
locking, resolving to "not this one").

For v1 the app carries **no separate signature mark**: its identity rests on the warm
near-black ground, the single amber accent, and the display-serif titles (§2/§3), the
"spend boldness in one place" restraint landing on the type and palette rather than an
applied emblem. A distinct signature remains open for a later pass if one is wanted.

## Chrome cleanup (#115, referenced here)

The default-Gradio footer ("Built with Gradio / Use via API / Settings") is retired
as part of this pass's default-furniture cleanup, so the app does not read as a
scaffold. (Owned by #115; noted here because it is one of #132's observed
default-Gradio tells.)

The floating "Plot" chip Gradio draws over a `gr.Plot` goes with it (#118): it names
the component's type over a card the app has already titled in its own type, and it
is the one text the acceptance pass found below WCAG AA, since it sits on the well
where `--text-mute` is 4.23:1 (§2). Retired rather than recoloured, because the card
title already says what the chip says.

## The shell (#327)

Decided 2026-11-15. The frame every tab sits in, measured on the running app at 390,
1440 and 1920px before and after. Where it reverses an earlier section, that section
carries a dated note pointing here.

- **One load, dark from the start.** One download of the page per visit (it was two),
  dark under either operating system setting, dark with scripts off, and the address
  left as the visitor typed it (§1).
- **One capped, centred column.** 1,280px with its padding, 1,216px of content, at
  every width from 1440 up (§3).
- **One slim header.** The graph mark, the short name ("7PH Graph"), the tab strip and
  the data freshness on one 44px row, where the title, the strip and a heading
  repeating the open tab took three. The first result card on Meta starts at 363px at
  1440 (it was 454px) and at 477px at 390 (it was 593px). Below 1100px the strip takes
  its own row under the name and the freshness (§11). _Amended 2026-11-16 (#332):
  below 1140px, for a first tab 33px wider._
- **The privacy note is one line.** Pilots and the player leaderboard carried the note
  in full above what they draw, 384px tall at 390px wide, which put the pilot picker
  at 697px of an 844px screen. It is one line there now (three on a phone, 72px, the
  picker at 271px): what goes, when, and that no reason is needed, word for word as
  the note promises them, with a link that opens the FAQ tab at the note in full. The
  link changes no address. The footer's line under every tab is unchanged.
- **The phone floor.** Tabs 44px tall. A chart's plot area is 265px of a 390px screen
  (it was 217px): the page's margin is 16px a side (32px) and a card's inset 12px
  (20px). Reduced motion was checked and already held: with the setting on, no element
  on the page keeps a transition or an animation, against 21 with it off.

## The controls (#328)

Decided 2026-11-15. How a tab is driven, measured on the running app at 390, 1440 and
1920px before and after. It reverses two earlier decisions, "Draw is the one bold
action" (§13) and "empty until drawn" (§12), and each carries a dated note pointing
here. Four smaller statements moved with them and carry dated notes of their own:
the cold start (§8), the Pilots tab's Draw (§11), the subject's accent edge and the
panel's padding (§13), and where the invitation and the year's scope are stated
(§14).

- **One row.** A tab's controls sit side by side on one line: a panel 87px tall at
  1440 and 1920, where it was a stack of full-width rows 205px tall on Meta and
  334px on Cards. The subject's search takes the room that is left and is the widest
  thing in the row, so every row runs out to the panel's far edge. At 640px and
  under the row stacks, one control per line. The first
  result card on Meta starts at 236px at 1440 (it was 363px) and at 372px at 390 (it
  was 477px). The ticket asked for 320px at 390 and the page does not get there: the
  intro breaks over two lines (51px), and the cut chips and the search cannot share a
  332px line, so they stack.
- **Chips for the small options.** View, Board, Year and Cards to show are chip
  selectors, as the Meta cut always was, where each was a dropdown on a line of its
  own. Only a search is a dropdown. A chip in the panel carries no radio mark, the
  Meta cut's chips included. Measured at 1440 on the Cards co-occurrence view, the
  row with the most in it: with the marks the Card search is 222px beside View chips
  of 292px, so the subject's search is not the widest thing in its row; without them
  it is 282px beside 244px. The picked chip is told apart by its fill, its edge and
  its ink.
- **Two pilots, one size.** _Added 2026-11-15, after the maintainer's look at the
  running page._ The two pilots of a head-to-head take even shares of the room,
  357px each at 1440, where the first was 448px beside 266px for the second. The
  view needs both, so neither is the lesser search. _Tried and withdrawn at the same
  look: a set width for every search (320px, and 576px for the focus search on
  Meta). It left every row stopping short of the panel's far edge, which read as
  the row being cut off. A row fills its panel._
- **Labels on one line.** _Added 2026-11-15, after the same look._ The row lines
  its controls up on their tops, and a search is the row's 38px whether it holds
  nothing, a name or a line of picks. Gradio builds the several-pick search 39.6px
  tall empty and 47px with a pick in it, and the row lined up on bottoms, so on
  Meta the search's label sat 1.6px above the chips' label, 9px once an archetype
  was focused, and the panel grew 7px on a first pick. A pick's token in the panel
  is 31px, where Gradio's is 35px. Four archetypes sit on one line at 1440. Past
  one line of picks the search grows downwards and the labels stay put.
- **A pick draws.** Picking a card or a pilot draws its view, and so does changing
  any chip or filter of that view. On Meta and Archetypes a pick always drew; Cards
  and Pilots waited on a Draw 1,174px wide. While the query runs, a line where the
  results will land says "Drawing…", the word the button used to say.
- **One Draw left.** The pilot head-to-head needs two pilots, so picking the first
  cannot draw it. It keeps a Draw, the width of its label, that takes no press until
  both are chosen. Pressing Draw with nothing picked did nothing and said nothing;
  that state cannot be reached now.
- **A search says what it holds.** Every search field has a placeholder with what it
  searches and how many: "Search 5,514 cards", "Search 1,365 pilots", "Search 143
  archetypes". The count is the number of choices the field offers.
- **Examples on Cards.** Cards opens on three chips under its controls, each a card
  to try in one click: the three cards in the most decks, lands left out, since the
  eight cards in the most decks of all are fetch lands and three of those would be
  one example three times. They leave once a card is picked and return when it is
  cleared.
- **No person is named.** No placeholder and no example names a pilot. Pilots has the
  count-only placeholder and no examples: an example there is a person put on show
  without having asked to be.
- **A result lands in view.** A focus archetype on Meta drew its chart 965px down a
  900px window and an archetype's timeline 1,330px down. After a pick the page brings
  the result's top up to the middle of the window, when it starts lower than that,
  and leaves the page alone otherwise. The middle and not the top: at the top, a
  phone's stacked controls left the screen while the reader was still using them.
  The move is smooth unless the reader has asked for less motion.

## The pages (#329)

Decided 2026-11-16. What a pilot's or a card's page states in numbers, and the three
parts every later page is built from, measured on the running app at 390, 1440 and
1920px before and after. The parts are written once in `graph7ph.parts`, apart from
the app's query glue. Two earlier sections carry dated notes pointing here: the card
head's caption (§12) and "subject once" (§14).

- **A page opens with its numbers.** A drawn pilot or card page starts with the
  subject's name in the display face (30px, 24px at 640px and under), a one-line
  descriptor, and four tiles. It was graphs first: a leading pilot's one headline
  sentence sat 2,641px down, and nothing on the page was a table.
- **Tile.** A small card: what the figure is in the control-label role, the figure in
  the display face at 26px, a quiet note under it. Four across, two across at 640px
  and under. _Added 2026-11-16, after the maintainer's look:_ the tiles stand 24px
  clear of the first card, a step more than the 20px between two cards. They sat 1px
  over it, because the space was a margin that fell outside the block Gradio sets the
  head in.
- **Pilot tiles are neutral facts.** Events played, archetypes played, the
  most-played archetype, the best finish. No tile averages a pilot's finishes; that
  reading stays in the performance chart's caption, beside the sample it rests on.
  The most-played tile says when others are level with it, and the best finish is the
  lowest place, with how often.
- **Card tiles.** Adoption in the record's latest year, the change from the year
  before (§4), the decks running it, the archetypes running it.
- **Two cards get a head about the pair.** _Added 2026-11-16, after the maintainer's
  look at the running page._ With a second card chosen, Co-occurrence is about the
  decks running both, and its head described the first card alone. It names both
  ("Brainstorm and Emperor of Bones") over four tiles. Two say how much the pair is
  played: the share of the latest year's decks running both, and its change. Two are
  the co-occurrence itself, once from each side: of the decks running one card, the
  share that also run the other (28.08% of Brainstorm's, 51.79% of Emperor of
  Bones's, each over its count pair). The graph held those two only on a hover, on
  each card's line to the hub. The first pair of tiles moves when either card is
  played more or less; the second is what the two cards have to do with each other.
  With one card the head stays that card's.
- **And a line on the chart.** _Added the same day._ The adoption chart on that view
  draws the decks running both as a third line, "Together", after the two cards'
  own. Its last point is the head's first tile, so that figure has its picture and
  its trend. A deck runs both on either board, as the two-card graph's hub counts
  it. One query feeds the line and the tiles (`trends.pair_adoption_over_time`).
- **A tile is read off the picture under it.** A pilot's events, archetypes and
  most-played archetype are counted off the neighbourhood and affinity graphs' own
  nodes, from the one query result the page also draws. The best finish is read off
  the record, which holds a place as a number where the graph holds it as a label. A
  card's tiles are the usage graph's card and archetype nodes and the adoption
  chart's last two points, and a board filter scopes them as it scopes both pictures.
  Co-occurrence draws no usage graph, so there the two usage tiles come from the same
  query run unfiltered: the figures Card overview shows on "Main or side".
- **The record.** A pilot's page closes with every deck they entered as a table:
  event, date, archetype, finish, decklist link. A new query feeds it
  (`query.pilot_record`), because no query on the page held a date or a deck's
  archetype. The finish is the place over the field (§4), each number
  marked where the project decided it. The date is the event's first deck
  registration, the proxy the head-to-head's axis already uses (ADR 0013), and the
  column is headed "Registered" and the caption says so: 64 of 162 events share that
  day with another event, because events are often written up together. Accepted by
  the maintainer on those terms, and recorded in ADR 0013.
- **A card's two lists.** Card overview lists the archetypes running the card under
  their broad classes, with the share of each one's decks that do: the usage graph's
  edges as rows. Co-occurrence lists the cards drawn beside the subject, ranked, with
  the rates their edges carry, and its caption says what a rate is a share of. Each
  list sits on the view whose graph it tabulates. A share in either list is written to
  two decimals (§4) off the two counts its edge carries, where the edge's own label
  rounds to a whole percent, and the archetypes are sorted by that share: the graph
  orders on the rounded percent, which a column of exact shares would show as out of
  order. _Changed 2026-11-16 (#330): Co-occurrence's list is the panel beside its
  graph and no longer a card under the plots._
- **One table.** Hairline rows and nothing else: no frame, no band, no fill. Numbers
  right-aligned in tabular figures. A column that qualifies the one beside it is set
  back. A group of rows is headed by a line of its own, with any figures of its own
  under the columns they belong to. _Changed 2026-11-16, after the maintainer's
  look:_ that line's name is in the display face at 16px, where it was the row's own
  face in bold and read as one more row. Text breaks wherever it must
  and a number never does, so a table is never wider than its card: at 390px the
  standings were 306px in a 284px card and the gems table ran 125 to 157px past its
  own, and both now end at the card's inside edge. On a narrow screen a column can be
  left out and its value folded in as a second line of the cell beside it, as the
  record's date is under the event's name. The standings and the gems table are this
  table too. The gems table names each archetype once, on a line over its gems, where
  it was a column with every other archetype banded.
- **Not built: a sticky header and sortable columns.** Neither came cheap. Measured
  with the header set sticky, it scrolled away with its table (400px above the window
  after a 400px scroll): ten of the wrappers Gradio puts round a card clip their
  overflow. Sorting needs script, and Gradio does not run script set inside an HTML
  block.
- **One caption rule.** A caption is ordinary text in the text colour (`--text`). It
  is never in the accent, which is kept for what can be clicked: a decklist link is
  the one accent in a table. It sets apart at most one figure, in the
  blue the charts draw with (§5). It is written as short sentences, not as fragments
  joined with middle dots, and it does not count nodes ("106 nodes"). A filter is said
  once: the board is in a card page's descriptor and the adoption chart's caption,
  and no longer in the usage graph's too. The app's one imputed mark has one legend
  wherever it is drawn ("* marks a number this project worked out, not one the source
  recorded"). Applied on this ticket to every caption on Pilots and Cards. The other tabs keep the earlier caption
  roles (`.t-caption`, `.t-fieldstat`, accent inside a card) until their own tickets
  reach them. _Changed 2026-11-16 (#333): the accent is gone from those two roles on
  every tab, since a name is now a link in it; their wording and figures still wait
  for their own tickets. A decklist link is no longer the one accent in a table: a
  name that opens a page is one too. See "A name opens its page (#333)"._

## The graph beside its panel (#330)

Decided 2026-11-16, after the maintainer looked at three layouts on the running app
(today's page, a side panel, a card floating over a full-width graph) and chose the
side panel. Measured on the running app at 390, 1440 and 1920px before and after.
The prototype is kept on the branch `prototype/330-graph-side-panel`.

- **Two columns.** A graph sits beside a panel at one height: the graph about two
  thirds of the card (2.2 to 1), the panel the rest, never under 300px. It was alone
  in a frame 90% of the window tall, with its key over it and the answer to a click
  at its far edge: on a 900px window the frame was 810px and the answer sat at
  766px of it, in view only with the page scrolled exactly into place.
- **The panel holds three things**, parted by hairlines: the colour key, the facts
  of whichever node is clicked, and the page's list. It scrolls inside its own
  height, so a long list never moves the graph.
- **A frame fitted to the window.** `clamp(525px, 78vh, 840px)`, so the whole frame
  is on screen under the card's title. At 1440 by 900 that is 702px, down from 810,
  and the drawing is no smaller: 638px tall on every view, against 634. A graph is
  fitted by its height far more often than by its width, and the frame's own 16px
  padding is gone, which a fit's own margin made redundant. At 1920 by 1080 the
  drawing is smaller than it was, by about 5% for a pilot's graph (719 by 722 against
  756 by 761) and 11% for two cards' (719 by 671 against 805 by 761). The page's
  column is capped at 1280px at every width (#327), so a wider window gives the graph
  no more width, where the old frame grew with the window's height to 950px.
- **A click is answered where the reader is looking.** The clicked node's facts show
  at the top of the panel. If the page has the top of the panel off screen, or the
  reader has scrolled the panel down its list, the answer is brought into view by the
  least scroll that shows it, and by none when it already is.
- **Each kind says what a reader asks next.** The answer was "Kind: Deck, Name:
  Grixis", which the label had already said. A deck now states its pilot, event,
  finish, date and archetype, with its list one click away. A placement states its
  deck's. An event states its date and field. A pilot counts the events or the
  broad classes round them. A broad class or an archetype states the events played
  on it, or the decks running the card and their share. A card states its decks and
  share, or the rate its edge carries. A gem states the whole of its claim. No kind
  states only its kind and name. A node with more than one neighbour also names up
  to eight of them, each a way to that node. A deck and a placement name none, nor
  does any node of a co-occurrence graph, where the list under the facts is those
  same neighbours in full.
- **"Macro" is not printed.** The key and a node's hover said "Macro", which
  CONTEXT.md rules out of every surface. Both say "Broad class".
- **The list, and what it is on each view.** A pilot's neighbourhood: their latest
  ten decks, with the whole record still the last table on the page. A
  head-to-head: the events both played, with each pilot's finish. An affinity
  graph: every archetype played, by events. A card's usage: the ten archetypes
  running it most, with the full table still under the plots. Co-occurrence: every
  card the graph draws, with its rate, which was a card of its own under the plots
  and is no longer drawn there. The gem view: the gem table.
- **A row is its node.** A click on a row of the list, or on a neighbour named under
  a node's facts, selects that node in the graph and shows its facts. The selected
  node's row is marked in a tint of the accent. _Amended 2026-11-16 (#333): a name in
  a row that has a page of its own is a link to it, and a click on the name itself
  opens the page. The rest of the row still selects the node. See "A name opens its
  page (#333)"._
- **A selection cannot be missed.** _Added 2026-11-16, after the maintainer's look._
  The selected node takes a 3px ring in the text colour and every edge it reaches is
  drawn in that colour, 2.5px thicker. The widget's own highlight was a ring in a
  shade of the node's fill and one canvas unit on each edge, under half a pixel at
  the zoom a hundred nodes are fitted at. Both are sized on screen, from the zoom,
  as the labels are.
- **The gem table's last column is "Odds it is luck".** It was "By chance", which a
  newcomer could read as how the card got into the deck.
- **The imputed mark's legend rides the facts that carry a mark.** The panel prints
  a finish or a field for every deck drawn, most of them the source's own. The
  legend is said once on a card, as near the mark as it can be: in the card's head
  where the graph's own labels carry a mark, otherwise over the list if the list
  prints one, and under a node's facts only when nothing above them has. A caption
  in the panel is the caption role a size down, 13px.
- **Phone.** Under 760px the two stack: the list first, then the key, the graph at
  525px, and the clicked node's facts under it (ADR 0018 stands: the graph is not
  the readable thing there, so the list leads).
- **Labels in the app's face.** The graph is a document of its own and loaded no
  face, so its labels were drawn in Arial. It now asks the app for Hanken Grotesk,
  one 35 KB download cached for a year, and does not fit the graph until the face is
  in: a label's box is measured at the fit, and a face arriving later leaves the
  graph fitted to another face's letters.
- **What it costs.** The page as transferred is 236.4 KB, from 233.6 KB. The chart
  library is unchanged. Every node's facts ride with its graph, so a draw is heavier
  on the stream, which is not compressed: a 100-node pilot neighbourhood is 133 KB
  of markup, from 90 KB, and a card's usage graph 75 KB, from 58 KB.

## The subject, and a node in focus (#331)

Decided 2026-11-16, after the maintainer looked at three variants on the running app
at 1440px (today's graph; the subject marked, with a focus on hover and selection;
and that with only the best-connected nodes named at rest) and chose the second as
shown. Measured
on the running app at 390, 1440 and 1920px before and after, on Brainstorm's usage
graph (106 nodes), a 181-node pilot neighbourhood and the head-to-head of the two
top-ranked pilots (47 nodes). The prototype is kept on the branch
`prototype/331-subject-and-focus`.

- **The subject stands out.** The node a graph was asked about is a larger dot with
  a larger name: 40 of vis.js's units against the 25 of every other dot, and 15px
  on screen against the 12px of every other name. A card's usage graph drew all 106
  of its nodes one size, the card among them. The subject is the pilot, or both of a
  head-to-head; the card, or the one or two cards a co-occurrence graph starts
  from. The gem view is about the whole format and marks nothing.
- **The two pilots of a head-to-head rest apart, by weight.** They came to rest
  26px apart at 1440px, one dot over the other, because every event they share is
  tied to both. Each is now given a mass of 25 against the 1 of every other node,
  and they rest 168 to 185px apart over the runs measured, the layout differing a
  little each draw (193 to 196px from 32px at 1920px, 65 to 70px from 12px at 390px).
  The weight goes to a subject that shares a neighbour with another subject, which
  is what pulls a pair to one point. Two cards that share no deck are drawn as two
  nodes with nothing between them, and get none: weight threw that pair 600px and
  more apart, fitted as two specks.
  The ticket asked for starting positions, which do nothing: started 300, 600 or
  1,200 units apart, with or without vis.js's own pre-layout, the pair settles 23px
  apart. This is the one change to the physics, and it is a parameter of two nodes:
  physics stays on, neither pilot is pinned, and each still answers a drag (ADR
  0018). It spreads the graph, so the head-to-head is fitted at a smaller zoom,
  0.47 to 0.50 from 0.56 at 1440px. The other two graphs are fitted as they were.
- **A node in focus brings its neighbourhood forward.** The focus is the node under
  the pointer, or else the selected one. Every node it does not reach and every edge
  that does not touch it is taken 82% of the way to the ground: a dot lands between
  1.17:1 and 1.30:1 on it, an edge at 1.40:1, and a name, taken 72% of the way, at
  2.33:1. Each edge it reaches is drawn in the text colour and 2.5px wider, as a
  selection's edges were since #330. Nothing changes at rest, and losing the focus
  puts every colour back.
- **The pointer comes first.** With one node selected, the pointer over another
  lights that other's neighbourhood, and the selection takes the focus back when the
  pointer leaves. Meanwhile the selected node keeps its ring and its shade, since
  the panel beside the graph is still showing its facts.
- **A subject's dot never dims**, wherever the focus is, and is drawn over whatever
  the layout leaves lying across it. The neighbourhood in focus is drawn over
  everything it is not, and a dimmed name loses its halo, so nothing dimmed cuts
  across what is lit. vis.js itself still draws a selected node, then a hovered one,
  over all of these.
- **The pointer gives the focus up when it leaves the graph.** vis.js is not told
  that it left, so the last node it crossed on the way to the panel kept the focus,
  and a row clicked there lit the wrong neighbourhood.
- **An edge selected on its own stays lit**, in the text colour and 2.5px wider, as
  it has been since #330, and dims nothing: the focus is a node's.
- **A node in focus states its quantities.** At rest an edge's quantity is drawn at
  vis.js's own size scaled by the zoom, between 5 and 8px on the three graphs
  measured. The edges a
  node in focus reaches state theirs in the text colour at the size of a name, and
  the other quantities are not drawn while there is a focus. On a node with twenty
  or more edges those quantities overprint one another ("tempo" on Brainstorm's
  graph); the panel's list carries the same figures.
- **A phone has no pointer**, so there the focus is the selection: a tap on a node.
  The graph is not asked what the pointer is over on a device that cannot hover.
  A browser makes up mouse events for a tap, and they land where the finger was
  after the tapped node's row has scrolled the page under it, 65px down the graph
  on the suite's pilot: on 5 loads in 25 that was another node, which took the
  focus from the tapped one.
- **Not taken: only the best-connected nodes named at rest.** It left 8 names of
  106 on the card graph and was the only variant that stopped the archetype names
  overprinting round each broad class. On the pilot graph it still left the 60 event names overprinting at the
  centre, and hid the deck and finish names of the outer ring, which read today.
  Every name stays drawn.
- **What it costs.** The page as transferred is 233.6 KB, from 231.6 KB: the gem
  graph is drawn as the page loads and carries the focus script. The chart library
  is unchanged at 1,245.5 KB. Each graph drawn is 5.8 KB heavier on the stream,
  which is not compressed: Brainstorm's usage graph is 56.9 KB of document, from
  51.1 KB.

## The field and the archetype (#332)

Decided 2026-11-16. Which tab answers what about archetypes, measured on the running
app at 390, 1440 and 1920px before and after. It reverses the split #145 recorded
(§11: Meta for "who is played", Archetypes for "who wins"), and four earlier
statements carry dated notes pointing here: the tab table, the two-tabs bullet and
the header's one-row width (§11), and the year's scope (§14).

- **Why the split went.** To see how one archetype is doing took two tabs and two
  picks: its share history on Meta under "focus on specific archetypes", and its
  finishes on Archetypes, picked again. Both answers landed off screen (965px and
  1,330px down a 900px window before #328 brought them into view), and no page
  stated the archetype's headline numbers at all.
- **Cut by level.** **Metagame** (the Meta tab, renamed) is the whole field: meta
  share over time, then the metagame landscape. **Archetypes** is one archetype's
  page, working like Cards and Pilots. Each tab now holds both questions, how much
  and how well, at its own level.
- **The year sits beside the landscape.** It governs the landscape alone, so it is
  in a panel of its own directly over the landscape's card, under the share charts,
  labelled "Year". In the tab's top panel it would have read as governing the share
  chart too.
- **An archetype's page.** Pick one and the page opens with its name, a descriptor
  (the years it has a deck in) and four tiles, then a card each for its share over
  time and its finishes over time. At 1440 the name is at 240px and the tiles at
  315px, with no scroll; the finishes chart was the first thing a pick drew and
  started 1,017px down.
- **The tiles are the archetype's dot on the landscape, in numbers.** For the
  record's latest year: its share of that year's decks (over the count pair), its
  rank by share among the archetypes played, its mean finish, and the events that
  finish rests on (with the scored decks). Archetypes on as many decks share a
  rank, and the tile says how many are level: 23 deck counts are held by more than
  one of 2026's 131 archetypes. An archetype with no deck in the latest year (6 of
  the 137 offered) says so in every tile.
- **The mean carries its interval.** The finish tile's note is the 90% range the
  landscape draws as that dot's bar ("90% range 0.50 to 0.62, 1 = first"), since no
  mean is stated without one (ADR 0013, amended for this tile). In 2026, 110 of the
  131 means have a range that straddles the middle of the field, and a tile is the
  one place a mean would otherwise stand alone at 26px.
- **A tile is read off what the app already holds.** The share and the rank are read
  off the meta-share matrix, so the share is its chart's last point. The finish, its
  range and the events are the archetype's cell of the latest year's landscape, read
  once when the app starts. No query was added. The archetype's own timeline is the
  second reading: measured on the record, that year's points (the events counted,
  the mean weighted by the decks each rests on) equal the landscape's cell for all
  137 offered archetypes in all four years, and a test holds the tiles to the
  timeline, to the landscape's hover strings and to the ends of its bars.
- **A second archetype compares, and the page stays the first one's.** The
  descriptor says who it is compared with, the share chart draws both lines, and
  the finishes chart draws the events both attended with its tally and its coin
  check, as before. The tiles stay the first archetype's. A pair that never met
  refuses in the finishes card alone: the head and the share chart need no shared
  event.
- **One colour down the page.** The page's two charts draw an archetype in the same
  hue, the palette's first for the subject and second for the one it is compared
  with. So the hue is the page's, not the one the archetype carries on Metagame's
  share chart (§5).
- **The share chart is drawn plainly.** Each line once, at full strength, under a
  legend titled "Archetype". The field chart draws every line twice, faded and
  raised, with a legend that says to click: how a reader finds one line among
  thirty, and nothing to do to the one or two they picked.
- **The caption rule reaches the page.** Both captions are sentences in the text
  colour with at most one figure set apart (§"The pages (#329)"). The finishes
  caption keeps its tally and its coin check word for word; the span, which was a
  clause behind a middle dot, is a sentence ("Counted over every year in the
  data."), and the caret's legend is a caption of its own. Metagame's two cards
  keep the earlier roles until their own tickets (#334, #337).
- **The picker is unchanged.** It offers the archetypes that can draw a finishes
  line, with the event count in the label, as the timeline's picker did. The six
  archetypes scored at fewer than two events have no page; their share is on
  Metagame's focus chart.
- **Not taken: a tally on the pilot head-to-head.** ADR 0013 keeps that view as
  individual events, because two pilots usually share one or two a year, and a
  count there would be a scoreboard between two named people. A test now holds it.
- **Not taken: top pilots or signature cards per archetype.** Each would need a new
  query.
- **The header makes room for the longer name.** "Metagame" is 33px wider than
  "Meta", so seven tabs need 659px where they needed 626px. The header is one row
  from 1140px up (it was 1100px, §11 and "The shell (#327)"): from 1100 to 1105px
  the row clipped the last tab by up to 5px with nothing saying the strip scrolled.
  On a phone the tabs take their own 16px a side again, where #327 took four pixels
  off each: at 390px that override now put the strip's edge in the gap before
  "Pilots", and without it the edge cuts "Hidden gems", which is what says the
  strip scrolls (#172).
- **What it costs.** The landscape opened its own tab at 253px and now starts
  1,134px down Metagame at 1440 and 1920 (1,254px at 390), a screen below the share
  chart. The page as transferred is 239.3 KB, from 239.4 KB (239,299 bytes, from
  239,387), and the chart library 1,275.4 KB, unchanged. No text sits on a new ground:
  the contrast audit fails the same one pair on main and on the branch (the dimmed
  legend entries, 2.10).

## A name opens its page (#333)

Decided 2026-11-16. Names were plain text everywhere: from the leaderboard to a
pilot's page was the Pilots tab and the name typed in, and from a gem to its card
the same on Cards. Measured on the running app at 390, 1440 and 1920px before and
after.

- **The trial came first, and passed.** The ticket rested on a Gradio 6 feature
  nothing here had used: custom markup that raises a click the app can answer
  (`trigger('click', ...)` in an HTML block's `js_on_load`). Tried on the standings
  alone, on the real record at 1440px and at 390px as a touch device: the click
  reached the app with the pilot's key once per click, the page was drawn 0.58 to
  0.59 s later, no document was requested, a mark set on the window before the click
  was still there after it, and the address was unchanged. The result is on the
  issue.
- **A name is a link, and one script answers them all.** A pilot, a card or an
  archetype named in a table, a list or a node's details is a link carrying its kind
  and its key. One hidden block at the foot of the page listens on the document, so a
  table redrawn after a pick needs nothing wired to it. The click opens the subject's
  tab and raises the block's click; the app answers by setting the subject's picker,
  and the picker's own change draws the page, as a typed pick does.
- **No address, no reload.** The link holds no address and the click changes none:
  shareable addresses were ruled out, so nothing in this app is reached by one.
- **A subject's page is its tab's first view, with nothing compared.** A pilot opens
  on the overview whatever view Pilots was left on, a card on its overview, and an
  archetype alone, whatever it was last compared with.
- **Where a name is a link.** Pilots in the standings. The card and the archetype in
  the gems table. The archetype in a pilot's record, in the latest decks beside the
  neighbourhood, in the archetypes played beside the affinity graph, and in both of
  a card's archetype lists. The card in the played-with list. In a clicked node's
  details: the node's own name, a deck's pilot and archetype, and the archetype a
  gem was found in.
- **Where it is not.** A name is a link only when its picker could draw it. Six
  archetypes of the real record have no finishes line and so no page, and stay text.
  A page never links to itself: on a pilot's or a card's overview, that pilot or
  card is text in its own graphs' panels, where a link would open what is already
  open. A head-to-head's two pilots and the cards a co-occurrence graph starts from
  are links, to their overviews. The neighbours listed under a node's facts still select their
  node (#330), so the graph can be walked from the panel; the node walked to then
  names its page. The tiles, the head-to-head's column headings and anything a chart
  draws are not links. No query changed: a graph's nodes already carry their keys, and a
  record's names are matched to the pickers' own catalogues.
- **In a panel row, the name opens the page and the rest of the row selects the
  node.** Both were wanted of one row. The name is in the accent and the row is not,
  which is what tells them apart.
- **A jump to another tab starts at the top of it.** A name far down the standings
  would otherwise open a page already drawn part way down it. A jump within a tab
  leaves the window alone, and the draw brings its result into view as any pick's
  does (#328).
- **Each chart says where it is explained.** The nine charts each carry "How this is
  measured" at the far end of the title's line, a link to the chart's own FAQ entry.
  It is the privacy line's jump, and one script now answers both. The standings are
  a table and carry none; the graphs carry none, though four have an entry
  (affinity, usage, co-occurrence, gems), since the ticket asks it of charts.
- **The accent is a link's, everywhere now.** A card's subtitle was in the accent
  on Metagame, Hidden gems and the leaderboard: the standings' caption
  sat in orange over a table of names that are now orange links. Inside a card both
  older caption roles are in the text colour, and a browser test walks every tab and
  finds nothing in the accent inside a table or a caption but a link. A decklist
  link keeps the accent: it is a link.
- **A name is reached by keyboard.** It is a link, so Tab reaches it, it shows the
  app's focus ring (solid, in the bright accent), and Enter follows it.
- **What it costs.** The page as transferred is 240.4 KB, from 239.3 KB (240,408
  bytes, from 239,326), and the chart library 1,275.4 KB, unchanged. A draw is a
  little heavier for the links in it: a pilot's record card of 33 decks is 10.8 KB
  of markup from 8.8 KB, a staple card's archetypes card 15.0 KB from 9.3 KB, the
  gems card with its graph 57.3 KB from 54.8 KB. A click from the head-to-head view
  draws twice, once for the pilot and once for the view (1.4 to 1.8 s in the trial,
  against 0.6 s).

## The leaderboard (#336)

Decided 2026-11-16. The tab opened on its chart, the chart drew 176 dashed lines
behind its eight named ones, and the standings started a screen down with each rank's
range written as two numbers. Measured on the running app over the real record at 390,
1440 and 1920px, before and after.

- **The standings come first.** The table starts 222px down at 1440 and at 1920,
  from 985px, and 390px down at 390, from 1,169px. The chart follows it: 2,042px down
  on a desktop and 2,719px on a phone.
- **A rank's range is drawn beside the numbers that state it.** A bar on a track,
  in the last column. The track is every place in the field, first at its left end
  and last at its right (184 places today), and it is the same track in every row, so
  two rows' bars can be compared. The bar is placed by the two ranks the row states,
  and a browser test reads all fifty bars back off the page as ranks at the three
  widths.
- **The track names its ends and marks the rank.** _Added after the maintainer's
  look, the same day: the bar alone was "not intuitive"._ Nothing on the row said
  what the track was or where on it the pilot stood. The column's header now reads
  "1st" over the track's left end and "184th" over its right, and a bright tick on
  each track marks the pilot's own rank, so a row reads: the tick is where they
  stand, the bar is how far that could move. The caption says so: its one qualifier
  (§14) now reads the drawing and the numbers together, "the tick is a pilot's
  rank, and the bar and the Rank CI beside it are where that rank landed in 90% of
  a thousand redraws of their own record", and the FAQ's answer on how settled the
  order is says it at length. A tick and not a dot, since a top
  rank's bar is a few pixels long and a dot hid it. Every one of the 184 ranks sits
  inside its own range, so no tick stands off its bar. The same browser test holds
  each tick to its row's rank and the two names to the track's two ends. It caught
  the names 8px off the tracks: the header's label box was sized in the header's
  smaller face. The marks are not text, so their contrast is worked out by hand from
  the tokens (§2): the bar (`--text-mute`) on the track (`--border`) is 3.36, the
  tick (`--text`) on the bar 3.28 and on the track 11.02. The bar was `--text-dim`
  (5.72 on the track) until the tick needed to be the brighter of the two.
- **The scale is the whole field, not the rows shown.** The fifty rows' ranges end at
  127th, so a scale cut there would draw longer bars, but its right end would mean
  nothing a reader could name. On the whole field a top rank's bar is short (1 to 24
  is 33px of 256) and the overlap of the leading group still reads.
- **The bar is neutral.** Colour in this table names a drawn pilot, and the accent is
  a link's. No bar is drawn behind the score: the top scores differ in the third
  decimal, so the bars would all be one length.
- **On a phone the track sits over its numbers.** Beside them it took the room the
  names need. The track is 60px there and 256px on a desktop. One name wraps to a
  second line, as one did before. What it costs: a row is 42px tall, from 30px, so the
  tab is 3,613px long at 390, from 3,001px.
- **One band stands for the other contenders.** From the lowest of their scores to
  the highest at each date, in the accent at 0.10, with the legend saying so ("All
  other contenders, lowest to highest"). The reach and not the middle of the field,
  for the reason ADR 0017's dated note gives. 0.10 was settled by eye against 0.07
  and 0.16.
- **The axis title is a whole phrase:** "Every major event played up to this date",
  where "Every major played up to" left the tick under it to finish the sentence.
- **Ranks, scores and ranges are as they were.** No query changed and no build input
  was touched.
- **What it costs.** The page as transferred is 236.9 KB, from 240.6 KB (236,850
  bytes, from 240,593): 176 lines of five points each left the page and fifty bars
  joined it. The chart library is 1,275.4 KB, unchanged. No text sits on a new ground:
  the contrast audit of the tab finds none below AA on main (912 text nodes) or on
  the branch (918: the two end names at three widths, in the header's own ink).
