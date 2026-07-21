import json
from pathlib import Path

import ladybug
import pytest

from graph7ph.build import build_graph
from graph7ph.db import open_for_reading
from graph7ph.models import load_snapshot
from graph7ph.provenance import provenance_path

FIXTURES = Path(__file__).parent / "fixtures"


def build_and_open(root: Path, snapshot: Path) -> ladybug.Connection:
    """Build ``snapshot`` into an artifact under ``root`` and read the graph back.

    The one spelling of build-then-open, so a change to how the graph is opened
    is made once (issue #53). A plain function rather than only a fixture because
    the two callers want different scopes: see ``built_graph`` below, and
    ``test_baseline.py``'s module-scoped ``conn`` (issue #52).
    """
    artifact = root / "graph"
    build_graph(load_snapshot(snapshot), artifact)
    return open_for_reading(artifact)


@pytest.fixture(scope="session")
def snapshot_dir() -> Path:
    """A tiny, self-consistent snapshot: 3 decks across 2 pilots, 121 cards.

    Neither file is in label order, on purpose: a fixture already sorted lets a
    catalogue query that has lost its ORDER BY pass by luck (issue #56). Do not
    sort them. ``test_the_fixture_reaches_the_graph_out_of_label_order`` is what
    holds them to it, and is the honest statement of the property, since the
    cards reach the graph in file order but the pilots reach it through
    ``resolve_pilots``.

    Session-scoped because it hands back a constant path and nothing can mutate
    it, which is what lets ``test_baseline.py``'s module-scoped ``conn`` request
    it: a fixture cannot depend on one of narrower scope (issue #52).
    """
    return FIXTURES


@pytest.fixture
def built_graph():
    """:func:`build_and_open` as a fixture, which is how 100-odd tests reach it.

    Pure delegation, kept because it is the spelling those call sites already use
    (issue #53) and rewriting them to import the function buys nothing. Prefer
    the function directly where a fixture cannot be used, as ``test_baseline.py``
    does from module scope.

    The connection it hands back is read-only: a test that reads a graph it just
    built has no reason to hold a writer over it, and one that plants a node
    opens its own through ``db.open_for_writing``.
    """
    return build_and_open


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
