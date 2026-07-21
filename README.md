# 7 Point Highlander Graph

A knowledge graph of the Australian 7 Point Highlander (7PH) Magic: The Gathering
metagame. It links events, pilots, decks, and cards down to card attributes, for
exploration and analytics.

**[Try the live explorer →](https://huggingface.co/spaces/Alejandrofupi/7ph-graph)**

## What it does

Pick a view (a pilot's neighbourhood or archetype affinity, a card's usage or
co-occurrence, or hidden gems), set filters, and see the result as an interactive
graph. Click a node for its details; a deck links out to Moxfield.

A result too large to read is neither drawn nor truncated: the app reports its
node-kind distribution and asks you to narrow the query.

## Quickstart

Requires Python 3.11-3.14 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run graph7ph fetch   # download 7phstats data into snapshots/<timestamp>/
uv run graph7ph build   # load the latest snapshot into data/graph
uv run graph7ph app     # launch the explorer
```

The graph artifact is not committed, so `fetch` and `build` are what create it.
Fetch is a few MB; build takes a minute or two.

## How it works

`fetch` and `build` are the only steps that talk upstream. The app reads a
prebuilt artifact and never fetches or builds, so a deployed instance carries no
upstream credential. Every entrypoint resolves the artifact from `GRAPH7PH_DB`
(default `data/graph`).

Builds are append-only over snapshots and gated: a new artifact is promoted only
if it validates, with the previous one retained for rollback.

## Documentation

| | |
| --- | --- |
| [CONTEXT.md](CONTEXT.md) | Domain language: pilot, deck, archetype, points, era |
| [docs/adr/](docs/adr/) | Architecture decision records |
| [docs/development.md](docs/development.md) | Refreshing data, tests, the no-regression gate |
| [docs/deploy.md](docs/deploy.md) | Deploying to a Hugging Face Space or Colab |

## Data

Metagame data comes from [7phstats](https://7phstats.com); decklists link out to
[Moxfield](https://moxfield.com). This project is free and non-commercial, per
Moxfield's API terms. It is unofficial and not affiliated with either service, or
with Wizards of the Coast.

## License

[MIT](LICENSE). This covers the code in this repository; the upstream data it
fetches remains subject to the terms above.
