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
uv run graph7ph build   # load the latest snapshot into graph.kuzu
uv run graph7ph app     # launch the Gradio explorer
```

The explorer renders a chosen pilot's neighbourhood (their decks and the cards in
them) as an interactive graph.

## Tests

```sh
uv run pytest
```
