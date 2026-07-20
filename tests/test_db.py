from pathlib import Path

import pytest

from graph7ph.db import (
    DB_ENV_VAR,
    artifact_path,
    database_path,
    open_for_reading,
    open_for_writing,
)


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


PLANT_A_NODE = "CREATE (:T {id: $id})"
COUNT_NODES = "MATCH (t:T) RETURN count(t)"


def _written(artifact):
    """A one-table graph at ``artifact`` holding a single node, settled."""
    with open_for_writing(artifact) as conn:
        conn.execute("CREATE NODE TABLE T(id INT64, PRIMARY KEY(id))")
        conn.execute(PLANT_A_NODE, {"id": 1})


def test_the_write_path_leaves_a_settled_database_behind(tmp_path):
    # Closing is what folds the write-ahead log back in, and getting it wrong is
    # invisible until something reads the file (issue #50). The write seam owns
    # that unwinding so no caller has to remember it.
    #
    # Unlike the build-level test of the same invariant, this one fails when the
    # closing is deleted rather than passing regardless. Measured: dropping the
    # context managers from `open_for_writing` leaves `graph.ladybug.wal` in the
    # bundle and this goes red.
    #
    # That sensitivity rests on `conn` still being bound in this frame at the
    # assert, which is why the write is spelled out here rather than sharing
    # `_written` with the test below. Lift these four lines into a helper and the
    # binding dies with the call, refcounting settles the file for free, and this
    # test passes whatever the seam does. Measured, not theorised: that refactor
    # was made and reverted.
    artifact = tmp_path / "graph"

    with open_for_writing(artifact) as conn:
        conn.execute("CREATE NODE TABLE T(id INT64, PRIMARY KEY(id))")
        conn.execute(PLANT_A_NODE, {"id": 1})

    assert [p.name for p in artifact.iterdir() if p.name.endswith(".wal")] == []


def test_the_read_path_sees_the_settled_graph_and_cannot_write_to_it(tmp_path):
    # The app and the baseline gate both read an artifact a build may be
    # rewriting, so their handle is opened read-only rather than by convention.
    artifact = tmp_path / "graph"
    _written(artifact)

    reader = open_for_reading(artifact)

    assert reader.execute(COUNT_NODES).get_next()[0] == 1
    with pytest.raises(RuntimeError):
        reader.execute(PLANT_A_NODE, {"id": 2})

    # What makes that refusal mean read-only rather than a bad query: the very
    # same statement lands through the write seam. Asserted this way round rather
    # than by matching the engine's wording, which a Ladybug upgrade may reword
    # without changing the behaviour named here.
    with open_for_writing(artifact) as writer:
        writer.execute(PLANT_A_NODE, {"id": 2})

    assert open_for_reading(artifact).execute(COUNT_NODES).get_next()[0] == 2
