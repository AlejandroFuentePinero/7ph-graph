"""The golden-subgraph harness: capture, compare, and the case list (issue #45)."""

import json
from collections import defaultdict
from pathlib import Path

import kuzu
import pytest

from graph7ph.baseline import (
    CASES,
    TOLERANCE,
    Case,
    capture,
    check,
    compare,
    subgraph_blob,
)
from graph7ph.build import build_graph, graph_counts
from graph7ph.models import load_snapshot
from graph7ph.query import (
    CardCooccurrence,
    CardUsage,
    Edge,
    HiddenGems,
    Node,
    PilotAffinity,
    PilotNeighbourhood,
    Subgraph,
    card_catalogue,
    card_usage_subgraph,
    pilot_catalogue,
    pilot_subgraph,
)

# The two magnitudes the tolerance sits between, both measured on the real graph
# (issue #45): engine-to-engine float noise, and how close a gem gets to the band.
FLOAT_NOISE = 5.6e-17
GEM_THRESHOLD_MARGIN = 8.6e-4


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    """A connection to the tiny fixture snapshot, built once for the module."""
    db_path = tmp_path_factory.mktemp("baseline") / "graph.kuzu"
    build_graph(load_snapshot(FIXTURES), db_path)
    return kuzu.Connection(kuzu.Database(str(db_path)))


def _baseline(cases, subgraphs):
    """A minimal baseline dict holding just the given cases' subgraphs."""
    return {
        "counts": {},
        "catalogues": {},
        "queries": {
            case.name: {"spec": repr(case.spec), **subgraph_blob(subgraphs[case.name])}
            for case in cases
        },
    }


def test_row_order_is_a_difference_only_for_the_ordered_queries():
    # Same nodes, different row order. A different engine may legitimately return
    # the rows of an unordered query in a different order (issue #45), so that is
    # not a regression; for a query that pins order in Cypher or sorts in Python
    # before emitting, order is part of the contract and a swap is a difference.
    nodes = [Node("card:x", "X", "Card"), Node("card:y", "Y", "Card")]
    forwards = Subgraph(nodes=nodes, edges=[])
    backwards = Subgraph(nodes=list(reversed(nodes)), edges=[])

    unordered = [Case("gems", HiddenGems())]
    ordered = [Case("usage", CardUsage("x"))]

    assert compare(
        _baseline(unordered, {"gems": forwards}),
        _baseline(unordered, {"gems": backwards}),
        unordered,
    ) == []
    assert compare(
        _baseline(ordered, {"usage": forwards}),
        _baseline(ordered, {"usage": backwards}),
        ordered,
    ) != []


def test_a_changed_row_is_a_difference_even_for_an_unordered_query():
    # Order-insensitive must not degrade into "any two same-length results match".
    cases = [Case("gems", HiddenGems())]
    before = Subgraph(nodes=[Node("card:x", "X", "Card")], edges=[])
    after = Subgraph(nodes=[Node("card:z", "Z", "Card")], edges=[])

    assert compare(
        _baseline(cases, {"gems": before}), _baseline(cases, {"gems": after}), cases
    ) != []


def test_edge_order_follows_the_same_rule_as_node_order():
    edges = [Edge("a", "b", "one"), Edge("a", "c", "two")]
    forwards = Subgraph(nodes=[], edges=edges)
    backwards = Subgraph(nodes=[], edges=list(reversed(edges)))

    unordered = [Case("gems", HiddenGems())]
    ordered = [Case("usage", CardUsage("x"))]

    assert compare(
        _baseline(unordered, {"gems": forwards}),
        _baseline(unordered, {"gems": backwards}),
        unordered,
    ) == []
    assert compare(
        _baseline(ordered, {"usage": forwards}),
        _baseline(ordered, {"usage": backwards}),
        ordered,
    ) != []


def _gem(mean_norm):
    return Subgraph(
        nodes=[Node("card:x", "X", "Card", decks=6, mean_norm=mean_norm)], edges=[]
    )


def test_float_noise_between_engines_is_not_a_difference():
    # `avg(d.placementNorm)` differs between engines in the last bits, because
    # aggregation order changes and float addition is not associative. Measured
    # largest difference across every query: 5.6e-17.
    cases = [Case("gems", HiddenGems())]
    assert compare(
        _baseline(cases, {"gems": _gem(0.25)}),
        _baseline(cases, {"gems": _gem(0.25 + FLOAT_NOISE)}),
        cases,
    ) == []


def test_a_real_shift_in_mean_placement_is_a_difference():
    # The closest any gem sits to the 0.33 band is 8.6e-4, so a shift of that size
    # is the smallest one that could move a card in or out of the answer.
    cases = [Case("gems", HiddenGems())]
    assert compare(
        _baseline(cases, {"gems": _gem(0.25)}),
        _baseline(cases, {"gems": _gem(0.25 + GEM_THRESHOLD_MARGIN)}),
        cases,
    ) != []


def test_the_tolerance_sits_between_the_noise_and_the_band_margin():
    # The tolerance is only safe while it swallows engine noise without being able
    # to hide a card crossing the gem band. If a future engine's noise grows past
    # it, or gems crowd the threshold more tightly, this is the assertion that says so.
    assert FLOAT_NOISE < TOLERANCE < GEM_THRESHOLD_MARGIN


def test_the_pilot_catalogue_offers_only_pilots_the_query_can_answer_for(conn):
    # The dropdown must not offer a pilot whose neighbourhood comes back empty,
    # and reads in label order.
    catalogue = pilot_catalogue(conn)

    assert catalogue == sorted(catalogue)
    assert catalogue
    for _label, value in catalogue:
        assert pilot_subgraph(conn, value).nodes, value


def test_the_card_catalogue_offers_only_cards_the_query_can_answer_for(conn):
    catalogue = card_catalogue(conn)

    assert catalogue == sorted(catalogue)
    assert catalogue
    for _label, value in catalogue:
        assert card_usage_subgraph(conn, value).nodes, value


def test_the_counts_read_back_from_an_artifact_match_the_build_that_wrote_it(
    tmp_path, snapshot_dir
):
    # The harness grades an artifact, not a build, so it has to be able to read
    # all 18 counts out of a graph someone else built.
    db_path = tmp_path / "graph.kuzu"
    built = build_graph(load_snapshot(snapshot_dir), db_path)
    reopened = kuzu.Connection(kuzu.Database(str(db_path)))

    assert graph_counts(reopened) == built


def test_a_captured_subgraph_survives_a_round_trip_through_json():
    # The baseline is written to disk and read back before it grades anything, so
    # a value that changes type on the way through (a `pin` tuple becomes a JSON
    # list) would fail every case for a reason that is not a regression.
    cases = [Case("cooc", CardUsage("x"))]
    subgraph = Subgraph(
        nodes=[
            Node("card:x", "X", "Card", weight=3, group="cooccur", shape="circle",
                 pin=(300.0, -80.0), decks=6, total_decks=50, mean_norm=0.25),
        ],
        edges=[Edge("card:x", "card:y", "40%", visible=True, decks=2,
                    total_decks=5, events=1)],
    )
    captured = _baseline(cases, {"cooc": subgraph})

    assert compare(captured, json.loads(json.dumps(captured)), cases) == []


def test_the_cases_exercise_every_query_and_the_branches_that_matter():
    # A baseline that skips a branch grades the migration on a query nobody ran.
    # Each assertion below is one line of the coverage issue #45 asks for.
    by_type = defaultdict(list)
    for case in CASES:
        by_type[type(case.spec)].append(case.spec)

    assert set(by_type) == {
        PilotNeighbourhood, PilotAffinity, CardUsage, CardCooccurrence, HiddenGems
    }
    # A single pilot, and a head-to-head between two who share events.
    assert {s.pilot2 is None for s in by_type[PilotNeighbourhood]} == {True, False}
    # A board filter of Main, Side, and neither.
    assert {s.board for s in by_type[CardUsage]} >= {None, "Main", "Side"}
    # A staple and a rare card, on both the usage and the co-occurrence view.
    assert len({s.canon for s in by_type[CardUsage]}) > 1
    assert len({s.canon for s in by_type[CardCooccurrence]}) > 1
    # One seed and two, and `drop_lands` both ways.
    assert {s.canon2 is None for s in by_type[CardCooccurrence]} == {True, False}
    assert {s.drop_lands for s in by_type[CardCooccurrence]} == {True, False}
    # The gem view unfiltered and narrowed to an archetype.
    assert {s.archetype is None for s in by_type[HiddenGems]} == {True, False}


def test_every_case_is_filed_under_its_own_name():
    names = [case.name for case in CASES]
    assert len(names) == len(set(names))


def test_a_changed_table_count_is_a_difference():
    # All 18 counts are part of the baseline: a query can return the right shape
    # over a graph that loaded the wrong number of rows.
    before = {**_baseline([], {}), "counts": {"decks": 4591, "cards": 4995}}
    after = {**_baseline([], {}), "counts": {"decks": 4590, "cards": 4995}}

    diffs = compare(before, after, [])
    assert any("decks" in d for d in diffs)


def test_a_changed_catalogue_is_a_difference():
    # The dropdowns are what the app offers, so a dropped or reordered entry is a
    # user-visible regression even though no subgraph changed.
    before = {**_baseline([], {}), "catalogues": {"pilots": [["A", "a"], ["B", "b"]]}}
    after = {**_baseline([], {}), "catalogues": {"pilots": [["B", "b"], ["A", "a"]]}}

    assert compare(before, after, []) != []


def _fixture_cases(conn):
    """Cases the tiny fixture graph can actually answer, one per ordering rule."""
    pilot = pilot_catalogue(conn)[0][1]
    card = card_catalogue(conn)[0][1]
    return [
        Case("pilot", PilotNeighbourhood(pilot)),  # order-insensitive
        Case("usage", CardUsage(card)),  # order-exact
        Case("cooc", CardCooccurrence(card)),
    ]


def test_the_gate_passes_a_graph_that_reproduces_its_baseline(conn, tmp_path):
    cases = _fixture_cases(conn)
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(capture(conn, cases), indent=2))

    assert check(conn, path, cases) == []


def test_the_gate_reports_a_graph_that_does_not_reproduce_its_baseline(conn, tmp_path):
    # A baseline that no longer describes the graph must name what moved, so the
    # later migration tickets can act on the diff rather than just a red light.
    cases = _fixture_cases(conn)
    path = tmp_path / "baseline.json"
    baseline = capture(conn, cases)
    baseline["counts"]["decks"] += 1
    baseline["queries"]["usage"]["nodes"][0]["label"] = "something else"
    path.write_text(json.dumps(baseline, indent=2))

    diffs = check(conn, path, cases)

    assert any("counts.decks" in d for d in diffs)
    assert any("usage.nodes" in d for d in diffs)


def test_the_baseline_records_all_eighteen_counts_and_both_catalogues(conn):
    captured = capture(conn, [])

    assert len(captured["counts"]) == 18
    # The fixture snapshot is 3 decks across 2 pilots and 121 cards (see conftest),
    # so the recorded counts are checked against the source, not against the same
    # call that produced them.
    assert captured["counts"]["decks"] == 3
    assert captured["counts"]["pilots"] == 2
    assert captured["counts"]["cards"] == 121
    assert set(captured["catalogues"]) == {"pilots", "cards", "gem_archetypes"}


def test_a_catalogue_difference_names_the_first_entry_that_moved():
    # 4995 cards means "the catalogues differ" is not an answer anyone can act on.
    before = {**_baseline([], {}), "catalogues": {"cards": [["A", "a"], ["B", "b"]]}}
    after = {**_baseline([], {}), "catalogues": {"cards": [["A", "a"], ["C", "c"]]}}

    diffs = compare(before, after, [])

    assert len(diffs) == 1
    assert "[1]" in diffs[0] and "B" in diffs[0] and "C" in diffs[0]


def test_a_table_the_baseline_never_saw_is_a_difference():
    # A migration that adds a table is a change to the graph, not a free pass.
    before = {**_baseline([], {}), "counts": {"decks": 3}}
    after = {**_baseline([], {}), "counts": {"decks": 3, "sideboards": 9}}

    diffs = compare(before, after, [])
    assert len(diffs) == 1
    assert "sideboards" in diffs[0]


def test_a_case_the_baseline_holds_but_nobody_ran_is_a_difference():
    # Otherwise a later ticket can silence a failing case by deleting it from
    # CASES: the stale entry goes unread and the gate still reports no regression.
    cases = [Case("kept", CardUsage("x"))]
    empty = Subgraph(nodes=[], edges=[])
    baseline = _baseline([Case("kept", CardUsage("x")), Case("dropped", HiddenGems())],
                         {"kept": empty, "dropped": empty})
    now = _baseline(cases, {"kept": empty})

    diffs = compare(baseline, now, cases)

    assert len(diffs) == 1
    assert "dropped" in diffs[0]


def test_a_catalogue_only_the_capture_has_is_a_difference():
    before = {**_baseline([], {}), "catalogues": {}}
    after = {**_baseline([], {}), "catalogues": {"decks": [["A", "a"]]}}

    diffs = compare(before, after, [])
    assert any("decks" in d for d in diffs)


def test_a_row_that_appeared_or_vanished_is_named_not_just_counted():
    # "342 in the baseline, 343 now" leaves whoever is grading the migration to
    # diff an 853KB file by hand. Say which row moved.
    cases = [Case("gems", HiddenGems())]
    before = Subgraph(nodes=[Node("card:x", "X", "Card")], edges=[])
    after = Subgraph(
        nodes=[Node("card:x", "X", "Card"), Node("card:new", "New", "Card")], edges=[]
    )

    diffs = compare(_baseline(cases, {"gems": before}),
                    _baseline(cases, {"gems": after}), cases)

    assert any("card:new" in d for d in diffs)
    assert any("only now" in d or "only in the capture" in d for d in diffs)


def test_float_noise_cannot_reorder_an_unordered_comparison():
    # The order-insensitive path sorts both sides before comparing. If the sort key
    # quantised floats, a value sitting on a rounding boundary would round one way
    # in the baseline and the other in the capture, so every row after it would be
    # compared against the wrong partner: a phantom failure produced by the very
    # mechanism meant to prevent them.
    cases = [Case("gems", HiddenGems())]
    boundary = 0.1234565  # rounds up or down at 6dp depending on its last bits

    def gems(offset):
        return Subgraph(
            nodes=[
                Node("card:a", "A", "Card", mean_norm=boundary + offset),
                Node("card:b", "B", "Card", mean_norm=0.9),
            ],
            edges=[],
        )

    assert compare(_baseline(cases, {"gems": gems(0)}),
                   _baseline(cases, {"gems": gems(FLOAT_NOISE)}), cases) == []


def test_a_dropped_row_does_not_drag_every_other_row_into_the_report():
    # Matching rows for the add/remove report has to respect the same float
    # tolerance the rest of the comparison does. Keying on the raw float instead
    # makes every row with engine-scale noise look both added and removed, burying
    # the one row that actually moved.
    cases = [Case("gems", HiddenGems())]

    def gem(canon, mean_norm):
        return Node(f"card:{canon}", canon.title(), "Card", decks=6, mean_norm=mean_norm)

    before = Subgraph(nodes=[gem("a", 0.25), gem("b", 0.31), gem("c", 0.2)], edges=[])
    after = Subgraph(
        nodes=[gem("a", 0.25 + FLOAT_NOISE), gem("b", 0.31 + FLOAT_NOISE)], edges=[]
    )

    diffs = compare(_baseline(cases, {"gems": before}),
                    _baseline(cases, {"gems": after}), cases)

    assert sum("card:a" in d or "card:b" in d for d in diffs) == 0
    assert any("card:c" in d for d in diffs)
