# Deploy

The deployed app is a Gradio explorer over a prebuilt artifact, decoupled from
the pipeline: it loads the promoted graph at startup and never fetches or builds.
`app.py` is the entrypoint. Every entrypoint resolves the artifact the same way,
from `GRAPH7PH_DB` (default `data/graph`), so pointing that at another path moves
the build's output and the app's input together.

Live at [www.7phgraph.com](https://www.7phgraph.com), which is the Space
`Alejandrofupi/7ph-graph` reached through a custom domain.

## Hugging Face Space

Hosting a Gradio Space costs a **PRO subscription** ($9/month): CPU Basic
hardware is free, but the Hub answers 402 to putting a Gradio app on it without
PRO. Free ZeroGPU hardware is not a way around that, because its runtime kills
any app that registers no `@spaces.GPU` function, and this one has no GPU work to
do. Colab is the free alternative, below.

### Visibility and the custom domain

The Space is **protected** rather than public: the Hub keeps the repository
private while the running app stays reachable. That is what the deploy needs,
because the bundle it ships carries the built graph and the ingestion reports,
and a public Space offers all of it for download from its Files tab. Protected
visibility is a PRO feature like the custom domain, so both are covered by the
subscription the Space already requires.

The address to publish is [www.7phgraph.com](https://www.7phgraph.com), a CNAME
to `hf.space` entered under the Space's Custom Domain setting. It has to be a
subdomain: the Hub confirms the domain by resolving a CNAME at the exact name
submitted, and an apex cannot carry one. Note that the Space's own
`alejandrofupi-7ph-graph.hf.space` address also keeps serving the app under
protected visibility, with no setting to disable it, so the custom domain is the
URL to hand out rather than the only one that answers.

The two addresses sit behind different proxies, and the page can draw at one and
not the other: on 2026-11-15 the Space's own address worked while the custom
domain showed a login form over "Failed to fetch" (#307). A `200` from `curl`
does not show it either, because the page is served and only then fails to fetch
its config. So the deploy script loads the custom domain in a browser itself,
as its last step: see [After the upload](#after-the-upload).

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
the working tree (`.env`, `snapshots/`) can reach the Space. The artifact is the
one thing staged whole rather than named file by file, so what it carries beside
the database ships with it: the ingestion reports (`ingest.json`,
`reconciliation.json`), which restate associations already public on Moxfield,
the corpus figures the FAQ quotes (`figures.json`, issue #264), and the build
stamp (`provenance.json`), a digest of the sources and a build time. Redeploy
after a refresh by re-running the script.

### Preflights

The script refuses a checkout whose shipped files no commit holds, four kinds of
unfit bundle, and a set of packages the Space could not install, all of them
before it uploads anything, so a refusal leaves the Space exactly as it was:

- **Uncommitted changes to what ships.** The deploy stages the working tree, so
  an edit nobody has committed under `src/graph7ph`, `app.py`,
  `pyproject.toml`, `uv.lock`, `deploy/` or `curation/` would go up with it,
  another session's half-made one included. The refusal names the files. Commit them, or
  wait for the session making them. `curation/` is not staged, but the graph is
  built from it, so it is held to the same rule. Every upload is then named
  `Deploy <commit>`, which is how to tell which code a Space is running.
- **No database at the artifact path.** Nothing to deploy: run
  `uv run graph7ph build`.
- **An uncheckpointed write-ahead log in the bundle.** A build is running or
  crashed mid-write, and the database alone is missing its tail. Let it finish,
  or rebuild.
- **Stale provenance.** The artifact was built from sources this tree has since
  moved past, so the Space would serve a graph built from code that no longer
  exists. Rebuild. This is the same staleness the no-regression gate refuses on
  (see [development.md](development.md)).
- **A failed baseline gate.** The bundle still answers, and answers differently
  from the checked-in oracle. The staleness check above cannot stand in for this,
  because its digest deliberately excludes the query modules the gate re-runs
  live: change an `ORDER BY` and the digest does not move, so the first three
  refusals all pass. The gate prints its own account to stderr before the script
  gives up, and it exits non-zero for four reasons of which a regression is only
  one, so read that before rebuilding: it also refuses when the oracle is missing
  or malformed, and when the database will not open. If the difference is
  intended, recapture the oracle as [development.md](development.md) describes.

- **A lock that has drifted, or packages the Space could not install.** The
  Space's `requirements.txt` is written from `uv.lock` at deploy time, so a lock
  that no longer matches `pyproject.toml` is refused: run `uv lock`, run the suite
  on the result, and commit. The file is then resolved together with the line a
  Gradio Space installs on its own (`gradio[oauth,mcp]` at the card's
  `sdk_version`), for the card's Python on Linux, and a disagreement between the
  two stops the deploy. Left to the Space, the same disagreement fails its build
  after the upload and takes the site down.

### After the upload

A Space acknowledges an upload and only then builds and starts it, and a running
Space is not yet a drawn page. So the script keeps going after the upload, and
none of what follows can take the upload back:

1. **It waits for the Space to run the new commit**, for up to 15 minutes, and
   stops if the Hub reports the build or the start as failed.
2. **It loads the published address in a headless browser.** That is the Space's
   custom domain, read from the Hub's own record of the Space, or the Space's
   `hf.space` address where it has none. The address passes only with the app's
   tabs on screen, every request answered and no error thrown
   ([scripts/check_live_page.py](../scripts/check_live_page.py), which can also
   be run by hand against any address).

A failure of either ends the run with a banner and a failing status. The banner
says what was seen and how to go back: the commit the Space ran before, read from
the name of its last deploy, and the three commands that check it out beside this
tree, rebuild its graph and deploy it with this same script.

The check needs Chromium, which the suite's browser tests need too:
`uv run playwright install chromium`, once.

The Space's own `hf.space` address is not loaded where there is a custom domain.
Gradio's client asks the Hub about the Space from any `hf.space` address, a
protected Space refuses an anonymous visitor, and that one refused request would
fail the check at an address nobody is sent to.

### What the Space installs

There is no `requirements.txt` in the repo. The deploy writes one from `uv.lock`
(`uv export`, the runtime packages only, each pinned), so the Space runs the
versions the suite ran on and a lock upgrade reaches it with nothing to edit by
hand. The pins matter most for Ladybug: a release can change the on-disk storage
format, and the app must read an artifact built by the same version. The file
stands alone rather than installing this project: a Space mounts only that file
and runs `pip` before the repo exists, so it cannot pip install the package. The
deploy instead stages the package at the Space's root, where Python imports it
without an install.

Two things are still typed by hand in the Space card
([deploy/README.md](../deploy/README.md)), and the suite holds both: `sdk_version`
to the lock's gradio, and `python_version` to the interpreter in
`.python-version`. A gradio upgrade therefore edits the card in the same commit.

### The usage record

The Space records its own use: each page it serves and each view a visitor draws,
one line each (issue #356, the reasoning in
[research/usage-series.md](research/usage-series.md)). Nothing else counts visits
to this app, and nothing a visitor downloads is different for it: no script, no
outside request, no cookie.

Two things on the Space make it record, both set by hand and neither in the repo:

- **A private Storage Bucket attached as a volume.** `Alejandrofupi/7ph-graph-usage`,
  mounted read-write at `/usage`. A Space's own disk is wiped at every restart; the
  bucket is not part of the Space and outlives it. Attached with
  `hf spaces volumes set Alejandrofupi/7ph-graph -v hf://buckets/Alejandrofupi/7ph-graph-usage:/usage`,
  which replaces any volumes already set, and listed with `hf spaces volumes ls`.
- **A variable naming the folder.** `GRAPH7PH_USAGE_DIR=/usage`, a variable and not
  a secret. With it unset, or naming a folder that is not there, the app records
  nothing and behaves exactly as it does otherwise: that is every local run, Colab
  and the suite.

The app holds no credential for this. It writes files to a folder.

What a line holds: the time, which address was used (the custom domain or the
Space's own), the site a visitor came from where the browser said, and for a draw
the function and view drawn and a token Gradio makes per page load, which ties one
visit's draws together. What it never holds: what was looked up (a draw's inputs
carry card and pilot names and are discarded), a visitor's address, or anything
that tells one person from another. So the record counts visits, not people.

Notes are saved every five minutes as one new file under `/usage/<year>/<month>/<day>/`.
A restart loses whatever was noted since the last save, at most five minutes. Each
start of the app writes a `start` line at its first save, so a restart shows in
the record, and so does a folder that takes no files: the run log then carries a
line opening `*** USAGE RECORD NOT WRITTEN`, and one opening `*** USAGE RECORD OFF`
when the variable names a folder that is not mounted. Neither stops the app.

Reading it is a step of the refresh round: see
[development.md](development.md#reading-the-usage-record).

## Colab

```python
!git clone https://github.com/AlejandroFuentePinero/7ph-graph.git
%cd 7ph-graph
!pip install .
# The artifact is not in the repo, so build one (fetch is a few MB, build a minute
# or two), or upload a prebuilt data/graph bundle next to this notebook instead.
!graph7ph fetch && graph7ph build

from graph7ph.app import build_app, launch_kwargs
from graph7ph.db import artifact_path
build_app(artifact_path()).launch(share=True, **launch_kwargs())
```
