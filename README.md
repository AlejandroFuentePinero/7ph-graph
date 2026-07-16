# 7ph-graph

A knowledge graph of the Australian 7 Point Highlander (7PH) Magic: The Gathering
metagame. See [CONTEXT.md](CONTEXT.md) for the domain language and
[docs/adr/](docs/adr/) for the design decisions.

## Setup

```sh
uv sync
```

## Usage

```sh
uv run graph7ph fetch   # download 7phstats data into snapshots/<timestamp>/
uv run graph7ph build   # load the latest snapshot into data/graph.kuzu
uv run graph7ph app     # launch the Gradio explorer
```

The explorer drives the query spine from simple controls: pick a view (a pilot's
neighbourhood or archetype affinity, a card's usage or co-occurrence, or hidden
gems), set filters, and see the result as an
interactive graph. Click a node for its details; a deck links out to Moxfield. A
result too large to read is not drawn or truncated: the app reports its node-kind
distribution and asks you to narrow the query (the threshold is a single config
constant, `graph7ph.explore.RENDER_THRESHOLD`).

## Refresh

`fetch` then `build` is the whole refresh: each fetch is kept as an append-only
snapshot, the build unions every snapshot and gates the newest against what the
graph already holds, and the new artifact is promoted only if it validates, with
the previous one retained at `data/graph.kuzu.backup` for an instant rollback
(ADR 0003). A build that flags dropped ids or changed historical facts says so
and writes the detail to `data/graph.kuzu.ingest.json`.

Fetch and build are the only steps that talk upstream. Any credential they need
belongs to this pipeline environment (a local `.env`, which is gitignored, or the
CI secret store later): it is never read by the app and never deployed with it.

## Deploy

The deployed app is a Gradio explorer over a prebuilt artifact, decoupled from
the pipeline: it loads the promoted graph at startup and never fetches or builds.
`app.py` is the entrypoint. Every entrypoint resolves the artifact the same way,
from `GRAPH7PH_DB` (default `data/graph.kuzu`), so pointing that at another path
moves the build's output and the app's input together.

To a free Hugging Face Space (CPU basic is enough; the graph is ~50 MB and is
served from the Space's own filesystem):

```sh
uvx --from huggingface_hub hf auth login      # once
scripts/deploy_space.sh <user>/<space>        # code + artifact, nothing else
```

The script creates the Space if it does not exist, stages the exact files to
deploy, and uploads them as a single commit, so the Space restarts once, onto a
complete artifact, with anything left by a previous deploy cleared. Only staged
files can be uploaded, so nothing else in the working tree (`.env`, `snapshots/`,
the ingestion reports) can reach the Space. Redeploy after a refresh by
re-running the script.

[requirements.txt](requirements.txt) is what the Space installs. It pins `kuzu`
exactly, because a Kùzu release can change the on-disk storage format and the app
must read an artifact built by the same version, and it pins `gradio` to the
version the Space card declares. Keep both in step with `uv.lock` (bump the card
in [deploy/README.md](deploy/README.md) alongside them).

On Colab instead:

```python
!git clone https://github.com/AlejandroFuentePinero/graph-7ph.git
%cd graph-7ph
!pip install -r requirements.txt
# The artifact is not in the repo, so build one (fetch is a few MB, build a minute
# or two), or upload a prebuilt data/graph.kuzu next to this notebook instead.
!graph7ph fetch && graph7ph build

from graph7ph.app import build_app
from graph7ph.db import artifact_path
build_app(artifact_path()).launch(share=True)
```

## Tests

```sh
uv run pytest
```
