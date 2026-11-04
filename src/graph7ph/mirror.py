"""Mirror the snapshots to a private Hugging Face dataset (issue #267).

Why the snapshots need a second copy, and where the command sits in a round, is
in ``docs/development.md`` under "Refreshing the data". This module sends every
snapshot directory the build would read that the dataset does not already hold
by name, and never deletes or overwrites, locally or remotely. The Hub client is
injected as ``api`` so the decision of what to send is tested without a network
round-trip, as :mod:`graph7ph.fetch` injects its downloader.
"""

from pathlib import Path

from huggingface_hub.errors import RepositoryNotFoundError

# The build's own listing, so the mirror sends exactly the directories the build
# reads and nothing beside them (hidden staging and backup copies). Imported under
# its private name rather than renamed: `ingest.py` is a build input, and renaming
# a helper there would stale the live bundle and skip the live-graph tests over a
# change that builds the same graph.
from graph7ph.ingest import _snapshot_dirs as snapshot_dirs


class MirrorRefused(Exception):
    """The dataset is not one the mirror will write to; the message names the remedy."""


def mirror_snapshots(snapshots_root: Path, dataset: str, api) -> list[str]:
    """Upload every snapshot absent from ``dataset``; return the names sent, oldest first.

    Refuses, before any upload, a dataset that does not exist (creating it is the
    owner's one-off step, as the Space is for the deploy) and one that is public:
    the snapshots name people beside their decks, and a public dataset offers every
    file for download.
    """
    try:
        info = api.repo_info(dataset, repo_type="dataset")
    except RepositoryNotFoundError:
        # The Hub answers 404 both to a dataset that is not there and to one this
        # login cannot see, so the remedy names both.
        raise MirrorRefused(
            f"No dataset at {dataset}, or this login cannot see it. Create it once, "
            f"private, at https://huggingface.co/new-dataset and log in with "
            f"`uvx --from huggingface_hub hf auth login`, then re-run."
        )
    if not info.private:
        raise MirrorRefused(
            f"{dataset} is public; make it private in its settings before mirroring."
        )
    present = {entry.path for entry in api.list_repo_tree(dataset, repo_type="dataset")}
    sent = []
    for snapshot in snapshot_dirs(snapshots_root):
        if snapshot.name in present:
            continue
        api.upload_folder(
            folder_path=snapshot,
            path_in_repo=snapshot.name,
            repo_id=dataset,
            repo_type="dataset",
            commit_message=f"Mirror snapshot {snapshot.name}",
        )
        sent.append(snapshot.name)
    return sent
