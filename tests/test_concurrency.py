"""What the engine promises about sharing one graph across handles.

These characterize Ladybug itself rather than any `graph7ph` module. The app's
deployment model rests on them: it opens the artifact read-only, keeps it open
while a build may rewrite the same database, and serves requests from Gradio's
worker threads. That model was inherited from the Kùzu era, where a read-only
and a read-write handle could not coexist at all (Kùzu's open defect #3295), so
#48 changed the vendor underneath it and #49 pins what the replacement actually
promises.
"""

from concurrent.futures import ThreadPoolExecutor

import ladybug

from graph7ph.build import build_graph
from graph7ph.db import database_path
from graph7ph.models import load_snapshot


def _built_db_path(tmp_path, snapshot_dir):
    """The fixture snapshot built into an artifact, returning the database path."""
    artifact = tmp_path / "graph"
    build_graph(load_snapshot(snapshot_dir), artifact)
    return str(database_path(artifact))


def _pilots(conn):
    return conn.execute("MATCH (p:Pilot) RETURN count(p)").get_next()[0]


def test_a_build_can_write_while_the_app_holds_the_graph_open_read_only(
    tmp_path, snapshot_dir
):
    # Kùzu #3295 refused the writer outright. Ladybug does not, which is what
    # lets a rebuild run against the artifact a running app is serving from.
    path = _built_db_path(tmp_path, snapshot_dir)
    reader = ladybug.Connection(ladybug.Database(path, read_only=True))
    before = _pilots(reader)

    writer_db = ladybug.Database(path)
    try:
        writer = ladybug.Connection(writer_db)
        writer.execute(
            "CREATE (:Pilot {pilot: 'ghost', displayName: 'Ghost', lowConfidence: false})"
        )
        assert _pilots(writer) == before + 1
        # The reader is not disturbed by a write in flight.
        assert _pilots(reader) == before
    finally:
        # Closed before anything is asserted about what another handle can see.
        # Ladybug folds the write-ahead log in on close, so only a closed
        # database is the settled file a promotion actually publishes; asserting
        # cross-handle visibility against an open writer would measure WAL replay
        # instead. `build_graph` closes in a try/finally for the same reason.
        writer_db.close()

    # Once the write has settled, the reader still holds the snapshot it opened
    # on, and only a newly opened database sees the new data. A rebuild therefore
    # reaches a running app when the app reopens the database, which is what
    # makes promoting a freshly built artifact a restart rather than a hot swap.
    assert _pilots(reader) == before
    assert _pilots(ladybug.Connection(ladybug.Database(path, read_only=True))) == before + 1


def test_many_connections_over_one_shared_database_each_read_the_whole_graph(
    tmp_path, snapshot_dir
):
    # The app shares one read-only Database and opens a Connection per request
    # across Gradio's worker threads. Opening and reading from many at once must
    # give every one of them the complete answer.
    #
    # Named for what it establishes, not for the design it supports: it does not
    # show that a *shared* Connection would break, so it cannot catch a refactor
    # that hoists the connection out of the request path. Connection
    # thread-safety remains an assumption inherited from the Kùzu era.
    db = ladybug.Database(_built_db_path(tmp_path, snapshot_dir), read_only=True)
    expected = _pilots(ladybug.Connection(db))

    with ThreadPoolExecutor(max_workers=8) as pool:
        counts = list(pool.map(lambda _: _pilots(ladybug.Connection(db)), range(32)))

    assert counts == [expected] * 32
