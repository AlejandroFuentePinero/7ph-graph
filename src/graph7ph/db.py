"""Small helpers over the Ladybug Python API."""

import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import ladybug

DB_ENV_VAR = "GRAPH7PH_DB"
DEFAULT_ARTIFACT_PATH = Path("data/graph")
DB_FILENAME = "graph.ladybug"


class NotABundle(ValueError):
    """A path the artifact bundle should occupy is a regular file instead.

    Raised before anything is written, so the live graph is untouched and the
    file itself is left alone. The CLI reports it as an abort rather than a
    crash, alongside ``SchemaError`` and ``YearStraddle``: all three mean the
    build cannot honestly proceed (ADR 0003).
    """


def _require_bundle_path(path: Path) -> Path:
    """``path`` as a Path, refused by name if something that is not a bundle is there.

    A bundle is a directory. Every operation that clears, creates or renames one
    goes through here first, so a stray regular file is refused identically
    wherever it turns up rather than surfacing as whichever errno that particular
    syscall happens to raise (issue #52).
    """
    path = Path(path)
    if path.exists() and not path.is_dir():
        raise NotABundle(f"{path} is a file, not an artifact bundle directory")
    return path


def artifact_path() -> Path:
    """Where the graph artifact lives: ``$GRAPH7PH_DB``, or the default.

    Every entrypoint that touches the artifact resolves it here (the CLI, the
    deployed ``app.py``, and the deploy script's own default), so the build
    writes and the app reads the same graph wherever it is pointed.
    """
    return Path(os.environ.get(DB_ENV_VAR, DEFAULT_ARTIFACT_PATH))


def database_path(artifact: Path) -> Path:
    """The database inside the artifact bundle at ``artifact``.

    The artifact is a directory holding the database alongside its reconciliation
    and ingest reports, so that promotion stays a single rename of a single path
    (issue #47). This is the only place that names the database, so swapping the
    engine changes the file inside the bundle, not the bundle's own shape.
    """
    return Path(artifact) / DB_FILENAME


def open_database(artifact: Path, *, read_only: bool = False) -> ladybug.Database:
    """Open the database inside the artifact bundle at ``artifact``.

    The single place a Database is constructed, as :func:`database_path` is the
    single place the file is named (ADR 0008), so an open option (a buffer pool
    size, a timeout) is added here rather than at every caller. Readers pass
    ``read_only=True``, which is what lets several of them share the file with a
    build rewriting it.

    Returns the Database rather than a Connection because the app shares one
    Database across requests and opens its own Connection per request. Callers
    that want both at once want :func:`open_for_writing` or
    :func:`open_for_reading`.

    Note the limit of this seam: ``app.py`` still constructs its per-request
    ``ladybug.Connection`` itself, deliberately, so that the per-request model
    stays visible at the point it matters. A change of engine therefore still
    touches ``app.py``; only the opening of the database is centralised here.
    """
    return ladybug.Database(str(database_path(artifact)), read_only=read_only)


def open_for_reading(artifact: Path) -> ladybug.Connection:
    """A read-only Connection over its own Database, for a one-shot read.

    For callers that read a graph once and are done with it, the CLI's baseline
    gate being the one in ``src``. Deliberately not what the app uses: the app
    shares one Database and opens a Connection per request, and this opens a
    Database of its own per call, so it is the wrong shape to hoist into a
    request path rather than a convenient one.
    """
    return ladybug.Connection(open_database(artifact, read_only=True))


@contextmanager
def open_for_writing(artifact: Path) -> Iterator[ladybug.Connection]:
    """Open the graph in ``artifact`` for writing, settled again on the way out.

    Ladybug keeps a write-ahead log beside the database while it is open and folds
    it in when the last Connection to it goes, so a writer that is not closed
    leaves a torn bundle behind. The Connection is what settles it, not the
    Database: ``Database.close()`` with a Connection still alive leaves the log
    sitting there (measured: 317 bytes on 0.18.2), and closing it first does
    nothing at all. Hence both are context-managed here, unwinding in reverse:
    Connection, then Database. Unwinding on the way out of a failure too, so an
    abandoned bundle is left settled rather than mid-write.
    """
    _require_bundle_path(artifact).mkdir(parents=True, exist_ok=True)
    with open_database(artifact) as db, ladybug.Connection(db) as conn:
        yield conn


def remove_artifact(path: Path) -> None:
    """Clear an artifact bundle (live, incoming, or backup), if it is there.

    The database and any write-ahead log it leaves behind live inside the bundle,
    so clearing the bundle clears them with it. A regular file at ``path`` is not
    a bundle this ever made, so it is refused rather than deleted: clearing a
    bundle is not a licence to delete whatever else is standing there (issue #52).
    Refusing here is also what keeps :func:`ingest.promote` honest, since it
    clears the backup path immediately before renaming the live bundle onto it.
    """
    if _require_bundle_path(path).is_dir():
        shutil.rmtree(path)


def rows(result: ladybug.QueryResult) -> Iterator[list]:
    """Yield each row of a Ladybug query result as a list of column values."""
    while result.has_next():
        yield result.get_next()
