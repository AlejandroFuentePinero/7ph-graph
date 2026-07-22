"""The deploy script's guards over the artifact bundle (``scripts/deploy_space.sh``).

These run the real script as a deploy would, with ``GRAPH7PH_DB`` pointed at a
bundle, and assert on its exit status and message. The Hub is stubbed out rather
than reached: a fake ``uvx`` early on ``PATH`` fails the Space-exists check, so a
run that gets that far proves it cleared the guards without uploading anything.

``uv`` is stubbed nowhere except over the baseline gate, and there only for the
one test that has to get past it (see ``passing_gate``): the script asks the
package for its database filename and for its judgement on the bundle's
provenance through ``uv run``, so these need ``uv`` on ``PATH`` and this project
importable. A failure in every test here at once usually means that, and not the
deploy path.
"""

import os
import shutil
import subprocess
from contextlib import chdir
from pathlib import Path

import pytest

from graph7ph.db import DB_FILENAME
from graph7ph.provenance import provenance_path, stamp

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "deploy_space.sh"


@pytest.fixture
def no_hub(tmp_path) -> Path:
    """A ``PATH`` prefix whose ``uvx`` refuses, so no run can reach the Hub."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "uvx"
    stub.write_text("#!/bin/sh\nexit 1\n")
    stub.chmod(0o755)
    return bin_dir


@pytest.fixture
def passing_gate(tmp_path) -> Path:
    """A ``PATH`` prefix whose ``uv`` answers the baseline gate green.

    The one guard no bundle a test can build will ever clear honestly: the gate
    re-runs every case in ``baseline.CASES`` against the checked-in oracle, and
    two of those cases need at least 50 ranked decks before ``hidden_gems`` will
    answer at all, so a tmp bundle cannot pass it and the real graph cannot be
    built here. What the deploy script contributes is the wiring, and that is what
    this stubs for: the gate's own verdict is graded in tests/test_cli.py, and
    ``test_a_bundle_whose_graph_the_engine_will_not_open_is_refused`` runs the
    real one.

    Only the gate is answered. Every other ``uv`` the script runs is handed to the
    real one, because the script asks the package for its database filename and its
    provenance judgement through ``uv run python`` and those must stay honest.
    """
    real_uv = shutil.which("uv")
    # Named here rather than left to become the string "None" inside the stub,
    # which would fail every `uv` the script runs with a message naming neither
    # `uv` nor this fixture, and so would not read as the missing-`uv` failure the
    # module docstring tells the reader to expect.
    assert real_uv, "these tests need `uv` on PATH; see the module docstring"
    bin_dir = tmp_path / "gate"
    bin_dir.mkdir()
    stub = bin_dir / "uv"
    stub.write_text(
        '#!/bin/sh\n'
        'if [ "$2" = "graph7ph" ] && [ "$3" = "baseline" ]; then exit 0; fi\n'
        f'exec "{real_uv}" "$@"\n'
    )
    stub.chmod(0o755)
    return bin_dir


def _deploy(artifact: Path, no_hub: Path, *also: Path) -> subprocess.CompletedProcess:
    """Run the script as a caller standing outside the repo with a relative bundle.

    Both halves of that are load-bearing, and together they are what make the
    probes' absolute artifact path falsifiable. A ``GRAPH7PH_DB`` of ``graph``
    means the caller's ``graph``, and a probe that has cd'd to the repo root is not
    standing where the caller stood, so it resolves that name against the wrong
    directory. Handed an absolute path from the repo root instead, as these tests
    did until issue #71, a probe that resolved it wrongly would still land on the
    right artifact and pass.

    ``no_hub`` stays a required argument, and any further ``PATH`` prefix comes
    after it, so that no call can be written that leaves the Hub unstubbed. The
    order between them does not matter, since each stub holds a different command.
    """
    return subprocess.run(
        [str(SCRIPT), "someone/some-space"],
        cwd=artifact.parent,
        env={
            **os.environ,
            "GRAPH7PH_DB": artifact.name,
            "PATH": os.pathsep.join([str(no_hub), *map(str, also), os.environ["PATH"]]),
        },
        capture_output=True,
        text=True,
    )


def _bundle(tmp_path) -> Path:
    """A bundle fit to ship: settled, and stamped with today's sources.

    The database and the build stamp are what the guards look for, so the two
    ingestion reports a real bundle also carries are left out rather than staged
    as scenery. The stamp is written by ``provenance.stamp`` rather than
    hand-authored, so these fixtures cannot drift from the shape the build
    writes and the guard reads. It is a real digest of the working tree, which is
    what makes a bundle here deployable without building one: the database is
    pretend, but the question the staleness guard asks is about the sources, and
    the answer to that is honest.
    """
    artifact = tmp_path / "graph"
    artifact.mkdir()
    (artifact / DB_FILENAME).write_bytes(b"pretend database")
    # Stamped from the repo root, because the digest folds in `curation/pilots.toml`
    # at its relative default and the script's own probe runs from there. Left to
    # inherit pytest's working directory instead, a run started anywhere else would
    # digest the dictionary as absent, and the settled-bundle test below would fail
    # over where pytest was invoked rather than over the guard it is watching.
    with chdir(ROOT):
        stamp(artifact)
    return artifact


@pytest.mark.parametrize("log", [b"", b"unsettled writes"])
def test_a_bundle_carrying_a_write_ahead_log_is_refused(tmp_path, no_hub, log):
    # A clean close folds the log in and removes it, so a `.wal` that is present at
    # all means the build did not close cleanly, whatever its size (issue #50).
    # Emptiness is not proof the writes settled: an interrupted build can leave a
    # log before it has written a byte into it. Note the closing that settles it is
    # the Connection's, not the Database's (tests/test_build.py pins that).
    artifact = _bundle(tmp_path)
    (artifact / f"{DB_FILENAME}.wal").write_bytes(log)

    result = _deploy(artifact, no_hub)

    assert result.returncode != 0
    assert "uncheckpointed write-ahead log" in result.stderr


def test_a_bundle_with_no_database_in_it_is_refused(tmp_path, no_hub):
    # The artifact is a directory now (issue #47), so an existing directory is not
    # proof of a graph: a half-cleared one would stage and ship a Space that dies
    # at startup.
    empty = tmp_path / "graph"
    empty.mkdir()

    result = _deploy(empty, no_hub)

    assert result.returncode != 0
    assert "No graph artifact" in result.stderr


def test_a_bundle_built_from_other_sources_is_refused(tmp_path, no_hub, make_stale):
    # The case this guard exists for (issue #63): an ingest or curation change
    # landed and nobody rebuilt, so the bundle and the package staged beside it
    # are two different builds. The refusal names the artifact's build time,
    # which is what tells the reader which of the two is the stale one.
    artifact = _bundle(tmp_path)
    built_at = make_stale(artifact)

    result = _deploy(artifact, no_hub)

    assert result.returncode != 0
    assert built_at in result.stderr
    assert "graph7ph build" in result.stderr


def test_a_bundle_carrying_no_build_stamp_is_refused(tmp_path, no_hub):
    # Every bundle built before #55, the one in data/graph included. An artifact
    # that cannot say what it was built from cannot be shown to match the code
    # deployed beside it, and the Space would serve a graph nobody can account
    # for.
    artifact = _bundle(tmp_path)
    provenance_path(artifact).unlink()

    result = _deploy(artifact, no_hub)

    assert result.returncode != 0
    assert "no build provenance" in result.stderr


def test_a_bundle_whose_graph_the_engine_will_not_open_is_refused(tmp_path, no_hub):
    # The fourth guard, and the only one that reads the graph rather than stat-ing
    # the bundle around it (issue #71). The fixture's database is a placeholder, so
    # what the gate refuses on here is that it will not open, which is the cheapest
    # bundle the gate can be shown to speak about at all: the regression it exists
    # for needs the real graph and the checked-in oracle, and is graded in
    # tests/test_baseline.py and tests/test_cli.py where both are in reach.
    #
    # The refusal being this particular one is also what says the gate ran where it
    # was meant to, since the two ways of misinvoking it both refuse differently.
    # Run from anywhere but the repo root, it digests `curation/pilots.toml` as
    # absent and refuses on staleness first; pointed at the default artifact rather
    # than at this one, it never sees this database at all. Both were measured
    # against this test, and both fail it.
    artifact = _bundle(tmp_path)

    result = _deploy(artifact, no_hub)

    assert result.returncode != 0
    assert "Cannot open" in result.stderr
    assert str(artifact.resolve()) in result.stderr
    # Refused with the other three, before the staging directory is made, so a
    # bundle that cannot be graded gets no further than one that has no database.
    assert "No Space at" not in result.stderr


def test_a_settled_bundle_is_not_refused(tmp_path, no_hub, passing_gate):
    # The guards have to let a promoted artifact through, or the deploy path is
    # closed. Reaching the Space-exists check is what proves they did: it sits
    # past all four and past the staging block, and the stubbed `uvx` fails it
    # before anything is uploaded, which is why a non-zero status here is the stub
    # and not a refusal. Named messages rather than status alone, so a guard that
    # fired for the wrong reason cannot pass as the stub.
    #
    # The fourth guard is answered green by `passing_gate` rather than cleared,
    # because no bundle assembled here can clear it (see that fixture). So what
    # this still holds is the other three, and everything between the guards and
    # the Hub: the staging block runs for real here and nowhere else.
    #
    # Named for what it can see. It asserts an absence, so deleting the guards
    # outright leaves it green; what it catches is a guard that fires on a good
    # bundle. Deletion is caught by the refusal tests above, which is where that
    # cover belongs.
    result = _deploy(_bundle(tmp_path), no_hub, passing_gate)

    assert "No graph artifact" not in result.stderr
    assert "uncheckpointed write-ahead log" not in result.stderr
    assert "Refusing to deploy" not in result.stderr
    assert "No Space at someone/some-space" in result.stderr
