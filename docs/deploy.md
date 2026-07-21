# Deploy

The deployed app is a Gradio explorer over a prebuilt artifact, decoupled from
the pipeline: it loads the promoted graph at startup and never fetches or builds.
`app.py` is the entrypoint. Every entrypoint resolves the artifact the same way,
from `GRAPH7PH_DB` (default `data/graph`), so pointing that at another path moves
the build's output and the app's input together.

Live at
[huggingface.co/spaces/Alejandrofupi/7ph-graph](https://huggingface.co/spaces/Alejandrofupi/7ph-graph).

## Hugging Face Space

Hosting a Gradio Space costs a **PRO subscription** ($9/month): CPU Basic
hardware is free, but the Hub answers 402 to putting a Gradio app on it without
PRO. Free ZeroGPU hardware is not a way around that, because its runtime kills
any app that registers no `@spaces.GPU` function, and this one has no GPU work to
do. Colab is the free alternative, below.

Create the Space once by hand at
[huggingface.co/new-space](https://huggingface.co/new-space) (Gradio SDK, CPU
Basic hardware). Then, to deploy:

```sh
uvx --from huggingface_hub hf auth login      # once
scripts/deploy_space.sh <user>/<space>        # code + artifact, nothing else
```

The script stages the exact files to deploy and uploads them as a single commit,
so the Space restarts once, onto a complete artifact, with anything left by a
previous deploy cleared. Only staged files can be uploaded, so nothing else in
the working tree (`.env`, `snapshots/`, the ingestion reports) can reach the
Space. Redeploy after a refresh by re-running the script.

[requirements.txt](../requirements.txt) is what the Space installs, and it stands
alone rather than installing this project: a Space mounts only that file and runs
`pip` before the repo exists, so it cannot pip install the package. The deploy
instead stages the package at the Space's root, where Python imports it without
an install. The pins are exact and must move with `uv.lock`: a Ladybug release
can change the on-disk storage format, and the app must read an artifact built by
the same version. `gradio` and `python_version` match what the Space card
declares (see [deploy/README.md](../deploy/README.md)).

## Colab

```python
!git clone https://github.com/AlejandroFuentePinero/7ph-graph.git
%cd 7ph-graph
!pip install .
# The artifact is not in the repo, so build one (fetch is a few MB, build a minute
# or two), or upload a prebuilt data/graph bundle next to this notebook instead.
!graph7ph fetch && graph7ph build

from graph7ph.app import build_app
from graph7ph.db import artifact_path
build_app(artifact_path()).launch(share=True)
```
