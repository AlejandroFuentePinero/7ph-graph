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
DB="${GRAPH7PH_DB:-$ROOT/data/graph.kuzu}"

if [ ! -d "$DB" ]; then
    echo "No graph artifact at $DB; run 'uv run graph7ph build' first" >&2
    exit 1
fi

# A promoted artifact is checkpointed, so its write-ahead log is empty. A
# non-empty one means a build is running or crashed mid-write, and the data files
# alone are missing its tail: deploying that would ship a torn graph.
if [ -s "$DB/.wal" ]; then
    echo "$DB has an uncheckpointed write-ahead log; rebuild before deploying" >&2
    exit 1
fi

hf() { uvx --from huggingface_hub hf "$@"; }

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

# The Space card (front matter pins the SDK and runtime) becomes the Space's
# README; the repo's own README stays a GitHub page.
cp "$ROOT/deploy/README.md" "$STAGE/README.md"
cp "$ROOT/app.py" "$ROOT/requirements.txt" "$ROOT/pyproject.toml" "$STAGE/"
cp -R "$ROOT/src" "$STAGE/src"
mkdir -p "$STAGE/data"
# Verbatim, dotfiles included: Kùzu opens a read-only database only if its .lock
# is present, and only if .shadow and .wal are both present or both absent. The
# empty-WAL check above is what keeps the copy a checkpointed, self-contained one.
cp -R "$DB" "$STAGE/data/graph.kuzu"
find "$STAGE/src" -name __pycache__ -type d -exec rm -rf {} +

# A Space cannot be created by `hf upload` (it has no --space-sdk), so the first
# deploy would fail against a Space that does not exist yet.
hf repos create "$SPACE" --type space --space-sdk gradio --exist-ok >/dev/null

# One commit: code and artifact land together, and large files go up as LFS.
hf upload "$SPACE" "$STAGE" . --repo-type=space --delete "*" \
    --commit-message="Deploy explorer and graph artifact"

echo "Deployed to https://huggingface.co/spaces/$SPACE"
