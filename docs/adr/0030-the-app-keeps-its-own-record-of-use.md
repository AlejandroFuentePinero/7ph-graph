# The app keeps its own record of use

## Context

Nothing counted visits to this app as a series. Issue #252 looked at every place a count could come from ([research/usage-series.md](../research/usage-series.md)):

- **The DNS provider sees no request.** `www.7phgraph.com` is a DNS-only CNAME to the Space, so Cloudflare logs nothing for it. Proxying it would remove the CNAME the Hub's domain check reads.
- **The Hub's Analytics panel has a visit count and nothing else.** No dates, no referrers, no API.
- **The Space's run log holds no requests.**
- **A beacon script was ruled out.** The page makes no outside request and the deploy's own check holds it to that; a beacon would be the first, and the page check fails when the beacon's host is down.

So the only observer left is the app's own process. What it can see was measured locally and then on the live Space (#356):

- **A page load is two requests the app can tell apart from everything else.** A browser asks for `/` and then, once its script runs, for `/theme.css`. A fetcher that runs no script asks for `/` alone. Two of those arrived within ten minutes of the first deploy.
- **Launching the app asks for `HEAD /` and its startup route**, never `GET /`, so the app's own start counts nothing.
- **A draw is a call the app can name**, with a token Gradio makes per page load that ties one visit's draws together. Switching a tab sends nothing.
- **At the public address the app is handed `host`, `referer` and `sec-fetch-dest`, and no forwarded address.** A visit that followed a link from another site was recorded with that site (2026-11-17).

Where the record could be kept was the open question, because a Space's own disk is wiped at every restart and the Space held no credential and wrote nothing. Measured on 2026-11-16 and 2026-11-17:

- **A `cpu-basic` Space accepts a private Storage Bucket as a volume**, with no bill inside the account's storage allowance.
- **Files written into it from the Space land.** This Space's first file arrived five minutes after the app started.
- **They outlive a restart and a redeploy.** Three files written before a restart of this Space were pulled afterwards into an empty folder with the same checksums, beside the new run's first file. The two sister apps measured the same, one of them across a redeploy (mtgo-insights#222, arena-insights#96).

## The decision

The app records its own use, and that record is the one thing the Space writes.

A listener in front of the app notes three things: a page served, the stylesheet a browser then asks for, and each call a visitor's pick makes. A writer on its own thread saves the notes every five minutes as one new file under the bucket's mount (`src/graph7ph/usage.py`). Three rules shape it:

- **A visitor is never affected.** The listener reads requests and touches no response. A note goes on a bounded queue and the request goes on; a full queue drops the note, and a folder that cannot be written costs notes, never a request.
- **Nothing a visitor looked up is kept.** A draw's inputs carry card and pilot names. They are read for the view key alone, kept only when it is exactly one of the app's own. No address and no user agent is kept, so the record counts visits and cannot count people.
- **Each batch is a new file.** A bucket is object storage: a file written once and closed is what it is built for, so nothing is ever appended to.

Recording is off unless `GRAPH7PH_USAGE_DIR` names a folder that exists, which is true on the Space and nowhere else. The Space holds no credential for it. The line format is shared with mtgo-insights and arena-insights, whose recorders came from this one: a field or a kind may be added, none is changed, so one reader reads all three buckets.

## Consequences

- The Space carries two settings that are not in the repo, a volume and a variable. `docs/deploy.md` says what they are and how to set them again.
- A restart loses the notes of at most the last five minutes.
- Unique visitors, tab views and time on the page cannot be counted. Each would need script in the page, which is a different decision.
- Every deploy adds one page load, because the deploy's check opens the site in a browser.
- The record is read by `graph7ph usage` over a local copy of the bucket (`docs/development.md`). Nothing of it appears in the app.
- `tests/test_usage.py` holds the three rules: the real page and config are the same bytes recorded or not, a pilot looked up in the real app is in no recorded line, and a folder that cannot be written never reaches a request.
- The report counts a visit as a page load whose script then ran (the stylesheet request). Counting the loads a browser asked for as a page was dropped on six days of real lines (#356): the two disagreed on 51 of 353 page loads, in both directions. The dropped reading missed 13 loads whose script ran, from browsers that do not say they are asking for a page, and counted 38 whose script never ran.

## What would reopen this

- **The Hub withdrawing or charging for a bucket volume on this hardware.** The fallback is the same recorder pushing to a private dataset with `CommitScheduler`, which puts one write token, scoped to that dataset, on the Space.
- **The Hub publishing its visit series through an API.** The page counts could then come from there, and this record would be kept for draws alone.
- **A question that needs people rather than visits.** That is a script in the page and a consent question, not a change to this recorder.
