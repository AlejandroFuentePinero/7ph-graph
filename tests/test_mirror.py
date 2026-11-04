"""The snapshot mirror (``graph7ph mirror``), issue #267.

The Hub is a fake that records uploads and answers the two questions the mirror
asks of it (does the dataset exist, is it private), so the tests cover the
decision of what to send and never a network round-trip.
"""

from pathlib import Path

import httpx
import pytest
from huggingface_hub.errors import RepositoryNotFoundError

from graph7ph.mirror import MirrorRefused, mirror_snapshots


class FakeHub:
    """Enough of ``HfApi`` for the mirror: one private dataset, a set of folders.

    ``present`` is the top-level folders already in the dataset; every upload is
    recorded with the local directory it came from and the name it was sent under.
    """

    def __init__(self, present=(), *, private=True, exists=True):
        self.present = set(present)
        self.private = private
        self.exists = exists
        self.uploads: list[tuple[Path, str]] = []

    def repo_info(self, repo_id, *, repo_type):
        assert repo_type == "dataset"
        if not self.exists:
            # The real error, as the Hub raises it, so the mirror's handling is
            # graded against the class it will meet rather than a stand-in.
            request = httpx.Request("GET", f"https://huggingface.co/api/datasets/{repo_id}")
            raise RepositoryNotFoundError(
                f"404: {repo_id}", response=httpx.Response(404, request=request)
            )
        return type("Info", (), {"private": self.private})()

    def list_repo_tree(self, repo_id, *, repo_type):
        assert repo_type == "dataset"
        return [type("Folder", (), {"path": name})() for name in sorted(self.present)]

    def upload_folder(self, *, folder_path, path_in_repo, repo_id, repo_type,
                      commit_message):
        assert repo_type == "dataset"
        self.uploads.append((Path(folder_path), path_in_repo))
        self.present.add(path_in_repo)


def _snapshot(root: Path, name: str, body: str = "{}") -> Path:
    snap = root / name
    snap.mkdir(parents=True)
    (snap / "decks.json").write_text(body)
    return snap


def test_every_snapshot_absent_from_the_dataset_is_sent_under_its_own_name(tmp_path):
    # The dataset is a copy of the snapshots root, directory for directory, so a
    # rebuild from it is a rebuild from the same sequence. Oldest first, which is
    # the order the build folds them in and the order a reader expects to see.
    _snapshot(tmp_path, "20260718T085655Z")
    _snapshot(tmp_path, "20260713T232944Z")
    hub = FakeHub()

    sent = mirror_snapshots(tmp_path, "someone/snapshots", hub)

    assert sent == ["20260713T232944Z", "20260718T085655Z"]
    assert hub.uploads == [
        (tmp_path / "20260713T232944Z", "20260713T232944Z"),
        (tmp_path / "20260718T085655Z", "20260718T085655Z"),
    ]


def test_a_hidden_directory_beside_the_snapshots_is_not_a_snapshot(tmp_path):
    # The same rule the build applies (`ingest._snapshot_dirs`): an interrupted
    # fetch's staging directory and the hand-made backup copies that sit beside the
    # snapshots are dot-prefixed, and the mirror is a copy of what the build reads.
    _snapshot(tmp_path, "20260713T232944Z")
    _snapshot(tmp_path, ".20260718T085655Z.partial-x1")
    _snapshot(tmp_path / ".pre-adoption-20261017", "20260713T232944Z")
    hub = FakeHub()

    assert mirror_snapshots(tmp_path, "someone/snapshots", hub) == ["20260713T232944Z"]


def test_a_snapshot_the_dataset_already_holds_is_not_sent_again(tmp_path):
    # Snapshots are append-only, so presence by name is the whole question: a
    # second run sends nothing, and a local edit to a mirrored snapshot (the
    # standing practice for a gate flag, ADR 0025) is never pushed over the copy.
    _snapshot(tmp_path, "20260713T232944Z", body="edited locally")
    _snapshot(tmp_path, "20260718T085655Z")
    hub = FakeHub(present=["20260713T232944Z"])

    first = mirror_snapshots(tmp_path, "someone/snapshots", hub)
    second = mirror_snapshots(tmp_path, "someone/snapshots", hub)

    assert first == ["20260718T085655Z"]
    assert second == []
    assert [name for _, name in hub.uploads] == ["20260718T085655Z"]


def test_a_dataset_that_does_not_exist_is_refused_before_anything_is_sent(tmp_path):
    # Creating the dataset is the owner's one-off step, as creating the Space is for
    # the deploy, so the mirror refuses with the remedy rather than creating one.
    # Refused before any upload, so nothing lands anywhere by accident.
    _snapshot(tmp_path, "20260713T232944Z")
    hub = FakeHub(exists=False)

    with pytest.raises(MirrorRefused) as exc:
        mirror_snapshots(tmp_path, "someone/snapshots", hub)

    assert "No dataset at someone/snapshots" in str(exc.value)
    assert "https://huggingface.co/new-dataset" in str(exc.value)
    assert hub.uploads == []


def test_a_public_dataset_is_refused_before_anything_is_sent(tmp_path):
    # The snapshots name people beside their decks, and a public dataset offers
    # every file for download from its Files tab, as the deploy doc says of a
    # public Space. A dataset that has gone public is refused, not written to.
    _snapshot(tmp_path, "20260713T232944Z")
    hub = FakeHub(private=False)

    with pytest.raises(MirrorRefused) as exc:
        mirror_snapshots(tmp_path, "someone/snapshots", hub)

    assert "someone/snapshots is public" in str(exc.value)
    assert hub.uploads == []


def _cli(tmp_path, monkeypatch, hub, dataset="someone/snapshots"):
    """Run the subcommand with the Hub client replaced by ``hub``."""
    import argparse

    from graph7ph.__main__ import _mirror

    monkeypatch.setattr("graph7ph.__main__.HfApi", lambda: hub)
    _mirror(argparse.Namespace(snapshots=tmp_path, dataset=dataset))


def test_the_command_reports_each_snapshot_it_sent(tmp_path, monkeypatch, capsys):
    # A round opens with this command, so its output is the round's first record of
    # what moved: one line per snapshot sent, naming where it went.
    _snapshot(tmp_path, "20260713T232944Z")
    _snapshot(tmp_path, "20260718T085655Z")

    _cli(tmp_path, monkeypatch, FakeHub(present=["20260713T232944Z"]))

    out = capsys.readouterr().out
    assert "Mirrored 20260718T085655Z to someone/snapshots" in out
    assert "20260713T232944Z" not in out


def test_the_command_says_so_when_the_dataset_is_already_current(
    tmp_path, monkeypatch, capsys
):
    # A second run is the common case, and silence would leave the reader unsure
    # whether it ran at all.
    _snapshot(tmp_path, "20260713T232944Z")

    _cli(tmp_path, monkeypatch, FakeHub(present=["20260713T232944Z"]))

    assert "Nothing to mirror: every snapshot is already in someone/snapshots" in (
        capsys.readouterr().out
    )


def test_the_command_turns_a_refusal_into_a_sentence_not_a_traceback(
    tmp_path, monkeypatch
):
    # The same abort shape as the build's: a sentence naming the remedy.
    _snapshot(tmp_path, "20260713T232944Z")

    with pytest.raises(SystemExit) as exc:
        _cli(tmp_path, monkeypatch, FakeHub(exists=False))

    assert "No dataset at someone/snapshots" in str(exc.value)
