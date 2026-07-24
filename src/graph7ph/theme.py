"""The v1 dark theme: the design tokens, the type scale, and the CSS that commits
every surface to them.

The single source of truth is ``docs/design/v1-visual-direction.md`` (§2 tokens, §3
type scale). This module is that document as data: the tokens defined once, the eight
type roles, and a ``build_css`` that serialises them into the ``:root`` custom
properties and role styles the app injects at the ``gr.Blocks`` level. ``dark_theme``
binds Gradio's own chrome to the same tokens and ``FORCE_DARK_JS`` retires the browser
light/dark inheritance, so the app reads as one coherent dark build. ``contrast_ratio``
lets the tests hold every text role to WCAG AA on the ground, so a token edited to an
illegible value fails a test rather than a reader.
"""

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

# System sans throughout (§3): zero webfont load, deploys clean on the Space. Named
# once so the theme's font and the injected CSS name the same stack.
FONT_STACK = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'

# §3. Reading measure: no paragraph runs the full width of a wide monitor.
MEASURE_CH = 62


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

    Defines the tokens once in ``:root`` and reads them by role for the eight-role
    type scale (§3). The heading roles land on the page's own markdown; the control
    label lands on Gradio's field title (its ``block-info`` span); the rest are
    utility classes (``.t-lede`` and kin) a later slice applies where the result
    framing needs them. The reading measure bounds prose to ``MEASURE_CH``.
    """
    root = "\n".join(f"  --{name}: {hex_};" for name, hex_ in TOKENS.items())
    return f""":root {{
{root}
}}

.gradio-container {{ font-family: {FONT_STACK}; }}

/* §3 type scale. Heading roles apply to the page's own markdown; the utility
   classes carry the rest for deliberate use where results are framed. */
.prose h1, .t-page-title {{
  font-size: 30px; font-weight: 700; letter-spacing: -0.02em; line-height: 1.12; color: var(--text);
}}
.prose h2, .t-section-heading {{
  font-size: 20px; font-weight: 650; letter-spacing: -0.01em; line-height: 1.25; color: var(--text);
}}
.prose h3, .t-result-title {{
  font-size: 22px; font-weight: 680; letter-spacing: -0.01em; line-height: 1.2; color: var(--text);
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
/* Numeric readout: proportional figures so a standalone large number is not loosened
   by tabular spacing (§3). `.tabular` opts columns of digits back into alignment. */
.t-numeric {{ font-size: 15px; font-weight: 550; color: var(--text); font-variant-numeric: proportional-nums; }}
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
