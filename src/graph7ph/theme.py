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
from functools import lru_cache
from pathlib import Path

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


def build_css() -> str:
    """The stylesheet the app injects at the ``gr.Blocks`` level.

    Prepends the two ``@font-face`` faces (§3), defines the tokens once in ``:root``,
    and reads them by role for the eight-role type system (§3), the insight-card and
    control-panel surfaces (§12/§13), and the 7-pip signature (§15). The heading roles
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
/* Field-standing line: the one-sentence read of a performance chart, promoted above a
   plain caption so it lands as the answer rather than fine print. The share itself
   carries the accent so the eye catches the number first; the sample size trails quiet. */
.t-fieldstat {{ font-size: 14.5px; font-weight: 400; line-height: 1.5; color: var(--text-dim); }}
.t-fieldstat .pct {{ color: var(--accent-bright); font-weight: 650; font-variant-numeric: tabular-nums; }}
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

/* §12 insight card: each plot is a bounded answer on the ground, not a slab in a
   continuous sheet. The card carries the surface, edge, radius and padding; adjacent
   cards are held apart by the bottom-margin gap, not a shared rule. The inner Gradio
   blocks are flattened so the card is the only surface (no card-in-card). */
.insight-card {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 1rem 1.25rem 1.25rem; margin-bottom: 1.25rem;
}}
.insight-card > .block, .insight-card .form,
.insight-card > .block > .block {{
  background: transparent; border: none; box-shadow: none;
}}
.insight-card .t-result-title {{ margin-bottom: 0.1rem; }}

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
    )


# Retires the browser light/dark inheritance: force the ``__theme=dark`` query param
# once on load, so Gradio applies its dark component rules and never the light ones.
FORCE_DARK_JS = """
() => {
  const url = new URL(window.location);
  if (url.searchParams.get('__theme') !== 'dark') {
    url.searchParams.set('__theme', 'dark');
    window.location.replace(url.href);
  }
}
"""
