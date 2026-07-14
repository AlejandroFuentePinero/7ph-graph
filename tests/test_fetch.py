import pytest

from graph7ph.fetch import DATA_FILES, BASE_URL, fetch_snapshot


def test_fetch_writes_all_data_files_into_timestamped_snapshot(tmp_path):
    calls = []

    def fake_download(url: str) -> bytes:
        calls.append(url)
        return f"BODY::{url}".encode()

    snap = fetch_snapshot(
        tmp_path, timestamp="20260714T090000Z", download=fake_download
    )

    # A snapshot is its own timestamped directory under the snapshots root.
    assert snap == tmp_path / "20260714T090000Z"
    assert sorted(p.name for p in snap.iterdir()) == sorted(DATA_FILES)

    # Every file holds the bytes fetched from its own source URL, once each.
    for name in DATA_FILES:
        url = f"{BASE_URL}/{name}"
        assert (snap / name).read_bytes() == f"BODY::{url}".encode()
    assert sorted(calls) == sorted(f"{BASE_URL}/{n}" for n in DATA_FILES)


def test_failed_download_leaves_no_partial_snapshot(tmp_path):
    def flaky(url: str) -> bytes:
        if url.endswith("events.json"):  # third file: two are already written
            raise RuntimeError("network blip")
        return b"{}"

    with pytest.raises(RuntimeError):
        fetch_snapshot(tmp_path, timestamp="20260714T090000Z", download=flaky)

    # No snapshot, and no leftover staging dir, is left behind.
    assert list(tmp_path.iterdir()) == []


def test_refuses_to_overwrite_an_existing_snapshot(tmp_path):
    ts = "20260714T090000Z"
    fetch_snapshot(tmp_path, timestamp=ts, download=lambda url: b"{}")

    with pytest.raises(FileExistsError):
        fetch_snapshot(tmp_path, timestamp=ts, download=lambda url: b"NEW")

    # The original snapshot is untouched.
    assert (tmp_path / ts / "decks.json").read_bytes() == b"{}"
