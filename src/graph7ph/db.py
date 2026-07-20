"""Small helpers over the Ladybug Python API."""

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import ladybug

DB_ENV_VAR = "GRAPH7PH_DB"
DEFAULT_ARTIFACT_PATH = Path("data/graph")
DB_FILENAME = "graph.ladybug"


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


def remove_artifact(path: Path) -> None:
    """Clear an artifact bundle (live, incoming, or backup), if it is there.

    The database and any write-ahead log it leaves behind live inside the bundle,
    so clearing the bundle clears them with it. Tolerates a plain file at ``path``,
    which is what a pre-#47 artifact becomes once the engine writes a single file.
    """
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def rows(result: ladybug.QueryResult) -> Iterator[list]:
    """Yield each row of a Ladybug query result as a list of column values."""
    while result.has_next():
        yield result.get_next()
