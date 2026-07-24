"""The one categorical colour vocabulary (v1 visual direction §5), as data.

The one eight-hue set the Plotly charts and the pyvis graph are to draw their
series and node kinds from, so a Deck is the same blue whether it is a dot in the
graph or a line on a chart. This module is the vocabulary; wiring the two call
sites (``app.py`` charts, ``render.py`` graph) onto it is the graph and chart child
issues (#111, #112, #117), not this one. This is presentation hue, distinct from the
domain ``Colour`` (the Magic W/U/B/R/G of a card): entities here are chart series or
graph node kinds.

The single source of truth is ``docs/design/v1-visual-direction.md`` §5. The hues
are validated colour-blind-safe on the dark surface (``#1c1917``): worst adjacent
CVD ΔE 8.4, all eight clear 3:1 contrast. Re-validate after any hue change with the
``dataviz`` skill's CVD checker (the ΔE half this repo does not reimplement); the
``validate_palette.js`` it names ships with that skill, not this repo, so run it from
the skill's own directory::

    node validate_palette.js "#3987e5,#d95926,#199e70,#c98500,#d55181,#008300,#9085e9,#e66767" --mode dark --surface "#1c1917"

and the contrast half is guarded in-repo by ``test_palette``.
"""

from collections.abc import Iterable

# §5, slot order = index: slot 1 blue, slot 2 orange, ... slot 8 red.
CATEGORICAL: tuple[str, ...] = (
    "#3987e5",  # 1 blue
    "#d95926",  # 2 orange
    "#199e70",  # 3 aqua
    "#c98500",  # 4 yellow
    "#d55181",  # 5 magenta
    "#008300",  # 6 green
    "#9085e9",  # 7 violet
    "#e66767",  # 8 red
)


# §5–6: colour tops out at eight distinguishable series; past this the caller
# switches to emphasis (§6), never a ninth hue and never a cycle back to slot 1.
MAX_SLOTS = len(CATEGORICAL)


def assign(entities: Iterable[str]) -> dict[str, str | None]:
    """Map entities to hues in fixed slot order, keyed by entity (§5).

    The first :data:`MAX_SLOTS` distinct entities take :data:`CATEGORICAL` slots
    1..8 in the order given; a repeat shares its first slot. Past the eighth, an
    entity maps to ``None`` rather than a ninth hue or a cycle back to slot 1 (§5),
    the signal to the caller to switch to emphasis (§6).

    Keyed by entity so colour follows the entity, not its rank (the ADR-0013
    reversal, §9). Survivors keep their hue across a re-cut only when the caller
    assigns over the stable entity universe once and *filters* this map for the
    drawn series; re-calling ``assign`` on the filtered subset re-ranks it and
    repaints, the exact behaviour §5 forbids.
    """
    return {
        e: CATEGORICAL[i] if i < MAX_SLOTS else None
        for i, e in enumerate(dict.fromkeys(entities))
    }
