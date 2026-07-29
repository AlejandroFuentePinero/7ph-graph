"""The one categorical colour vocabulary (v1 visual direction §5), as data.

The one eight-hue set the Plotly charts and the pyvis graph are to draw their
series and node kinds from, so a Deck is the same blue whether it is a dot in the
graph or a line on a chart. This module is the vocabulary; wiring the two call
sites (``app.py`` charts, ``render.py`` graph) onto it is the graph and chart child
issues (#111, #112, #117), not this one. This is presentation hue, distinct from the
domain ``Colour`` (the Magic W/U/B/R/G of a card): entities here are chart series or
graph node kinds.

Eight is where *direct* colour stops. :data:`EXTENDED` carries the scale further for the
one chart that draws its lines faded and raises one on a click (§6 emphasis), where hue
traces a line rather than naming it; nothing else may draw from it. A raised line does
draw its extended hue at full strength, which is what makes the raise read, but it is
never asked to carry identity on its own: the legend and the hover name it, and a reader
raises a line or two, not fifteen.

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


# §5 extended (issue #117): twenty-four hues that carry the meta chart past the eighth
# line. Chosen by farthest-point selection, each one the candidate furthest from every
# hue already in the scale *as drawn* — that is, composited onto the surface at the
# emphasis opacity, not compared as hex. The distinction is the whole point. A faded
# colour keeps only a fifth of its distance from its neighbours, so two hues that look
# unrelated at full strength can be one colour on the canvas; an earlier cut of this
# scale, derived by rotating the signed eight, put two lines 4.6 units apart on screen
# while every hex differed. Candidates were drawn from a grid held to the signed set's
# own band (saturation .45-.70, lightness .45-.68, contrast >= 3:1 on the surface), so
# the extension reads as more of the same family rather than as the neon a pure
# separation search reaches for. Regenerate the same way if the signed eight change;
# ``test_palette`` holds the property the search was maximising.
_EXTENSION: tuple[str, ...] = (
    "#99e56c", "#6ce5ce", "#55c322", "#a525d0", "#dada2b", "#e56cd6",
    "#45de88", "#d8b479", "#a0a63f", "#6955be", "#25d0d0", "#c3224b",
    "#3fa0a6", "#2bda4b", "#dc9438", "#68c85b", "#cf81a3", "#da2bae",
    "#aa60c3", "#8dda2b", "#a66c3f", "#5fb1e3", "#81cf9a", "#346bb2",
)

# Colour still tops out at eight *distinguishable* hues and this scale does not pretend
# otherwise. Past the eighth, hue is a tracing cue: enough for the eye to follow one
# line across the years on a chart where every line is faded, never enough to name it.
# Naming stays with the legend and the hover, which is why the extension is only drawn
# where those are present and a raise is what the reader reaches for.
EXTENDED: tuple[str, ...] = CATEGORICAL + _EXTENSION


def extended(entities: Iterable[str]) -> dict[str, str]:
    """Map entities to the extended scale in fixed order, keyed by entity.

    :func:`assign`'s rules, over a longer scale: fixed order, keyed by entity, a
    repeat shares its first hue. The difference is the overflow. ``assign`` stops at
    the eighth because a ninth *direct* colour would claim a distinguishability the
    set does not have; this scale keeps going because its caller draws every line
    faded, where the job of hue is to keep a line traceable rather than to name it.

    Past the thirty-second entity the scale cycles, so two entities can share a hue.
    The cycle is reached: the meta chart assigns over its whole 126-archetype universe
    once and filters that map for the lines it draws, so an archetype holds its hue
    whatever else is on the chart (§5). Four archetypes land on each hue at that size,
    so a filtered map is not safe to draw on its own: pass it through
    :func:`disambiguate` for the lines actually drawn.
    """
    return {
        e: EXTENDED[i % len(EXTENDED)]
        for i, e in enumerate(dict.fromkeys(entities))
    }


def disambiguate(hues: dict[str, str], drawn: Iterable[str]) -> dict[str, str]:
    """Filter a universe's hue map to ``drawn``, moving any line that would collide.

    Assigning over a stable universe is what stops a survivor repainting when another
    line leaves (§5), but a universe longer than the scale cycles, and two lines in the
    same hue is the one thing hue must never do: the legend can name them and the chart
    still cannot tell them apart. So the universe hue is the default and the exception
    is narrow, a line whose hue is already taken by another line on this chart moves to
    the first free hue in scale order.

    Two passes, and the order matters. Every line that can keep its universe hue is
    given it first, and only then are the collided ones placed into what is left over.
    Resolving in one walk instead hands a collided line "the first free hue in scale
    order", which is free only because the line that owns it has not been reached yet,
    so adding a chip repaints an archetype that was already on the chart — the §5
    failure this function exists to close, reintroduced by the fix for it.

    Walked in the universe's own order, not the caller's, so which of a colliding pair
    keeps the hue is a property of the two archetypes rather than of the order a reader
    happened to pick them in. What survives every change to the drawn set is a line
    holding its own universe hue. A *displaced* line has no hue of its own to hold, so
    it moves as the leftovers change; that is the residue of a 32-hue scale over a
    126-archetype universe, and it is the smaller failure. Measured on the shipped
    artifact, the widest cut never reaches it at all (its 31 lines rank into the
    distinct first 32 slots, so nothing is displaced).
    """
    wanted = set(drawn)
    out = {e: h for e, h in hues.items() if e in wanted}
    taken: set[str] = set()
    displaced: list[str] = []
    for entity, hue in out.items():
        if hue in taken:
            displaced.append(entity)
        else:
            taken.add(hue)
    spare = (h for h in EXTENDED if h not in taken)
    for entity in displaced:
        out[entity] = next(spare, out[entity])
    return out
