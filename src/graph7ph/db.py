"""Small helpers over the Kùzu Python API."""

from collections.abc import Iterator

import kuzu


def rows(result: kuzu.QueryResult) -> Iterator[list]:
    """Yield each row of a Kùzu query result as a list of column values."""
    while result.has_next():
        yield result.get_next()
