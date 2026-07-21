import ast
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from graph7ph.build import build_graph
from graph7ph.curation import Curation
from graph7ph.models import load_snapshot
from graph7ph.provenance import (
    BUILD_INPUTS,
    PACKAGE_DIR,
    provenance_path,
    source_digest,
    staleness,
)


def _sources(root: Path, *, curation: str = "[pilots]\n") -> tuple[Path, Path]:
    """A stand-in package tree: every build input, plus a query module beside them."""
    package = root / "graph7ph"
    package.mkdir(parents=True)
    for name in BUILD_INPUTS:
        (package / name).write_text(f"# {name}\n")
    (package / "query.py").write_text("# query.py\n")
    curation_path = root / "curation" / "pilots.toml"
    curation_path.parent.mkdir(parents=True)
    curation_path.write_text(curation)
    return package, curation_path


def test_the_digest_moves_with_a_build_input_and_holds_across_the_query_layer(tmp_path):
    # What the digest is for: telling whether the artifact on disk was built from
    # the code standing here now. Only the code that ends up *inside* the artifact
    # counts. Issue #55 is explicit that query-layer changes are already graded
    # correctly against a stale bundle, so moving query.py must not demand a
    # rebuild, or the gate becomes noise and gets ignored.
    package, curation = _sources(tmp_path)
    before = source_digest(package, curation)

    (package / "query.py").write_text("# query.py, rewritten\n")
    assert source_digest(package, curation) == before

    (package / "pilots.py").write_text("# pilots.py, rewritten\n")
    assert source_digest(package, curation) != before


def _first_party_imports(module: Path) -> set[str]:
    # Both spellings: `from graph7ph.x import y` is the repo idiom, but a plain
    # `import graph7ph.x` would otherwise be invisible here, and a module this
    # walk cannot see is a module BUILD_INPUTS can silently omit.
    names = set()
    for node in ast.walk(ast.parse(module.read_text())):
        if isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return {n.split(".")[1] + ".py" for n in names if n.startswith("graph7ph.")}


def test_two_source_trees_that_differ_only_at_a_file_boundary_digest_apart(tmp_path):
    # A digest built by concatenating name and content lets content absorb the
    # name marker that follows it, so these two trees, which differ in build.py,
    # hashed identically until the lengths went in.
    a, _ = _sources(tmp_path / "a")
    b, _ = _sources(tmp_path / "b")
    (a / "build.py").write_text("x")
    (a / "curation.py").write_text("ycuration.pyz")
    (b / "build.py").write_text("xcuration.pyy")
    (b / "curation.py").write_text("z")

    assert source_digest(a, tmp_path / "absent.toml") != source_digest(
        b, tmp_path / "absent.toml"
    )


def test_every_module_a_build_runs_is_a_build_input():
    # The one way this gate fails silently: a new module joins the build path and
    # nobody adds it to BUILD_INPUTS, so the gate goes green on exactly the change
    # it exists to catch. Walking the import graph from the two entry points a
    # build runs through means the list cannot quietly fall behind the code.
    reached, pending = set(), ["ingest.py", "build.py"]
    while pending:
        name = pending.pop()
        if name in reached:
            continue
        reached.add(name)
        pending.extend(_first_party_imports(PACKAGE_DIR / name))

    # `provenance` is reached because the build stamps the bundle, but it shapes
    # the stamp rather than the graph, so it is deliberately not a build input.
    assert reached - {"provenance.py"} == set(BUILD_INPUTS)


def test_a_built_bundle_carries_the_digest_of_the_sources_that_made_it(
    snapshot_dir, tmp_path
):
    # The stamp is written where the reports are, inside the bundle, so it travels
    # with the single rename that promotes the artifact (ADR 0008) and a rollback
    # carries the provenance matching the graph it restores.
    artifact = tmp_path / "graph"

    build_graph(load_snapshot(snapshot_dir), artifact)

    assert provenance_path(artifact).parent == artifact
    stamp = json.loads(provenance_path(artifact).read_text())
    assert stamp["source_digest"] == source_digest()
    # Timestamped in UTC, so a refusal can say how old the artifact it rejected is.
    assert datetime.fromisoformat(stamp["built_at"]).tzinfo is not None
    assert datetime.fromisoformat(stamp["built_at"]) <= datetime.now(timezone.utc)


def test_a_bundle_built_from_other_sources_reads_as_stale(
    snapshot_dir, tmp_path, make_stale
):
    # The case the gate exists for: an ingest or curation change landed and nobody
    # rebuilt. A bundle fresh out of the build must read clean first, or the
    # refusal below would prove nothing.
    artifact = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), artifact)
    assert staleness(artifact) is None

    built_at = make_stale(artifact)

    complaint = staleness(artifact)
    assert complaint is not None
    assert built_at in complaint
    assert "graph7ph build" in complaint


def test_a_bundle_built_from_an_injected_curation_is_never_gradeable(
    snapshot_dir, tmp_path
):
    # The dictionary is a build input (ADR 0005), so a build handed one in code is
    # reproducible from no state of the working tree. Stamping it with the digest
    # of the checked-in file it never read would be a false green: the gate would
    # pass a bundle built from curation nobody can see.
    artifact = tmp_path / "graph"

    build_graph(load_snapshot(snapshot_dir), artifact, Curation.empty())

    assert json.loads(provenance_path(artifact).read_text())["source_digest"] is None
    assert staleness(artifact) is not None


def test_a_half_written_stamp_reads_as_stale_rather_than_crashing(
    snapshot_dir, tmp_path
):
    # The stamp is written with `write_text`, which is not atomic, so an
    # interrupted build can leave one truncated. The gate is a pass/fail step
    # where a crash and a regression must not look alike, so it has to read as
    # ungradeable rather than spilling a JSONDecodeError.
    artifact = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), artifact)
    provenance_path(artifact).write_text('{"source_digest": "abc"')

    complaint = staleness(artifact)
    assert complaint is not None
    assert "graph7ph build" in complaint


@pytest.mark.parametrize("payload", ["null", "[]", '"a string"'])
def test_a_stamp_that_is_not_an_object_reads_as_stale_rather_than_crashing(
    snapshot_dir, tmp_path, payload
):
    # Parsing is not the same question as being a stamp: a file can be perfectly
    # good JSON and still carry nothing to read fields off. The refusal has to be
    # the same as for an unparseable one, because the callers are pass/fail steps
    # where a crash and a refusal must not look alike, and one of them is a shell
    # script under `set -e` that would abort on a traceback with no message of its
    # own (issue #63).
    artifact = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), artifact)
    provenance_path(artifact).write_text(payload)

    complaint = staleness(artifact)
    assert complaint is not None
    assert "graph7ph build" in complaint


def test_a_bundle_with_no_stamp_at_all_reads_as_stale(snapshot_dir, tmp_path):
    # Every bundle built before this ticket, the one in data/graph included. An
    # unprovable artifact has to read as ungradeable rather than as fresh, or the
    # gate goes green on exactly the artifacts it knows least about.
    artifact = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), artifact)
    provenance_path(artifact).unlink()

    complaint = staleness(artifact)
    assert complaint is not None
    assert "graph7ph build" in complaint
