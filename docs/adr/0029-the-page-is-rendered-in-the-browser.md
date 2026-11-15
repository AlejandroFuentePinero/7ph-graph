# The page is rendered in the browser

## Context

Gradio can build the page on the server before sending it (server-side rendering), and a Hugging Face Space asks for that by default (`GRADIO_SSR_MODE`). Through Gradio 5.50 the Space ran that way, and it was kept on purpose: measured for #93 on a 5 Mbps link, the ready-made page put first content on screen at 0.34 s against 0.70 s without it.

Issue #307 moved the app to Gradio 6.32. The Space built and started, the Space's own address worked, the suite was green, and the public address, https://www.7phgraph.com, drew a login form over "Failed to fetch".

What was measured on 2026-11-15:

- **Why the public address broke.** Gradio 6's renderer is a Node process in front of the app. It writes the app's own address into the page and builds it as the scheme in the `x-forwarded-proto` header, or `http` when there is none. The Space's own address reports `https`. The custom domain's proxy does not: the live config answers `"root": "https://alejandrofupi-7ph-graph.hf.space"` at one address and `"root": "http://www.7phgraph.com"` at the other. An `https` page told to fetch its config over `http` is refused by the browser as mixed content.
- **Nothing in the app can correct it.** The renderer runs in front of the app, so a middleware never sees the request first, and the head the app passes is not in the page the renderer writes. Both were tried and reverted in #307.
- **Server rendering no longer puts content on screen sooner.** The page Gradio 6 renders on the server is 134 KB and does not contain the app's title or any of its text. On a link throttled to 5 Mbps with 40 ms latency (Chromium, cache off, median of five loads, the staged app installed as the Space installs it):

| | in the browser | on the server |
| --- | --- | --- |
| first paint | 0.82 s | 1.78 s |
| title on screen | 2.91 s | 4.19 s |
| first chart drawn | 5.27 s | 5.91 s |

The throttle was engaged: the loads ran at 429 to 483 kB/s under a 625 kB/s cap, against about 2,500 kB/s without it. Unthrottled, the title is on screen at 0.43 s in the browser and 1.57 s on the server.

## The decision

Every launch renders in the browser: `launch_kwargs` passes `ssr_mode=False`, which outranks what the Space asks for. This is the design, not a workaround waiting for a fix. On Gradio 6 it is the mode that works at the public address and the faster of the two.

## Consequences

- The page shell carries the widget tree: 1,072 KB, sent as 223 KB by the app's own compression (`serve.py`).
- No Node process runs on the Space.
- `tests/test_behind_https.py` holds the decision. It stands a proxy that ends TLS and reports no scheme in front of the app, launched as the Space launches it, and loads the page in a browser. Without the setting it fails with the refusal production showed.
- The page-style tests run once, in the mode that ships. They used to run in both.
- The graph library stays under `/static/graph7ph`, a prefix server rendering required (#97) and nothing requires now.

## What would reopen this

Both of these, not either: a Gradio whose server-rendered page carries the app's content again, and a correct scheme at the public address, from Gradio or from the proxy. Three things to know on that day:

- Run `tests/test_behind_https.py` with the setting removed before anything else.
- Under server rendering only paths that open with one of Gradio's own routes (`gradio.routes.INTERNAL_ROUTES`) reach the app, so an asset served from anywhere else answers 404 on the Space while every local run is green.
- The page's stylesheet meets Gradio's in a different order there, and the title's size was lost only in that mode. The page-style tests would need their second run back.
