import pytest

from graph7ph.db import (
    DB_ENV_VAR,
    NotABundle,
    artifact_path,
    database_path,
    open_for_reading,
    open_for_writing,
)

PLANT_A_NODE = "CREATE (:T {id: $id})"
COUNT_NODES = "MATCH (t:T) RETURN count(t)"


def _written(artifact):
    """A one-table graph at ``artifact`` holding a single node, settled."""
    with open_for_writing(artifact) as conn:
        conn.execute("CREATE NODE TABLE T(id INT64, PRIMARY KEY(id))")
        conn.execute(PLANT_A_NODE, {"id": 1})


def test_the_database_is_the_file_the_engine_leaves_inside_the_bundle(tmp_path):
    # The artifact is a bundle directory holding the database alongside its
    # reports, not the database itself (issue #47), so every path the build and
    # the app resolve is relative to that directory.
    #
    # Asserted against what a write actually leaves on disk rather than against
    # `database_path`'s own composition: the previous form here asserted that `/`
    # and `.parent` are inverses, which holds for any filename whatsoever and so
    # could never disagree with the code (issue #52).
    #
    # What it catches, measured: pointing `database_path` at a sibling (the
    # pre-#47 layout this defends against) empties the bundle and goes red. What
    # it deliberately does not catch: renaming `DB_FILENAME`, since the write
    # resolves through the same function and both sides move together. That name
    # is not this test's business; `scripts/deploy_space.sh` reads it out of the
    # package precisely so nothing has to restate it.
    artifact = tmp_path / "graph"

    _written(artifact)

    assert artifact.is_dir()
    assert [p.name for p in artifact.iterdir()] == [database_path(artifact).name]


def test_the_artifact_override_decides_where_the_database_is_written(
    monkeypatch, tmp_path
):
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.setenv(DB_ENV_VAR, str(elsewhere))

    assert artifact_path() == elsewhere

    # The override is only real if a write through it lands there, so the graph is
    # built at whatever `artifact_path` resolved and looked for at the path the
    # environment named.
    _written(artifact_path())

    assert database_path(elsewhere).is_file()


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


def test_a_file_where_the_bundle_should_be_is_refused_by_name(tmp_path):
    # A bundle is a directory, so a regular file at that path is not one the
    # build can open, clear, or promote. Refused as a named abort rather than
    # the `FileExistsError` the bundle's own mkdir would raise, so the CLI can
    # report it the way it reports any other data it cannot build (issue #52).
    #
    # Reachable through a stray file at `<artifact>.incoming` during a build, or
    # a caller building straight at a file path. Not through `GRAPH7PH_DB`
    # pointing at a file: `promote` renames that aside into `.backup` and never
    # reaches here (measured).
    artifact = tmp_path / "graph"
    artifact.write_text("not a bundle")

    with pytest.raises(NotABundle, match=str(artifact)):
        with open_for_writing(artifact):
            pass

    # The file is left where it was: refusing is not a licence to delete it.
    assert artifact.read_text() == "not a bundle"


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
