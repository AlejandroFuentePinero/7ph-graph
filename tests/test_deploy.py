"""The deploy script's guards over the artifact bundle (``scripts/deploy_space.sh``).

These run the real script as a deploy would, with ``GRAPH7PH_DB`` pointed at a
bundle, and assert on its exit status and message. The Hub is stubbed out rather
than reached: a fake ``uvx`` early on ``PATH`` fails the Space-exists check, so a
run that gets that far proves it cleared the guards without uploading anything.

Only ``uvx`` is stubbed, not ``uv``: the script asks the package for its database
filename through ``uv run``, so these need ``uv`` on ``PATH`` and this project
importable. A failure in every test here at once usually means that, and not the
deploy path.
"""

import os
import subprocess
from pathlib import Path

import pytest

from graph7ph.db import DB_FILENAME

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


def _deploy(artifact: Path, no_hub: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(SCRIPT), "someone/some-space"],
        env={
            **os.environ,
            "GRAPH7PH_DB": str(artifact),
            "PATH": f"{no_hub}{os.pathsep}{os.environ['PATH']}",
        },
        capture_output=True,
        text=True,
    )


def _bundle(tmp_path) -> Path:
    """A settled bundle: the database, with nothing left to replay beside it.

    The database is what the guards look for, so the sidecar reports a real
    bundle also carries are left out rather than staged as scenery.
    """
    artifact = tmp_path / "graph"
    artifact.mkdir()
    (artifact / DB_FILENAME).write_bytes(b"pretend database")
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


def test_a_settled_bundle_is_not_refused(tmp_path, no_hub):
    # The guards have to let a promoted artifact through, or the deploy path is
    # closed. Reaching the Space-exists check is what proves they did: it sits
    # past both, and the stubbed `uvx` fails it before anything is uploaded, which
    # is why a non-zero status here is the stub and not a refusal. Named messages
    # rather than status alone, so a guard that fired for the wrong reason cannot
    # pass as the stub.
    #
    # Named for what it can see. It asserts an absence, so deleting both guards
    # outright leaves it green; what it catches is a guard that fires on a good
    # bundle. Deletion is caught by the refusal tests above, which is where that
    # cover belongs.
    result = _deploy(_bundle(tmp_path), no_hub)

    assert "No graph artifact" not in result.stderr
    assert "uncheckpointed write-ahead log" not in result.stderr
    assert "No Space at someone/some-space" in result.stderr
