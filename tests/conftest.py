from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def snapshot_dir() -> Path:
    """A tiny, self-consistent snapshot: 3 decks across 2 pilots, 121 cards."""
    return FIXTURES
