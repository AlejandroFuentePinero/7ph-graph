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

- The Plotly `#9ca3af` text and the `_PALETTE` mid-luminance band are re-derived
  against the dark surface (the band is loosened or removed, and re-justified if
  kept).
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

## 3. Type scale

System sans throughout: `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
sans-serif`. Zero webfont load; deploys clean on the Space. Eight roles:

| Role | Size | Weight | Tracking | Line-height | Colour |
|---|---|---|---|---|---|
| Page title | 30 | 700 | -0.02em | 1.12 | text |
| Section heading | 20 | 650 | -0.01em | 1.25 | text |
| Result title | 22 | 680 | -0.01em | 1.2 | text |
| Lede | 17 | 400 | — | 1.5 | text-dim |
| Control label | 12 | 600 | 0.06em, uppercase | — | text-mute |
| Body | 15 | 400 | — | 1.6 | text |
| Caption | 13 | 400 | — | 1.5 | text-mute |
| Numeric readout | 15 | 550 | — | — | text |

- **Reading measure** bounded to ~62ch; no paragraph runs the full width of a
  wide monitor.
- **Figures**: `font-variant-numeric: tabular-nums` only where digits align in a
  column (axis ticks, table rows). Standalone large numbers (coverage stats,
  hero) use proportional figures — tabular makes a `121` look loose at display
  size.

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
  more hues — switch to emphasis (§6).

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
    cannot provide (#78).
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

The app is organised by **subject**, not by output modality. Today it splits
Explore (graph views) from Trends (chart views) — a split by rendering pipeline,
not by what a visitor wants to know, which scatters each subject across both tabs
(Pilot head-to-head appears in both). v1 regroups the **same nine views, all
preserved**, under three subject tabs. This is reorganisation, not reduction:
nothing is added or removed, every query, filter, and view reachable today stays
reachable.

| Tab | Views (all existing, kept as-is) |
|---|---|
| **Pilots** | neighbourhood & head-to-head (graph), archetype affinity (graph), performance over time (chart), head-to-head timeline (chart) |
| **Cards** | usage (graph), co-occurrence (graph), adoption over time (chart) |
| **Meta** | meta share over time (chart), hidden gems (graph) |

- **Subject selected once.** The subject (a pilot, a card) is chosen at the tab
  level and reused across that tab's views, so a visitor picks a pilot once and
  moves between their graph and their trends without re-selecting. Head-to-head,
  which needs two pilots, takes the second in its own control.
- **Both modalities under one subject.** A subject's graph views and its
  time-series views sit together; the graph and chart pipelines stay separate
  under the hood (ADR-0013), but that split is no longer visible in the
  navigation.
- Within a tab, a view-picker selects the specific view — the same pattern the
  current tabs use, now scoped to one subject.

Placement note: *hidden gems* is entered by archetype and outputs cards; it sits
under **Meta** (beside meta share) rather than Cards, so the Meta tab is not a
single-view tab and the two archetype/meta-level views stay together.
