from pathlib import Path

import ladybug
import pytest

from graph7ph.build import build_graph
from graph7ph.db import open_for_reading
from graph7ph.models import load_snapshot

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def snapshot_dir() -> Path:
    """A tiny, self-consistent snapshot: 3 decks across 2 pilots, 121 cards."""
    return FIXTURES


@pytest.fixture
def built_graph():
    """Build a snapshot into an artifact under ``root`` and read the graph back.

    Replaces the build-then-open helper that was copied verbatim into two test
    modules (issue #53), so a change to how the graph is opened is made once.
    Read-only, since a test that reads a graph it just built has no reason to
    hold a writer over it.
    """
    def build(root: Path, snapshot: Path) -> ladybug.Connection:
        artifact = root / "graph"
        build_graph(load_snapshot(snapshot), artifact)
        return open_for_reading(artifact)

    return build
