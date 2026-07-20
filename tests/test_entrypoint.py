"""The Space entrypoint's artifact check (``app.py`` at the repo root).

The check guards at import, before the explorer is built, so these run it as the
Space does: a subprocess whose ``GRAPH7PH_DB`` points somewhere, asserting on the
exit status and the message rather than reaching inside the module.
"""

import os
import subprocess
import sys
from pathlib import Path

from graph7ph.build import build_graph
from graph7ph.models import load_snapshot

ROOT = Path(__file__).resolve().parents[1]


def _import_entrypoint(artifact):
    # The real environment with only the artifact path overridden: stripping it to
    # a hand-built PATH would couple these to one machine's layout, and the app
    # imports gradio, which resolves cache and config directories from the
    # environment it is given.
    return subprocess.run(
        [sys.executable, "-c", "import app"],
        cwd=ROOT,
        env={**os.environ, "GRAPH7PH_DB": str(artifact)},
        capture_output=True,
        text=True,
    )


def test_the_space_refuses_to_serve_a_missing_artifact(tmp_path):
    result = _import_entrypoint(tmp_path / "absent")

    assert result.returncode != 0
    assert "No graph artifact" in result.stderr


def test_the_space_refuses_an_artifact_directory_with_no_database(tmp_path):
    # The artifact is a directory now (issue #47), so an existing directory no
    # longer proves there is a graph in it. The Space must say so up front rather
    # than dying deeper in with a Kùzu error once a visitor hits it.
    empty = tmp_path / "graph"
    empty.mkdir()

    result = _import_entrypoint(empty)

    assert result.returncode != 0
    assert "No graph artifact" in result.stderr


def test_the_space_serves_a_built_artifact(tmp_path, snapshot_dir):
    artifact = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), artifact)

    result = _import_entrypoint(artifact)

    assert result.returncode == 0, result.stderr
