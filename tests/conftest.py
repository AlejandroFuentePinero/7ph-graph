import json
from pathlib import Path

import ladybug
import pytest

from graph7ph.build import build_graph
from graph7ph.db import open_for_reading
from graph7ph.models import load_snapshot
from graph7ph.provenance import provenance_path

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


@pytest.fixture
def make_stale():
    """Rewrite a built bundle's stamp so it reads as built from other sources.

    The honest simulation of the case issue #55 is about: an ingest or curation
    change landed and nobody rebuilt. Editing the stamp rather than the package
    under test is the same thing from the bundle's side, and shared between the
    provenance and CLI tests so the stamp's shape is written down once. Returns
    the build time it wrote, for the tests that assert a refusal names it.
    """
    def stale(artifact: Path) -> str:
        built_at = "2026-07-01T00:00:00+00:00"
        provenance_path(artifact).write_text(
            json.dumps({"source_digest": "0" * 64, "built_at": built_at})
        )
        return built_at

    return stale
