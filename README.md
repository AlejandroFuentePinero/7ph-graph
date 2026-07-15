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

## Tests

```sh
uv run pytest
```
