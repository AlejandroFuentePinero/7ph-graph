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
uv run graph7ph build   # load the latest snapshot into data/graph
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
the previous one retained at `data/graph.backup` for an instant rollback
(ADR 0003). A build that flags dropped ids or changed historical facts says so
and writes the detail to `data/graph/ingest.json`.

Fetch and build are the only steps that talk upstream. Any credential they need
belongs to this pipeline environment (a local `.env`, which is gitignored, or the
CI secret store later): it is never read by the app and never deployed with it.

## Deploy

The deployed app is a Gradio explorer over a prebuilt artifact, decoupled from
the pipeline: it loads the promoted graph at startup and never fetches or builds.
`app.py` is the entrypoint. Every entrypoint resolves the artifact the same way,
from `GRAPH7PH_DB` (default `data/graph`), so pointing that at another path
moves the build's output and the app's input together.

Live at
[huggingface.co/spaces/Alejandrofupi/7ph-graph](https://huggingface.co/spaces/Alejandrofupi/7ph-graph).

Hosting a Gradio Space costs a **PRO subscription** ($9/month): CPU Basic
hardware is free, but the Hub answers 402 to putting a Gradio app on it without
PRO. Free ZeroGPU hardware is not a way around that, because its runtime kills
any app that registers no `@spaces.GPU` function, and this one has no GPU work to
do. Colab is the free alternative, below.

Create the Space once by hand at [huggingface.co/new-space](https://huggingface.co/new-space)
(Gradio SDK, CPU Basic hardware). Then, to deploy:

```sh
uvx --from huggingface_hub hf auth login      # once
scripts/deploy_space.sh <user>/<space>        # code + artifact, nothing else
```

The script stages the exact files to deploy and uploads them as a single commit,
so the Space restarts once, onto a complete artifact, with anything left by a
previous deploy cleared. Only staged files can be uploaded, so nothing else in
the working tree (`.env`, `snapshots/`, the ingestion reports) can reach the
Space. Redeploy after a refresh by re-running the script.

[requirements.txt](requirements.txt) is what the Space installs, and it stands
alone rather than installing this project: a Space mounts only that file and runs
`pip` before the repo exists, so it cannot pip install the package. The deploy
instead stages the package at the Space's root, where Python imports it without
an install. The pins are exact and must move with `uv.lock`: a Ladybug release can
change the on-disk storage format, and the app must read an artifact built by the
same version. `gradio` and `python_version` match what the Space card declares
(see [deploy/README.md](deploy/README.md)).

On Colab instead:

```python
!git clone https://github.com/AlejandroFuentePinero/graph-7ph.git
%cd graph-7ph
!pip install -r requirements.txt
# The artifact is not in the repo, so build one (fetch is a few MB, build a minute
# or two), or upload a prebuilt data/graph bundle next to this notebook instead.
!graph7ph fetch && graph7ph build

from graph7ph.app import build_app
from graph7ph.db import artifact_path
build_app(artifact_path()).launch(share=True)
```

## Tests

```sh
uv run pytest
```

## No-regression gate

`baseline/subgraphs.json` records what every query entry point answers, plus the
18 table counts and the dropdown catalogues, captured from the built graph. It is
the oracle the Ladybug migration is graded against (issue #45):

```sh
uv run graph7ph baseline            # grade the built graph, non-zero on any difference
uv run graph7ph baseline --capture  # rewrite the baseline from the built graph
```

Rows are compared under each query's own rule: order-exact where the query sorts
before emitting, order-insensitive for the two that do not, and floats within a
tolerance, because aggregation order changes the last bits of a mean between
engines.
