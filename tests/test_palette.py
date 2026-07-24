"""The one categorical vocabulary (v1 visual direction §5), tested on its mapping."""

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
    for hue in palette.CATEGORICAL:
        assert theme.contrast_ratio(hue, surface) >= 3.0, hue
