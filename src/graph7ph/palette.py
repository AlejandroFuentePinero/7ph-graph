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


# The first-played ramp (issue #346, the design doc's "The Community page"): one hue
# stepped from deep to pale, for a quantity with an order, the year a pilot first
# played, oldest first. The pilot orange (slot 2) is its second stop, so the ramp
# reads as the pilots' own colour. Not a ninth categorical hue: these are steps of
# one, and are only ever drawn touching each other in a bar.
FIRST_PLAYED: tuple[str, ...] = ("#9c3a12", CATEGORICAL[1], "#ee8f5f", "#f6c3a3")


def first_played(years: int) -> list[str]:
    """``years`` colours along :data:`FIRST_PLAYED`, oldest first, its two ends
    included. Four take the four stops as they are; any other number is read off
    the line through them."""
    stops = [[int(stop[i:i + 2], 16) for i in (1, 3, 5)] for stop in FIRST_PLAYED]
    colours = []
    for step in range(years):
        at = step * (len(stops) - 1) / max(years - 1, 1)
        low = min(int(at), len(stops) - 2)
        mix = [round(a + (b - a) * (at - low)) for a, b in zip(stops[low], stops[low + 1])]
        colours.append("#{:02x}{:02x}{:02x}".format(*mix))
    return colours


# §5: colour tops out at eight distinguishable series; past this there is no hue to
# give, never a ninth and never a cycle back to slot 1.
MAX_SLOTS = len(CATEGORICAL)


def assign(entities: Iterable[str]) -> dict[str, str | None]:
    """Map entities to hues in fixed slot order, keyed by entity (§5).

    The first :data:`MAX_SLOTS` distinct entities take :data:`CATEGORICAL` slots
    1..8 in the order given; a repeat shares its first slot. Past the eighth, an
    entity maps to ``None`` rather than a ninth hue or a cycle back to slot 1 (§5):
    the caller has more series than direct colour can tell apart.

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
