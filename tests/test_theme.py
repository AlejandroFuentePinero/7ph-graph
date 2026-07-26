import re

from graph7ph import theme


def test_every_shell_text_role_clears_wcag_aa_on_the_ground():
    # The dark theme commits to a known ground (#131110), so every text role the
    # shell sets must be legible on it: WCAG AA body text is a 4.5:1 contrast ratio.
    # A token edited to an illegible value fails here rather than in someone's eyes.
    ground = theme.TOKENS["bg"]
    for role in ("text", "text-dim", "text-mute"):
        assert theme.contrast_ratio(theme.TOKENS[role], ground) >= 4.5, role


def test_every_shell_text_role_clears_wcag_aa_on_the_card_surface():
    # Most of the app's text is not on the ground: an insight card, a chart, and the
    # graph all paint `--surface` underneath, so a role legible on the ground and not
    # on the card would pass the test above and fail the reader. The #118 acceptance
    # pass measures the rendered page and finds the muted role at its tightest here
    # (4.57:1), so this is the pair with no headroom to spare.
    surface = theme.TOKENS["surface"]
    for role in ("text", "text-dim", "text-mute"):
        assert theme.contrast_ratio(theme.TOKENS[role], surface) >= 4.5, role


def test_the_accents_clear_the_ui_contrast_floor_on_the_ground():
    # The accent carries action and active state, the bright accent carries links and
    # on-surface emphasis; both must clear the 3:1 floor WCAG sets for UI and large
    # text on the ground they sit on.
    ground = theme.TOKENS["bg"]
    for role in ("accent", "accent-bright"):
        assert theme.contrast_ratio(theme.TOKENS[role], ground) >= 3.0, role


def test_the_highlighted_figure_is_readable_on_both_grounds():
    # The field-standing line's figure is body-sized text carrying the reading, so it is
    # held to AA body contrast (4.5:1), not the 3:1 UI floor: it is set in a hue chosen
    # for chart marks, and a slot re-ordered in the palette must not quietly dim it.
    for ground in (theme.TOKENS["bg"], theme.TOKENS["surface"]):
        assert theme.contrast_ratio(theme.FIGURE_BLUE, ground) >= 4.5, ground


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


def test_every_prose_role_takes_the_reading_measure_and_the_faq_is_the_exception():
    # AC (#85, criterion 4): "no line exceeds a comfortable measure at any viewport
    # width". Every prose role takes the measure directly, so the criterion holds
    # everywhere the app sets prose.
    css = theme.build_css()

    prose = re.search(r"\.prose p, \.prose li, \.t-lede, \.t-body\s*\{(.*?)\}", css, re.DOTALL)
    assert prose and f"max-width: {theme.MEASURE_CH}ch" in prose.group(1)

    # The FAQ is the one deliberate exception, held here so it reads as a decision rather
    # than an oversight: the maintainer wants full-width boxes one question per row, and
    # a measure-bound paragraph inside a wide box was rejected twice (2026-07-26). The
    # answers therefore run the width of their box, which on a wide monitor is longer
    # than the measure. Criterion 4 is answered against that layout, not the reverse.
    assert re.search(r"\.faq-card p, \.faq-card li\s*\{[^}]*max-width: none", css)
    # And no bound has crept back onto the card or a grid onto its container, either of
    # which would quietly undo the layout that was asked for.
    assert not re.search(r"\.faq-card\s*\{", css)
    assert ".faq-grid" not in css


def test_the_default_gradio_footer_is_retired():
    # AC (#132 / #115): the "Built with Gradio / Use via API / Settings" footer is one
    # of the default-furniture tells; the stylesheet hides it so the app does not read
    # as a scaffold.
    css = theme.build_css()
    footer = re.search(r"footer\s*\{(.*?)\}", css, re.DOTALL)
    assert footer and "display: none" in footer.group(1)


def test_the_floating_component_label_over_a_plot_is_retired():
    # AC (#118): the same default furniture the footer rule retires, one level down.
    # Gradio floats a chip reading "Plot" over every `gr.Plot`, naming the component's
    # type over a card the app has already titled, and it was the one text in the
    # shipped theme that missed WCAG AA (`--text-mute` on `--surface-2` is 4.23:1). The
    # rule is scoped to the cards, so it cannot silently hide a label elsewhere.
    css = theme.build_css()
    rule = re.search(r"\.insight-card label\.float\s*\{(.*?)\}", css, re.DOTALL)
    assert rule and "display: none" in rule.group(1)


def test_the_head_carries_a_real_favicon_and_social_preview():
    # AC (#115): a real favicon and social preview replace the Gradio defaults. The
    # head fragment carries our own icon as a self-hosted data URI (no external
    # request, matching the embedded fonts), and the Open Graph / Twitter card meta a
    # shared link unfurls from, so the link no longer wears Gradio's furniture.
    head = theme.build_head()

    icon = re.search(r"<link[^>]*rel=['\"]icon['\"][^>]*>", head)
    assert icon and "data:image/svg+xml" in icon.group(0)  # our own mark, self-hosted

    assert re.search(r"property=['\"]og:title['\"]", head)
    assert re.search(r"property=['\"]og:description['\"]", head)
    assert re.search(r"name=['\"]twitter:card['\"]", head)
    # The preview names the tool, not "Gradio": the title carries the app's own name.
    assert "7 Point Highlander" in head


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
