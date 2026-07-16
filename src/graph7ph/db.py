"""Small helpers over the Kùzu Python API."""

import os
from collections.abc import Iterator
from pathlib import Path

import kuzu

DB_ENV_VAR = "GRAPH7PH_DB"
DEFAULT_DB_PATH = Path("data/graph.kuzu")


def artifact_path() -> Path:
    """Where the graph artifact lives: ``$GRAPH7PH_DB``, or the default.

    Every entrypoint that touches the artifact resolves it here (the CLI, the
    deployed ``app.py``, and the deploy script's own default), so the build
    writes and the app reads the same graph wherever it is pointed.
    """
    return Path(os.environ.get(DB_ENV_VAR, DEFAULT_DB_PATH))


def rows(result: kuzu.QueryResult) -> Iterator[list]:
    """Yield each row of a Kùzu query result as a list of column values."""
    while result.has_next():
        yield result.get_next()
