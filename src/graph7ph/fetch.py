"""Fetch the 7phstats data files into an append-only, timestamped snapshot.

Each fetch writes a fresh ``<snapshots-root>/<timestamp>/`` directory holding
every source file, so past snapshots are never overwritten (ADR 0003). The HTTP
call is injected as ``download`` so the snapshot layout can be tested without a
network round-trip.
"""

import shutil
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_URL = "https://7phstats.com/data"

# Every JSON file the 7phstats front-end loads (confirmed against its bundle).
DATA_FILES = (
    "decks.json",
    "cards_index.json",
    "events.json",
    "archetypes.json",
    "points_updates.json",
    "recommendations.json",
)

Downloader = Callable[[str], bytes]


def _http_get(url: str) -> bytes:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.content


def fetch_snapshot(
    snapshots_root: Path,
    *,
    base_url: str = BASE_URL,
    files: tuple[str, ...] = DATA_FILES,
    timestamp: str | None = None,
    download: Downloader = _http_get,
) -> Path:
    """Download every file into a new timestamped snapshot; return its path.

    Snapshots are append-only: the files are written to a hidden temp directory
    and promoted with a single atomic rename once all downloads succeed, so a
    mid-download failure leaves no partial snapshot, and an existing snapshot for
    the same timestamp is never overwritten."""
    timestamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshots_root = Path(snapshots_root)
    snapshot_dir = snapshots_root / timestamp
    if snapshot_dir.exists():
        raise FileExistsError(f"snapshot {snapshot_dir} already exists")

    snapshots_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=snapshots_root, prefix=f".{timestamp}.partial-"))
    try:
        for name in files:
            (staging / name).write_bytes(download(f"{base_url}/{name}"))
        staging.rename(snapshot_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return snapshot_dir
