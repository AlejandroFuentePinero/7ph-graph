"""The golden-subgraph harness: what the graph answers today, as a checked-in oracle.

The Ladybug migration's whole promise is no regression, so this module captures
what every query entry point returns on the current engine and grades a later
build against it (issue #45). A capture is plain JSON, so the baseline is
reviewable in a diff rather than an opaque pickle.

Comparison is not byte equality. ``Subgraph.nodes`` and ``.edges`` are lists
built in query row order, and two of the queries impose no order at all, so the
harness applies the rule each query actually promises:

- **Order-exact** where the query pins a total order in Cypher or sorts in
  Python before emitting. Order is genuinely part of the contract for the
  two-seed co-occurrence view, whose shared-card column pins y-positions by
  list index.
- **Order-insensitive** for ``pilot_subgraph`` and ``hidden_gems_subgraph``,
  which have no ``ORDER BY`` and build their lists directly in row order. A
  different engine may legitimately return the same rows in a different order.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import ladybug

from graph7ph.build import graph_counts
from graph7ph.query import (
    CardCooccurrence,
    CardUsage,
    HiddenGems,
    PilotAffinity,
    PilotNeighbourhood,
    QuerySpec,
    Subgraph,
    card_catalogue,
    gem_archetypes,
    pilot_catalogue,
    run_query,
)

# Where the checked-in oracle lives. Kept at the repo root beside `curation/`,
# not under `data/`, because it is reviewed source rather than a build output.
BASELINE_PATH = Path("baseline/subgraphs.json")

# The two queries with no ORDER BY, which build their lists directly in row
# order. Everything else sorts before emitting, so its order is a promise.
ORDER_INSENSITIVE = (PilotNeighbourhood, HiddenGems)

# How far a float riding out on a node or edge may move and still count as
# unchanged. Floats need a tolerance because `avg(d.placementNorm)` differs
# between engines in the last bits: aggregation order changes, and float addition
# is not associative. Measured on the real graph across every query, the largest
# such difference is 5.6e-17, and the closest any hidden gem sits to the 0.33
# band is 8.6e-4. This tolerance sits between the two, eight orders above the
# noise and five orders below the margin, so it swallows engine noise and still
# cannot hide a card crossing into or out of the gem answer. It stops being safe
# if a future engine's noise grows past it, or if gems start crowding the
# threshold; `test_the_tolerance_sits_between_the_noise_and_the_band_margin`
# holds both ends.
TOLERANCE = 1e-9


class MalformedBaseline(ValueError):
    """The baseline file is not a capture this harness can grade against.

    Raised rather than letting a KeyError escape, so a bad oracle reads as a bad
    oracle: later tickets invoke the gate as a pass/fail step, where a crash and a
    regression must not look alike.
    """


@dataclass(frozen=True)
class Case:
    """One named query in the baseline: a spec, and the name its result is filed under."""

    name: str
    spec: QuerySpec


# The cases the baseline covers: every query entry point, with enough parameter
# variation to exercise the branches that could diverge between engines. The
# parameters are real values from the built graph, chosen so each case has a
# shape worth grading: a pilot with many events and a head-to-head between two
# who share most of them; a staple card and a rare one; a co-occurrence pair that
# shares 1575 decks and one that shares none; every board filter; `drop_lands`
# both ways; and the gem view both unfiltered and narrowed to the largest
# archetype. Every case earns its place by answering differently from its
# siblings: a variation that returns byte-identical output to another case grades
# nothing twice. Editing a case's parameters invalidates the baseline, and
# `compare` says so rather than grading the new query against the old answer.
# The variation is over row shape as well as parameters: a pilot who owns one of
# the 24 decks with no recorded placement is here so that at least one captured
# row is built from a NULL the engine handed back, which no amount of parameter
# variation reaches (issue #54).
CASES: list[Case] = [
    Case("pilot_many_events", PilotNeighbourhood("LuckyTealLynx")),
    Case("pilot_head_to_head", PilotNeighbourhood("LuckyTealLynx", "Michael B")),
    Case("pilot_unplaced_deck", PilotNeighbourhood("AmberAmberPanda")),
    Case("affinity_many_events", PilotAffinity("LuckyTealLynx")),
    Case("affinity_second_pilot", PilotAffinity("Michael B")),
    Case("usage_staple_any_board", CardUsage("pyroblast")),
    Case("usage_staple_main", CardUsage("pyroblast", "Main")),
    Case("usage_staple_side", CardUsage("pyroblast", "Side")),
    Case("usage_rare_any_board", CardUsage("abbot of keral keep")),
    Case("cooc_staple", CardCooccurrence("pyroblast")),
    Case("cooc_rare", CardCooccurrence("abbot of keral keep")),
    Case("cooc_rare_no_lands", CardCooccurrence("abbot of keral keep", drop_lands=True)),
    Case("cooc_pair_shared_decks", CardCooccurrence("pyroblast", "brainstorm")),
    Case("cooc_pair_shared_decks_no_lands",
         CardCooccurrence("pyroblast", "brainstorm", drop_lands=True)),
    Case("cooc_pair_no_shared_decks",
         CardCooccurrence("abiding grace", "________ goblin")),
    Case("gems_whole_meta", HiddenGems()),
    Case("gems_one_archetype", HiddenGems("grixis")),
]


def _row_blob(record: dict) -> dict:
    """A Node or Edge as JSON, dropping the fields it left unset.

    Most fields on most nodes are ``None``, so omitting them keeps the checked-in
    baseline readable. ``pin`` is a tuple in Python and a list in JSON, so it is
    written as a list here and a captured baseline round-trips through JSON
    unchanged.
    """
    return {
        key: list(value) if isinstance(value, tuple) else value
        for key, value in record.items()
        if value is not None
    }


def subgraph_blob(subgraph: Subgraph) -> dict:
    """A Subgraph as the JSON the baseline files under a case name."""
    return {
        "nodes": [_row_blob(asdict(n)) for n in subgraph.nodes],
        "edges": [_row_blob(asdict(e)) for e in subgraph.edges],
    }


def capture(conn: ladybug.Connection, cases: list[Case] = CASES) -> dict:
    """What the graph behind ``conn`` answers today, as JSON.

    The counts and the two dropdown catalogues ride along with the subgraphs: a
    query can return the right shape over a graph that loaded the wrong number of
    rows, and the catalogues are what the app offers before any query runs.
    """
    return {
        "counts": asdict(graph_counts(conn)),
        # As lists, not tuples: JSON has no tuple, so a captured baseline read back
        # off disk would otherwise never equal one taken live.
        "catalogues": {
            "pilots": [list(pair) for pair in pilot_catalogue(conn)],
            "cards": [list(pair) for pair in card_catalogue(conn)],
            "gem_archetypes": [list(pair) for pair in gem_archetypes(conn)],
        },
        "queries": {
            case.name: {"spec": repr(case.spec), **subgraph_blob(run_query(conn, case.spec))}
            for case in cases
        },
    }


def check(conn: ladybug.Connection, path: Path, cases: list[Case] = CASES) -> list[str]:
    """Grade the graph behind ``conn`` against the baseline at ``path``.

    An empty list means no regression: the graph reproduces every answer the
    baseline holds, under each query's own ordering rule.
    """
    expected = json.loads(Path(path).read_text())
    # Validated before the capture runs, so an unusable baseline is reported in a
    # moment rather than after every query has been re-run against it.
    _require_sections(expected)
    return compare(expected, capture(conn, cases), cases)


def _require_sections(baseline: dict) -> None:
    for section in ("counts", "catalogues", "queries"):
        if section not in baseline:
            raise MalformedBaseline(f"no {section!r} section")


def _same(expected: object, actual: object) -> bool:
    """Whether two rows match, comparing floats within ``TOLERANCE``.

    A catalogue entry is a ``[label, value]`` pair of strings rather than a node or
    edge, so it compares whole.
    """
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        return expected == actual
    if expected.keys() != actual.keys():
        return False
    return all(
        abs(value - actual[key]) <= TOLERANCE
        if isinstance(value, float) and isinstance(actual[key], float)
        else value == actual[key]
        for key, value in expected.items()
    )


def _identity(row: object) -> str:
    """What makes a row the same row, ignoring the floats a tolerance governs.

    Rows are matched on their non-float content (a node's id, label, kind and
    counts), so a mean that moved in its last bits reads as the same row with a
    changed value rather than as one row removed and another added.
    """
    if not isinstance(row, dict):
        return json.dumps(row, sort_keys=True)
    return json.dumps(
        {k: v for k, v in row.items() if not isinstance(v, float)}, sort_keys=True
    )


def _compare_rows(where: str, expected: list, actual: list, ordered: bool) -> list[str]:
    """How two row lists differ, under the ordering rule the query promises.

    An ordered query compares position by position, because its order is part of
    the answer. Everything else matches rows by identity, so a row is named as
    added, removed, or changed rather than reported as a positional mismatch that
    says nothing about what moved.
    """
    if ordered and len(expected) == len(actual):
        return [
            f"{where}[{i}]: baseline {e}, now {a}"
            for i, (e, a) in enumerate(zip(expected, actual))
            if not _same(e, a)
        ]

    want = {_identity(row): row for row in expected}
    got = {_identity(row): row for row in actual}
    diffs = []
    if len(expected) != len(actual):
        diffs.append(f"{where}: {len(expected)} in the baseline, {len(actual)} now")
    diffs += [f"{where}: only in the baseline, {want[k]}" for k in sorted(want.keys() - got.keys())]
    diffs += [f"{where}: only now, {got[k]}" for k in sorted(got.keys() - want.keys())]
    diffs += [
        f"{where}: {want[k]} became {got[k]}"
        for k in sorted(want.keys() & got.keys())
        if not _same(want[k], got[k])
    ]
    return diffs


def compare(expected: dict, actual: dict, cases: list[Case]) -> list[str]:
    """Every way ``actual`` differs from the baseline, in readable lines.

    An empty list means the build reproduces the baseline. Each case is graded
    under its own ordering rule (see the module docstring).
    """
    _require_sections(expected)
    diffs: list[str] = []
    for table in expected["counts"].keys() | actual["counts"].keys():
        want, got = expected["counts"].get(table), actual["counts"].get(table)
        if want != got:
            diffs.append(f"counts.{table}: baseline {want}, now {got}")
    for name in expected["catalogues"].keys() | actual["catalogues"].keys():
        diffs += _compare_rows(
            f"catalogues.{name}",
            expected["catalogues"].get(name, []),
            actual["catalogues"].get(name, []),
            ordered=True,
        )
    # Every case either side knows about, not just the ones we were asked to run:
    # walking `cases` alone would let a later ticket silence a failing case by
    # deleting it, leaving its baseline entry unread and the gate still green.
    by_name = {case.name: case for case in cases}
    for name in expected["queries"].keys() | by_name.keys():
        want, got = expected["queries"].get(name), actual["queries"].get(name)
        case = by_name.get(name)
        if case is None:
            diffs.append(f"{name}: in the baseline but not in the cases being run")
            continue
        if want is None or got is None:
            diffs.append(f"{name}: missing from the {'baseline' if want is None else 'capture'}")
            continue
        # The spec is recorded so a case whose parameters were edited fails loudly
        # rather than grading the new query against the old query's output.
        if want["spec"] != got["spec"]:
            diffs.append(f"{name}: spec changed, baseline {want['spec']}, now {got['spec']}")
            continue
        ordered = not isinstance(case.spec, ORDER_INSENSITIVE)
        diffs += _compare_rows(f"{name}.nodes", want["nodes"], got["nodes"], ordered)
        diffs += _compare_rows(f"{name}.edges", want["edges"], got["edges"], ordered)
    return diffs
