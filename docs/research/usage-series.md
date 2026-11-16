# A usage series for www.7phgraph.com

Research for issue #252. How to get a number for how much the app is used, sampled over time, without
changing what a visitor gets. Findings and one recommendation. Nothing was built, and nothing on the
running app, the DNS or the Space was changed.

## The answer in short

Count visits inside the app's own process and append them to a file that outlives the Space. The
server already sees every page request at both addresses, including the visitors a browser beacon
loses to ad blockers, and it is the only place that can see which views people draw. A beacon was
tested and works, but it adds the page's first outside request, cannot see anything past the page
load, and makes the deploy's own check depend on a third party being up.

A worked example of what the series would hold. Someone follows a link from a Discord post to
`www.7phgraph.com`, opens the Cards tab, looks up one card and leaves. The record gets three lines:
a page load at that time with the referring site, and two view draws tied to the same visit
(`cards_drawing`, `draw_cards`). It does not get the card's name, the visitor's address, or the tab
switch, which never reaches the server.

Reach is cheap. Behaviour is half reachable: which views are drawn, yes; which tabs are opened, no.

## What this rests on

Three evidence bases, all dated 2026-11-16.

**Live measurements from the command line.**

- The zone's nameservers are `itzel.ns.cloudflare.com` and `yahir.ns.cloudflare.com`.
- `www.7phgraph.com` resolves as a CNAME to `hf.space`. Its response carries `server: uvicorn` and
  `x-proxied-replica`, and no `cf-ray`. Cloudflare still sees nothing at `www`.
- The apex answers `301` to `https://www.7phgraph.com/` with `server: cloudflare` and a `cf-ray`.
- The Space's own address, `alejandrofupi-7ph-graph.hf.space`, still answers `200` with the app.
- The Hub's record of the Space: created 2026-07-16, `cpu-basic`, one replica, both domains `READY`,
  no volume attached, sleeps after 48 hours without a visit (`gcTimeout: 172800`).
- The Space's run log, read through the Hub's documented log endpoint: 105 lines since the start of
  2026-11-15, **none of them a request**. Gradio writes no access log. The only trace of traffic in
  it is one traceback from a scanner asking for a WordPress path (`/gravitysmtp-settings`).
- The Hub's published API description (314 paths, [19]) has **no endpoint for a Space's visits**. The two
  nearest are a live metrics stream described as "such as current Zero-GPU usage" and the log stream
  above. The Analytics panel is reachable through the web page only.

**Three local probes**, the app launched from this checkout exactly as the Space launches it
(`launch_kwargs()`), driven by headless Chromium. They are throwaway and are not in the repo; each
finding below that says "measured" names what was run.

**Primary documentation**, cited inline by number, listed under [Sources](#sources).

**What could not be checked.** Whether a Cloudflare token exists on this machine: the check was
refused by the session's permission rules, so every Cloudflare account question is written out as
the query to run instead. And the Hub's Analytics panel, which has no API.

## The six questions

### 1. What does Cloudflare already hold for this zone?

**Unanswered for the account-side numbers; the retention windows are answered from the docs.**

What Cloudflare can hold is two things: DNS queries for the zone's names, and HTTP requests to the
apex, which is the only proxied name and serves nothing but the redirect.

Retention, from the docs rather than from the account:

- DNS analytics keep **31 days** of history at zone level on the Free plan, queryable 30 days at a
  time [6].
- The adaptive HTTP datasets "retain at least 31 days of data on every plan" [4].
- The aggregated HTTP datasets (daily and hourly roll-ups) "keep their own per-plan limits", and the
  docs send the reader to a `settings` query for the exact figure per zone [4][5]. This is the one
  place a longer apex history could exist. It is not stated here because no source states it for
  this zone.

So unless the daily roll-up reaches further, apex-redirect history older than mid-October 2026 is
already gone, and the window moves forward a day per day.

Neither number is a count of visits:

- **Apex redirects are a floor on nothing useful.** They count only people who typed or followed the
  bare domain. The published address is `www`, so a visit that starts there never touches the apex.
- **DNS queries for `www` are a trend, not a count.** A resolver that has the answer cached asks
  nothing, so many visitors behind one resolver are one query, while one visitor across several
  cache expiries is several, and scanners resolve the name too. It is neither a floor nor a ceiling.
  And it misses the `hf.space` address by construction.

The gap the ticket asks for, apex hits against direct-to-`www` traffic, cannot be quantified today:
neither side has a number. It becomes one division once the recommended log exists.

**To close it when a token exists.** The token is a read-only API token scoped to this zone
(`Zone -> Analytics -> Read`, as the ticket says; if the DNS dataset reports `enabled: false`, it
also needs `Zone -> DNS -> Read`). Verify with `GET /client/v4/user/tokens/verify`, then post these
to `https://api.cloudflare.com/client/v4/graphql`.

What the zone's plan actually retains, per dataset. Run this first, because it also returns the
field names the next two queries must use:

```graphql
query Retention($zoneTag: string) {
  viewer {
    zones(filter: { zoneTag: $zoneTag }) {
      settings {
        httpRequests1dGroups { enabled notOlderThan maxDuration availableFields }
        httpRequestsAdaptiveGroups { enabled notOlderThan maxDuration availableFields }
        dnsAnalyticsAdaptiveGroups { enabled notOlderThan maxDuration availableFields }
      }
    }
  }
}
```

`notOlderThan` is how far back a query can read, in seconds [5]. That is the retention figure.

Apex redirects per day, over the whole window the first query reports:

```graphql
query ApexRedirects($zoneTag: string, $from: Date, $to: Date) {
  viewer {
    zones(filter: { zoneTag: $zoneTag }) {
      httpRequestsAdaptiveGroups(
        filter: { date_geq: $from, date_leq: $to, clientRequestHTTPHost: "7phgraph.com" }
        orderBy: [date_ASC]
        limit: 1000
      ) {
        count
        dimensions { date edgeResponseStatus }
      }
    }
  }
}
```

DNS queries per day for the published name:

```graphql
query WwwLookups($zoneTag: string, $from: Date, $to: Date) {
  viewer {
    zones(filter: { zoneTag: $zoneTag }) {
      dnsAnalyticsAdaptiveGroups(
        filter: { date_geq: $from, date_leq: $to, queryName: "www.7phgraph.com" }
        orderBy: [date_ASC]
        limit: 1000
      ) {
        count
        dimensions { date }
      }
    }
  }
}
```

The dataset names and the `settings` fields are from the docs [4][5][6]. The dimension and filter
names in the last two are written from the published schema and were not run: check them against
`availableFields` from the first query before trusting an empty result.

What the answers would change: nothing in the recommendation. They would give a 31-day baseline of
apex redirects and lookups, useful once as the "before" for the series and as a second reading of
seasonality. The dashboard view that shows the same is the zone's Analytics tab for the HTTP side
and DNS -> Analytics for the lookups.

### 2. Beacon-based analytics

**Answered. A beacon works and is harmless to the reader, and it is not the recommendation.**

*What Cloudflare Web Analytics measures.* Page loads, by a script that "will fire on every
pageview". It does not need the
site proxied: a DNS-only site pastes a script tag by hand [1][2]. A DNS-only site counts against a
limit of 10 per account [3]. Beacon data is kept unsampled for 7 days, then "aggregated down to
around 10%", and "you can access data for the previous six months" [1]. It does not record query
strings [1].

*Custom events.* **Not supported.** The FAQ's answer is "Not yet, but we may add support for this
in the future" [1]. So question 5 cannot be answered inside a Cloudflare-only frame, as the ticket
suspected.

*Can it ride Gradio's `head`.* Yes. Gradio's own guide shows an analytics script passed as `head`
[8]. Measured: a script tag appended to `launch_kwargs()["head"]`, pointing at a stand-in beacon
host on another port, ran once per page load (`window.__beacon == 1`) and its one
`navigator.sendBeacon` arrived. This works because the page is rendered in the browser (ADR 0029).
Under server rendering the app's head never reached the page (same ADR).

*When the beacon is blocked or fails.* Measured, three cases:

| Case | Page | What the reader sees | The deploy's own check |
| --- | --- | --- | --- |
| Beacon host up | 7 tabs drawn | nothing | clean |
| Blocked by the browser (`ERR_BLOCKED_BY_CLIENT`) | 7 tabs drawn, no error thrown | nothing | not run |
| Beacon host down (`ERR_CONNECTION_REFUSED`) | 7 tabs drawn in 1.1 s | nothing | **fails**: "a request failed" |

The reader is never affected. The deploy is: `scripts/check_live_page.py` passes only with every
request answered, so a beacon host that is unreachable when a deploy finishes ends the run with the
failure banner and its rollback instructions, for an app that is fine. And Cloudflare states that
its beacon "is blocked by these services" [1], so the count is short by the share of visitors who
run a blocker. That share is unknown for this audience and nothing here measures it.

*Does it count anything useful in a single-page Gradio app.* Only the load. Cloudflare's beacon
reports again on "every route change" [1], and this app has none. Measured: clicking through
Archetypes, Cards and Hidden gems left `location.href` unchanged and `history.length` at 2. The
stand-in beacon saw two hits in total, both from the load. Shareable addresses were ruled out for
this app, so there is no route to change.

*Beacons that do support custom events.* Two were checked, since the comparison had to widen:

| | Cloudflare Web Analytics | GoatCounter | Umami Cloud (Hobby) |
| --- | --- | --- | --- |
| Price | free | free "for reasonable public usage" [11] | free |
| Custom events | no [1] | yes, `goatcounter.count({path, event: true})` [12] | yes [13] |
| Retention | six months [1] | not stated on the pages read | six months [13] |
| Read by API | yes, GraphQL [3] | export stated, API not stated on the pages read [11] | no on the free plan [13] |
| Cookies | not stated in the FAQ | none [11] | not checked |

An event-capable beacon could record a tab click, which nothing else can. It would mean writing
event code into the page for each thing worth counting, on top of the costs in the table above.

### 3. Proxying `www` through Cloudflare

**Rejected. It removes the record the Hub's domain check reads, and it cannot be tried without
breaking the no-change rule.**

The Hub accepts a custom domain by a CNAME "pointing your domain to `hf.space`", and its own
troubleshooting step is to look the CNAME up and "verify it points to `hf.space`" [7]. A proxied
CNAME at Cloudflare stops answering as a CNAME: proxied records "are flattened by default, as they
return Cloudflare anycast IPs" [9]. So an orange cloud replaces the one thing the Hub documents
checking. Whether the Hub re-checks a domain that is already `READY`, and whether its certificate
for the domain would renew, is not documented, and the only way to find out is to flip the record
on the live site.

The ticket also asks about the websocket transport. There is none to survive: on this Gradio the
page talks to the app by HTTP posts and a server-sent event stream (`serve.py` documents the
stream; the second probe saw the posts). The stream is the part a proxy can damage, by buffering
it, which is the failure `serve.py` already keeps compression away from.

One more reason, from this project's own record: the custom domain's proxy path is the fragile one.
On 2026-11-15 the public address broke while the Space's own address worked, because the two sit
behind different proxies (ADR 0029). Adding a third proxy to that path for a visit count is the
wrong trade.

### 4. Server-side counting

**Answered. The process sees enough, nothing it writes survives today, and there is a free place
for it to write that does.**

*What the process can observe.* Measured with a logging middleware placed in front of the app's own
(`serve.APP_KWARGS`):

- A browser's page load is `GET /`, `GET /theme.css` and 67 asset requests. A fetcher that runs no
  script (a link preview, a scanner, `curl`) makes `GET /` and nothing else. So a page served and a
  page actually opened by a browser are separable by what follows the first request.
- Launching the app makes two requests of its own before any visitor (`GET
  /gradio_api/startup-events`, `HEAD /`). They must not be counted.
- A page load starts no server function: it sent no `queue/join` post at all.
- **A tab switch sends nothing.** Archetypes and Cards produced zero requests; Hidden gems produced
  three, all static files for the graph widget.
- Drawing a view sends one post per step to `/gradio_api/queue/join`, carrying the function's
  number, a `session_hash` and the inputs. Picking a card on Cards sent three (`cards_drawing`,
  `draw_cards`, one unnamed follow-up). The `session_hash` is a random token made per page load, so
  it ties one visit's draws together without identifying anyone.
- The request headers locally were `accept`, `accept-encoding`, `connection`, `host` and
  `user-agent`. **Which headers the Hub's proxy adds or strips at the public address is not known**
  and cannot be seen from outside. `host` does arrive as the address the visitor used: ADR 0029
  records the app building its own root as `www.7phgraph.com` at one address and the `hf.space`
  name at the other. So the two addresses can be told apart, which no domain-level measure can do.
  Whether `referer` arrives is the open one.

*Does anything written inside the Space survive.* No. A Space's disk "is ephemeral, meaning its
content will be lost if your Space restarts or is stopped" [14]. This Space restarts on every
deploy and stops after 48 hours without a visit. The run log is no substitute: it covers the
current run only and holds no requests.

*Is there a free sink.* Two, both on the Hub, both inside the subscription the Space already needs
(a PRO account includes 1 TB of private storage, and the limits "apply to all types of repositories
(models, datasets, buckets, ...)" [16]):

- **A private Storage Bucket attached to the Space as a volume.** Buckets are "the recommended way
  to persist data in your Space", mounted "at the path you specify" as local files, read-write
  [14][15][17]. The app appends to a file. It needs no token and makes no network call of its own.
- **A private dataset, pushed by `CommitScheduler`.** Documented for exactly this ("user feedback on
  a deployed Space"): a background thread commits a local folder every few minutes, append-only,
  and on a failed upload "the error is logged and the scheduler retries at the next scheduled
  commit" [18]. It needs a write token stored in the Space as a secret.

*Or sampled from outside.* The only outside sample available is the run log, which holds nothing,
or the Analytics panel, which has no API. So the series has to be written by the app.

### 5. In-app behaviour

**Half reachable. Reach is cheap; behaviour is cheap only where it already touches the server.**

| What | Reachable without touching the page | How |
| --- | --- | --- |
| A page was served | yes | `GET /` |
| A browser opened it | yes | the requests that follow `GET /` |
| Which address was used | yes | `host` |
| Where the visitor came from | not known until deployed | `referer`, if the proxy passes it |
| Which views were drawn | yes | the function behind each `queue/join` |
| Where a visit ended | yes, as its last draw | `session_hash` |
| Which tab was opened | **no** | never leaves the browser |
| How long a visit lasted | no | nothing marks a tab closing |
| Unique people | no, by choice | would need an address or a cookie |

Tab opens are the one thing only script in the page can see, and that is an event-capable beacon
plus event code per tab (question 2). It is left out of the recommendation: a draw already says
which area was used, and a tab opened with nothing drawn in it is the weakest signal on the list.

**Where this meets the privacy stance.** The inputs of a draw contain the subject: the measured
post for a card lookup carried `["card_overview", "brainstorm", ...]`, and a pilot lookup carries a
pilot's name the same way. A log of which pilot was looked up, and when, is a record about people
in the community that the app has no reason to hold, and it sits badly beside protected visibility
and the rule that no named pilot goes in the spotlight. So the record keeps the function's name and
the view key, and never the inputs. For the same reason it keeps no visitor address, which also
rules out counting unique people. Visits and draws are what the series counts.

### 6. Cadence and where the series lives

**Captured continuously, read monthly, kept in the bucket.**

- **Sampling.** There is none. Every page load and every draw is one appended line at the moment it
  happens, with its time, so any cadence (daily, weekly, around an event) is a grouping chosen when
  the record is read, not a decision baked in when it is written. This matters for a series
  expected to be seasonal around events: a monthly sample cannot be cut finer afterwards, and a
  per-event log can.
- **What runs it.** The app. Nothing is scheduled anywhere else, so nothing can silently stop
  running.
- **Where it accumulates.** The private bucket, which exists apart from the Space and survives its
  restarts, redeploys and sleep. In six months it is still there with nothing having been done.
- **Reading it.** At the monthly refresh round, which already ends in an owner-run deploy: pull the
  bucket to a local folder outside the repo (`hf buckets sync`, [15]) and run a report over it.
  The report is for the maintainer and nothing of it appears in the app.
- **The cross-check.** The Hub's Analytics panel, read by eye at the same round. It is the only
  independent count of the same visits, and the series should land near it.

## Recommendation

**One option: an append-only visit log written by the app's own middleware to a private Storage
Bucket mounted into the Space.**

| | |
| --- | --- |
| Money | $0. Inside the PRO subscription the Space already requires [16]. |
| Owner's setup | Create one private bucket and attach it to the Space. One restart. |
| Build | One middleware beside the three in `serve.py`, a writer that never raises into a request, tests, and a report command. Small: about a day. |
| Cadence | Every event as it happens; read at the monthly refresh. |
| Where the series lives | The bucket. A local copy is pulled at each read. |
| What a visitor gets | The same bytes as today. No script, no outside request, no cookie. |

Why this one:

1. **It is the only option that changes nothing the visitor downloads.** The page stays free of
   outside requests, which the app has kept deliberately (self-hosted fonts, the icon in the head,
   the graph library served from the app).
2. **It counts everyone at both addresses.** No blocker loss, and the `hf.space` address that every
   domain-level measure misses is in the same file, labelled.
3. **It is the only one that reaches behaviour at all** without writing event code into the page.
4. **The record is the app's own and does not age out.** Both free beacons keep six months.

What the build must hold to, since "no impact" is the constraint that decides this:

- A failed or slow write never reaches a request. The line is queued and written off the request
  path, and a full queue drops lines rather than waiting.
- No inputs, no addresses, no user-agent strings beyond a coarse class. The view key and the
  function name only.
- With no bucket mounted (every local run, Colab, the suite) the middleware does nothing.
- The page and the config are byte-identical with and without it, held by a test.

Four things the build has to measure first, because no source settles them:

1. **Whether a `cpu-basic` Space accepts a bucket volume.** The docs state no hardware condition
   and do not say it is free of one [14][17].
2. **Whether appended lines survive the container being stopped**, at a redeploy and at the 48-hour
   sleep. A bucket is object storage behind a mount, and how often a write is flushed is not
   documented.
3. **Which headers reach the app at the public address**, `referer` above all.
4. **Which follow-up request marks a real browser** when assets are already cached on a repeat
   visit.

If 1 or 2 fails, the sink becomes the private dataset with `CommitScheduler` [18] and everything
else stands. That costs one write token stored in the Space, scoped to that one dataset, which is
why it is the fallback and not the choice: the Space carries no credential today.

## Rejected

| Option | Why |
| --- | --- |
| Read the Cloudflare traffic dashboard | It sees only the apex redirect. `www` is DNS-only. |
| Proxy `www` through Cloudflare | Breaches no-impact: it removes the CNAME the Hub's domain check reads, cannot be tested without changing live DNS, and adds a proxy to the path that already broke once (question 3). |
| Cloudflare Web Analytics beacon | No custom events, sees only the page load in an app with one address, six months of history, short by every blocker, the page's first outside request, and a deploy that fails when the beacon host is unreachable (question 2). Second choice if the server log proves unbuildable. |
| GoatCounter or Umami beacon | Same costs as above. They add tab clicks, at the price of event code in the page. Umami's free plan has no API and six months of history. |
| DNS query counts as the series | A trend, not a count, 31 days deep, blind to the `hf.space` address (question 1). |
| Sampling the Space's run log from outside | The log holds no requests and is wiped at every restart. Lossy by construction. |
| Printing visits to the run log and scraping it | Same loss: anything between the last scrape and a restart is gone, and the sampler would have to outrun a restart it cannot predict. |
| Reading the Analytics panel as the series | No API, three rolling totals and no dates, no referrers, a definition of "visit" the Hub does not publish, and a past outage where it stopped counting for days [10]. Kept as the cross-check only. |
| Recording inputs, addresses or unique visitors | Collides with the privacy stance (question 5). |
| Tab-open tracking | Needs script and event code in the page for the weakest signal on the list. |
| A paid persistent disk or upgraded hardware | Unneeded: the bucket is inside the current subscription. |

## Traffic to date

**About 3,000 visits in the Space's first 39 days, and no fresher figure reachable from here.**

That is the owner's reading of the Hub's Analytics panel on 2026-08-24 ("nearly 3000 visits"), for
a Space created on 2026-07-16. What it covers: visits to the Space as the Hub counts them, all time
to that date. The Hub does not publish what it counts as a visit, so whether it includes both
addresses, the owner's own visits, link previews or scanners is not known. It is the launch window,
so it says nothing about the level since.

No other population has a number. Apex redirects and DNS lookups need the Cloudflare token
(question 1). The run log holds one scanner probe in about 26 hours and no visits.

To bring it up to date: open the Space's Analytics panel on the Hub and note its three figures (all
time, last month, last week) with the date. Those three, read once now, are also the "before" the
new series should be checked against.

## Follow-up

- **Build the visit log**: the recommendation above, starting with the four measurements. Filed as
  #356.
- **Pull the Cloudflare baseline once** when a token exists: the three queries under question 1.
  Worth doing early because the window is 31 days and moving. No ticket: it is one command against
  an account only the owner can open.
- **Unrelated, seen in the run log**: a request for an unknown one-word path
  (`/gravitysmtp-settings`) raises a `KeyError` inside Gradio's page lookup and is logged as a full
  traceback. Not investigated here.

## Sources

1. Cloudflare Web Analytics FAQ: https://developers.cloudflare.com/web-analytics/faq/
2. Cloudflare Web Analytics, get started: https://developers.cloudflare.com/web-analytics/get-started/
3. Cloudflare Web Analytics limits: https://developers.cloudflare.com/web-analytics/limits/
4. Cloudflare GraphQL Analytics API limits: https://developers.cloudflare.com/analytics/graphql-api/limits/
5. Cloudflare GraphQL Analytics API, settings: https://developers.cloudflare.com/analytics/graphql-api/features/discovery/settings/
6. Cloudflare DNS analytics: https://developers.cloudflare.com/dns/additional-options/analytics/
7. Hugging Face, Spaces custom domain: https://huggingface.co/docs/hub/spaces-custom-domain
8. Gradio, custom CSS and JS: https://www.gradio.app/guides/custom-CSS-and-JS
9. Cloudflare DNS proxy status: https://developers.cloudflare.com/dns/proxy-status/
10. Hugging Face forum, Spaces analytics not counting (May 2025, fixed in two days): https://discuss.huggingface.co/t/potential-issue-with-spaces-analytics-not-working/154627
11. GoatCounter: https://www.goatcounter.com/
12. GoatCounter, events: https://www.goatcounter.com/help/events
13. Umami pricing: https://umami.is/pricing
14. Hugging Face, disk usage on Spaces: https://huggingface.co/docs/hub/spaces-storage
15. Hugging Face, Storage Buckets: https://huggingface.co/docs/hub/storage-buckets
16. Hugging Face, storage limits: https://huggingface.co/docs/hub/storage-limits
17. `huggingface_hub`, manage your Space (volumes): https://huggingface.co/docs/huggingface_hub/guides/manage-spaces
18. `huggingface_hub`, scheduled uploads: https://huggingface.co/docs/huggingface_hub/guides/upload#scheduled-uploads
19. Hugging Face Hub API description: https://huggingface.co/.well-known/openapi.json
