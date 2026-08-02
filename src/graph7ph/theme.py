"""The v1 dark theme: the design tokens, the type system, and the CSS that commits
every surface to them.

The single source of truth is ``docs/design/v1-visual-direction.md`` (§2 tokens, §3
type system, §12 insight cards, §13 control panel, §15 signature). This module is that
document as data: the tokens defined once, the two self-hosted faces, the eight type
roles, the card / panel surfaces, and a ``build_css`` that serialises them into the
``:root`` custom properties and role styles the app injects at the ``gr.Blocks`` level.
``dark_theme`` binds Gradio's own chrome to the same tokens and ``FORCE_DARK_JS``
retires the browser light/dark inheritance, so the app reads as one coherent dark
build. ``contrast_ratio`` lets the tests hold every text role to WCAG AA on the ground,
so a token edited to an illegible value fails a test rather than a reader.
"""

import base64
import html
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
_FONT_DIR = Path(__file__).parent / "assets" / "fonts"
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


@lru_cache(maxsize=1)
def _font_faces() -> str:
    """The ``@font-face`` rules for the two faces, woff2 base64-embedded as data URIs.

    Embedding (rather than linking) means the Space serves no external font request and
    needs no static-file config: the whole face travels in the injected stylesheet. Read
    once and memoised, since the bytes never change within a process.
    """
    rules = []
    for spec in FONTS.values():
        b64 = base64.b64encode((_FONT_DIR / spec["file"]).read_bytes()).decode("ascii")
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


# The highlighted figure in a field-standing line (`.t-fieldstat .pct`), taken from the
# shared categorical set rather than named again here, so the number the eye lands on is
# the same blue the charts draw a series in.
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
    control-panel surfaces (§12/§13), and the graph mark (§15). The heading roles
    land on the page's own markdown; the control label lands on Gradio's field title
    (its ``block-info`` span); the rest are utility classes. The reading measure bounds
    prose to ``MEASURE_CH``. A quiet quality floor (reduced motion, visible focus) rides
    at the end, and the default-Gradio footer is retired (#115).
    """
    root = "\n".join(f"  --{name}: {hex_};" for name, hex_ in TOKENS.items())
    return f"""{_font_faces()}

:root {{
{root}
}}

.gradio-container {{ font-family: {FONT_STACK}; }}

/* §3 type system. Titles take the display serif; body, ledes, captions, labels and
   numerics take the grotesque sans. Heading roles apply to the page's own markdown;
   the utility classes carry the rest for deliberate use where results are framed. */
.prose h1, .t-page-title {{
  font-family: {DISPLAY_STACK}; font-size: 34px; font-weight: 600; letter-spacing: -0.01em; line-height: 1.25; color: var(--text);
  /* Fraunces' caps and ascenders overshoot a tight line box; the extra leading plus a
     little top padding keeps the tallest glyphs off the clipped top edge. */
  padding-top: 0.08em;
}}
.prose h2, .t-section-heading {{
  font-family: {DISPLAY_STACK}; font-size: 22px; font-weight: 600; letter-spacing: -0.005em; line-height: 1.2; color: var(--text);
}}
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
.prose p, .prose li, .t-body {{ font-size: 15px; font-weight: 400; line-height: 1.6; color: var(--text); }}
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
.t-control-label {{
  font-size: 12px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-mute);
}}
.gradio-container span[data-testid="block-info"] {{ letter-spacing: 0.06em; text-transform: uppercase; }}

/* Reading measure: bound prose, not the widgets, so paragraphs stay legible on a
   wide monitor while controls and charts still fill their space. */
.prose p, .prose li, .t-lede, .t-body {{ max-width: {MEASURE_CH}ch; }}

/* The FAQ tab is the one place the reading measure is dropped: its boxes are full-width,
   one per row, and the answer runs the width of its box. A measure-bound paragraph in a
   wide box reads as a narrow ragged column against empty space, which is worse here than
   a long line, because an answer is a handful of sentences read once rather than a page
   of prose. The box is the bound; the paragraph fills it.

   Twice tried and twice reverted at the maintainer's call (2026-07-26), so do not "fix"
   this again without asking: capping the card put one narrow column down the left of a
   wide screen with the rest empty, and flowing the cards into as many measure-wide
   columns as fit broke the one-question-per-row reading order. The layout is the
   decision; #85's criterion 4 is answered against it, not the other way round. */
.faq-card p, .faq-card li {{ max-width: none; }}
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
/* Every block inside the card, at any depth: Gradio nests a component's block as deep as
   it likes, and the two levels this used to name left the heading and the FAQ prose
   sitting on a lighter inner slab, a card drawn inside a card. `.styler` is the wrapper
   a `gr.Group` puts around its children; it fills itself with the block *border* colour
   so that a 1px gap between grouped children reads as a divider, which over a card of
   our own is one more inner surface. The card is the only surface it needs. */
.insight-card .block, .insight-card .form, .insight-card .styler {{
  background: transparent; border: none; box-shadow: none;
}}
.insight-card .t-result-title {{ margin-bottom: 0.1rem; }}
/* The plot's subtitle, under its title inside the card: it names what the plot is
   bounded to (a cut, a board, a season), or states the read of it, which is part of
   reading the plot rather than fine print about it, so it is set in the accent rather
   than in the caption's mute. Both classes appear in that slot (a caption where the
   subtitle names a bound, a field-standing line where it states a reading), and a
   subtitle should not change colour with which of the two a plot happens to use. */
.insight-card .t-caption, .insight-card .t-fieldstat {{ color: var(--accent-bright); }}

/* The player leaderboard's standings, the one table the app draws (#135). It sits inside
   an insight card, so it carries no surface or border of its own: the rules between rows
   are the card's own hairline token and the header is set in the control-label role, so
   the table reads as part of the card rather than as an embedded spreadsheet. Figures are
   tabular and right-aligned, since the whole point of the column is comparing thousandths
   down it; the rank stays muted because the name is what a reader is scanning for.
   The swatch is the tie back to the chart: a drawn contender's row is dotted in the hue
   their line takes, and a row past the eighth keeps the same indent with no dot, so the
   names stay in one column rather than stepping in and out of it. */
.leaderboard {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
.leaderboard th {{
  padding: 0.4rem 0.5rem; text-align: left; border-bottom: 1px solid var(--border);
  font-size: 11px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--text-mute);
}}
.leaderboard td {{
  padding: 0.4rem 0.5rem; border-bottom: 1px solid var(--border); color: var(--text);
}}
.leaderboard tbody tr:last-child td {{ border-bottom: none; }}
.leaderboard .rank, .leaderboard .score {{
  text-align: right; font-variant-numeric: tabular-nums;
}}
.leaderboard .rank {{ width: 3ch; color: var(--text-mute); }}
/* The rank interval is the column that qualifies the rank, so it is set back like the
   rank itself rather than competing with the score: a reader scanning the board reads
   name and score, and reads this when they stop to ask how firm a place is. */
.leaderboard .spread {{ color: var(--text-mute); white-space: nowrap; }}
.leaderboard .swatch {{
  display: inline-block; width: 9px; height: 9px; border-radius: 999px;
  margin-right: 0.6rem; vertical-align: middle;
}}
.leaderboard .swatch-hue {{ box-shadow: 0 0 0 3px var(--surface); }}

/* The gem table is grouped by archetype rather than ranked outright (#184), so every
   other archetype is banded whole: the blocks read apart at a glance, and a block of
   one row bands as plainly as a block of six. The band is the well surface, one step
   off the card and the same step an inset takes, since a group is a grouping and not a
   category: a hue per archetype would read as a key, and the graph beside the table
   gives archetypes no hue to key against. */
.leaderboard tbody tr.band td {{ background: var(--surface-2); }}

/* The results stack wraps a view's insight cards and is toggled as a whole: hidden
   before a Draw (so the view opens as controls over empty ground, the guidance living
   in the control's help text, not a row of duplicated "nothing yet" cards), shown once
   a Draw fills it. It is a bare container: no surface of its own, so only the cards
   inside it read as objects. */
.results-stack {{ background: transparent; border: none; box-shadow: none; padding: 0; }}

/* §13 control panel: the controls for a view read as one raised input surface, one
   step lighter than the cards below so it reads as more prominent, obviously the
   place to drive from. */
.control-panel {{
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: 12px; padding: 1rem 1.25rem; margin-bottom: 1.5rem;
}}

/* The subject is the control everything else in a tab hangs off, so it is marked
   visually primary with an accent edge, distinct from the surrounding filters. */
.primary-control {{ border-left: 3px solid var(--accent); padding-left: 0.75rem; }}

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
     and below a single line: 4px of input margin around ~17px of 14px text for a plain
     dropdown, or a token chip (`--spacing-md` around a 21px line) when a multiselect
     holds a pick. An empty multiselect is the plain height, so the size follows the
     chips rather than the control's type. Past one row of chips the field grows and the
     button stays on its last row, which is what bottom-anchoring is for. */
  --clear-size: 37px;
}}
.gradio-container .row.clearable:has(.token) {{ --clear-size: 45px; }}
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

/* Every tab reachable at phone width (#172), the same kind of override one more level
   down. Gradio renders the tab strip twice: a `visually-hidden` mirror it measures, and
   the real `role="tablist"` it paints. Its overflow pass walks the MIRROR's buttons and
   slices everything past the container's width into a "..." menu, and the seven tabs
   need 607px against 326px of container at 390px, so four of them go behind that menu.
   Worse, when a hidden tab is the selected one, no button on the real strip carries
   `.selected` or `aria-selected`: four of seven tabs show no "you are here" on the
   device most of the reading happens on. Letting the mirror wrap leaves the pass with
   nothing overflowing, so the menu hides itself and all seven render on the real strip.

   The real strip then scrolls sideways rather than wrapping, measured both ways: wrapping
   the visible strip costs 39px off the top of every phone page for a ragged three-row
   block of chrome, and needs a media query to keep it off desktop. Scrolling costs
   nothing at any width (the first `.t-lede` stays at 210.1 on phone and 167.6 on desktop,
   and at 1440 scrollWidth equals clientWidth at 1216, so no scroll region exists there at
   all). The hairline moves off Gradio's absolutely-positioned `::after` and onto the
   strip's own border box, or it would scroll out from under the buttons with them;
   `--border-color-primary` is bound to `--border` in `dark_theme`, so it is the same pixel.

   Every selector here is (0,4,1) or higher out of necessity, the trap the clearable row
   documents above: this stylesheet is emitted BEFORE Gradio's bundle and its own tab
   rules are (0,3,0), so anything at or below that ties and loses on order. None of them
   names the `svelte-` hash, deliberately, so a Gradio upgrade that rehashes cannot
   silently drop them. */
.gradio-container .tabs > .tab-wrapper > .tab-container.visually-hidden {{ flex-wrap: wrap; }}
.gradio-container .tabs > .tab-wrapper > .tab-container[role="tablist"] {{
  overflow-x: auto; overflow-y: hidden; scrollbar-width: none;
  border-bottom: 1px solid var(--border);
}}
.gradio-container .tabs > .tab-wrapper > .tab-container[role="tablist"]::after {{ content: none; }}
.gradio-container .tabs > .tab-wrapper > .tab-container[role="tablist"]::-webkit-scrollbar {{ display: none; }}
.gradio-container .tabs > .tab-wrapper > .tab-container[role="tablist"] > button {{ flex: 0 0 auto; }}

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
"""


def dark_theme():
    """A Gradio theme that binds the chrome to the tokens, in both light and dark
    mode, so the app renders the dark palette regardless of the browser preference.

    Every colour var points at a ``--token`` (defined by :func:`build_css`), so the
    tokens stay the single source; setting the light variants to the same dark tokens
    means the pre-redirect paint is already dark, and :data:`FORCE_DARK_JS` commits
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
        # ink on a surface (#163 made the same call for the range slider).
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
        # control — worse than the Gradio blue being retired (3.68), which is the one
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

# The graph mark: three nodes joined in a triad, which is the least a node-link diagram
# can be and still read as one, so the favicon says what the app is. It replaces the
# 7-pip mark, which §15 had already dropped from the page for reading as an ambiguous
# "dot dot dot"; the same ambiguity was worse at icon size, where seven dots blur into a
# smudge while three nodes and three edges still resolve at 16px. Drawn from the tokens,
# so an accent change carries into the icon.
_NODES = [(16, 7.5), (23.5, 21), (8.5, 21)]


def _favicon_data_uri() -> str:
    """The graph mark as a base64 ``image/svg+xml`` data URI.

    Self-hosted like the fonts: the whole icon travels in the head, so the Space
    serves no external favicon request and needs no static-file route (which the
    SSR proxy would complicate, see ``serve.py``).
    """
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
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
        f"<rect width='32' height='32' rx='7' fill='{TOKENS['bg']}'/>"
        f"{edges}{nodes}</svg>"
    )
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


def build_head() -> str:
    """The HTML injected into ``<head>`` at the ``gr.Blocks`` level (issue #115).

    Replaces the default Gradio furniture on a shared link with the app's own: a real
    favicon (the graph mark) and the Open Graph / Twitter card a preview unfurls from.
    No preview image yet (deferred): a ``summary`` card carries the title and
    description, which is the honest card to declare until a real image is authored.
    """
    title, desc = html.escape(_APP_TITLE), html.escape(_APP_DESCRIPTION)
    return "\n".join([
        f'<link rel="icon" href="{_favicon_data_uri()}">',
        '<meta property="og:type" content="website">',
        f'<meta property="og:title" content="{title}">',
        f'<meta property="og:description" content="{desc}">',
        f'<meta property="og:url" content="{_APP_URL}">',
        '<meta name="twitter:card" content="summary">',
        f'<meta name="twitter:title" content="{title}">',
        f'<meta name="twitter:description" content="{desc}">',
    ])


# Retires the browser light/dark inheritance: force the ``__theme=dark`` query param
# once on load, so Gradio applies its dark component rules and never the light ones.
#
# The param is how Gradio is told, but it is not something a visitor should have to
# read or copy: a shared link carrying ``?__theme=dark`` advertises the workaround
# instead of the app. So once the reload has landed and Gradio has taken the theme from
# the URL, the param is dropped from the address bar with ``replaceState``, which
# rewrites history without navigating. The drop is deferred a frame so it cannot race
# Gradio's own read of the query string. A later reload starts clean, re-adds the param,
# and strips it again, which is the same single redirect this already cost.
FORCE_DARK_JS = """
() => {
  const url = new URL(window.location);
  if (url.searchParams.get('__theme') !== 'dark') {
    url.searchParams.set('__theme', 'dark');
    window.location.replace(url.href);
  } else {
    requestAnimationFrame(() => {
      url.searchParams.delete('__theme');
      window.history.replaceState({}, '', url.pathname + url.search + url.hash);
    });
  }
}
"""

# A plot drawn at build time inside a tab that is not the one the app opens on is laid
# out while its container is display:none, so Plotly measures nothing and falls back to
# its own default width: the chart comes up narrow against a wide card, and only
# straightens out when something re-renders it. Plotly's responsive mode listens for
# the window resize event, so a tab that shows such a plot fires one when it is opened.
# The frame lets the newly shown tab lay out first, or the measurement is still zero.
RESIZE_PLOTS_JS = """
() => { requestAnimationFrame(() => window.dispatchEvent(new Event('resize'))); }
"""
