# v1 acceptance evidence (#118)

The evidence for #85's two measurable gates: a screenshot of every tab and state at
phone and desktop width in the shipped dark theme, and a WCAG AA contrast pass on
what those screenshots actually rendered. The third gate, human sign-off, is
recorded on #85 itself.

Both are produced by one command, so this folder is rebuilt rather than maintained
by hand:

```sh
uv run --with playwright python scripts/acceptance_shots.py
uv run --with playwright python scripts/acceptance_shots.py --forced
open docs/design/acceptance/v1/gallery.html   # every state, both widths, one page
```

**The shots themselves are not in the repo**, only this index and the generated
contrast report: 42 screenshots and the raw per-node measurements are megabytes of
regenerable output, and the two commands above rebuild them in about fifteen
minutes. `gallery.html` is the review page — each state's phone and desktop shot
side by side, in matrix order — which is how the sign-off pass is meant to be read.

The script drives the real app on a real browser (Chromium), photographs each state
full-page, and while it is there walks every rendered text node — the page, the
Plotly SVG chrome, and the pyvis graph document inside its iframe — measuring each
one against the background it is actually painted on. Phone width is device
emulation at 390 CSS pixels, so the page reflows as it does in a hand; the
maintainer's own screenshot tool cannot do that (#118), which is why this is
automated rather than captured by hand.

## The matrix

Every state below was captured at both widths: `phone/<name>.png` and
`desktop/<name>.png`.

| State | What it shows |
|---|---|
| `meta-default` | Meta, cold start: the app opens on a drawn chart, not an empty canvas |
| `meta-focus` | Meta with two archetypes focused in the second card |
| `archetypes-landscape` | The metagame landscape scatter for the latest year |
| `archetypes-timeline` | One archetype's finishes over time |
| `archetypes-head-to-head` | Two archetypes compared on their shared events |
| `archetypes-refused` | A pair never both ranked at one event: the refusal |
| `cards-nothing-picked` | Cards before a pick: controls over empty ground |
| `cards-overview` | A card's usage graph and its adoption trend |
| `cards-cooccurrence` | A pair's co-occurrence graph and both adoption lines |
| `gems-drawn` | The whole format's gems, drawn on open |
| `pilots-nothing-picked` | Pilots before a pick |
| `pilots-running` | Mid-query: the progress feedback while a Draw runs |
| `pilots-overview` | A pilot's neighbourhood, archetype affinity and performance |
| `pilots-node-details` | A deck node clicked: the details panel and its Moxfield link |
| `pilots-thin-history` | A pilot with too little history to average: the refusal |
| `pilots-head-to-head` | A rivalry drawn |
| `pilots-same-pilot` | The same pilot picked twice |
| `pilots-never-met` | A pair who never met: the refusal |
| `faq` | The FAQ tab |
| `forced-too-large` | Too large to draw, with the draw limit lowered to 20 nodes |

The provenance and credit surface is a page footer, so it sits at the foot of every
shot rather than having one of its own.

### The forced state

Nothing in the shipped corpus reaches the too-large refusal: the biggest subgraph
any query draws is 172 nodes against a 250-node limit. It is a real code path with
a real message, so `--forced` lowers that floor and captures it; the shot is named
`forced-` to say that the threshold, not the corpus, produced it.

## The contrast pass

[`contrast.md`](contrast.md) is the generated summary: every ink-on-ground pair the
app actually painted, its worst measured ratio, and the AA floor that applies to it.
`contrast.json` holds the raw per-node measurements behind it.

One text was below AA and is fixed on this branch: Gradio's floating "Plot" chip,
`--text-mute` on the `--surface-2` well at 4.23:1. It is retired the way the Gradio
footer is (§2 and the chrome cleanup in `../v1-visual-direction.md`), since the card
it floats over is already titled in the app's own type.

A second pair reads at that same 4.23:1, and it is **not** fixed: the hidden-gems
leaderboard paints its `td.score.spread` cells in `--text-mute` on the banded row's
`--surface-2` (4 nodes, `gems-drawn`, both widths). This is the exact pairing §2 rules
out by name. It arrived with the banded rows in #184 (`d86f741`), after the audit run
these shots replace, and it reproduces at `main`, so it is not a #172 regression and
was not fixed here. It has no ticket yet; see `docs/research-log.md`.

What remains below the 4.5:1 line is the meta chart's **faded legend entries**, at
2.10:1. Those are the `legendonly` lines the emphasis model opens with, and Plotly
draws a switched-off legend entry at half opacity; a raised one measures 4.57:1.
WCAG 1.4.3 exempts text that is part of an inactive control, which these are, so the
pass records them rather than counting them as failures. Worth knowing rather than
filing away: under emphasis most of the legend starts switched off, so the exemption
covers the entries a reader is most likely to be reading.

## Findings for sign-off

What the matrix shows that the code did not say. None of these were changed on the
branch that captured them: they are chart and graph decisions for the maintainer, not
evidence work. Where a later ticket has since fixed one, it says so.

1. **No progress feedback on a Draw, at either width.** `pilots-running` is the
   mid-query shot, taken with a second of emulated latency: the control panel, then
   empty ground, for the whole round trip. #114 ticked this AC on the basis that
   progress "rides Gradio's default `show_progress`", but Gradio draws that
   indicator over the *output* components, and since #132/#138 every results stack
   is hidden until its callback returns (and a changed subject hides it again), so
   the indicator has nothing to sit on. On a fast local query it is invisible either
   way; on a phone it is a click that appears to do nothing.
2. **The chart legend eats the plot at phone width.** On `meta-default` — the
   landing view — the legend is drawn inside the plot area and takes about half the
   width, crushing 14 archetype lines into a strip, with Plotly's modebar drawn over
   the legend title. Same shape on `cards-overview` and `cards-cooccurrence`.
3. **The graph frame is a fixed 760px at every width** (`GRAPH_HEIGHT`, `app.py`).
   At 390px that is a tall box with a small cluster in the middle whose labels are
   unreadable, and on `cards-cooccurrence` the labels are not drawn at all. #85's own
   criterion is "no fixed 700/760px letterbox".
4. **Four of the seven tabs sat behind Gradio's "..." overflow at phone width**
   (Hidden gems, Pilots, Player leaderboard, FAQ). Gradio's own behaviour, visible in
   every phone shot taken before #172. Fixed there: the bar scrolls horizontally, so
   all seven tabs render on the strip at 390px and the overflow menu hides itself.
5. **Two controls wore Gradio's default palette**, against §2's one-accent
   commitment. The selected tab's label and underline were Gradio's primary blue
   `#3b82f6` (measured on all 46 visits), and the radio chips sat on its grey
   `#52525b`; neither hex is a token. Both cleared AA, so this was a visual-system
   finding, not a contrast one. Fixed in #172: both vars are bound to tokens in
   `dark_theme()`, and neither hex is painted anywhere at either width.
6. **`SliceTooSmall` names the archetype's tag, not its display name** — "academy
   has 62 ranked decks", where the dropdown says "Academy" (`query.py`). Only
   reachable behind `--forced` today, since the dropdown offers no slice that small,
   so it is latent rather than live.
