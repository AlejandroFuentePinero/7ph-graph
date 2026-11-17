"""The v1 dark theme: the design tokens, the type system, and the CSS that commits
every surface to them.

The single source of truth is ``docs/design/v1-visual-direction.md`` (§2 tokens, §3
type system, §12 insight cards, §13 control panel, §15 signature). This module is that
document as data: the tokens defined once, the two self-hosted faces, the eight type
roles, the card / panel surfaces, and a ``build_css`` that serialises them into the
``:root`` custom properties and role styles the app injects at the ``gr.Blocks`` level.
``dark_theme`` binds Gradio's own chrome to the same tokens and ``serve`` tells the
page to draw dark whatever the browser prefers, so the app reads as one coherent dark
build. ``contrast_ratio`` lets the tests hold every text role to WCAG AA on the ground,
so a token edited to an illegible value fails a test rather than a reader.
"""

import base64
import html
import json
import tempfile
from functools import lru_cache
from pathlib import Path

from graph7ph import palette

# §2. Defined once here, referenced everywhere by role. The names are the CSS custom
# property suffixes (``bg`` -> ``--bg``), so this dict and the stylesheet cannot drift.
TOKENS: dict[str, str] = {
    "bg": "#131110",  # ground: warm near-black, biased toward the accent
    "surface": "#1c1917",  # cards, chart surface, graph ground
    "surface-2": "#24201d",  # wells, insets
    "border": "#37312b",  # hairline dividers
    "text": "#f2ede6",  # primary ink
    "text-dim": "#b4aca2",  # ledes, secondary
    "text-mute": "#8a8178",  # captions, axis/tick labels
    "accent": "#e26a2c",  # primary action, active state
    "accent-bright": "#f4823f",  # on-surface emphasis, links, raised chart line
}

# §3 (revised by #132). Two self-hosted faces echoing Claude's presentation pairing:
# a display serif for titles, a grotesque sans for everything else. Named once (a face
# swap is one edit here, not a sweep across the stylesheet), each mapped to its woff2
# and the weights the type roles ask of it.
FONT_DIR = Path(__file__).parent / "assets" / "fonts"
FONTS: dict[str, dict] = {
    # (family, file, css font-weight declaration). Hanken is a variable font: one woff2
    # covers the 400-700 range, so its face declares the range, not a single weight.
    "display": {"family": "Fraunces", "file": "fraunces-600.woff2", "weight": "600"},
    "body": {"family": "Hanken Grotesk", "file": "hanken.woff2", "weight": "400 700"},
}

# The fallback stacks: a system serif/sans stands in for the first paint before the
# woff2 decodes (``font-display: swap``), so nothing renders in an invisible face.
DISPLAY_STACK = f"'{FONTS['display']['family']}', Georgia, 'Times New Roman', serif"
FONT_STACK = (
    f"'{FONTS['body']['family']}', -apple-system, BlinkMacSystemFont, "
    '"Segoe UI", Roboto, sans-serif'
)

# §3. Reading measure: no paragraph runs the full width of a wide monitor.
MEASURE_CH = 62

# A tab button's height, and so the header row's (#327). Gradio's is 31px; 44px is
# the least a finger can be asked to hit, and a desktop row of the same height is
# still one slim row, so one size serves every width.
TAB_PX = 44

# The narrowest window whose header is one row (#327). Measured on the real record:
# the mark and name (139px), seven tabs (659px), the freshness line (195px) and the
# two gaps between them need 1,041px of column, which is a 1,105px window once the
# page's padding is added. Rounded up, so a longer tab label has a little room
# before the strip starts to scroll. It was 1100 while the first tab was "Meta" and
# the seven needed 626px: "Metagame" (#332) is 33px wider, and from 1100 to 1105px
# the row clipped the last tab by up to 5px. An eighth tab, "Community" (#346), is
# 104px more: the eight need 763px, which is 1,145px of column and a 1,209px window,
# and from 1140 to 1209px the row clipped the last tab by up to 69px.
HEADER_ONE_ROW_PX = 1240

# The page's column, padding included (#327). Gradio caps the page by the window it
# is in, in steps, and from 1536px up the step is the window itself. This is the
# step a 1440 window gets, held at every width above it.
COLUMN_PX = 1280


# The frame every graph is drawn in, and the panel beside it (#330). The frame's
# height is a share of the window between a floor and a ceiling, and the panel takes
# the same height and scrolls inside it, so the graph and the answer to a click on
# it are on screen together. It was 90vh up to 950px with the graph alone in it: on
# a 900px window the frame was 810px tall and the answer sat at its far edge, in
# view only with the page scrolled exactly into place.
#
# A graph is fitted by its height far more often than by its width, so on a 1440 by
# 900 window a frame this tall and about two thirds as wide draws it no smaller
# than the full-width one did (measured over every view, #330). The graph's share
# of the width is `GRAPH_SHARE` to the panel's one: at two to one the widest
# drawing, a card's usage, came out 13px narrower than it had been.
#
# Not so on a taller window. The page's column is capped (`COLUMN_PX`), so a 1920
# by 1080 window gives the graph no more width than a 1440 one, where the old frame
# grew with the window's height to 950px: a pilot's graph is drawn about 5% smaller
# there than it was, and a wide one more. The ceiling is as high as leaves the
# frame on screen under its card's title at that size.
GRAPH_HEIGHT = 840
GRAPH_MIN_HEIGHT = 525
GRAPH_VIEWPORT_SHARE = "78vh"
GRAPH_SHARE = 2.2
# Under this width the two stack: the list first, then the graph (ADR 0018), at the
# frame's floor.
GRAPH_STACK_PX = 760


@lru_cache(maxsize=1)
def _font_faces() -> str:
    """The ``@font-face`` rules for the two faces, woff2 base64-embedded as data URIs.

    Embedding (rather than linking) means the Space serves no external font request and
    needs no static-file config: the whole face travels in the injected stylesheet. Read
    once and memoised, since the bytes never change within a process.
    """
    rules = []
    for spec in FONTS.values():
        b64 = base64.b64encode((FONT_DIR / spec["file"]).read_bytes()).decode("ascii")
        rules.append(
            f"@font-face {{\n"
            f"  font-family: '{spec['family']}';\n"
            f"  font-style: normal;\n"
            f"  font-weight: {spec['weight']};\n"
            f"  font-display: swap;\n"
            f"  src: url(data:font/woff2;base64,{b64}) format('woff2');\n"
            f"}}"
        )
    return "\n".join(rules)


def _relative_luminance(hex_colour: str) -> float:
    """A hex colour's sRGB relative luminance, 0 (black) to 1 (white), per WCAG 2."""
    def _linear(channel: float) -> float:
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
    r, g, b = (_linear(int(hex_colour[i:i + 2], 16) / 255) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    """The WCAG contrast ratio between two hex colours, from 1 (identical) to 21."""
    a, b = _relative_luminance(fg), _relative_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


# The highlighted figure in a caption (`.caption .figure`) and in a field-standing line
# (`.t-fieldstat .pct`), taken from the shared categorical set rather than named again
# here, so the number the eye lands on is the same blue the charts draw a series in.
FIGURE_BLUE = palette.CATEGORICAL[0]


# The FAQ category tags and the hue each is tinted with. The three entity categories
# take the graph's own colour for that node kind, so a "Pilots" tag and a pilot node are
# the same orange; the shared primitive every metric is built on belongs to no entity, so
# it takes the neutral surface instead of a ninth hue (§5-6: colour names entities).
FAQ_TAGS: dict[str, str | None] = {
    "Metric": None,
    "Archetypes": palette.CATEGORICAL[3],
    "Pilots": palette.CATEGORICAL[1],
    "Cards": palette.CATEGORICAL[2],
}


def _faq_tag_tints() -> str:
    """One tinted-pill rule per FAQ category, keyed by its lowercased name."""
    rules = []
    for name, hue in FAQ_TAGS.items():
        fill, edge = (
            ("var(--surface-2)", "var(--border)") if hue is None
            else (_rgba(hue, 0.18), _rgba(hue, 0.55))
        )
        rules.append(f".faq-tag-{name.lower()} {{ background: {fill}; border-color: {edge}; }}")
    return "\n".join(rules)


def _rgba(hex_colour: str, alpha: float) -> str:
    """A hex colour as an ``rgba()`` string at the given alpha."""
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r}, {g}, {b}, {alpha})"


def build_css() -> str:
    """The stylesheet the app injects at the ``gr.Blocks`` level.

    Prepends the two ``@font-face`` faces (§3), defines the tokens once in ``:root``,
    and reads them by role for the eight-role type system (§3), the insight-card and
    control-panel surfaces (§12/§13), and the graph mark (§15). The insight title
    lands on the page's own markdown; the control label lands on Gradio's field title
    (its ``block-info`` span); the rest are utility classes. The reading measure bounds
    prose to ``MEASURE_CH``. A quiet quality floor (reduced motion, visible focus) rides
    at the end, and the default-Gradio footer is retired (#115).
    """
    root = "\n".join(f"  --{name}: {hex_};" for name, hex_ in TOKENS.items())
    return f"""{_font_faces()}

:root {{
{root}
}}

/* Everything below is nested in this one block, which adds three pseudo-classes of
   specificity to every rule in it and changes nothing else: `:root:root:root .x` matches
   exactly what `.x` matches. Gradio 6 needs it. Its own component rules now outrank a
   bare class from the app's stylesheet, so without the lift the page keeps the tokens
   and loses the design: the title falls to Gradio's 26px, a card to Gradio's group
   surface and radius, a plot gains Gradio's block border (issue #307). Lifted once
   here rather than with `!important` on each declaration, so a rule added later is
   lifted with the rest and none can be forgotten. The tokens stay outside, on a plain
   `:root`, and so do the `@font-face` rules, which cannot be nested. */
:root:root:root {{

.gradio-container {{ font-family: {FONT_STACK}; }}

/* One column, centred (#327). On a 1920 window the page was 1,856px of content,
   and an FAQ answer ran the width of it. `.app` is the element Gradio caps. */
.gradio-container .app {{ max-width: {COLUMN_PX}px; margin-left: auto; margin-right: auto; }}

/* The header (#327): the mark and the short name, the tab strip, and how fresh the
   data is, on one row. It replaced a full-width title, the strip on a row of its own
   and a heading under that repeating the open tab, three rows that put the first
   result on Meta 454px down a 900px window.

   The strip is Gradio's, nested inside its tabs element, and the name and the
   freshness are the app's, nested inside an HTML block, so no one element holds all
   three. The wrappers between are taken out of the layout (`display: contents`) and
   the page's column becomes a grid, which makes the three its own items: name, strip
   and freshness in the first row, sized by what they hold, and everything else (the
   open tab, the footer) across the full width below. The column is found by what it
   holds, since Gradio gives it no name of the app's.

   The hairline under the row is the column's own, drawn across all three. It was the
   strip's border, which now stops where the strip does. */
.column:has(> .masthead) {{
  display: grid; grid-template-columns: auto minmax(0, 1fr) auto;
  column-gap: 1.5rem; align-items: center;
}}
.column:has(> .masthead) > *, .column:has(> .masthead) > .tabs > .tabitem {{ grid-column: 1 / -1; }}
.masthead, .masthead > .html-container, .column:has(> .masthead) > .tabs {{ display: contents; }}
.column:has(> .masthead)::before {{
  content: ""; grid-area: 1 / 1 / 2 / -1; align-self: end;
  border-bottom: 1px solid var(--border);
}}
.brand {{ grid-area: 1 / 1; display: flex; align-items: center; gap: 0.55rem; }}
.brand-mark svg {{ display: block; width: 24px; height: 24px; }}
/* The page's one top-level heading, set at the row's scale. Every property Gradio
   gives a heading in an HTML block is stated, margin and padding included. */
.brand .brand-name {{
  font-family: {DISPLAY_STACK}; font-size: 20px; font-weight: 600; letter-spacing: -0.005em;
  line-height: 1; color: var(--text); margin: 0; padding: 0; white-space: nowrap;
}}
.column:has(> .masthead) > .tabs > .tab-wrapper {{
  grid-area: 1 / 2; min-width: 0; height: auto; margin: 0; padding: 0;
}}
.freshness {{
  grid-area: 1 / 3; font-size: 13px; line-height: 1.5; color: var(--text-mute);
  white-space: nowrap;
}}
/* Below the width where the tabs fit beside the rest, the strip takes a row of its
   own under the name and the freshness, and scrolls sideways there (#172, below). */
@media (max-width: {HEADER_ONE_ROW_PX - 1}px) {{
  .column:has(> .masthead) {{ grid-template-columns: auto minmax(0, 1fr); }}
  .column:has(> .masthead)::before {{ grid-row: 2; }}
  .column:has(> .masthead) > .tabs > .tab-wrapper {{ grid-area: 2 / 1 / 3 / -1; }}
  .freshness {{ grid-area: 1 / 2; justify-self: end; }}
}}

/* §3 type system. Titles take the display serif; body, ledes, captions, labels and
   numerics take the grotesque sans. The insight title applies to the page's own
   markdown; the utility classes carry the rest for deliberate use where results are
   framed. The page title and the section heading had rules here until #327, which
   left the page with neither: the header's name is set above. */
/* Insight title (§3, renamed from result title): a plot's title inside its card,
   no longer echoing the subject (§14). Kept as `.t-result-title` so the framing
   helpers and their tests keep one class name. */
.prose h3, .t-result-title {{
  font-family: {DISPLAY_STACK}; font-size: 20px; font-weight: 600; line-height: 1.25; color: var(--text);
}}
/* Lede applies to a tab's intro line; the descendant selector wins over `.prose p`
   (body) by specificity, so the intro reads as a lede rather than plain body. Gradio
   puts `elem_classes` on BOTH the outer Block and the `.prose` element itself, so the
   `.t-lede .prose p` descendant reliably matches (reading only Markdown.svelte, which
   shows the class on `.prose`, makes this selector look dead; it is not). */
.t-lede, .t-lede .prose p {{
  font-size: 17px; font-weight: 400; line-height: 1.5; color: var(--text-dim);
}}
/* `.info-text` is a control's help line. Gradio 5 drew it as Markdown, so it took the
   body role through `.prose p`; Gradio 6 draws it as a plain div of its own, which
   would otherwise fall to Gradio's small muted caption. */
.prose p, .prose li, .t-body, .info-text {{ font-size: 15px; font-weight: 400; line-height: 1.6; color: var(--text); }}
.t-caption {{ font-size: 13px; font-weight: 400; line-height: 1.5; color: var(--text-mute); }}
/* State message (#114): the one on-theme surface for every state with nothing (or not
   yet) to draw (nothing picked, empty result, a refused trend, too-large-to-draw). It
   pairs with `.t-body` for the type, adding only the padding that seats the line where a
   plot would sit, so it reads the same as the Markdown refusal notes (`.prose p`). This
   retires the inline-styled divs' `padding:1rem;font-family:sans-serif`. */
.t-state {{ padding: 0.75rem 0; }}
/* Field-standing line: the one-sentence read of a performance chart, promoted above a
   plain caption so it lands as the answer rather than fine print. In a card it *is* the
   subtitle (no plain caption sits beside it), so it takes the same accent, below. The
   figure is set apart so the eye catches the number first, and takes the shared palette's
   slot-1 blue rather than a stronger orange: a highlight in the sentence's own hue would
   read as more of the sentence. The blue is the one the charts already draw with (§5),
   and it clears WCAG AA on both grounds, which `test_theme` holds it to. The sample size
   trails quiet, so the line reads sentence first, figure first inside it, source last. */
.t-fieldstat {{ font-size: 14.5px; font-weight: 400; line-height: 1.5; color: var(--text-dim); }}
.t-fieldstat .pct {{ color: {FIGURE_BLUE}; font-weight: 650; font-variant-numeric: tabular-nums; }}
.t-fieldstat .sample {{ color: var(--text-mute); font-size: 13px; }}
/* Numeric readout (§3): the sans's tabular figures where digits align in a column.
   `.tabular` opts any run of digits back into alignment. */
.t-numeric {{ font-size: 14px; font-weight: 550; color: var(--text); font-variant-numeric: tabular-nums; }}
.tabular {{ font-variant-numeric: tabular-nums; }}

/* Control label (§3). Size, weight and colour ride Gradio's own theme vars (set in
   `dark_theme`); the uppercase and tracking, which those vars cannot carry, land on
   the field-title span here. `.t-control-label` is the standalone utility. */
.t-control-label, .tile-label {{
  font-size: 12px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-mute);
}}
.gradio-container span[data-testid="block-info"] {{ letter-spacing: 0.06em; text-transform: uppercase; }}

/* Reading measure: bound prose, not the widgets, so paragraphs stay legible on a
   wide monitor while controls and charts still fill their space. */
.prose p, .prose li, .t-lede, .t-body {{ max-width: {MEASURE_CH}ch; }}
/* The help line again, which had the measure through `.prose p` while it was Markdown. */
.info-text {{ max-width: {MEASURE_CH}ch; }}

/* The FAQ tab is the one place the reading measure is dropped: its boxes are full-width,
   one per row, and the answer runs the width of its box. A measure-bound paragraph in a
   wide box reads as a narrow ragged column against empty space, which is worse here than
   a long line, because an answer is a handful of sentences read once rather than a page
   of prose. The box is the bound; the paragraph fills it.

   Twice tried and twice reverted at the maintainer's call (2026-07-26), so do not "fix"
   this again without asking: capping the card put one narrow column down the left of a
   wide screen with the rest empty, and flowing the cards into as many measure-wide
   columns as fit broke the one-question-per-row reading order. The layout is the
   decision; #85's criterion 4 is answered against it, not the other way round.

   The privacy line on Pilots and the player leaderboard is outside the measure too,
   for its own reason: it is one line (#327), and the measure would break it in three. */
.faq-card p, .faq-card li, .privacy-line .t-body {{ max-width: none; }}
/* The question is the box's own heading and the reader's entry point into it, so it
   takes the accent: eight boxes at a glance are scanned by their questions. */
.faq h3 {{ color: var(--accent-bright); }}

/* The category tag above each question: what the answer is about, so the boxes read as
   grouped rather than as eight unrelated notes. Set as a quiet pill in the caption role,
   tinted by the entity it names, and the tint is the graph's own hue for that kind
   (render.py's `_KIND_ORDER`: Pilot slot 2, Card slot 3, Archetype slot 4), so a tag and
   the nodes it describes agree on colour. The hue carries the tint only, never the label
   text, which stays on `--text` and so keeps its contrast without depending on a hue
   validated for chart marks rather than for 11px type. */
.faq-tag {{
  display: inline-block; margin-bottom: 0.5rem; padding: 0.1rem 0.55rem;
  border: 1px solid; border-radius: 999px;
  font-size: 11px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--text);
}}
{_faq_tag_tints()}

/* §12 insight card: each plot is a bounded answer on the ground, not a slab in a
   continuous sheet. The card carries the surface, edge, radius and padding; adjacent
   cards are held apart by the bottom-margin gap, not a shared rule. The inner Gradio
   blocks are flattened so the card is the only surface (no card-in-card). */
.insight-card {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 1rem 1.25rem 1.25rem; margin-bottom: 1.25rem;
}}
/* Gradio 6 draws a `gr.Group` as two nested elements and puts `elem_classes` on both,
   where Gradio 5 put them on one. Left alone, every surface the app gives a group (a
   card, a control panel) is drawn twice, one inside the other, with
   its padding and edge doubled. The inner copy is flattened, for any group and any
   class, so the outer element is the one surface, as it was. */
.gr-group > .gr-group {{
  background: transparent; border: 0; border-radius: 0; box-shadow: none;
  padding: 0; margin: 0;
}}
/* A `gr.HTML` is padded when its `padding` argument says so, and only then, as in
   Gradio 5. Gradio 6 pads the container inside the block whatever the argument says,
   which pushes every heading the app sets flush (`padding=False`) off its line. What
   the argument still does is mark the block `.padded`, so the container's padding is
   put back on exactly those. */
.html-container {{ padding: 0; }}
.block.padded > .html-container {{ padding: var(--block-padding); }}
/* A link in a `gr.HTML` is text in its line. Gradio 6 pads every one as if it were a
   button, which opens a gap either side of the address in the footer. */
.gradio-style a {{ padding: 0; }}
/* The radio and checkbox marks take their edge from the theme, where it is the input's
   border width (none). Gradio 6 resets both to a 1px edge in a rule that outranks the
   component's own, which drew an outline round every unselected mark. */
[type="checkbox"], [type="radio"] {{ border-width: var(--checkbox-border-width); }}
/* `border: 0`, not `border: none`: Gradio 6 writes `border-style: solid` on a block's
   own style attribute, which no stylesheet rule outranks, so a rule that only removes
   the style leaves the width at the shorthand's `medium` and the block draws a 3px
   frame in the text colour. A width of zero draws nothing whatever the style. */
.insight-card .block, .insight-card .form, .insight-card .styler {{
  background: transparent; border: 0; box-shadow: none;
}}
.insight-card .t-result-title {{ margin-bottom: 0.1rem; }}
/* The plot's subtitle, under its title inside the card: it names what the plot is
   bounded to (a cut, a board, a season), or states the read of it, which is part of
   reading the plot rather than fine print about it, so it is set in the text colour
   rather than in the caption's mute. It was in the accent until #333, when a name
   became a link: the accent is kept for what can be clicked (the caption rule,
   #329), and an orange sentence over a table of orange names read as one more link.
   Both classes appear in that slot (a caption where the subtitle names a bound, a
   field-standing line where it states a reading), and a subtitle should not change
   colour with which of the two a plot happens to use. */
.insight-card .t-caption, .insight-card .t-fieldstat {{ color: var(--text); }}
/* A chart's title with the way to how it is measured (#333): the title, and at the
   far end of its line a small link to the chart's FAQ entry. Where the two do not
   fit one line, on a phone, the link goes under the title. */
.titled {{
  display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between;
  gap: 0 1rem;
}}
.titled a.measured {{
  font-size: 13px; font-weight: 400; color: var(--accent-bright); text-decoration: none;
  white-space: nowrap;
}}
.titled a.measured:hover {{ text-decoration: underline; }}
/* A name that opens its subject's page (#333, `parts.opens`), wherever it is
   drawn: in a table, heading a table's group, as a clicked node's name or among its
   facts. In the accent, as what can be clicked is, and otherwise set as the text
   it stands in. */
a[data-opens] {{ color: var(--accent-bright); text-decoration: none; }}
a[data-opens]:hover {{ text-decoration: underline; }}

/* The parts every page shares (#329, `graph7ph.parts`): the page head with its
   tiles, the caption, and the one table.

   A subject's page opens with its name in the display face, a one-line descriptor
   and four tiles. A tile is a small card: what the figure is in the control-label
   role (`.tile-label`, set with `.t-control-label` above), the figure in the display
   face, and a quiet note under it. Four across, two across at 640px and under. */
/* The space under the tiles is padding and not a margin: the block Gradio sets the
   head in is as tall as its content box, so a bottom margin fell outside it and the
   tiles sat 1px over the first card. A step more than the gap between two cards, so
   the head reads apart from the stack under it. */
.page-head {{ margin-top: 0.25rem; padding-bottom: 1.5rem; }}
.page-name {{
  font-family: {DISPLAY_STACK}; font-size: 30px; font-weight: 600; letter-spacing: -0.01em;
  line-height: 1.15; color: var(--text); margin: 0;
}}
.page-descriptor {{ font-size: 15px; line-height: 1.5; color: var(--text-dim); margin: 0.2rem 0 0.9rem; }}
.tiles {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0.75rem; }}
.tile {{
  min-width: 0; background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 0.7rem 1rem 0.8rem;
}}
.tile-value {{
  font-family: {DISPLAY_STACK}; font-size: 26px; font-weight: 600; line-height: 1.2;
  color: var(--text); margin-top: 0.15rem; overflow-wrap: anywhere;
}}
.tile-note {{ font-size: 13px; line-height: 1.4; color: var(--text-mute); }}

/* The caption rule (#329): a caption is ordinary text in the text colour, and never
   in the accent, which is kept for what can be clicked. At most one figure in it is
   set apart, in the blue the charts draw with (§5). */
.caption {{ font-size: 14px; font-weight: 400; line-height: 1.5; color: var(--text); }}
.caption .figure {{ color: {FIGURE_BLUE}; font-weight: 650; font-variant-numeric: tabular-nums; }}

/* The one table. It sits inside a card, so it carries no surface or frame of its
   own: a hairline under each row in the card's border token and nothing else, the
   header in the control-label role, and numbers right-aligned in tabular figures so
   a column of them reads down. Text breaks wherever it must, so a table is never
   wider than the card it is in; a number never breaks.
   `margin-top` and `padding` are zeroed because Gradio 6 gives a table in an HTML
   block both. It also frames the table, every row and every cell with a 1px line in
   the text colour, all cleared first so the one line a cell draws is the hairline
   under it (issue #324). */
.data-table {{ width: 100%; border-collapse: collapse; font-size: 14px; margin-top: 0; padding: 0; }}
.data-table, .data-table tr, .data-table th, .data-table td {{ border: 0; }}
.data-table th {{
  padding: 0.4rem 0.5rem; text-align: left; vertical-align: bottom;
  border-bottom: 1px solid var(--border);
  font-size: 11px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--text-mute);
}}
.data-table td {{
  padding: 0.4rem 0.5rem; border-bottom: 1px solid var(--border); color: var(--text);
  overflow-wrap: anywhere;
}}
.data-table tbody tr:last-child td {{ border-bottom: none; }}
.data-table .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.data-table td.num {{ white-space: nowrap; }}
/* A figure that qualifies the one beside it is set back, so the eye reads the
   name and the main figure first. */
.data-table td.quiet {{ color: var(--text-mute); }}
/* A link is the one thing in a table in the accent. A link out, to a decklist, is
   one short word and stays on its line; a name breaks wherever a name in plain text
   would, so that a table of names is still no wider than its card. */
.data-table a {{ color: var(--accent-bright); text-decoration: none; white-space: nowrap; }}
.data-table a:hover {{ text-decoration: underline; }}
.data-table a[data-opens] {{ white-space: normal; }}
/* A line that heads the rows under it: the group's name in the display face, a size
   up from a row, so it reads as a heading over them and not as one more row (the
   titles on the page are in that face, and nothing else in a table is). Any figures
   of its own sit under the columns they belong to. Space over it is what parts one
   group from the last. */
.data-table .group th {{
  font-family: {DISPLAY_STACK}; font-size: 16px; font-weight: 600; letter-spacing: 0;
  text-transform: none; color: var(--text);
}}
.data-table .group th, .data-table .group td {{ padding-top: 1.1rem; vertical-align: bottom; }}
/* The standings' tie back to their chart (#135): a drawn contender's row is dotted in
   the hue their line takes, and a row past the eighth keeps the same indent with no
   dot, so the names stay in one column rather than stepping in and out of it. */
.data-table .swatch {{
  display: inline-block; width: 9px; height: 9px; border-radius: 999px;
  margin-right: 0.6rem; vertical-align: middle;
}}
.data-table .swatch-hue {{ box-shadow: 0 0 0 3px var(--surface); }}
/* The standings' rank range, drawn beside its numbers (#336). The track is every
   place in the field, first at its left edge and last at its right, and the bar
   covers the places from the row's low rank to its high one: a place is one
   `--places`th of the track, so a range of a single place still shows. `--low`,
   `--high` and `--rank` are set on each row's track, and `--places` once, on the
   element the table sits in, so every row is on the one scale. The numbers take a
   set width, or the tracks would start at a
   different point in each row. Neutral, not a hue: colour in this table names a
   drawn pilot. The track is as long as the window leaves room for, since a top
   rank's range is a small part of it.
   What makes a bar readable: the header names the track's two ends (`.rank-ends`,
   as wide as a track and set where one is, so each end sits over the end it names),
   and a tick marks the pilot's own rank, at the middle of its place. A tick and not
   a dot: a top rank's bar is a few pixels long, and a dot hid it. The bar is set
   back behind the tick, which is the one bright thing on the track. The ends are
   told to take their header's ink: left alone they take the main ink, which Gradio
   gives every element inside an HTML block (see `.rank-numbers` below). */
.standings {{ --track: clamp(4rem, 18vw, 16rem); }}
.standings .rank-track, .standings .rank-ends {{
  display: inline-block; vertical-align: middle; width: var(--track); margin-right: 0.6rem;
}}
.standings .rank-track {{
  position: relative; height: 8px; border-radius: 2px; background: var(--border);
}}
.standings .rank-ends {{ display: inline-flex; justify-content: space-between; }}
.standings .rank-ends, .standings .rank-ends span {{ color: inherit; }}
.standings .rank-bar {{
  position: absolute; top: 0; bottom: 0; border-radius: 2px;
  left: calc((var(--low) - 1) / var(--places) * 100%);
  width: calc((var(--high) - var(--low) + 1) / var(--places) * 100%);
  background: var(--text-mute);
}}
.standings .rank-mark {{
  position: absolute; top: 50%; width: 2px; height: 14px; border-radius: 1px;
  left: calc((var(--rank) - 0.5) / var(--places) * 100%);
  transform: translate(-50%, -50%);
  background: var(--text);
}}
/* `color: inherit`, or the numbers lose the set-back ink of their column: Gradio
   colours every element inside an HTML block, this one included. The width is in
   rem and not ch, since the header's label wears this class too, in a smaller face:
   in ch its box was 8px narrower than a row's, and the ends sat 8px off the tracks. */
.standings .rank-numbers {{ display: inline-block; min-width: 4rem; color: inherit; }}
/* On a narrow screen the track sits over its numbers, not beside them: beside
   them it took the room the names need. */
@media (max-width: 640px) {{
  .standings {{ --track: 3.75rem; }}
  .standings .rank-track {{ display: block; margin: 0 0 4px auto; }}
  .standings .rank-ends {{ display: flex; margin: 0 0 4px auto; letter-spacing: 0; }}
  .standings .rank-numbers {{ min-width: 0; }}
}}
/* Metagame's list of the most played (#334). A row draws its share twice: as a bar
   on a track, and as the last of a column per year on record. Both are placed by
   what the markup states (`--share` on each bar and column), on two scales set
   once on the element the table sits in, so every row is on the same ones:
   `--leader`, the first row's share, is a full track, and `--tallest`, the largest
   share any listed archetype took in any year, is a full column. The latest year is
   in the blue the charts draw with, in the bar and in its column, and the years
   before it are set back. The columns stand on a hairline, so a year with no deck
   reads as an empty place and not as a missing one; it is a shadow and not a
   border, so the frame's height is the columns' own scale. The header names the
   first and last year over the columns' two ends (`.share-years`, as wide as the
   columns), in its own ink (see `.rank-ends` above). */
.share-list {{ --track: clamp(4rem, 26vw, 24rem); margin-top: 0.6rem; }}
.share-list td {{ vertical-align: middle; }}
.share-list .share-track {{
  display: inline-block; vertical-align: middle; position: relative;
  width: var(--track); height: 8px; margin-right: 0.7rem;
  border-radius: 2px; background: var(--border);
}}
.share-list .share-bar {{
  position: absolute; left: 0; top: 0; bottom: 0; border-radius: 2px;
  width: calc(var(--share) / var(--leader) * 100%); background: {FIGURE_BLUE};
}}
/* The share takes a set width, or the tracks would end at a different point in each
   row. So does the share a change started from, set back beside it. */
.share-list .share-now {{ display: inline-block; min-width: 3.4rem; }}
.share-list .share-from {{
  display: inline-block; min-width: 6.2rem; margin-left: 0.6rem; text-align: left;
  color: var(--text-mute);
}}
.share-list .share-history, .share-list .share-years {{ display: inline-flex; width: 6rem; }}
.share-list .share-years {{ justify-content: space-between; letter-spacing: 0; }}
.share-list .share-years, .share-list .share-years span {{ color: inherit; }}
.share-list .share-history {{
  height: 28px; align-items: flex-end; gap: 4px; vertical-align: middle;
  box-shadow: 0 1px 0 var(--border);
}}
.share-list .share-year {{
  flex: 1 1 0; border-radius: 1px 1px 0 0; background: var(--text-mute);
  height: calc(var(--share) / var(--tallest) * 100%);
}}
.share-list .share-year.now {{ background: {FIGURE_BLUE}; }}
/* On a narrow screen the track sits over its share, as wide as the cell, and the
   share a change started from goes under the change. The header's two years lose
   their century: at four digits each they ran into one another over the columns. */
@media (max-width: 640px) {{
  .share-list .share-track {{ display: block; width: 100%; margin: 0 0 4px; }}
  .share-list .share-now {{ min-width: 0; }}
  .share-list .share-from {{
    display: block; min-width: 0; margin: 0; text-align: right; font-size: 12px;
  }}
  .share-list .share-history, .share-list .share-years {{ width: 2.75rem; }}
  .share-list .share-history {{ height: 22px; gap: 2px; }}
  .share-list .century {{ display: none; }}
}}
/* A cell's second line, and a column left out, both for a narrow screen (below). */
.data-table .sub {{ display: none; }}
@media (max-width: 640px) {{
  .page-name {{ font-size: 24px; }}
  .tiles {{ grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0.5rem; }}
  .tile {{ padding: 0.6rem 0.75rem 0.7rem; }}
  .tile-value {{ font-size: 22px; }}
  .data-table {{ font-size: 13px; }}
  .data-table th, .data-table td {{ padding: 0.35rem 0.3rem; }}
  .data-table th:first-child, .data-table td:first-child {{ padding-left: 0; }}
  .data-table th:last-child, .data-table td:last-child {{ padding-right: 0; }}
  .data-table .wide-only {{ display: none; }}
  .data-table .sub {{ display: block; font-size: 12px; color: var(--text-mute); }}
}}

/* A graph beside the panel that answers it (#330, `parts.beside_graph`): the graph
   about two thirds, the panel the rest, at one height. The panel holds the colour key,
   the facts of the clicked node and the page's list, parted by hairlines, and scrolls
   inside its own height so the graph never moves off screen under a long list. */
.graph-split {{
  display: grid; grid-template-columns: minmax(0, {GRAPH_SHARE}fr) minmax(300px, 1fr);
  height: clamp({GRAPH_MIN_HEIGHT}px, {GRAPH_VIEWPORT_SHARE}, {GRAPH_HEIGHT}px);
  margin-top: 0.6rem;
}}
.graph-split > iframe {{ display: block; width: 100%; height: 100%; border: 0; }}
.graph-panel {{ min-width: 0; overflow-y: auto; border-left: 1px solid var(--border); }}
.graph-panel > section {{
  padding: 0.85rem 0.6rem 0.85rem 1rem; border-bottom: 1px solid var(--border);
}}
.graph-panel > section:first-child {{ padding-top: 0.2rem; }}
.graph-panel > section:last-child {{ border-bottom: 0; }}
/* The graph's own document shows and hides the facts and the prompt by this
   attribute, which a `display` of the app's own would otherwise outrank. */
.graph-panel [hidden] {{ display: none; }}
.panel-eyebrow {{
  margin: 0 0 0.5rem; font-size: 11px; font-weight: 600; letter-spacing: 0.06em;
  text-transform: uppercase; color: var(--text-mute);
}}
/* The colour key: a swatch beside each name, so colour is never the only thing
   saying what a node is (§7). A chip is the size a node's label settles to. */
.key-chips {{ display: flex; flex-wrap: wrap; gap: 0.35rem 0.85rem; }}
.key-chip {{
  display: inline-flex; align-items: center; gap: 0.4rem;
  font-size: 12px; color: var(--text-dim);
}}
.key-dot {{
  flex: 0 0 auto; display: inline-block; width: 10px; height: 10px; border-radius: 50%;
}}
/* A clicked node's facts: its kind beside its colour, its name in the display face,
   then label and value rows, the labels set as a table's header is, a weight
   lighter since they sit beside their values and not over a column of them. */
.panel-prompt {{ margin: 0; font-size: 13px; line-height: 1.5; color: var(--text-mute); }}
.node-kind {{
  display: flex; align-items: center; gap: 0.4rem; font-size: 11px; font-weight: 600;
  letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-dim);
}}
.node-name {{
  margin: 0.15rem 0 0.6rem; font-family: {DISPLAY_STACK}; font-size: 20px;
  font-weight: 600; line-height: 1.2; color: var(--text); overflow-wrap: anywhere;
}}
.facts {{
  display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 0.3rem 0.9rem;
  margin: 0; font-size: 14px;
}}
.facts dt {{
  font-size: 11px; font-weight: 400; letter-spacing: 0.06em; text-transform: uppercase;
  line-height: 1.9; color: var(--text-mute);
}}
.facts dd {{
  margin: 0; color: var(--text); font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}}
/* What a mark beside one of the facts means, under the facts that carry it. */
.facts-note {{ margin-top: 0.6rem; font-size: 12px; line-height: 1.45; color: var(--text-mute); }}
.node-link {{ margin-top: 0.7rem; font-size: 14px; }}
.node-link a {{ color: var(--accent-bright); text-decoration: none; }}
.node-link a:hover {{ text-decoration: underline; }}
/* The neighbours named under a node's facts. Each is a way to that node, so the
   row answers the pointer as a list row does. */
.node-facts .panel-eyebrow {{ margin-top: 0.9rem; }}
.linked {{ list-style: none; margin: 0; padding: 0; font-size: 13px; }}
/* Named through the panel, so the size is the list's: Gradio sets an item of any
   list in an HTML block at the body size. */
.graph-panel .linked li {{
  display: flex; align-items: baseline; gap: 0.45rem; margin: 0 -0.3rem;
  padding: 0.22rem 0.3rem; border-radius: 4px; cursor: pointer; list-style: none;
  font-size: 13px; line-height: 1.4;
}}
.graph-panel .linked li:hover {{ background: {_rgba(TOKENS["border"], 0.45)}; }}
.linked-name {{ flex: 1 1 auto; min-width: 0; color: var(--text); overflow-wrap: anywhere; }}
.linked-note {{
  color: var(--text-dim); font-variant-numeric: tabular-nums; white-space: nowrap;
}}
.linked-more {{ margin-top: 0.3rem; font-size: 12px; color: var(--text-mute); }}
/* The list, in the one table, set a step tighter for a column a third as wide. A
   row that names a node selects it on a click, and the selected node's row is
   marked, in a tint and not in the accent itself, which stays the links'. */
.graph-panel .caption {{ font-size: 13px; margin: -0.2rem 0 0.5rem; }}
.graph-panel .data-table {{ font-size: 13px; }}
.graph-panel .data-table th, .graph-panel .data-table td {{ padding: 0.35rem 0.35rem; }}
.graph-panel .data-table th:first-child, .graph-panel .data-table td:first-child {{
  padding-left: 0;
}}
/* The date under an event's name, at every width: the panel has no room for a
   column of its own for it. */
.graph-panel .data-table .sub {{ display: block; font-size: 11px; color: var(--text-mute); }}
.graph-panel .data-table tr[data-node] {{ cursor: pointer; }}
.graph-panel .data-table tr[data-node]:hover td {{ background: {_rgba(TOKENS["border"], 0.45)}; }}
.graph-panel .data-table tr.picked td {{ background: {_rgba(TOKENS["accent"], 0.15)}; }}
/* Either tint lightens the ground, and the mute a quiet figure and a date are set in
   is 4.57:1 on the card, 4.03:1 on the hover and 3.76:1 on the selection. In a
   tinted row they step up to the dim, which is 6.4:1 or better on both. */
.graph-panel .data-table tr[data-node]:hover td.quiet,
.graph-panel .data-table tr[data-node]:hover .sub,
.graph-panel .data-table tr.picked td.quiet,
.graph-panel .data-table tr.picked .sub {{ color: var(--text-dim); }}
/* Stacked on a narrow screen, where the graph is not the readable thing (ADR 0018):
   the list first, then the key over the graph and the clicked node's facts under
   it, as close to the graph as they were when they lived inside its frame. The
   panel's box is dropped so its three parts take their places round the graph. */
@media (max-width: {GRAPH_STACK_PX}px) {{
  .graph-split {{ display: flex; flex-direction: column; height: auto; }}
  .graph-panel {{ display: contents; }}
  .graph-panel > section {{ padding-left: 0; padding-right: 0; }}
  .graph-panel > .panel-list {{
    order: 1; padding-top: 0.2rem; border-bottom: 1px solid var(--border);
  }}
  .graph-panel > .panel-key {{ order: 2; padding-top: 0.85rem; }}
  .graph-split > iframe {{ order: 3; height: {GRAPH_MIN_HEIGHT}px; flex: none; }}
  .graph-panel > .panel-answer {{
    order: 4; border-top: 1px solid var(--border); border-bottom: 0; padding-bottom: 0;
  }}
}}

/* The results stack wraps a view's insight cards and is toggled as a whole: hidden
   before a Draw (so the view opens as controls over empty ground, the guidance living
   in the control's help text, not a row of duplicated "nothing yet" cards), shown once
   a Draw fills it. It is a bare container: no surface of its own, so only the cards
   inside it read as objects. So is the view body round it, the group that holds one
   view's subject line and its results stack and is shown while that view is picked.

   Gradio 6 fills a group twice with the border colour, so the 1px gaps between its
   children read as dividers: on the group, and on an unclassed `.styler` inside it
   that `elem_classes` never reaches. Cleared on the group alone, the fill showed
   behind the subject line, between the cards and under the last one, and an empty
   view body drew its own edge as a 2px strip (issue #324). Held on the running page
   by `tests/test_explorer_page.py`. */
.view-body, .results-stack, .view-body > .styler, .results-stack > .styler {{
  background: transparent; border: 0; box-shadow: none; padding: 0;
}}

/* §13 control panel: the controls for a view read as one raised input surface, one
   step lighter than the cards below so it reads as more prominent, obviously the
   place to drive from.

   One row (#328). The panel was a stack of full-width rows, each control on a line
   of its own whatever its size: 334px tall on Cards, with a Draw 1,174px wide. Its
   controls now sit side by side on one line and wrap only where there is no room,
   which on a phone is one control per line (below).

   Gradio draws a group as two nested elements and lays the controls out in a
   `.styler` inside the second, wrapping each run of form controls in a `.form` of
   its own. The styler is the row. The forms are taken out of the layout so that
   every control is an item of that one row, whichever form Gradio put it in. */
.control-panel {{
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: 12px; padding: 0.6rem 1rem 0.8rem; margin-bottom: 1rem;
  /* Every field, chip and button in the row is this tall, so they share a top and a
     bottom edge. It is the height Gradio builds a dropdown's input at. */
  --control-size: 38px;
}}
/* Lined up on their tops, so every label is on one line whatever is under it. */
.control-panel .styler {{
  display: flex; flex-flow: row wrap; align-items: flex-start; gap: 0.6rem 1.25rem;
  background: transparent;
}}
/* Cleared as well as taken out of the layout: Gradio fills a form with the border
   colour, and though one that lays nothing out paints nothing, the acceptance
   pass reads a text's ground off its ancestors' fills and took that one for it. */
.control-panel .form {{ display: contents; background: transparent; }}
/* A control is its label over its field, with no surface, edge or inset of its own:
   the panel is the surface. Chips and buttons take the width of their words. */
.control-panel .block {{
  flex: 0 0 auto; width: auto; margin: 0; padding: 0; border: 0; border-radius: 0;
  background: transparent; box-shadow: none; overflow: visible;
}}
/* A label sits on the panel now, where it sat on its control's own darker surface.
   The muted ink every other label takes is 4.23 on the panel's well, under the 4.5
   of WCAG AA, so a label here takes the dim ink one step up, which is 7.2. */
.control-panel span[data-testid="block-info"] {{
  margin-bottom: 0.3rem; color: var(--text-dim);
}}
/* A chip: an option of a chip selector, or the one checkbox. The picked chip's fill,
   edge and ink are the theme's (`dark_theme`). */
.control-panel .block .wrap {{ gap: 0.4rem; }}
.control-panel fieldset.block label, .control-panel .checkbox-container {{
  box-sizing: border-box; height: var(--control-size); padding: 0 0.75rem;
  align-items: center;
}}
/* A chip selector's chips carry no radio mark. The mark costs 24px a chip. Measured
   at 1440 on the Cards co-occurrence view, the row with the most in it: with the
   marks the Card search is 222px beside View chips of 292px, so the subject's search
   is not the widest thing in its row, and the second card's search is on its 160px
   floor. Without them the two searches are 282px and 183px and the View chips 244px.
   The picked chip is told apart by its fill, its edge and its ink. The mark stays in
   the page for the keyboard and a screen reader, and the ring a focused mark would
   wear is drawn on its chip instead. */
.control-panel fieldset.block label input[type="radio"] {{
  position: absolute; width: 0; height: 0; margin: 0; opacity: 0;
}}
.control-panel fieldset.block label input[type="radio"] + span {{ margin-left: 0; }}
.control-panel fieldset.block label:has(input:focus-visible) {{
  outline: 2px solid var(--accent-bright); outline-offset: 2px;
}}
/* The checkbox is a chip of the same kind, with its box kept: a lone chip has no
   neighbours to be told apart from, so the box is what says it is on or off. */
.control-panel .checkbox-container {{
  display: flex; gap: 0.5rem; border: 1px solid var(--border); border-radius: var(--radius-sm);
  background: var(--surface); color: var(--text-dim); font-size: 14px;
}}
.control-panel .checkbox-container:has(input:checked) {{
  background: {_rgba(TOKENS["accent"], 0.14)}; border-color: {_rgba(TOKENS["accent"], 0.55)};
  color: var(--accent-bright);
}}
.control-panel .checkbox-container .label-text {{ color: inherit; font-size: inherit; margin: 0; }}
.control-panel button.primary {{
  flex: 0 0 auto; width: auto; min-width: 5.5rem; height: var(--control-size);
  padding: 0 1.1rem; font-size: 15px;
}}
/* The two controls with no label over them sit on the fields' line, not the labels'. */
.control-panel button.primary, .control-panel .block:has(> .checkbox-container) {{
  align-self: flex-end;
}}
/* A search takes the room that is left, and the subject's search (`.primary-control`,
   the control everything else in a tab hangs off) twice the share of any other, so
   it is the first and the widest thing in the row, and the row always runs out to
   the panel's far edge. That is what marks it primary.
   The class used to draw an accent edge down the field's left side and inset the
   field from it; the edge had not been drawn since the move to Gradio 6, which left
   the inset alone, and in a row the inset only put the first label off the panel's
   edge. A search's input is a well in the panel, one hairline round it like a
   chip's; the label sits on the panel. */
.gradio-container .control-panel .row.clearable {{
  flex: 1 1 12rem; width: auto; background: transparent; --clear-size: var(--control-size);
}}
.gradio-container .control-panel .row.clearable:has(.primary-control) {{ flex: 2 1 16rem; }}
/* The second pilot of a head-to-head is a subject as much as the first: the view
   needs both. While it is on show the two take even shares of the room, where the
   first was 448px beside 266px for the second. */
.gradio-container .control-panel:has(#second-pilot-search) .row.clearable {{
  flex: 1 1 12rem;
}}
.control-panel .row.clearable .block {{ flex: 1 1 0; min-width: 0; }}
.control-panel .row.clearable .container > .wrap {{
  background: var(--surface); box-shadow: inset 0 0 0 1px var(--border);
}}
/* A search is the row's height whether it holds a name or nothing: the field's
   floor is the row's height. */
.control-panel .row.clearable .wrap-inner {{
  box-sizing: border-box; min-height: var(--control-size);
  padding-top: 3px; padding-bottom: 3px;
}}
/* The clear button's tint is over the panel's well too, where its resting ink is
   4.29. It takes the bright accent, 5.5 there: the step up the picked chip already
   takes for the same reason (`dark_theme`). */
.gradio-container .control-panel .row.clearable .clear-btn {{
  margin: 0; color: var(--accent-bright);
}}
.control-panel input::placeholder {{ color: var(--text-dim); opacity: 1; }}

/* The examples a tab offers before a first pick (#328): a quiet line under the
   panel, each chip an unpicked chip of the panel's own, since taking one is a pick. */
.gradio-container .row.examples {{
  align-items: center; gap: 0.5rem; flex-wrap: wrap; margin: -0.25rem 0 1rem;
}}
.gradio-container .row.examples > * {{ flex: 0 0 auto; width: auto; min-width: 0; }}
.gradio-container .row.examples .example-chip {{
  padding: 0.3rem 0.7rem; border: 1px solid var(--border); border-radius: 8px;
  background: var(--surface); color: var(--text-dim); box-shadow: none;
  font-size: 14px; font-weight: 400;
  transition: color 120ms ease, border-color 120ms ease, background 120ms ease;
}}
.gradio-container .row.examples .example-chip:hover {{
  background: {_rgba(TOKENS["accent"], 0.08)}; border-color: {_rgba(TOKENS["accent"], 0.55)};
  color: var(--accent-bright);
}}

/* A data control and its clear (×) button read as one field: the button on the input's
   own baseline (bottom-aligned, since the label and any help text push the input down),
   carrying a quiet accent square of its own.

   Every rule here is anchored on `.gradio-container .row.clearable` on purpose. Gradio's
   own row and button rules are class-scoped (`.stretch`, `.secondary`) and load after
   this stylesheet, so a plain `.clearable` selector ties on specificity and loses on
   order: the button would keep its variant surface and stretch to the row's full height.
   Out-specifying is what makes these rules land, not an accident of nesting.

   The row also carries the field's surface. Gradio paints that surface on the block it
   wraps a dropdown in, and that block stops at the field's edge, so the strip the button
   sits in showed the lighter control-panel surface behind it: a pale full-height slab
   beside every control, which reads as the button and is not. */
.gradio-container .row.clearable {{
  align-items: flex-end; gap: 0.4rem; flex-wrap: nowrap;
  background: var(--surface); border-radius: 8px;
  /* The button is square and exactly as tall as the input beside it, so the two share a
     top and a bottom edge. Gradio builds that input as `--spacing-md` of padding above
     and below a single line: 4px of input margin around ~17px of 14px text. */
  --clear-size: 37px;
}}
/* Descendant rather than child: Gradio wraps a dropdown in a `.form` div of its own and
   is free to wrap the button too, so a `>` here would depend on a nesting it does not
   promise. The field keeps its width; only the button shrinks. */
.gradio-container .row.clearable .clear-btn {{
  flex: 0 0 auto; align-self: flex-end; min-width: 0;
  /* Level with the input, not with the field's whole block: the label and any help text
     sit above the input, and `--spacing-xl` is the block's own bottom padding, so the
     button's lower edge lands on the input's rather than below it. Square and the same
     height, so the two read as one field, and Gradio's button centres the glyph in it.
     `border-box` so the declared size is the outer edge the eye lines up, borders in. */
  box-sizing: border-box; width: var(--clear-size); height: var(--clear-size);
  /* And inset from the panel's edge by the same amount every field is: Gradio wraps a
     field in a block padded by `--block-padding`, whose horizontal half holds the input
     clear of the panel wall. The button is bare, so without this its right edge overhung
     every input box in the panel by those 12px, which reads as the button sitting too
     far right rather than as the column of fields it belongs to. */
  margin-bottom: var(--spacing-xl); margin-right: calc(var(--spacing-xl) + 2px);
  padding: 0;
  /* Its own quiet accent square, so the one control in the panel that undoes something
     is visible as a control rather than as a glyph floating beside the field. The
     resting state is the hover state at lower strength, so hovering brightens what is
     already there instead of introducing a box that was not there a moment ago. */
  background: {_rgba(TOKENS["accent"], 0.10)}; box-shadow: none;
  border: 1px solid {_rgba(TOKENS["accent"], 0.30)}; border-radius: 8px;
  color: var(--accent); font-weight: 400; line-height: 1;
  transition: color 120ms ease, border-color 120ms ease, background 120ms ease;
  /* The label ("Clear") is the button's accessible name and must stay in the DOM;
     zeroing the type collapses it without hiding it from the accessibility tree, the
     way `display: none` would. The glyph is drawn beside it at its own size. */
  font-size: 0;
}}
.gradio-container .row.clearable .clear-btn::before {{
  content: "\\00d7"; font-size: 20px; line-height: 1;
}}
.gradio-container .row.clearable .clear-btn:hover {{
  background: {_rgba(TOKENS["accent"], 0.22)}; border-color: var(--accent);
  color: var(--accent-bright); transform: none;
}}

/* The subject stated once for a whole result set (§14), above the cards: named in the
   display face so it reads as the heading of the answer, not another control label. */
.subject-line {{
  font-family: {DISPLAY_STACK}; font-size: 18px; font-weight: 600; color: var(--text-dim);
  margin: 0.25rem 0 1rem;
}}
.subject-line .subject-name {{ color: var(--text); }}

/* The phone floor (#327). Of a 390px screen a chart's plot area had 217px: the page
   kept 32px a side, a card 21px more, and the axis labels the rest. The page's margin
   is halved and a card's inset cut to 12px, which is 48px back to the chart. The
   control panel is inset as the cards are, so their contents stay on one edge. After
   the card and panel rules above, which these outrank by order alone. */
@media (max-width: 640px) {{
  .gradio-container .app {{ padding-left: 16px; padding-right: 16px; }}
  .insight-card {{ padding: 0.75rem 0.75rem 1rem; }}
  .control-panel {{ padding: 0.5rem 0.75rem 0.75rem; }}
  /* The row stacks (#328): one control under another, each the width of the panel. */
  .control-panel .styler {{ flex-flow: column nowrap; align-items: stretch; }}
  .gradio-container .control-panel .row.clearable,
  .gradio-container .control-panel .row.clearable:has(.primary-control),
  .gradio-container .control-panel:has(#second-pilot-search) .row.clearable {{
    flex: 0 0 auto;
  }}
  .control-panel button.primary, .control-panel .block:has(> .checkbox-container) {{
    align-self: stretch;
  }}
  /* The strip scrolls sideways here, and a tab cut at its edge is what says so
     (#172). With eight tabs at a tab's own 16px a side (#346), the edge at 390px
     fell exactly where the fourth tab's label ends, with nothing of the fifth on
     screen. At 9px a side it falls through the fifth's label, 31px in, and through
     a label at 375, 412 and 430px too; a tab is still 44px wide at its narrowest. */
  .gradio-container .tabs > .tab-wrapper > .tab-container[role="tablist"] > button {{
    padding-left: 9px; padding-right: 9px;
  }}
}}

/* Quiet quality floor (§ quality): a visible keyboard focus ring on the accent, and
   motion stilled for readers who ask for it. */
:focus-visible {{ outline: 2px solid var(--accent-bright); outline-offset: 2px; }}
@media (prefers-reduced-motion: reduce) {{
  *, *::before, *::after {{
    animation-duration: 0.01ms !important; transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }}
}}

/* Retire the default-Gradio footer ("Built with Gradio / Use via API / Settings"),
   one of #132's observed default-furniture tells (#115). */
footer {{ display: none !important; }}

/* And the floating "Plot" chip Gradio draws over a `gr.Plot`, the same furniture one
   level down: it names the component's type over a card the app has already titled in
   its own type ("Performance over time"), so it says nothing the reader does not have.
   Retired rather than recoloured, which is the other way it could go: the chip sits on
   the well surface, where `--text-mute` is 4.23:1, the one text in the shipped theme
   that missed WCAG AA in the #118 acceptance pass. Scoped to the cards, so a label on
   any future component outside one is untouched. */
.insight-card label.float {{ display: none; }}

/* And the chart library's own toolbar (#325): a 272 by 24px row of stock icons
   (camera, zoom, pan, select, reset) over the top right of every chart. On a desktop
   it fades in under the pointer; on a touch device it is always drawn, and at phone
   width it covers the top axis label. Nothing on it is needed to read a chart: the
   range controls and the legends are the chart's own. Retired here rather than
   through the library's `displayModeBar` switch because `gr.Plot` sends a figure
   without its config, so the switch would have to ride each of the nine plots by
   hand; this covers every chart, the next one included. Not scoped to the cards,
   since the class is the library's and names nothing else. */
.gradio-container .modebar-container {{ display: none; }}

/* Every tab reachable at phone width (#172), the same kind of override one more level
   down. Gradio renders the tab strip twice: a `visually-hidden` mirror it measures, and
   the real `role="tablist"` it paints. Its overflow pass walks the MIRROR's buttons and
   slices everything past the container's width into a "..." menu, and the seven tabs
   need 607px against 326px of container at 390px, so four of them go behind that menu.
   Worse, when a hidden tab is the selected one, no button on the real strip carries
   `.selected` or `aria-selected`: four of seven tabs show no "you are here" on the
   device most of the reading happens on. Giving the mirror's buttons no width leaves the
   pass with nothing overflowing, so the menu hides itself and all seven render on the
   real strip. No width, because Gradio 6 decides the slice by summing those buttons'
   widths. Gradio 5 asked where each one's right edge fell, and a mirror left to wrap was
   the answer to that; on Gradio 6 it sliced five tabs away with every stylesheet test
   green (#307), so what holds this now is a measurement of the running page
   (`tests/test_page_style.py`). Gradio 6 also offers `overflow_behavior="wrap"` on the
   tabs themselves, which is the wrapping measured and turned down in the next paragraph.

   The real strip then scrolls sideways rather than wrapping, measured both ways: wrapping
   the visible strip costs 39px off the top of every phone page for a ragged three-row
   block of chrome, and needs a media query to keep it off desktop. Scrolling costs
   nothing at any width (the first `.t-lede` stays at 210.1 on phone and 167.6 on desktop,
   and at 1440 scrollWidth equals clientWidth at 1216, so no scroll region exists there at
   all). The hairline is taken off Gradio's absolutely-positioned `::after`, which would
   scroll out from under the buttons with them. It sat on the strip's own border box
   until #327, and is now the header row's, drawn by the page's column (above).

   Every selector here is (0,4,1) or higher out of necessity, the trap the clearable row
   documents above: this stylesheet is emitted BEFORE Gradio's bundle and its own tab
   rules are (0,3,0), so anything at or below that ties and loses on order. None of them
   names the `svelte-` hash, deliberately, so a Gradio upgrade that rehashes cannot
   silently drop them. */
.gradio-container .tabs > .tab-wrapper > .tab-container.visually-hidden > button {{
  width: 0; min-width: 0; padding: 0; border: 0; overflow: hidden;
}}
.gradio-container .tabs > .tab-wrapper > .tab-container[role="tablist"] {{
  overflow-x: auto; overflow-y: hidden; scrollbar-width: none; height: auto;
}}
.gradio-container .tabs > .tab-wrapper > .tab-container[role="tablist"]::after {{ content: none; }}
.gradio-container .tabs > .tab-wrapper > .tab-container[role="tablist"]::-webkit-scrollbar {{ display: none; }}
.gradio-container .tabs > .tab-wrapper > .tab-container[role="tablist"] > button {{
  flex: 0 0 auto; height: {TAB_PX}px;
}}

/* The app's own credit surface in place of that footer (#115): coverage, the build
   snapshot, and who to reach, set apart from the tabs by a hairline and centred as a
   page footer. The coverage counts read a step up from the caption below them, the
   figures themselves in the primary ink so the numbers land first. The repo /
   7phstats / licence links this also carried were dropped at the maintainer's request
   during an app walkthrough, re-confirmed against #85's criterion 13 on 2026-07-26:
   no link, no credit, no licence in the app. That credit lives in `README.md` alone. */
.provenance {{
  border-top: 1px solid var(--border); margin-top: 2.5rem; padding-top: 1.25rem;
  text-align: center;
}}
.provenance .coverage {{
  font-size: 14px; color: var(--text-dim); margin-bottom: 0.4rem;
}}
.provenance .coverage b {{ color: var(--text); font-weight: 650; }}
.provenance .t-caption {{ color: var(--text-mute); }}

/* The Community page (#346, `graph7ph.community`): the landing page. The title and
   the one line, six figures in a row, three cards of hand-drawn charts, and a
   doorway to each content tab. The cards, their titles and their captions are the
   app's own (§12, the caption rule); what is set here is what only this page has.

   Every element states its own colour: Gradio colours everything inside an HTML
   block (`.prose *`), so one left to inherit takes the main ink. A paragraph is
   named with the page as well, or the body rule (`.prose p`) outranks it and gives
   it the body's ink and measure. A hue arrives on the element as `--k`, since
   which hue is the graph's to say (`render`). */
.community {{ padding-top: 1.5rem; }}
.cm-intro .page-name {{ font-size: 34px; text-wrap: balance; }}
.community .t-lede {{ font-size: 17px; line-height: 1.5; color: var(--text-dim); margin: 0.85rem 0 0; }}
/* Six figures on one hairline, each parted from the next by another: a row of
   readings, not six cards (the tiles of a subject's page are cards). */
.cm-figures {{
  display: grid; grid-template-columns: repeat(6, minmax(0, 1fr));
  margin: 2.5rem 0 0.85rem; border-top: 1px solid var(--border);
}}
.cm-fig {{ padding: 1.25rem 1.25rem 0.4rem 0; min-width: 0; }}
.cm-fig + .cm-fig {{ padding-left: 1.25rem; border-left: 1px solid var(--border); }}
/* A figure's label and a panel's are in the control-label role (§3), set above. */
.cm-label {{ display: flex; align-items: center; gap: 8px; line-height: 1; }}
.cm-dot {{ flex: none; width: 8px; height: 8px; border-radius: 50%; background: var(--k); }}
.cm-value {{
  font-size: 38px; font-weight: 600; line-height: 1.1; letter-spacing: -0.02em;
  color: var(--text); margin: 0.75rem 0 0.1rem;
}}
.cm-beneath, .community .cm-source {{ font-size: 13px; line-height: 1.5; color: var(--text-mute); }}
.community .cm-source {{ margin: 0 0 1.75rem; max-width: none; }}

.cm-card .caption {{ margin-top: 0.15rem; }}
.cm-plot {{ margin-top: 1.1rem; }}
.cm-plot svg {{ display: block; overflow: visible; }}
.cm-plot text {{
  font-family: {FONT_STACK}; font-size: 12px; fill: var(--text-mute);
  font-variant-numeric: tabular-nums;
}}
.cm-plot text.cm-ink {{ fill: var(--text); font-weight: 600; font-size: 13px; }}
/* A dot's own number, on the dot. White and not the main ink, which is 4.25 to 1
   on the event green at a size that needs 4.5: white is 4.95. */
.cm-plot text.cm-on {{ fill: #fff; font-weight: 600; font-size: 11px; pointer-events: none; }}
.cm-grid {{ stroke: var(--border); stroke-width: 1; }}
/* A dot: solid where the event is a major, a ring where it is not, and edged in
   the main ink while it is the one speaking. */
.cm-event {{ fill: var(--k); }}
.cm-event:not(.cm-major) {{ fill-opacity: 0.28; stroke: var(--k); stroke-width: 1.2; }}
.cm-event.cm-hot {{ stroke: var(--text); stroke-width: 1.5; }}
.cm-hair {{ stroke: var(--text-mute); stroke-width: 1; }}
.cm-month.cm-hot {{ filter: brightness(1.4); }}
.cm-sub {{ line-height: 1; margin: 1.4rem 0 0.5rem; }}
.cm-pair .cm-plot {{ margin-top: 0; }}

.cm-key {{
  display: flex; flex-wrap: wrap; gap: 6px 18px; margin-top: 0.9rem;
  font-size: 13px; line-height: 1.5; color: var(--text-dim);
}}
.cm-key span {{ color: var(--text-dim); }}
.cm-key i {{
  display: inline-block; width: 10px; height: 10px; border-radius: 50%;
  margin-right: 7px; vertical-align: -1px; background: var(--k);
}}
.cm-key i.cm-square {{ border-radius: 2px; }}
/* An event that is no major: a ring, as its dot is. */
.cm-key i.cm-ring {{
  background: color-mix(in srgb, var(--k) 28%, transparent);
  box-shadow: inset 0 0 0 1.2px var(--k);
}}

/* A bar per year. Its length is the year's share of the fullest year's, less the
   room the total takes at its end, and each part's is its pilots'. */
.cm-years {{ display: grid; gap: 12px; margin-top: 1.25rem; }}
.cm-row {{ display: grid; grid-template-columns: 76px minmax(0, 1fr); align-items: center; gap: 12px; }}
.cm-year {{ font-size: 14px; font-weight: 600; line-height: 1.2; color: var(--text); }}
.cm-year small {{ display: block; font-size: 12px; font-weight: 400; color: var(--text-mute); }}
.cm-track {{ display: flex; align-items: center; gap: 10px; min-width: 0; }}
.cm-bar {{
  display: flex; gap: 2px; height: 28px; min-width: 0;
  flex: 0 1 calc((100% - 56px) * var(--share));
}}
.cm-seg {{
  min-width: 3px; border-radius: 4px; background: var(--k); padding-left: 8px;
  font-size: 13px; font-weight: 600; line-height: 28px; white-space: nowrap;
}}
.cm-seg span {{ color: inherit; }}
/* A part too short for its number keeps the number for the hover. */
.cm-seg.cm-tight span {{ visibility: hidden; }}
.cm-seg:hover, .cm-seg:focus-visible {{ filter: brightness(1.12); }}
.cm-seg:focus-visible {{ outline: 2px solid var(--accent-bright); outline-offset: 2px; }}
.cm-total {{ font-size: 14px; font-weight: 600; line-height: 1; color: var(--text); }}
.community .cm-note {{
  font-size: 15px; line-height: 1.6; color: var(--text-dim); margin: 1.25rem 0 0; max-width: none;
}}
.community .cm-note b {{ color: var(--text); font-weight: 600; }}

/* The doorways: a tab's name and what it answers, the whole box the link. */
.cm-doors {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1rem; margin-top: 0.5rem; }}
.cm-doors a.cm-door {{
  position: relative; display: block; padding: 1.2rem 1.4rem; text-decoration: none;
  text-align: left;
  background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
  transition: border-color 0.15s, background 0.15s;
}}
.cm-doors a.cm-door:hover {{ border-color: var(--accent); background: var(--surface-2); }}
.cm-door h3 {{
  font-family: {DISPLAY_STACK}; font-size: 20px; font-weight: 600; line-height: 1.25;
  color: var(--text); margin: 0 0 0.35rem; padding: 0 1.5rem 0 0;
}}
.community .cm-door p {{
  font-size: 14px; line-height: 1.5; color: var(--text-dim); margin: 0; max-width: none;
}}
.cm-arrow {{
  position: absolute; top: 1.1rem; right: 1.25rem; color: var(--text-mute);
  transition: color 0.15s, transform 0.15s;
}}
.cm-door:hover .cm-arrow {{ color: var(--accent-bright); transform: translateX(3px); }}

/* What a mark says under the pointer, a finger or the keyboard's focus. */
.cm-tip {{
  position: fixed; z-index: 20; display: none; pointer-events: none; max-width: 240px;
  padding: 8px 11px; border-radius: 8px; background: var(--surface-2);
  border: 1px solid var(--border); box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
  font-size: 13px; line-height: 1.4; color: var(--text-dim);
  font-variant-numeric: tabular-nums;
}}
.cm-tip b {{ display: block; font-size: 14px; font-weight: 600; color: var(--text); }}

@media (min-width: 641px) and (max-width: 1000px) {{
  .cm-figures {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
  .cm-fig:nth-child(3n + 1) {{ border-left: 0; padding-left: 0; }}
  .cm-fig:nth-child(n + 4) {{ border-top: 1px solid var(--border); }}
}}
@media (max-width: 900px) {{
  .cm-doors {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
}}
@media (max-width: 640px) {{
  .community {{ padding-top: 0.75rem; }}
  .cm-intro .page-name {{ font-size: 28px; }}
  .cm-figures {{ grid-template-columns: repeat(2, minmax(0, 1fr)); margin-top: 1.75rem; }}
  .cm-fig {{ padding: 1rem 0.75rem 0.75rem 0; }}
  .cm-fig + .cm-fig {{ padding-left: 1rem; }}
  .cm-fig:nth-child(odd) {{ border-left: 0; padding-left: 0; }}
  .cm-fig:nth-child(n + 3) {{ border-top: 1px solid var(--border); }}
  .cm-value {{ font-size: 32px; }}
  .cm-row {{ grid-template-columns: 52px minmax(0, 1fr); gap: 8px; }}
  .cm-doors {{ gap: 0.6rem; }}
  .cm-doors a.cm-door {{ padding: 1rem; }}
  .cm-door h3 {{ font-size: 18px; }}
}}

}}
"""


def dark_theme():
    """A Gradio theme that binds the chrome to the tokens, in both light and dark
    mode, so the app renders the dark palette regardless of the browser preference.

    Every colour var points at a ``--token`` (defined by :func:`build_css`), so the
    tokens stay the single source; the light variants are set to the same dark tokens
    so no rule of either mode can paint a light surface, and ``serve`` asks the page for
    Gradio's own dark component rules on top.
    """
    import gradio as gr

    # The font stack is applied in CSS (`.gradio-container`), not through the theme's
    # `font=`, which would quote the whole comma-joined stack as one family name.
    return gr.themes.Base().set(
        body_background_fill="var(--bg)", body_background_fill_dark="var(--bg)",
        background_fill_primary="var(--surface)", background_fill_primary_dark="var(--surface)",
        background_fill_secondary="var(--surface-2)", background_fill_secondary_dark="var(--surface-2)",
        block_background_fill="var(--surface)", block_background_fill_dark="var(--surface)",
        border_color_primary="var(--border)", border_color_primary_dark="var(--border)",
        block_border_color="var(--border)", block_border_color_dark="var(--border)",
        body_text_color="var(--text)", body_text_color_dark="var(--text)",
        body_text_color_subdued="var(--text-mute)", body_text_color_subdued_dark="var(--text-mute)",
        body_text_size="15px",
        block_label_text_color="var(--text-mute)", block_label_text_color_dark="var(--text-mute)",
        block_title_text_color="var(--text-mute)", block_title_text_color_dark="var(--text-mute)",
        block_label_text_size="12px", block_title_text_size="12px",
        block_label_text_weight="600", block_title_text_weight="600",
        input_background_fill="var(--surface-2)", input_background_fill_dark="var(--surface-2)",
        input_border_color="var(--border)", input_border_color_dark="var(--border)",
        panel_background_fill="var(--surface)", panel_background_fill_dark="var(--surface)",
        panel_border_color="var(--border)", panel_border_color_dark="var(--border)",
        link_text_color="var(--accent-bright)", link_text_color_dark="var(--accent-bright)",
        link_text_color_hover="var(--accent-bright)", link_text_color_hover_dark="var(--accent-bright)",
        link_text_color_active="var(--accent-bright)", link_text_color_active_dark="var(--accent-bright)",
        link_text_color_visited="var(--accent-bright)", link_text_color_visited_dark="var(--accent-bright)",
        button_primary_background_fill="var(--accent)", button_primary_background_fill_dark="var(--accent)",
        button_primary_background_fill_hover="var(--accent-bright)",
        button_primary_background_fill_hover_dark="var(--accent-bright)",
        button_primary_border_color="var(--accent)", button_primary_border_color_dark="var(--accent)",
        button_primary_text_color="var(--bg)", button_primary_text_color_dark="var(--bg)",
        # Gradio's own accent (`*primary_500`, #3b82f6) onto the app's on-surface accent.
        # This one kwarg carries the selected tab's ink, the 2px `::after` bar under it
        # (the largest blue object on the page, and one the #118 text-node audit never
        # counted because it is not text), the "..." dots, the radio mark and the checkbox
        # tick, since `checkbox_background_color_selected` and both accent border colours
        # default to `*color_accent`. `--accent-bright` rather than `--accent` on
        # measurement, not taste: `--accent` was built and measured and it drops the
        # selected chip's label to 4.40 on its own composited ground, a new WCAG AA
        # failure and the only one either candidate produced, where `--accent-bright`
        # gives 7.27 on the ground and 5.64 on the chip. It is also how this codebase
        # already splits the two: `--accent` is a fill or an edge, `--accent-bright` is
        # ink on a surface (#163 made the same call for the range slider, which wore the
        # accent until #366).
        color_accent="var(--accent-bright)",
        # The radio/checkbox chip, redesigned in the same change because the accent is
        # what was holding it up. Gradio chains `..._fill_selected` straight back to
        # `checkbox_label_background_fill` and takes its border width from the input's,
        # which is 0px here, so a picked chip and an unpicked one were byte-identical
        # (all three of Top 25% / Top 50% / Top 75% measured bg rgb(82,82,91), ink
        # rgb(242,237,230), border 0px) and the blue dot was the only cue that one was
        # picked: retiring the blue alone would have left the control with no selected
        # state. So a picked chip takes the quiet accent square `.clear-btn` already
        # establishes in `build_css` (and `_faq_tag_tints` one hue over) and reads as the
        # same kind of object, on a hairline-bordered well when it is not picked. Its ink
        # is the bright accent where `.clear-btn` rests on `--accent`, because `--accent`
        # measures 4.40 on the chip's tinted ground, under AA, against 5.64 for the bright
        # one: the same accent, one step up in strength. `--text-mute` is used nowhere
        # here on purpose (3.83 on the chip, 3.32 on `--surface-2`, which §2 already rules
        # out).
        checkbox_label_background_fill="var(--surface)",
        checkbox_label_background_fill_dark="var(--surface)",
        checkbox_label_background_fill_hover=_rgba(TOKENS["accent"], 0.08),
        checkbox_label_background_fill_hover_dark=_rgba(TOKENS["accent"], 0.08),
        checkbox_label_background_fill_selected=_rgba(TOKENS["accent"], 0.14),
        checkbox_label_background_fill_selected_dark=_rgba(TOKENS["accent"], 0.14),
        checkbox_label_border_width="1px", checkbox_label_border_width_dark="1px",
        checkbox_label_border_color="var(--border)", checkbox_label_border_color_dark="var(--border)",
        checkbox_label_border_color_selected=_rgba(TOKENS["accent"], 0.55),
        checkbox_label_border_color_selected_dark=_rgba(TOKENS["accent"], 0.55),
        checkbox_label_text_color="var(--text-dim)", checkbox_label_text_color_dark="var(--text-dim)",
        checkbox_label_text_color_selected="var(--accent-bright)",
        checkbox_label_text_color_selected_dark="var(--accent-bright)",
        checkbox_background_color="var(--surface-2)", checkbox_background_color_dark="var(--surface-2)",
        # The one place the accent above must not reach. Gradio draws the picked radio
        # dot and the checkbox tick as a hardcoded `#fff` glyph on this fill, and white
        # on `--accent-bright` is 2.59, under the 3:1 WCAG 1.4.11 asks of a non-text
        # control: worse than the Gradio blue being retired (3.68), which is the one
        # way this change could have gone backwards. `--accent` gives 3.32 and passes,
        # and it is the same split the comment above draws: this is a fill, not ink.
        # The #118 audit walks text nodes, so it cannot see a glyph like this one.
        checkbox_background_color_selected="var(--accent)",
        checkbox_background_color_selected_dark="var(--accent)",
        checkbox_border_color="var(--border)", checkbox_border_color_dark="var(--border)",
        # The neighbouring zincs, each one component away from being painted: a secondary
        # or a stop button, an accent border, a stat panel. Bound here rather than left to
        # be discovered, since `--button-secondary-*` is also what the chip fill chained
        # to and what `--button-cancel-*` chains to in turn, so the stop button follows
        # from these three with no kwargs of its own. `stat_background_fill` reads the
        # primary ramp rather than `*color_accent`, so the accent above does not carry it.
        button_secondary_background_fill="var(--surface-2)", button_secondary_background_fill_dark="var(--surface-2)",
        button_secondary_background_fill_hover="var(--surface)", button_secondary_background_fill_hover_dark="var(--surface)",
        button_secondary_border_color="var(--border)", button_secondary_border_color_dark="var(--border)",
        button_secondary_text_color="var(--text)", button_secondary_text_color_dark="var(--text)",
        border_color_accent="var(--accent)", border_color_accent_dark="var(--accent)",
        stat_background_fill="var(--accent)", stat_background_fill_dark="var(--accent)",
    )


# The share surface (issue #115). Fixed copy, so the preview a link unfurls reads as
# something the project chose rather than Gradio's default furniture. The URL is the
# custom domain the README points people to, not the Space it runs on: the Space is
# protected, so its Hub page answers only to its owner and would unfurl as nothing.
_APP_TITLE = "7 Point Highlander Graph"
_APP_DESCRIPTION = (
    "An interactive knowledge graph of the Australian 7 Point Highlander metagame: "
    "events, pilots, decks, and cards, for exploration and analytics."
)
_APP_URL = "https://www.7phgraph.com"

# The name in the browser tab and the page's header, shorter than the share card's
# because a tab strip truncates hard (the reasoning is at ``gr.Blocks`` in ``app``).
# Held here so the title the page is served with, the one its script sets and the
# one in the header are the same words.
TAB_TITLE = "7PH Graph"

# The graph mark: three nodes joined in a triad, which is the least a node-link diagram
# can be and still read as one, so the favicon says what the app is. It replaces the
# 7-pip mark, which §15 had already dropped from the page for reading as an ambiguous
# "dot dot dot"; the same ambiguity was worse at icon size, where seven dots blur into a
# smudge while three nodes and three edges still resolve at 16px. Drawn from the tokens,
# so an accent change carries into the icon.
_NODES = [(16, 7.5), (23.5, 21), (8.5, 21)]


def mark_svg() -> str:
    """The graph mark as SVG source: the favicon, and the mark in the page's header."""
    accent = TOKENS["accent-bright"]
    # Edges first, so the nodes cap their ends rather than the strokes crossing them.
    edges = "".join(
        f"<line x1='{x1}' y1='{y1}' x2='{x2}' y2='{y2}' stroke='{accent}' "
        f"stroke-width='1.5' stroke-opacity='0.75' stroke-linecap='round'/>"
        for (x1, y1), (x2, y2) in zip(_NODES, _NODES[1:] + _NODES[:1])
    )
    nodes = "".join(
        f"<circle cx='{x}' cy='{y}' r='3.2' fill='{accent}'/>" for x, y in _NODES
    )
    return (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
        f"<rect width='32' height='32' rx='7' fill='{TOKENS['bg']}'/>"
        f"{edges}{nodes}</svg>"
    )


def _favicon_data_uri() -> str:
    """The graph mark as a base64 ``image/svg+xml`` data URI.

    Self-hosted like the fonts: the whole icon travels in the head, so the Space
    serves no external favicon request and needs no static-file route.
    """
    b64 = base64.b64encode(mark_svg().encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


def favicon_file() -> Path:
    """The graph mark on disk, for ``Blocks.launch(favicon_path=...)``.

    The ``<link rel="icon">`` in the head is not enough by itself. The head this app
    passes is not in the page Gradio serves: it arrives afterwards from the config,
    once the page's script runs, by which point the browser has asked for
    ``/favicon.ico`` and taken the answer. So the icon has to be what ``/favicon.ico``
    itself answers with, and ``favicon_path`` is what sets that: ``gradio.routes``
    serves that route from this file, falling back to Gradio's own logo when it is
    unset, which is the logo the Space wore. Written out at launch rather than shipped
    as a checked-in file, so the mark stays derived from the tokens.
    """
    path = Path(tempfile.gettempdir()) / "graph7ph-icon.svg"
    path.write_text(mark_svg(), encoding="utf-8")
    return path


def build_head() -> str:
    """The HTML injected into ``<head>`` at the ``gr.Blocks`` level (issue #115).

    The favicon (the graph mark), in place of Gradio's. The preview card a shared link
    unfurls from is not here: Gradio hands this head to the page's script, and a
    preview fetcher runs no script, so the card is written into the page as it is
    served instead (:func:`preview_tags`).
    """
    return f'<link rel="icon" href="{_favicon_data_uri()}">'


def title_tag() -> str:
    """The page's ``<title>`` (issue #341).

    Gradio's page template has none: the tab is named by the page's script once it
    runs. ``serve`` writes this into the page as served, so a reader that runs no
    script finds the name, and a tab has it before the script arrives.
    """
    return f"<title>{html.escape(TAB_TITLE)}</title>"


def ground_style() -> str:
    """The ground and the ink as the page is served, ahead of the stylesheet (#327).

    The stylesheet reaches the page through its script. Until that runs, the page is
    painted by a rule in Gradio's own template, which reads the theme's body fill and
    text colour. This theme points both at tokens (``var(--bg)``, ``var(--text)``),
    and the template stores them in variables of its own, one of them also named
    ``--bg``: so it wrote ``--bg: var(--bg)``, a variable defined as itself, and the
    page was painted with nothing. The two tokens are defined here as well, on a
    selector that outranks the template's, so its rule paints the ground it was
    asked for.
    """
    return (
        f"<style>:root:root {{ --bg: {TOKENS['bg']}; --text: {TOKENS['text']}; }}</style>"
    )


def preview_tags() -> str:
    """The Open Graph / Twitter card a shared link unfurls from (issues #115, #320).

    The app's own, in place of Gradio's default furniture. ``serve`` writes it into
    the page as served, which is the only copy a preview fetcher reads.
    No preview image yet (deferred): a ``summary`` card carries the title and
    description, which is the honest card to declare until a real image is authored.
    """
    title, desc = html.escape(_APP_TITLE), html.escape(_APP_DESCRIPTION)
    return "\n".join([
        '<meta property="og:type" content="website">',
        f'<meta property="og:title" content="{title}">',
        f'<meta property="og:description" content="{desc}">',
        f'<meta property="og:url" content="{_APP_URL}">',
        '<meta name="twitter:card" content="summary">',
        f'<meta name="twitter:title" content="{title}">',
        f'<meta name="twitter:description" content="{desc}">',
    ])


# A plot drawn at build time inside a tab that is not the one the app opens on is laid
# out while its container is display:none, so Plotly measures nothing and falls back to
# its own default width: the chart comes up narrow against a wide card, and only
# straightens out when something re-renders it. Plotly's responsive mode listens for
# the window resize event, so a tab that shows such a plot fires one when it is opened.
# The frame lets the newly shown tab lay out first, or the measurement is still zero.
RESIZE_PLOTS_JS = """
() => { requestAnimationFrame(() => window.dispatchEvent(new Event('resize'))); }
"""


def placeholders_js(texts: dict[str, str]) -> str:
    """The page script that gives each search field its placeholder (issue #328).

    ``texts`` is the placeholder for each field, by the field's element id. Gradio's
    dropdown has no placeholder of its own to set, so the text is written onto the
    field's input once the page has loaded. A field that is hidden is not in the
    page at all, and is built afresh each time its view is picked, so the page is
    watched and a field is given its text whenever it arrives.
    """
    return f"""
() => {{
  const texts = {json.dumps(texts)};
  const write = () => {{
    for (const [id, text] of Object.entries(texts)) {{
      const field = document.querySelector(`#${{id}} input`);
      if (field && field.placeholder !== text) field.placeholder = text;
    }}
  }};
  write();
  new MutationObserver(write).observe(document.body, {{childList: true, subtree: true}});
}}
"""


def into_view_js(*ids: str) -> str:
    """The page script that brings a result into view once a pick has drawn it
    (issue #328).

    ``ids`` are the element ids a pick can draw into; the one on show is the result.
    A focus archetype on Meta drew its chart 965px down a 900px window and an
    archetype's timeline 1,330px down, so the pick looked like it had done nothing.

    The page moves the least that shows the result: its top is brought up to the
    middle of the window, and only when it starts below that or above the window. A
    result that lands under its controls stays where it is. One that lands lower
    comes up with whatever is above it, which on a phone is the stacked controls the
    reader is still using: putting the result at the top of the window took them off
    the screen, an open list of options with them.

    The result is waited for, a frame at a time, because the step that runs this
    follows the query and not the drawing of what it returned. A pick that draws
    nothing (a cleared field) is given up on after two seconds. The move is smooth
    unless the reader has asked for less motion.
    """
    return f"""
() => {{
  const ids = {json.dumps(ids)};
  const until = performance.now() + 2000;
  const look = () => {{
    const result = ids.map(id => document.getElementById(id))
      .find(el => el && el.getBoundingClientRect().height > 0);
    if (!result) {{ if (performance.now() < until) requestAnimationFrame(look); return; }}
    const top = result.getBoundingClientRect().top;
    const middle = window.innerHeight / 2;
    if (top >= 0 && top <= middle) return;
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    window.scrollBy({{top: top - middle, behavior: still ? 'auto' : 'smooth'}});
  }};
  requestAnimationFrame(look);
}}
"""


def fit_events_js(room: int) -> str:
    """The page script that opens a pair chart on as many events as its plot can show.

    A pair chart spaces its shared events evenly (issue #335), and a long rivalry
    still does not fit a phone: at 84 events each has under 4px there. A figure is one
    layout for every width and the server never learns the width, so the page decides.
    A chart that states how many events it holds (``layout.meta.events``) and whose
    plot gives each less than ``room`` pixels is opened on the latest that fit; the
    range control under it, which spans them all, reaches the rest.

    It follows the plot's width, so a chart first drawn at a width it does not keep
    is fitted again at the one it does. It stops at the first range it did not set:
    from then on the slice is the reader's.

    The chart is found again whenever the page changes, since a hidden chart is not
    in the page and is built afresh when shown.
    """
    return f"""
() => {{
  const ROOM = {room};
  const opened = new WeakMap();
  const same = (a, b) => Math.abs(a[0] - b[0]) < 1e-6 && Math.abs(a[1] - b[1]) < 1e-6;
  const fit = () => document.querySelectorAll('.js-plotly-plot').forEach(chart => {{
    const events = chart.layout?.meta?.events;
    const whole = chart.layout?.xaxis?.rangeslider?.range;
    const range = chart._fullLayout?.xaxis?.range;
    const width = chart._fullLayout?._size?.w;
    if (!events || !whole || !range || !width || !window.Plotly) return;
    const shown = Math.floor(width / ROOM);
    const want = shown < events ? [whole[1] - shown, whole[1]] : whole;
    if (!same(range, opened.get(chart.layout) || whole) || same(range, want)) return;
    opened.set(chart.layout, want);
    window.Plotly.relayout(chart, {{'xaxis.range': want}});
  }});
  fit();
  new MutationObserver(fit).observe(document.body, {{childList: true, subtree: true}});
}}
"""


def chart_names_js(card: str, also: str) -> str:
    """The page script that decides which names a chart of named dots draws, and
    makes its hover reach every dot (issue #337).

    ``card`` is the element id of the card holding the chart, and ``also`` the words
    that open the list of archetypes a dot stands in for, in the hover's own voice
    (the figure writes the rest of that line). Plotly prints a name
    beside every dot and cannot keep two apart, and the landscape's printed over each
    other at every width: 8 to 21 pairs a year at 390px and 2 to 11 at 1440, with the
    most played archetype's name cut off at the right edge of a phone. What fits is a
    matter of pixels, so it is settled here, on the drawn chart, each time the chart
    library draws it: on load, on a new year, on a resize, and on a drag of the range
    filter, which is how a reader opens a crowd and gets its names back.

    The names are taken most played first, by the dot's place on the share axis. Each
    is set to the right of its dot, or to the left where the right is not free, and
    is not drawn where neither is. A spot is free when the name stays inside the
    plot, clear of every name already set and of every other archetype's dot: a name
    across a neighbour's ring reads as neither's. A name not drawn is hidden, not
    removed, so the next drawing starts from all of them.

    A name left out is on the hover, so the hover has to reach every dot. The chart
    library answers for one dot, the smallest the pointer is inside, and of equal
    ones the later, so a dot whose middle is inside a smaller one may never answer
    for itself. Each dot that answers at another's middle is told so: the last field
    of its hover row, which the figure sends empty, is filled with ``also`` and the
    names of the archetypes it stands in for, and its box names them after its own.
    A dot outside a filtered range is not drawn and stands in for nothing. Which dot
    answers is worked out as the library works it out (plotly.js 3.8.2, scatter
    hover): each dot is as far from the pointer as the pointer is outside it, and
    never nearer than one less three over its radius, which is what lets a smaller
    dot win inside a larger and a fraction of a pixel outside its own edge; the
    nearest answers, and the later of two as near.

    The chart is found again whenever the page changes, since a hidden chart is not
    in the page and is built afresh when shown, and the names are set once more when
    the fonts have loaded, which changes how wide they are.
    """
    return f"""
() => {{
  const card = {json.dumps(card)};
  const also = {json.dumps(also)};
  const GAP = 2;
  const drawn = (chart, selector) => [...chart.querySelectorAll(selector)]
    .filter(mark => !mark.closest('.rangeslider-container')
      && getComputedStyle(mark.closest('.textpoint') || mark).display !== 'none');
  const tellStandIns = (chart, dots) => {{
    const trace = chart.data[0];
    const hidden = new Map();
    for (const own of dots) {{
      const far = dot => Math.max(
        Math.hypot(own.x - dot.x, own.y - dot.y) - dot.r, 1 - 3 / dot.r);
      const answers = dots.reduce((best, dot) => far(dot) <= far(best) ? dot : best);
      if (answers !== own) {{
        hidden.set(answers.index,
          [...(hidden.get(answers.index) || []), trace.text[own.index]]);
      }}
    }}
    trace.customdata.forEach((row, index) => {{
      row[row.length - 1] = hidden.has(index) ? also + hidden.get(index).join(', ') : '';
    }});
  }};
  const placeNames = (chart, dots, plot) => {{
    const hit = (a, b) => a.left < b.right + GAP && b.left < a.right + GAP
                       && a.top < b.bottom + GAP && b.top < a.bottom + GAP;
    const placed = [];
    const names = drawn(chart, '.scatterlayer .textpoint text')
      .sort((a, b) => b.__data__.x - a.__data__.x || a.__data__.i - b.__data__.i);
    for (const name of names) {{
      const group = name.parentElement;
      const offset = group.transform.baseVal.getItem(0).matrix;
      const box = name.getBoundingClientRect();
      const onRight = name.getAttribute('text-anchor') === 'start';
      const reach = Math.abs(offset.e);
      const across = (onRight ? -1 : 1) * (2 * reach + box.width);
      const spots = [true, false].map(toRight => {{
        const by = toRight === onRight ? 0 : across;
        return {{toRight, left: box.left + by, right: box.right + by,
                top: box.top, bottom: box.bottom}};
      }});
      const spot = spots.find(spot =>
        spot.left >= plot.left && spot.right <= plot.right
        && spot.top >= plot.top && spot.bottom <= plot.bottom
        && !placed.some(other => hit(spot, other))
        && !dots.some(dot => dot.index !== name.__data__.i && hit(spot, dot.box)));
      name.style.visibility = spot ? '' : 'hidden';
      if (!spot) continue;
      placed.push(spot);
      if (spot.toRight !== onRight) {{
        group.setAttribute('transform',
          `translate(${{spot.toRight ? reach : -reach}},${{offset.f}})`);
        name.setAttribute('text-anchor', spot.toRight ? 'start' : 'end');
      }}
    }}
  }};
  const find = () => document.querySelector(`#${{card}} .js-plotly-plot`);
  const draw = () => {{
    const chart = find();
    const plot = chart?.querySelector('.nsewdrag')?.getBoundingClientRect();
    if (!plot?.width) return;
    const dots = drawn(chart, '.scatterlayer .points path.point').map(dot => {{
      const box = dot.getBoundingClientRect();
      return {{index: dot.__data__.i, box, r: dot.__data__.mrc,
              x: box.left + box.width / 2, y: box.top + box.height / 2}};
    }});
    tellStandIns(chart, dots);
    placeNames(chart, dots, plot);
  }};
  let tended = null;
  const tend = () => {{
    const chart = find();
    if (!chart || !chart.on || chart === tended) return;
    tended = chart;
    chart.on('plotly_afterplot', draw);
    draw();
  }};
  tend();
  new MutationObserver(tend).observe(document.body, {{childList: true, subtree: true}});
  document.fonts.ready.then(draw);
}}
"""
