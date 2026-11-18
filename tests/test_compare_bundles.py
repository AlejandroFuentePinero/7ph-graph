"""The before-and-after comparison of what two bundles serve (issue #380).

``scripts/compare_bundles.py`` freezes the site as main serves it and compares a
later build against it, sorting every difference into explained (an archetype that
gained or lost decks), allowed with a reason, or regression. These build small
bundles from the suite's snapshot and grade what the script makes of them; the
rules a three-deck record cannot exercise are graded on captures written by hand.
"""

import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import pytest

from graph7ph.build import build_graph
from graph7ph.curation import Curation
from graph7ph.models import load_snapshot


def _script():
    """``scripts/compare_bundles.py`` as a module: ``scripts`` is not a package."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "compare_bundles.py"
    spec = importlib.util.spec_from_file_location("compare_bundles", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bundles = _script()


def _build(artifact: Path, snapshot: Path, curation: Curation | None = None) -> Path:
    build_graph(load_snapshot(snapshot), artifact, curation or Curation.empty())
    return artifact


@pytest.fixture(scope="module")
def reference(tmp_path_factory, snapshot_dir):
    """The suite's snapshot built once with no rulings, as a frozen main would be."""
    return bundles.capture(_build(tmp_path_factory.mktemp("before") / "graph", snapshot_dir))


def test_two_builds_of_one_record_differ_in_nothing(tmp_path, snapshot_dir, reference):
    after = bundles.capture(_build(tmp_path / "graph", snapshot_dir))

    assert bundles.compare(reference, after).differences == []


@pytest.fixture(scope="module")
def storm_into_grixis(tmp_path_factory, snapshot_dir, reference):
    """The suite's record with its one Storm deck folded into Grixis, its two Grixis
    decks' archetype, by an alias: the shape of a family fold."""
    folded = replace(Curation.empty(), archetype_aliases={"engine:storm": "engine:grixis"})
    graph = _build(tmp_path_factory.mktemp("after") / "graph", snapshot_dir, folded)
    return bundles.compare(reference, bundles.capture(graph))


def test_a_fold_names_the_archetypes_that_gained_and_lost_decks(storm_into_grixis):
    # The graph keys an archetype on its code without the snapshot's `engine:`.
    assert storm_into_grixis.moved == {"storm": (1, 0), "grixis": (2, 3)}


def test_every_difference_a_fold_makes_is_explained(storm_into_grixis):
    assert storm_into_grixis.differences
    assert {d.verdict for d in storm_into_grixis.differences} == {"explained"}


def test_a_fold_conserves_decks(storm_into_grixis):
    assert storm_into_grixis.conservation == []


@pytest.fixture(scope="module")
def renamed(tmp_path_factory, snapshot_dir, reference):
    """The suite's record with one pilot's display name pinned to another spelling."""
    pinned = replace(Curation.empty(), names={"AmberTealViper": "Tom Smith"})
    graph = _build(tmp_path_factory.mktemp("renamed") / "graph", snapshot_dir, pinned)
    return bundles.compare(reference, bundles.capture(graph))


def test_the_report_opens_with_how_many_differences_of_each_kind(storm_into_grixis):
    n = len(storm_into_grixis.differences)

    assert bundles.report(storm_into_grixis).splitlines()[0] == (
        f"{n} differences: {n} explained, 0 allowed with a reason, 0 regressions.")


def test_the_report_names_the_archetypes_that_moved_before_any_detail(storm_into_grixis):
    text = bundles.report(storm_into_grixis)
    summary = text[:text.index("Regressions")]

    assert "Grixis: 2 decks before, 3 after (1 from Storm)" in summary
    assert "Storm: 1 deck before, 0 after (1 to Grixis)" in summary
    assert "Every archetype holds the decks of the archetypes now filed under it." in summary


def test_the_report_of_two_identical_builds_says_there_is_nothing_to_read(
    tmp_path, snapshot_dir, reference
):
    same = bundles.compare(reference, bundles.capture(_build(tmp_path / "g", snapshot_dir)))

    assert bundles.report(same).splitlines()[0] == (
        "No differences: the build serves exactly what the reference does.")


def test_the_report_lists_each_regression_with_what_moved_and_why(renamed):
    assert ("  Pilot names, AmberTealViper: 'Tom S' became 'Tom Smith' "
            "(no archetype moves this answer)") in bundles.report(renamed).splitlines()


def test_a_pilot_renamed_with_no_archetype_moved_is_a_regression(renamed):
    result = renamed

    assert result.moved == {}
    assert ("pilots", ("AmberTealViper",), None, "Tom S", "Tom Smith") in {
        (d.section, d.key, d.field, d.before, d.after) for d in result.differences}
    assert {d.verdict for d in result.differences} == {"regression"}


# What the issue asks the comparison to cover, as the sections a capture files it
# under: each archetype's count, share, rank, finishes and events played by year and
# its share over time; card usage by archetype; hidden gems; every pilot's archetype
# affinity and deck record; the player leaderboard; every figure in the figures file;
# pilot identity from the reconciliation report; table counts. Plus each deck's
# primary archetype, which is what every verdict is read against.
COVERED = {
    "decks", "counts", "years", "archetypes", "meta_share", "ranks", "landscape",
    "events_played", "archetype_picker", "finishes",
    "card_usage", "card_usage_macros", "card_usage_cards",
    "gems", "gem_list", "gem_archetypes",
    "pilots", "affinity", "affinity_macros", "affinity_edges", "records",
    "leaderboard", "figures", "identity",
}

# The suite's record is three decks at two events in one year: too thin for a
# landscape (three archetypes), a gem (eight decks of one card) or a leaderboard
# (majors), and both its events published only a bracket, so no archetype has the
# two fully published events a finishes line needs. Those sections are empty on it
# and graded on hand-made captures.
THIN = {"landscape", "gems", "gem_archetypes", "leaderboard", "archetype_picker",
        "finishes"}


def test_a_capture_holds_every_answer_the_issue_lists(reference):
    assert reference.keys() == COVERED
    assert {name for name, entries in reference.items() if not entries} == THIN


# --- Freezing a reference and comparing against it ---------------------------------

SNAPSHOT = "20251215T000000Z"


@pytest.fixture
def snapshots(tmp_path) -> Path:
    """A snapshot root holding one snapshot, named as a fetch names it."""
    root = tmp_path / "snapshots"
    (root / SNAPSHOT).mkdir(parents=True)
    return root


def _fresh(artifact: Path, snapshot: Path) -> Path:
    """A bundle built under the checked-in dictionary, so its stamp is the working
    tree's and the freshness guards accept it."""
    build_graph(load_snapshot(snapshot), artifact)
    return artifact


def test_a_frozen_reference_records_the_commit_and_snapshots_it_came_from(
    tmp_path, snapshot_dir, snapshots
):
    target = tmp_path / "reference"
    bundles.freeze(_fresh(tmp_path / "graph", snapshot_dir), target, snapshots, "ec84585")

    recorded = json.loads((target / "reference.json").read_text())

    assert recorded["commit"] == "ec84585"
    assert recorded["snapshots"] == [SNAPSHOT]
    assert (target / "graph" / "graph.ladybug").exists()


def test_a_later_build_of_the_same_record_compares_clean_against_the_reference(
    tmp_path, snapshot_dir, snapshots
):
    target = tmp_path / "reference"
    bundles.freeze(_fresh(tmp_path / "a", snapshot_dir), target, snapshots, "ec84585")

    result = bundles.compare_to_reference(target, _fresh(tmp_path / "b", snapshot_dir),
                                          snapshots)

    assert result.differences == []


def test_freezing_refuses_a_bundle_built_from_other_sources(
    tmp_path, snapshot_dir, snapshots, make_stale
):
    graph = _fresh(tmp_path / "graph", snapshot_dir)
    make_stale(graph)

    with pytest.raises(SystemExit, match="not the ones in the working tree"):
        bundles.freeze(graph, tmp_path / "reference", snapshots, "ec84585")


def test_freezing_never_writes_over_a_reference(tmp_path, snapshot_dir, snapshots):
    target = tmp_path / "reference"
    target.mkdir()

    with pytest.raises(SystemExit, match="already holds"):
        bundles.freeze(_fresh(tmp_path / "graph", snapshot_dir), target, snapshots, "x")


def test_comparing_refuses_once_the_snapshots_differ_from_the_reference(
    tmp_path, snapshot_dir, snapshots
):
    target = tmp_path / "reference"
    bundles.freeze(_fresh(tmp_path / "a", snapshot_dir), target, snapshots, "ec84585")
    (snapshots / "20260101T000000Z").mkdir()

    with pytest.raises(SystemExit, match="20260101T000000Z"):
        bundles.compare_to_reference(target, _fresh(tmp_path / "b", snapshot_dir),
                                     snapshots)


def test_the_command_fails_on_a_regression(tmp_path, snapshot_dir, snapshots, capsys):
    # The same record with one deck's finish rewritten: no archetype moves it.
    target = tmp_path / "reference"
    bundles.freeze(_fresh(tmp_path / "a", snapshot_dir), target, snapshots, "ec84585")
    moved = tmp_path / "moved"
    moved.mkdir()
    (moved / "cards_index.json").write_text((snapshot_dir / "cards_index.json").read_text())
    decks = json.loads((snapshot_dir / "decks.json").read_text())
    decks[0]["placement"], decks[0]["placementNorm"] = 20, 0.6333333333333333
    (moved / "decks.json").write_text(json.dumps(decks))

    with pytest.raises(SystemExit) as exit_:
        bundles.main(["compare", "--reference", str(target),
                      "--db", str(_fresh(tmp_path / "b", moved)),
                      "--snapshots", str(snapshots)])

    assert exit_.value.code == 1
    assert "regression" in capsys.readouterr().out.splitlines()[0]


# --- Rules the suite's record is too thin to reach, on hand-made captures ----------


def _filed(**counts: int) -> dict[str, str]:
    """Decks by id, filed under archetypes in the given numbers: ``rakdos0``, ..."""
    return {f"{tag}{i}": tag for tag, n in counts.items() for i in range(n)}


def _refiled(decks: dict[str, str], **moves: str) -> dict[str, str]:
    """The same decks with every deck of each named archetype filed under another."""
    return {deck: moves.get(tag, tag) for deck, tag in decks.items()}


def _capture(decks: dict[str, str], binds: bool = False,
             at: dict[str, tuple[int, str, str]] | None = None, **sections: dict) -> dict:
    """A capture holding ``decks`` and the given sections' entries, each keyed by a
    tuple, with every other section empty. A deck is played in 2025 at event E1 by
    pilot P1 unless ``at`` places it at another ``(year, event, pilot)``."""
    capture = {name: {} for name in COVERED}
    capture["decks"] = {
        json.dumps([deck]): {"archetype": tag} | dict(zip(
            ("year", "event", "pilot"), (at or {}).get(deck, (2025, "E1", "P1"))))
        for deck, tag in decks.items()}
    capture["gem_list"] = {json.dumps(["budget_binds"]): binds}
    for name, entries in sections.items():
        capture[name] = {json.dumps(list(key)): value for key, value in entries.items()}
    return capture


def _verdicts(result) -> dict:
    return {(d.section, d.key, d.field): (d.verdict, d.reason) for d in result.differences}


def _shares(**n: int) -> dict:
    return {(tag, 2025): {"n": count} for tag, count in n.items()}


def test_a_rank_that_moved_because_another_archetype_grew_is_allowed():
    # Rakdos Eclipse folds into Rakdos, which grows from 3 decks to 7 and passes
    # Grixis on 5: Grixis gained and lost nothing, and drops from first to second.
    before_decks = _filed(grixis=5, rakdos=3, rakdos_eclipse=4)
    after_decks = _refiled(before_decks, rakdos_eclipse="rakdos")
    before = _capture(before_decks,
                      meta_share=_shares(grixis=5, rakdos=3, rakdos_eclipse=4),
                      ranks={("grixis", 2025): {"rank": 1, "level": 0}})
    after = _capture(after_decks, meta_share=_shares(grixis=5, rakdos=7),
                     ranks={("grixis", 2025): {"rank": 2, "level": 0}})

    verdict, reason = _verdicts(bundles.compare(before, after))[
        ("ranks", ("grixis", 2025), "rank")]

    assert verdict == "allowed"
    assert "rakdos" in reason


def test_a_rank_that_moved_with_nothing_crossing_it_is_a_regression():
    decks = _filed(grixis=5, rakdos=3)
    before = _capture(decks, meta_share=_shares(grixis=5, rakdos=3),
                      ranks={("grixis", 2025): {"rank": 1, "level": 0}})
    after = _capture(decks, meta_share=_shares(grixis=5, rakdos=3),
                     ranks={("grixis", 2025): {"rank": 2, "level": 0}})

    verdict, _ = _verdicts(bundles.compare(before, after))[
        ("ranks", ("grixis", 2025), "rank")]

    assert verdict == "regression"


def _landscape(mean_norm: float, events: int, low: float, high: float) -> dict:
    return {("grixis", 2025): {"mean_norm": mean_norm, "events": events,
                               "mean_low": low, "mean_high": high}}


def test_an_interval_that_moved_with_the_years_pooled_spread_is_allowed():
    # Grixis's own mean and events hold; the year's spread, pooled over every
    # archetype played, moved because Rakdos Eclipse folded into Rakdos.
    before_decks = _filed(grixis=5, rakdos=3, rakdos_eclipse=4)
    after_decks = _refiled(before_decks, rakdos_eclipse="rakdos")
    before = _capture(before_decks, landscape=_landscape(0.4, 5, 0.2, 0.6),
                      years={(2025,): {"spread": 0.29}})
    after = _capture(after_decks, landscape=_landscape(0.4, 5, 0.21, 0.59),
                     years={(2025,): {"spread": 0.28}})

    verdicts = _verdicts(bundles.compare(before, after))

    assert verdicts[("landscape", ("grixis", 2025), "mean_low")][0] == "allowed"
    assert verdicts[("landscape", ("grixis", 2025), "mean_high")][0] == "allowed"
    assert verdicts[("years", (2025,), "spread")][0] == "explained"


def test_an_interval_that_moved_under_an_unchanged_spread_is_a_regression():
    # Rakdos Eclipse folds into Rakdos that year, but the spread held, so nothing
    # the fold did can have moved Grixis's interval.
    before_decks = _filed(grixis=5, rakdos=3, rakdos_eclipse=4)
    before = _capture(before_decks, landscape=_landscape(0.4, 5, 0.2, 0.6),
                      years={(2025,): {"spread": 0.29}})
    after = _capture(_refiled(before_decks, rakdos_eclipse="rakdos"),
                     landscape=_landscape(0.4, 5, 0.21, 0.59),
                     years={(2025,): {"spread": 0.29}})

    verdicts = _verdicts(bundles.compare(before, after))

    assert verdicts[("landscape", ("grixis", 2025), "mean_low")][0] == "regression"


GEM = {("grixis", "snuff out"): {"decks": 9, "total": 180, "top_decks": 6}}


@pytest.mark.parametrize("binds, verdict", [(True, "allowed"), (False, "regression")])
def test_a_gem_of_an_unmoved_archetype_comes_and_goes_only_with_the_node_budget(
    binds, verdict
):
    # Rakdos Eclipse folds into Rakdos, and Grixis's gem leaves the list: only a
    # node budget the list competes for can push it off.
    before_decks = _filed(grixis=5, rakdos=3, rakdos_eclipse=4)
    before = _capture(before_decks, gems=GEM)
    after = _capture(_refiled(before_decks, rakdos_eclipse="rakdos"), binds=binds)

    assert _verdicts(bundles.compare(before, after))[
        ("gems", ("grixis", "snuff out"), None)][0] == verdict


def test_a_broad_class_drawn_only_above_a_moved_archetype_may_leave_with_it():
    # Before the fold, the card's Storm decks were combo and its Grixis decks tempo;
    # once Storm folds into Grixis, the archetype hangs under tempo and combo goes.
    before_decks = _filed(grixis=2, storm=1)
    before = _capture(
        before_decks,
        card_usage={("brainstorm", "any", "storm"): {"macro": "combo"},
                    ("brainstorm", "any", "grixis"): {"macro": "tempo"}},
        card_usage_macros={("brainstorm", "any", "combo"): {"decks": 1, "total": 1}})
    after = _capture(
        _refiled(before_decks, storm="grixis"),
        card_usage={("brainstorm", "any", "grixis"): {"macro": "tempo"}})

    assert _verdicts(bundles.compare(before, after))[
        ("card_usage_macros", ("brainstorm", "any", "combo"), None)][0] == "explained"


def test_a_broad_class_whose_own_usage_moved_is_a_regression():
    # Even above an archetype that moved: a class's share of its own decks is
    # counted over decks, which no fold refiles out of their class.
    before_decks = _filed(grixis=2, storm=1)
    usage = {("brainstorm", "any", "grixis"): {"macro": "tempo"}}
    before = _capture(before_decks, card_usage=usage, card_usage_macros={
        ("brainstorm", "any", "tempo"): {"decks": 2, "total": 2}})
    after = _capture(_refiled(before_decks, storm="grixis"), card_usage=usage,
                     card_usage_macros={
                         ("brainstorm", "any", "tempo"): {"decks": 1, "total": 2}})

    assert _verdicts(bundles.compare(before, after))[
        ("card_usage_macros", ("brainstorm", "any", "tempo"), "decks")][0] == "regression"


def test_a_deck_filed_out_of_an_archetype_that_did_not_fold_breaks_conservation():
    # Bus Stop Grixis folds into Grixis, and one Mardu deck goes with it (its
    # summed Grixis weights outran its Mardu tag): Grixis now holds 5 decks where
    # the archetypes filed under it held 7, and Mardu holds 2 where it held 3.
    before_decks = _filed(grixis=2, bus_stop_grixis=2, mardu=3)
    after_decks = _refiled(before_decks, bus_stop_grixis="grixis") | {"mardu0": "grixis"}

    result = bundles.compare(_capture(before_decks), _capture(after_decks))

    assert [(b.archetype, b.decks, b.held) for b in result.conservation] == [
        ("grixis", 5, 7), ("mardu", 2, 3)]


def test_the_report_names_a_conservation_break_by_its_archetypes():
    before_decks = _filed(grixis=2, bus_stop_grixis=2, mardu=3)
    after_decks = _refiled(before_decks, bus_stop_grixis="grixis") | {"mardu0": "grixis"}
    names = {("grixis",): {"name": "Grixis"}, ("bus_stop_grixis",): {"name": "Bus Stop Grixis"},
             ("mardu",): {"name": "Mardu"}}

    text = bundles.report(bundles.compare(_capture(before_decks, archetypes=names),
                                          _capture(after_decks, archetypes=names)))

    assert ("  Grixis has 5 decks, but the archetypes now filed under it "
            "(Bus Stop Grixis, Grixis, Mardu) held 7") in text.splitlines()


def test_a_fold_that_files_whole_archetypes_conserves_decks():
    before_decks = _filed(grixis=2, bus_stop_grixis=2, mardu=3)
    after_decks = _refiled(before_decks, bus_stop_grixis="grixis")

    assert bundles.compare(_capture(before_decks), _capture(after_decks)).conservation == []


def test_a_deck_whose_line_changed_archetype_without_moving_is_a_regression():
    decks = _filed(grixis=1)
    before = _capture(decks, records={("Jordan C", "grixis0"): {"archetype": "Grixis"}})
    after = _capture(decks, records={("Jordan C", "grixis0"): {"archetype": "Rakdos"}})

    assert _verdicts(bundles.compare(before, after))[
        ("records", ("Jordan C", "grixis0"), "archetype")][0] == "regression"


def test_an_archetype_that_moved_in_one_year_explains_nothing_about_another():
    # Rakdos Eclipse, played only in 2026, folds into Rakdos: nothing moved Rakdos's
    # 2025 decks, so its 2025 share has no reason to change.
    before_decks = _filed(rakdos=3, rakdos_eclipse=2)
    late = {deck: (2026, "E2", "P1")
            for deck in ("rakdos2", "rakdos_eclipse0", "rakdos_eclipse1")}
    before = _capture(before_decks, at=late, meta_share={
        ("rakdos", 2025): {"n": 2}, ("rakdos", 2026): {"n": 1}})
    after = _capture(_refiled(before_decks, rakdos_eclipse="rakdos"), at=late,
                     meta_share={("rakdos", 2025): {"n": 3}, ("rakdos", 2026): {"n": 3}})

    verdicts = _verdicts(bundles.compare(before, after))

    assert verdicts[("meta_share", ("rakdos", 2025), "n")][0] == "regression"
    assert verdicts[("meta_share", ("rakdos", 2026), "n")][0] == "explained"


def test_an_archetype_new_to_the_record_brings_its_zero_years_with_it():
    # The share matrix holds a row for every archetype across every year, zeros
    # included: GF Storm splits off Gluten Free Storm in 2026 alone, and its 2025 row
    # is a zero that exists because the archetype now does.
    before_decks = _filed(gluten_free_storm=3)
    late = {"gluten_free_storm2": (2026, "E2", "P1")}
    before = _capture(before_decks, at=late, meta_share={
        ("gluten_free_storm", 2025): {"n": 2}, ("gluten_free_storm", 2026): {"n": 1}})
    after = _capture(before_decks | {"gluten_free_storm2": "gf_storm"}, at=late,
                     meta_share={("gluten_free_storm", 2025): {"n": 2},
                                 ("gluten_free_storm", 2026): {"n": 0},
                                 ("gf_storm", 2025): {"n": 0}, ("gf_storm", 2026): {"n": 1}})

    verdicts = _verdicts(bundles.compare(before, after))

    assert verdicts[("meta_share", ("gf_storm", 2025), None)][0] == "explained"
    assert verdicts[("meta_share", ("gf_storm", 2026), None)][0] == "explained"


def test_a_year_with_decks_in_one_build_only_needs_decks_moved_that_year():
    # The same split, but the new row claims a 2025 deck no 2025 deck moved to make.
    before_decks = _filed(gluten_free_storm=3)
    late = {"gluten_free_storm2": (2026, "E2", "P1")}
    before = _capture(before_decks, at=late, meta_share={
        ("gluten_free_storm", 2025): {"n": 2}, ("gluten_free_storm", 2026): {"n": 1}})
    after = _capture(before_decks | {"gluten_free_storm2": "gf_storm"}, at=late,
                     meta_share={("gluten_free_storm", 2025): {"n": 2},
                                 ("gluten_free_storm", 2026): {"n": 0},
                                 ("gf_storm", 2025): {"n": 1}, ("gf_storm", 2026): {"n": 1}})

    assert _verdicts(bundles.compare(before, after))[
        ("meta_share", ("gf_storm", 2025), None)][0] == "regression"


def test_an_archetype_that_moved_at_one_event_explains_nothing_about_another():
    # The Rakdos Eclipse deck at E2 folds into Rakdos; Rakdos's finish at E1, where
    # nothing moved, has no reason to change.
    before_decks = _filed(rakdos=2, rakdos_eclipse=1)
    at = {"rakdos0": (2025, "E1", "P1"), "rakdos1": (2025, "E2", "P2"),
          "rakdos_eclipse0": (2025, "E2", "P3")}
    before = _capture(before_decks, at=at, finishes={
        ("rakdos", "E1"): {"mean_norm": 0.2}, ("rakdos", "E2"): {"mean_norm": 0.5}})
    after = _capture(_refiled(before_decks, rakdos_eclipse="rakdos"), at=at, finishes={
        ("rakdos", "E1"): {"mean_norm": 0.3}, ("rakdos", "E2"): {"mean_norm": 0.4}})

    verdicts = _verdicts(bundles.compare(before, after))

    assert verdicts[("finishes", ("rakdos", "E1"), "mean_norm")][0] == "regression"
    assert verdicts[("finishes", ("rakdos", "E2"), "mean_norm")][0] == "explained"


def test_an_archetype_that_moved_for_one_pilot_explains_nothing_about_another():
    # P2's Rakdos Eclipse deck folds into Rakdos; P1 played Rakdos and moved nothing.
    before_decks = _filed(rakdos=1, rakdos_eclipse=1)
    at = {"rakdos0": (2025, "E1", "P1"), "rakdos_eclipse0": (2025, "E1", "P2")}
    before = _capture(before_decks, at=at, affinity={
        ("P1", "rakdos"): 1, ("P2", "rakdos_eclipse"): 1})
    after = _capture(_refiled(before_decks, rakdos_eclipse="rakdos"), at=at, affinity={
        ("P1", "rakdos"): 2, ("P2", "rakdos"): 1})

    verdicts = _verdicts(bundles.compare(before, after))

    assert verdicts[("affinity", ("P1", "rakdos"), None)][0] == "regression"
    assert verdicts[("affinity", ("P2", "rakdos"), None)][0] == "explained"
    assert verdicts[("affinity", ("P2", "rakdos_eclipse"), None)][0] == "explained"


def test_rank_and_events_played_are_read_for_the_latest_year_alone(tmp_path, snapshot_dir):
    # The tiles state both for the record's latest year only. CFWAT25's one deck
    # moved into 2024 gives the record two years.
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "cards_index.json").write_text(
        (snapshot_dir / "cards_index.json").read_text())
    decks = json.loads((snapshot_dir / "decks.json").read_text())
    for deck in decks:
        if deck["event"] == "CFWAT25":
            deck["createdAt"] = "2024-12-06T00:00:00+00:00"
    (snapshot / "decks.json").write_text(json.dumps(decks))

    captured = bundles.capture(_build(tmp_path / "graph", snapshot))

    def years(section):
        return {json.loads(raw)[1] for raw in captured[section]}

    assert years("meta_share") == {2024, 2025}
    assert years("ranks") == years("events_played") == {2025}


def test_a_dead_identity_ruling_is_identity_and_a_stepped_aside_archetype_ruling_is_not(
    tmp_path, snapshot_dir
):
    rulings = replace(Curation.empty(), merges={"NoSuchPilot": "AmberTealViper"},
                      archetype_aliases={"engine:no_such_code": "engine:grixis"})
    identity = bundles.capture(_build(tmp_path / "graph", snapshot_dir, rulings))["identity"]

    dead = [json.loads(json.loads(raw)[1]) for raw in identity
            if json.loads(raw)[0] == "dead_entries"]

    assert [entry["kind"] for entry in dead] == ["merge"]


def test_an_archetype_renamed_in_a_fold_is_a_regression():
    # Grixis gains Bus Stop Grixis's decks, so every answer about it may move,
    # except its name: a family is filed under its parent's label.
    before_decks = _filed(grixis=2, bus_stop_grixis=2)
    before = _capture(before_decks, archetypes={("grixis",): {"name": "Grixis", "decks": 2}})
    after = _capture(_refiled(before_decks, bus_stop_grixis="grixis"),
                     archetypes={("grixis",): {"name": "Bus Stop Grixis", "decks": 4}})

    verdicts = _verdicts(bundles.compare(before, after))

    assert verdicts[("archetypes", ("grixis",), "name")][0] == "regression"
    assert verdicts[("archetypes", ("grixis",), "decks")][0] == "explained"
