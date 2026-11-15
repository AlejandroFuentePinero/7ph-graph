#!/bin/sh
# Deploy the explorer and its promoted graph artifact to a Hugging Face Space.
#
# Usage: scripts/deploy_space.sh <user>/<space>
#
# The deploy is assembled in a staging directory and uploaded as one commit, so
# the Space restarts once, onto a complete artifact. Staging is also what makes
# the allowlist real: only the files copied below exist to be uploaded, so
# nothing else in the working tree (.env, snapshots/) can ride along, and the
# pipeline's Moxfield credential stays in the pipeline (ADR 0003). The artifact
# is the one exception to naming files individually: it is copied whole, so the
# ingestion reports it carries since #47 (ingest.json, reconciliation.json) do
# ship to the Space. They restate associations already public on Moxfield. The
# build stamp added by #55 (provenance.json) ships too, carrying a digest of the
# sources and a build time, neither of which says anything about anyone.
# The `delete_patterns="*"` argument on the upload clears anything the previous deploy
# left behind, so a stale index file cannot mix with a freshly built graph.
set -eu

SPACE="${1:?usage: scripts/deploy_space.sh <user>/<space>}"
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
# Defaults to the repo's own artifact, not the caller's cwd, so the graph and the
# code deployed beside it always come from the same tree.
DB="${GRAPH7PH_DB:-$ROOT/data/graph}"

# The deploy stages the working tree, so what ships has to be what a commit holds:
# several sessions edit this checkout at once, and without this another session's
# half-made edit under src/ goes up with whatever deploy runs next (issue #299).
# The staleness guard below cannot see that, because the bundle was built from the
# same tree and the two agree. The commit is then named on the upload, since
# nothing else on the Space says which code it runs.
#
# Asked only of what reaches the Space, plus `curation/`: that is not staged, but
# the graph is built from it and the staleness guard holds the bundle to this
# tree's copy, so with it committed the commit named accounts for the graph as
# well as the code. A doc being edited holds nothing back. Untracked files count,
# because a new module under src/ would be copied up with the rest.
UNCOMMITTED=$(cd "$ROOT" && git status --porcelain -- src/graph7ph app.py pyproject.toml uv.lock deploy curation)
if [ -n "$UNCOMMITTED" ]; then
    echo "Refusing to deploy: what ships has changes no commit holds:" >&2
    printf '%s\n' "$UNCOMMITTED" | sed 's/^/  /' >&2
    echo "Commit them, or wait for the session making them, and re-run." >&2
    exit 1
fi
COMMIT=$(cd "$ROOT" && git rev-parse HEAD)

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

# A promoted artifact is checkpointed, and Ladybug folds the write-ahead log in
# and removes it once the last Connection to the database closes, so a settled
# bundle carries no `.wal` at all. Note it is the Connection closing that settles
# it and not `Database.close()`, which is why the build context-manages both
# (graph7ph.build, and the ordering test in tests/test_build.py). A reader does
# not create one, so a running local app does not trip this guard.
# One that is there means a build is running or crashed mid-write,
# and the database alone is missing its tail: deploying that would ship a torn
# graph. Presence is the signal, not size: an interrupted build can leave an empty
# log before it has written a byte, so an emptiness test would ship exactly the
# torn artifact this guards against (issue #50). Searched across the whole bundle
# rather than at a fixed path, because where the engine puts its log is the
# engine's business and this guard must not have to be edited when that changes:
# today's leaves `<db>.wal` beside the database. `cp -R "$DB"` below ships
# whatever the engine left in there, so the search has to cover everything the
# copy does, which a fixed path would not.
if [ -n "$(find "$DB" -name '*.wal')" ]; then
    echo "$DB has an uncheckpointed write-ahead log; rebuild before deploying" >&2
    exit 1
fi

# The third way a bundle can be unfit to ship, and the only one that leaves a
# Space that starts and answers: an artifact built from sources this tree has
# moved past. Change an ingest or curation module, skip the rebuild, deploy, and
# the Space serves a graph built from code that no longer exists, beside the code
# that replaced it. The judgement is the package's, not this script's: it is the
# same `staleness` the baseline gate refuses on (issue #55), asked here so the
# deploy path is held to the grading path's standard.
#
# Run from the repo root, because the digest folds in `curation/pilots.toml` at
# its relative default, which is why the artifact has to be named absolutely: a
# relative GRAPH7PH_DB is the caller's, and the probe is not standing where the
# caller stood. Its own variable rather than reassigning DB, so that the guards
# above and the `cp -R` below go on naming the artifact the way the caller did.
ABS_DB=$(CDPATH= cd -- "$DB" && pwd)
STALE=$(cd "$ROOT" && DB="$ABS_DB" uv run python -c '
import os
from pathlib import Path
from graph7ph.provenance import staleness
print(staleness(Path(os.environ["DB"])) or "", end="")
')

if [ -n "$STALE" ]; then
    echo "Refusing to deploy: $STALE" >&2
    exit 1
fi

# The fourth way, and the only one none of the three above can see: a bundle that
# still answers, and answers differently. The staleness probe cannot stand in for
# this, because `BUILD_INPUTS` (graph7ph.provenance) deliberately excludes
# `query.py`, `render.py` and `explore.py` on the grounds that the gate re-runs
# those live. So a changed `ORDER BY`, exactly the defect issue #56 was about,
# leaves the digest untouched and clears all three guards above (issue #71).
#
# Same `$ROOT` and same absolute artifact as the staleness probe, for two separate
# reasons. The oracle's path (`baseline.BASELINE_PATH`) is relative, so it is only
# found from the repo root; and `--db` defaults to the package's own artifact path
# rather than to the `$DB` this deploy is about to ship, so an absolute one is what
# points the gate at this bundle. Either mistake grades a graph nobody asked about,
# and the likelier outcome of that is a pass.
#
# Here with the other three and before `mktemp -d` below, so a refusal stages
# nothing.
#
# The gate's own account follows it to stderr, where the other three guards speak
# and where a refusal is still legible with stdout redirected to a log. Without
# that it prints the differences to stdout and the line below is left accusing a
# bundle with none of the evidence attached. The line below is deliberately not a
# verdict on the graph, because the gate exits non-zero for four different reasons
# and only one of them is a regression: it also refuses when the oracle is missing
# or malformed, and when the database will not open. Saying which is the gate's
# business, and it has just said it.
if ! (cd "$ROOT" && uv run graph7ph baseline --db "$ABS_DB" >&2); then
    echo "Refusing to deploy: $DB did not clear the baseline gate, above" >&2
    exit 1
fi

# The Hub API, not the `hf upload` CLI: that command always calls create_repo
# first, and the Hub answers 402 to creating a free-tier Gradio Space even when
# the Space already exists. upload_folder touches no creation endpoint.
hf_python() { uvx --from huggingface_hub python -c "$1"; }

STAGE=$(mktemp -d)
SPACE_INSTALL=$(mktemp)
trap 'rm -rf "$STAGE" "$SPACE_INSTALL"' EXIT

# The Space card (front matter pins the SDK and runtime) becomes the Space's
# README; the repo's own README stays a GitHub page.
cp "$ROOT/deploy/README.md" "$STAGE/README.md"
cp "$ROOT/app.py" "$STAGE/"
# What the Space installs is the runtime half of uv.lock, every package pinned, so
# it runs the versions the suite ran on: the Space resolves this file fresh on
# every build, and a package left out of it is whatever was newest that day (37 of
# 73 differed on 2026-11-15, when the file pinned five by hand; issue #298). The
# pins matter most for Ladybug, since a release can change the on-disk storage
# format and the app has to read an artifact the same version built. The dev group
# stays behind, and so does the project itself, which the Space cannot install
# (below). pydantic and requests go up although only fetch and build use them:
# they are the project's runtime dependencies, and two unused packages cost less
# than a second list to keep. `--locked`, as CI syncs: a tree whose pyproject.toml
# has drifted from uv.lock is refused here rather than exported stale.
if ! (cd "$ROOT" && uv export --quiet --locked --no-dev --no-emit-project --no-hashes) \
    > "$STAGE/requirements.txt"; then
    echo "Refusing to deploy: uv.lock does not match pyproject.toml, above. Run" >&2
    echo "'uv lock', run the suite on the result, commit, and re-run." >&2
    exit 1
fi
# The Space installs that file together with a line of its own, and its build
# refuses the lot when the two disagree. That refusal comes after the upload, so it
# leaves the site down until a build succeeds: arena-insights' was on 2026-11-12,
# when its lock pinned a pydantic the `mcp` extra of gradio caps. Resolved here
# first, for the card's Python on the Space's Linux, the same disagreement stops
# the deploy with the Space as it was. The Space's line is the one a Gradio Space's
# build log shows, copied from mtgo-insights' deploy, so a build that fails where
# this passed means the line changed there, and this copy is what to correct.
CARD="$ROOT/deploy/README.md"
SDK=$(sed -n 's/^sdk_version: *//p' "$CARD")
PYTHON=$(sed -n 's/^python_version: *"\(.*\)"$/\1/p' "$CARD")
printf '%s\n' "gradio[oauth,mcp]==$SDK" "uvicorn>=0.14.0" "websockets>=10.4" spaces \
    > "$SPACE_INSTALL"
if ! uv pip compile --quiet --python-version "$PYTHON" \
    --python-platform x86_64-unknown-linux-gnu \
    "$STAGE/requirements.txt" "$SPACE_INSTALL" > /dev/null; then
    echo "Refusing to deploy: the Space could not install these packages, above." >&2
    echo "Nothing on it has been touched. Correct the lock and re-run." >&2
    exit 1
fi
# The package sits at the Space's root, not under src/, and no pyproject goes up:
# a Space installs requirements.txt before the repo exists, so it can never pip
# install this project. Beside app.py it needs no install, because the app's
# working directory is on the import path.
cp -R "$ROOT/src/graph7ph" "$STAGE/graph7ph"
mkdir -p "$STAGE/data"
# The whole bundle verbatim, dotfiles included: what the engine keeps beside its
# database is the engine's business, so the copy takes everything rather than the
# names it expects. The write-ahead-log check above is what keeps that copy a
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
STAGE="$STAGE" SPACE="$SPACE" COMMIT="$COMMIT" hf_python '
import os
from huggingface_hub import HfApi
url = HfApi().upload_folder(
    folder_path=os.environ["STAGE"],
    repo_id=os.environ["SPACE"],
    repo_type="space",
    delete_patterns="*",
    commit_message="Deploy " + os.environ["COMMIT"],
)
print(url)
'

echo "Deployed $COMMIT to https://huggingface.co/spaces/$SPACE"
