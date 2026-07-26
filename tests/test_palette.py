"""The one categorical vocabulary (v1 visual direction §5), tested on its mapping."""

import itertools

from graph7ph import palette, theme


def test_distinct_entities_take_the_palette_in_fixed_slot_order():
    # §5: assigned in fixed slot order, the first entity slot 1 (blue), the
    # second slot 2 (orange), keyed by the entity so a chart or the graph looks a
    # series up by name, not by its position on screen.
    slots = palette.assign(["aggro", "control", "midrange"])
    assert slots == {
        "aggro": palette.CATEGORICAL[0],
        "control": palette.CATEGORICAL[1],
        "midrange": palette.CATEGORICAL[2],
    }


def test_a_repeated_entity_shares_one_slot():
    # An entity that appears twice is one entity, one hue: the repeat must not burn
    # a second slot and shove the next entity onto slot 3.
    slots = palette.assign(["aggro", "aggro", "control"])
    assert slots == {
        "aggro": palette.CATEGORICAL[0],
        "control": palette.CATEGORICAL[1],
    }


def test_past_eight_entities_the_ninth_gets_no_hue_never_a_cycle():
    # §5: colour tops out at eight distinguishable series. Past eight the caller
    # switches to emphasis (§6), so assign never generates a ninth hue and never
    # cycles back to slot 1, so an overflow entity maps to None, not to blue.
    entities = [f"e{n}" for n in range(10)]
    slots = palette.assign(entities)
    assert [slots[e] for e in entities[:8]] == list(palette.CATEGORICAL)
    assert slots["e8"] is None
    assert slots["e9"] is None
    assert palette.MAX_SLOTS == 8


def test_a_recut_never_repaints_the_surviving_entities():
    # §5, the ADR-0013 reversal: colour follows the entity, never its rank. Because
    # the mapping is keyed by entity, a filter that changes the series count is a
    # filter over this map, not a re-rank of the drawn set, so dropping a middle
    # entity leaves every survivor on its own hue instead of sliding it up a slot.
    universe = ["aggro", "burn", "control", "midrange"]
    scale = palette.assign(universe)
    recut = ["aggro", "control", "midrange"]  # a filter drops "burn"
    drawn = {e: scale[e] for e in recut}
    assert drawn == {
        "aggro": palette.CATEGORICAL[0],
        "control": palette.CATEGORICAL[2],  # still slot 3, not repainted to slot 2
        "midrange": palette.CATEGORICAL[3],
    }


def test_every_hue_clears_the_contrast_floor_on_the_surface():
    # The in-repo half of the §5 re-check (the ΔE half stays the dataviz validator's
    # job): every hue must clear the 3:1 floor on the dark surface it is drawn on, so
    # a hue edited to an illegible value fails here rather than on the chart.
    surface = theme.TOKENS["surface"]
    for hue in palette.CATEGORICAL + tuple(palette.EXTENDED):
        assert theme.contrast_ratio(hue, surface) >= 3.0, hue


def test_the_extended_scale_opens_on_the_signed_eight_then_keeps_going():
    # A faded-context chart (the meta cut) needs one hue per line past the eighth, so
    # the reader can trace a line across the years before raising it. The scale opens
    # on the signed eight in their own slot order, so the same archetype is the same
    # colour whether the cut draws five lines or thirty-one, and only then derives
    # more. Distinctness is what the extension owes: no two entities on one chart may
    # share a hue, up to the scale's length.
    entities = [f"e{n}" for n in range(len(palette.EXTENDED))]
    scale = palette.extended(entities)

    assert [scale[e] for e in entities[:8]] == list(palette.CATEGORICAL)
    assert len({scale[e] for e in entities}) == len(entities)


def _faded(hex_colour, alpha, background):
    """A hex colour composited onto a background at ``alpha``, as an (r, g, b) triple."""
    fg = [int(hex_colour[i:i + 2], 16) for i in (1, 3, 5)]
    bg = [int(background[i:i + 2], 16) for i in (1, 3, 5)]
    return [alpha * f + (1 - alpha) * b for f, b in zip(fg, bg)]


def test_the_extension_never_crowds_the_scale_worse_than_the_signed_eight_do():
    # The extension exists so a reader can trace one line across the years, and the
    # lines it colours are drawn faded, where every hue is pulled most of the way to
    # the background: at the emphasis opacity a colour keeps a fifth of its distance
    # from its neighbours. Hex inequality is therefore no guard at all, and an earlier
    # cut of this scale passed it while putting two lines 4.6 units apart on screen.
    #
    # The floor is the signed eight's own closest pair, faded the same way. It is the
    # separation the design system already accepts, it needs no invented constant, and
    # it says the honest thing: extending the scale may not crowd the chart worse than
    # the eight-hue set the project signed off already does.
    from graph7ph.app import _CONTEXT_ALPHA

    surface = theme.TOKENS["surface"]

    def closest_pair(hues):
        return min(
            sum((x - y) ** 2 for x, y in zip(_faded(a, _CONTEXT_ALPHA, surface),
                                             _faded(b, _CONTEXT_ALPHA, surface))) ** 0.5
            for a, b in itertools.combinations(hues, 2)
        )

    floor = closest_pair(palette.CATEGORICAL)
    assert closest_pair(palette.EXTENDED) >= floor


def test_the_extended_scale_follows_the_entity_not_its_rank():
    # §5's non-negotiable, which the extension inherits: a filter that changes the
    # series count must not repaint the survivors. Assigned over the whole drawn set
    # once and read as a map, dropping a middle entity leaves every survivor's hue
    # where it was.
    universe = [f"e{n}" for n in range(12)]
    scale = palette.extended(universe)
    recut = palette.extended([e for e in universe if e != "e3"])

    assert [recut[e] for e in universe[:3]] == [scale[e] for e in universe[:3]]
    assert recut["e11"] != scale["e11"]  # past the drop the slots do slide up
