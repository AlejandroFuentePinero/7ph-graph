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
