#!/bin/sh
# Deploy the explorer and its promoted graph artifact to a Hugging Face Space.
#
# Usage: scripts/deploy_space.sh <user>/<space>
#
# The deploy is assembled in a staging directory and uploaded as one commit, so
# the Space restarts once, onto a complete artifact. Staging is also what makes
# the allowlist real: only the files copied below exist to be uploaded, so
# nothing else in the working tree (.env, snapshots/, the ingestion reports) can
# ride along, and the pipeline's Moxfield credential stays in the pipeline
# (ADR 0003). `--delete "*"` clears anything the previous deploy left behind, so
# a stale index file cannot mix with a freshly built graph.
set -eu

SPACE="${1:?usage: scripts/deploy_space.sh <user>/<space>}"
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
# Defaults to the repo's own artifact, not the caller's cwd, so the graph and the
# code deployed beside it always come from the same tree.
DB="${GRAPH7PH_DB:-$ROOT/data/graph}"

# The artifact is a directory holding the database, so an existing directory is
# not proof of a graph: a half-cleared one would stage and ship a Space that dies
# at startup. The database inside it is what makes the bundle deployable. Its name
# is read from the package rather than copied here, so the engine swap changes
# graph7ph.db.DB_FILENAME alone and this guard follows it.
DB_FILENAME=$(cd "$ROOT" && uv run python -c 'from graph7ph.db import DB_FILENAME; print(DB_FILENAME)')

if [ ! -e "$DB/$DB_FILENAME" ]; then
    echo "No graph artifact at $DB; run 'uv run graph7ph build' first" >&2
    exit 1
fi

# A promoted artifact is checkpointed, so its write-ahead log is empty. A
# non-empty one means a build is running or crashed mid-write, and the data files
# alone are missing its tail: deploying that would ship a torn graph. Searched
# across the whole bundle rather than at a fixed path, because where the engine
# puts its log is the engine's business: Kùzu's directory database keeps a `.wal`
# inside itself, a single-file one leaves `<db>.wal` beside it (issue #47).
if [ -n "$(find "$DB" -name '*.wal' -size +0c)" ]; then
    echo "$DB has an uncheckpointed write-ahead log; rebuild before deploying" >&2
    exit 1
fi

# The Hub API, not the `hf upload` CLI: that command always calls create_repo
# first, and the Hub answers 402 to creating a free-tier Gradio Space even when
# the Space already exists. upload_folder touches no creation endpoint.
hf_python() { uvx --from huggingface_hub python -c "$1"; }

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

# The Space card (front matter pins the SDK and runtime) becomes the Space's
# README; the repo's own README stays a GitHub page.
cp "$ROOT/deploy/README.md" "$STAGE/README.md"
cp "$ROOT/app.py" "$ROOT/requirements.txt" "$STAGE/"
# The package sits at the Space's root, not under src/, and no pyproject goes up:
# a Space installs requirements.txt before the repo exists, so it can never pip
# install this project. Beside app.py it needs no install, because the app's
# working directory is on the import path.
cp -R "$ROOT/src/graph7ph" "$STAGE/graph7ph"
mkdir -p "$STAGE/data"
# The whole bundle verbatim, dotfiles included: what the engine keeps beside its
# database is the engine's business, so the copy takes everything rather than the
# names it expects. The empty-WAL check above is what keeps that copy a
# checkpointed, self-contained one. Staged under the default artifact name, which
# is what the Space resolves: it sets no GRAPH7PH_DB of its own.
cp -R "$DB" "$STAGE/data/graph"
find "$STAGE/graph7ph" -name __pycache__ -type d -exec rm -rf {} +

# Creating the Space is a one-off manual step, not this script's job: a Gradio
# Space on cpu-basic needs a PRO subscription (the Hub answers 402 otherwise),
# and free ZeroGPU is not an alternative, since its runtime kills any app with no
# @spaces.GPU function, which this one has no reason to have. What a given
# account may create keeps moving, so trust the Hub's own new-space form.
if ! SPACE="$SPACE" hf_python '
import os, sys
from huggingface_hub import repo_exists
sys.exit(0 if repo_exists(os.environ["SPACE"], repo_type="space") else 1)
' 2>/dev/null; then
    echo "No Space at $SPACE. Create it once at https://huggingface.co/new-space" >&2
    echo "(Gradio SDK, CPU Basic hardware, which needs PRO), then re-run." >&2
    exit 1
fi

# One commit: code and artifact land together, and large files go up as LFS.
# `delete_patterns` clears what a previous deploy left behind (.gitattributes is
# spared by the library, so LFS tracking survives).
STAGE="$STAGE" SPACE="$SPACE" hf_python '
import os
from huggingface_hub import HfApi
url = HfApi().upload_folder(
    folder_path=os.environ["STAGE"],
    repo_id=os.environ["SPACE"],
    repo_type="space",
    delete_patterns="*",
    commit_message="Deploy explorer and graph artifact",
)
print(url)
'

echo "Deployed to https://huggingface.co/spaces/$SPACE"
