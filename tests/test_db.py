from pathlib import Path

from graph7ph.db import DB_ENV_VAR, artifact_path, database_path


def test_the_database_lives_inside_the_artifact_directory():
    # The artifact is a bundle directory holding the database alongside its
    # reports, not the database itself (issue #47), so every path the build and
    # the app resolve is relative to that directory.
    artifact = Path("data/graph")

    db = database_path(artifact)

    assert db.parent == artifact


def test_the_artifact_override_still_points_at_a_directory(monkeypatch, tmp_path):
    monkeypatch.setenv(DB_ENV_VAR, str(tmp_path / "elsewhere"))

    assert artifact_path() == tmp_path / "elsewhere"
    assert database_path(artifact_path()).parent == tmp_path / "elsewhere"
