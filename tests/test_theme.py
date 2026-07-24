import re

from graph7ph import theme


def test_every_shell_text_role_clears_wcag_aa_on_the_ground():
    # The dark theme commits to a known ground (#131110), so every text role the
    # shell sets must be legible on it: WCAG AA body text is a 4.5:1 contrast ratio.
    # A token edited to an illegible value fails here rather than in someone's eyes.
    ground = theme.TOKENS["bg"]
    for role in ("text", "text-dim", "text-mute"):
        assert theme.contrast_ratio(theme.TOKENS[role], ground) >= 4.5, role


def test_the_accents_clear_the_ui_contrast_floor_on_the_ground():
    # The accent carries action and active state, the bright accent carries links and
    # on-surface emphasis; both must clear the 3:1 floor WCAG sets for UI and large
    # text on the ground they sit on.
    ground = theme.TOKENS["bg"]
    for role in ("accent", "accent-bright"):
        assert theme.contrast_ratio(theme.TOKENS[role], ground) >= 3.0, role


def test_the_two_faces_are_embedded_as_woff2_data_uris():
    # AC (#132, §3): the type system is two self-hosted faces, base64-embedded so the
    # Space serves no external font request. The stylesheet carries an @font-face for
    # each family with a woff2 data URI, and the fallback stacks name each family first.
    css = theme.build_css()

    for spec in theme.FONTS.values():
        assert f"font-family: '{spec['family']}'" in css
    assert css.count("src: url(data:font/woff2;base64,") == len(theme.FONTS)
    # No external font request: the faces travel in the CSS, never a gstatic link.
    assert "fonts.gstatic.com" not in css
    assert "fonts.googleapis.com" not in css
    # The display roles name the serif, the container the sans, each face first.
    assert theme.FONTS["display"]["family"] in theme.DISPLAY_STACK
    assert theme.FONTS["body"]["family"] in theme.FONT_STACK


def test_insight_card_and_control_panel_read_the_surface_tokens():
    # AC (#132, §12/§13): each plot is bounded in an insight card on the surface token,
    # and the controls sit in a raised control panel on the surface-2 well, one step
    # lighter. Both are defined once in the stylesheet, reading the tokens by role so a
    # hardcoded colour cannot creep in.
    css = theme.build_css()

    assert ".insight-card" in css
    assert ".control-panel" in css
    # The card is the surface; the panel is the raised well one step lighter.
    card = re.search(r"\.insight-card\s*\{(.*?)\}", css, re.DOTALL).group(1)
    panel = re.search(r"\.control-panel\s*\{(.*?)\}", css, re.DOTALL).group(1)
    assert "var(--surface)" in card
    assert "var(--surface-2)" in panel


def test_the_default_gradio_footer_is_retired():
    # AC (#132 / #115): the "Built with Gradio / Use via API / Settings" footer is one
    # of the default-furniture tells; the stylesheet hides it so the app does not read
    # as a scaffold.
    css = theme.build_css()
    footer = re.search(r"footer\s*\{(.*?)\}", css, re.DOTALL)
    assert footer and "display: none" in footer.group(1)


def test_every_token_is_declared_exactly_once_in_root():
    # "Defined once and referenced by role" (AC): the stylesheet's :root block declares
    # each token a single time, so no surface can quietly redefine --accent to its own
    # value and split the source of truth. Counts declarations, not formatting, so it
    # guards the property without pinning the exact CSS text.
    css = theme.build_css()
    root = re.search(r":root\s*\{(.*?)\}", css, re.DOTALL)
    assert root, "the stylesheet defines a :root token block"
    for token in theme.TOKENS:
        declared = re.findall(rf"(?m)^\s*--{re.escape(token)}\s*:", root.group(1))
        assert len(declared) == 1, f"--{token} declared {len(declared)} times, want 1"
