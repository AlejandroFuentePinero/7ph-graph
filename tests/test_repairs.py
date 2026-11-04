"""The source-repairs file: rulings about upstream's data, applied when a snapshot is
loaded, before the gate or any guard reads it (issue #268)."""

import pytest

from graph7ph.models import Card, Containment, Deck, Snapshot
from graph7ph.repairs import RepairError, apply_repairs, load_repairs


def deck(deck_id, *, pilot="pilot", event="E", placement=1, **over) -> Deck:
    fields = dict(
        deck_id=deck_id, name=f"{placement}st Pat P - Grixis - {event}", deck_name="Grixis",
        pilot=pilot, event=event, event_id="evt_E", event_type="Tournament",
        placement=placement, placement_norm=0.0, event_size=5,
        created_at="2025-06-01T00:00:00+00:00",
        colour="colour:U", macro="macro:tempo", engine_tags=[],
        engine_tag_labels={}, primary_tag="", primary_tag_weights={},
    )
    fields.update(over)
    return Deck(**fields)


def card(canon) -> Card:
    return Card(canon=canon, name=canon.title(), type="Lands", mana_value=0.0,
                reserved=False, points=0, points_companion=0)


def snap(*decks) -> Snapshot:
    return Snapshot(
        cards=[card("island")], decks=list(decks),
        containments=[Containment(deck_id=d.deck_id, canon="island", board="Main")
                      for d in decks],
    )


def repairs(tmp_path, text):
    path = tmp_path / "repairs.toml"
    path.write_text(text)
    return load_repairs(path)


HOLD_OUT = '''
[[hold_out]]
decks = ["d2"]
reason = "the event seats 25 decks for 24 players; this is the extra one"
as_of = "20261104T091012Z"
'''


def test_a_held_out_deck_leaves_every_snapshot_with_its_cards(tmp_path):
    snapshot, matched = apply_repairs(snap(deck("d1"), deck("d2")), repairs(tmp_path, HOLD_OUT))

    assert [d.deck_id for d in snapshot.decks] == ["d1"]
    assert [c.deck_id for c in snapshot.containments] == ["d1"]
    assert matched == {"d2"}


REFILE = '''
[[refile]]
decks = ["d2", "d3"]
event = "E26"
event_type = "Tournament"
field = 49
reason = "upstream folded the 2026 event into the 2025 one"
as_of = "20261104T091012Z"
'''


def test_a_refiled_deck_moves_to_the_new_event_carrying_the_ruled_type_and_field(tmp_path):
    source = snap(deck("d1"), deck("d2", placement=3, placement_norm=0.5),
                  deck("d3", placement=4))

    snapshot, matched = apply_repairs(source, repairs(tmp_path, REFILE))

    moved = next(d for d in snapshot.decks if d.deck_id == "d2")
    assert (moved.event, moved.event_id, moved.event_type, moved.event_size) == (
        "E26", None, "Tournament", 49)
    # The title's event segment follows, so the deck does not read as played at E.
    assert moved.name == "3st Pat P - Grixis - E26"
    # The source scored it against the field of the event it was not played at, so
    # the score is dropped for the build to re-mint, and both the norm and the
    # event's field say a refile decided them (ADR 0016).
    assert (moved.placement, moved.placement_norm, moved.norm_imputed) == (
        3, None, "refiled")
    assert moved.field_imputed == "refiled"
    assert next(d for d in snapshot.decks if d.deck_id == "d1") == source.decks[0]
    assert matched == {"d2", "d3"}


ADOPT = '''
[[adopt]]
decks = ["d1"]
fact = "pilot"
value = "FrostyGoldDolphin"
reason = "upstream re-keyed the pilot in the newest fetch; carried into the older ones"
as_of = "20261104T091012Z"
'''


def test_an_adopted_fact_replaces_the_source_value_on_every_snapshot_holding_the_deck(
    tmp_path
):
    snapshot, matched = apply_repairs(
        snap(deck("d1", pilot="BraveIndigoLynx"), deck("d2")), repairs(tmp_path, ADOPT))

    assert [d.pilot for d in snapshot.decks] == ["FrostyGoldDolphin", "pilot"]
    assert matched == {"d1"}


def test_a_refile_onto_an_event_the_source_has_is_refused(tmp_path):
    # The kind exists for an event the source does not have. A deck that belongs at
    # one it does have is a [[deck_event]] in the pilot dictionary, which adopts
    # that cohort's id, type and field; applying a refile there would seat decks
    # carrying the ruling's numbers beside decks carrying the source's.
    text = REFILE.replace('event = "E26"', 'event = "F"')

    with pytest.raises(RepairError, match=r"\[\[deck_event\]\]"):
        apply_repairs(snap(deck("d1", event="F"), deck("d2"), deck("d3")),
                      repairs(tmp_path, text))


@pytest.mark.parametrize("text", [
    "[[hold_out]\ndecks = [",  # not TOML
    HOLD_OUT.replace('decks = ["d2"]', 'decks = []'),
    HOLD_OUT.replace('decks = ["d2"]', 'decks = ["d2", "d2"]'),
    HOLD_OUT.replace('decks = ["d2"]', 'deck = "d2"'),
    HOLD_OUT.replace("reason =", "why ="),
    HOLD_OUT.replace('as_of = "20261104T091012Z"', 'as_of = 20261104'),
    HOLD_OUT.replace('as_of = "20261104T091012Z"', 'as_of = "2026-11-04"'),
    REFILE.replace('event_type = "Tournament"\n', ""),
    REFILE.replace("field = 49", "field = true"),
    REFILE.replace("field = 49", 'field = "49"'),
    REFILE.replace("field = 49", "field = 0"),
    ADOPT.replace('fact = "pilot"', 'fact = "event"'),
    ADOPT.replace('fact = "pilot"', 'fact = "placement"'),  # a str for an int
    # Self-contradictions: a held-out deck named by another entry, a deck refiled
    # twice, a fact adopted twice for one deck, two refiles to one event that
    # disagree about it.
    HOLD_OUT + REFILE,
    HOLD_OUT + ADOPT.replace('decks = ["d1"]', 'decks = ["d2"]'),
    REFILE + REFILE.replace('event = "E26"', 'event = "E27"'),
    ADOPT + ADOPT.replace("FrostyGoldDolphin", "SomeoneElse"),
    REFILE + REFILE.replace('decks = ["d2", "d3"]', 'decks = ["d4"]')
    .replace("field = 49", "field = 50"),
    REFILE + REFILE.replace('decks = ["d2", "d3"]', 'decks = ["d4"]')
    .replace("Tournament", "Teams"),
])
def test_a_malformed_or_self_contradicting_file_is_refused(tmp_path, text):
    with pytest.raises(RepairError):
        repairs(tmp_path, text)


def test_two_agreeing_refiles_and_two_facts_adopted_on_one_deck_load(tmp_path):
    loaded = repairs(
        tmp_path,
        REFILE + REFILE.replace('decks = ["d2", "d3"]', 'decks = ["d4"]')
        + ADOPT + ADOPT.replace('fact = "pilot"', 'fact = "placement"')
        .replace('value = "FrostyGoldDolphin"', "value = 3"),
    )

    assert [r.decks for r in loaded.refiles] == [("d2", "d3"), ("d4",)]
    assert [(a.fact, a.value) for a in loaded.adopts] == [
        ("pilot", "FrostyGoldDolphin"), ("placement", 3)]


def test_an_absent_file_is_an_empty_set_of_repairs(tmp_path):
    assert load_repairs(tmp_path / "none.toml").hold_outs == ()
