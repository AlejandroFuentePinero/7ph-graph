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

The **subject line** (§14) is the one role added by #132: it states the subject once
above the cards, in the display face at 18 (between the insight title and the lede),
so it reads as the heading of the answer, not another control label.

- **Reading measure** bounded to ~62ch; no paragraph runs the full width of a
  wide monitor.
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

Retires the current split (`n=12` in one chart, `12/2000 decks` in another).

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

## 6. Charts

- **Title** leaves the Plotly figure and becomes a page heading (result/section
  role, §3), not Plotly's font inside an image.
- **Gridlines**: solid hairline `#2c2c2a`, recessive, never dashed.
- **Axis / ticks**: muted `#898781`; ticks tabular and thousands-comma'd.
- **Marks**: keep the ADR-0013 read — thin dashed join (asserts no trend between
  years) + hollow observation markers — with a **2px surface ring** on markers so
  overlaps do not muddy.
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
- **Edges**: hairline on `--border`; tinted to the group in grouped views,
  neutral otherwise. Labelled quantities stay on the edge.
- **Height**: responsive, replacing the fixed 760/700px; details panel visible
  without scrolling.
- **Details panel**: field labels and hierarchy, the Moxfield link as an
  affordance, on the dark palette.

## 8. States, first contact, provenance

- **One treatment for all five states**: nothing picked, running, empty result,
  `SliceTooSmall`, too-large-to-draw (`_refine_alert`). Progress feedback on every
  query-running action. Add the missing Trends empty states.
- **Cold start** lands on a drawn default or 2–3 one-click examples (which one is
  a child-level decision), never an empty canvas.
- **Provenance / credit** on screen: a coverage row (108 events, 1,083 pilots,
  4,591 decks, 4,995 cards, 2023–2026) and which snapshot the artifact was built
  from; links to the repo, to 7phstats upstream, and to the licence; a real
  favicon and social preview.

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

| Tab | Views (one Draw per view; a view renders all its plots) |
|---|---|
| **Pilots** | **Pilot overview**: neighbourhood (solo) + archetype affinity + performance over time (one pilot); **Head-to-head**: neighbourhood pair + head-to-head timeline (two pilots, second required) |
| **Cards** | **Card overview**: usage + adoption over time (one card + board); **Co-occurrence**: co-occurrence graph + adoption over time (card + second card + top-N + drop-lands, board-agnostic) |
| **Meta** | meta share over time |
| **Archetypes** | the metagame landscape: meta share against finish for one year (entered by year) |
| **Hidden gems** | over-indexing cards for an archetype (entered by archetype) |

- **Two archetype tabs, two reader questions.** Meta and Archetypes (#145) are both
  about archetypes, so each states the question that is its own: Meta answers "who is
  played, over time", Archetypes answers "who wins". Each tab's lede carries its
  question, since the titles alone do not separate them. Archetypes is single-view and
  has no Draw button, following the Meta precedent: it draws Plotly aggregates, which
  are cheap enough to render on open and re-render on its year selector, where the
  Pilots tab's Draw exists for the pyvis graphs.
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

Placement note: *hidden gems* is entered by archetype and outputs cards. It once
sat under **Meta** (beside meta share) only to keep Meta from being a single-view
tab, but #125 reversed that trade-off: gems is now its own top-level tab, and Meta
holds meta share over time alone. The structure is five tabs, **Pilots (2), Cards
(2), Meta (1: meta share), Archetypes (1: the landscape, #145), Hidden gems (1)**,
plus the FAQ tab (#133), which carries no plot. The gems view itself is unchanged by
the move (its query, archetype entry, and the `SliceTooSmall` refusal per ADR 0012
are intact); only its placement changed.

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
  answers is space, not a line.
- **Card head.** Inside the card, top: the **insight title** (§3), then a
  **one-line caption** (§3 caption role) naming the filters and how much came back.
  The subject is not repeated here (§14): it is stated once for the whole result set,
  above the cards.
- **Plot region, sized to content.** Retire the fixed `min(78vh,860px)` slab. Size
  each plot to how much it has to show, within a bounded band:
  - graph (pyvis iframe): every graph plot shares **one frame height** (`GRAPH_HEIGHT`,
    760px), the size the pilot neighbourhood renders well at. A single uniform frame
    reads as one coherent canvas across the tabs rather than each plot jumping to its
    own node-count-scaled height; it is tall enough that a dense graph lays out legibly
    (the reason the earlier fixed slab was too small for complex graphs) and not so tall
    that a small one floats in emptiness;
  - trend (Plotly): renders at its own natural figure height inside the card, which
    is already content-sized (a chart was never the screen-tall slab the graph was).
  The details panel stays visible inside the graph card without scrolling (§7).
- **Empty until drawn.** A view's cards live in a results stack that is **hidden
  until a Draw fills it** (§14), so the view opens as its controls over empty ground,
  not a row of identical "nothing yet" prompt cards. The guidance lives in the
  subject control's help text (§14).
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
  result cards by fill and by space.
- **Subject stays primary.** The shared subject keeps its accent left-edge
  (`.primary-control`), now inside the panel, so the one control everything hangs
  off still reads first.
- **Draw** is the one bold action (`button_primary`, the amber accent), the
  brightest thing in the panel.
- **No control below its plot.** Every plot-affecting control lives in the panel,
  above the plots. Meta's "focus on specific archetypes" multiselect, today
  stranded *under* the cut chart, moves up into the panel with the cut control.

## 14. Copy rules (#132)

Copy is short, whole, and said once. This supersedes #113's multi-line tab ledes
and its on-surface methodology blocks.

- **Tab intro**: one short descriptive sentence, no more. (The current three-line
  Pilots lede becomes one.)
- **No per-plot description paragraph** and **no on-surface methodology.** The
  `<details> How this is measured` blocks leave the plots entirely; the methodology
  they hold moves to the FAQ / Methodology tab (#133). A plot carries only its
  title and one-line caption.
- **Subject once.** The chosen subject is stated a single time for the whole
  result (a subject line above the cards), never echoed in each card title. An
  insight is titled by its **plot type only**: `Neighbourhood`, not
  `Neighbourhood: Brennan C`.
- **Empty-state guidance lives in the control, not a card.** Before a Draw, the
  "pick and Draw" invitation rides the subject dropdown's **help text** (its `info`),
  one place at the control you drive from. This supersedes #113's per-plot empty
  notes: the results stack is hidden until drawn (§12), so no duplicated prompt cards
  appear. Post-Draw failure states (a refused pilot, a too-thin pair) still speak in
  their own card as a one-line note in the interface's voice (composes with #114).
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
