---
name: live-crawl
description: Run a site's whole crawl against the live portal exactly as foundry runs it, from your stealth browser, and fix what fails - rate limits, firewall challenges, refused requests, wrong parsing. Use before every submit, and when a test scrape or repair job reports errors.
---

# Crawl the live portal

```bash
"$FOUNDRY_PYTHON" -m foundry.core live <site key> --browser "$FOUNDRY_BROWSER"
```

This is the crawl foundry runs every six hours and for every test scrape: the spider from your clone, on the kit
foundry is deployed with, every request sent from a stealth browser on a US residential address. It prints `PASS`
or `FAIL`, the counts, each error with the status the portal answered (`url (429): ...`), and two listings. It must
pass before you submit; making it pass is your job.

## How requests are sent

Requests go at the spider's `DOWNLOAD_DELAY` and `CONCURRENT_REQUESTS_PER_DOMAIN` (or the portal's `delay` in
`buyer.toml`, which wins, with one request at a time). When the portal answers with a rate limit, the crawl slows
that site down, waits what it asks, and sends the request again, then speeds back up as requests go through; the
output says how far it had to slow.

Each request runs as a `fetch` from a page on the portal, so responses are what plain HTTP returns and the spider
needs no browser code. `fetch` drops the `Cookie`, `User-Agent` and `Referer` headers a spider sets; the page sends
its own, with the browser's cookies. Other headers (`Accept`, a tenant id, a bearer token) go through. A firewall
check (AWS WAF, Cloudflare, a reCAPTCHA browser check) stops the request unless the spider sets `solve`; the
`challenges` skill covers it.

## What the kit does for every crawl, and the hooks

Besides sending and pacing requests, a live crawl downloads every attachment as it goes (`attachments`), prints its
progress every 15 seconds, and keeps its queue on disk: a crawl foundry stops (its worker died, it stalled, a person
restarted it) carries on where it got to. When it carries on, it sends the spider's `start` requests again and skips
everything already sent, so set what the spider learns once (a tenant's name, its time zone) in `start`'s callbacks
or in `cb_kwargs`, never in a callback a resumed crawl won't reach again.

Because the queue is on disk, every request waiting in it is written out and read back: its `cb_kwargs` and `meta`
reach the callback as copies, never the objects the spider made. Don't keep a request's state in a dict the spider
also holds and compare them with `is`, or change it in one place and expect the other to see it; key shared state by
an id (`self.active[window] == state["epoch"]`). The `live` command keeps its queue on disk too, so it shows this.

Foundry's own crawls (scheduled and test scrapes) also keep everything the portal sent: every response but the
attachments, as a HAR on the run, without the session's cookies and `Authorization` header, so a person can see
exactly what a scrape got. The `live` command keeps nothing; record with `find-the-api` when you need the traffic
yourself.

Each default is the best practice; a portal with a quirk overrides it in the spider or in `buyer.toml`:

| Default | Override |
|---|---|
| pace from `DOWNLOAD_DELAY`, backing off on rate limits | `delay = S` in `buyer.toml`; `custom_settings = {"CONCURRENT_REQUESTS_PER_DOMAIN": 1}` in the spider |
| a crawl that sends nothing for 5 minutes is stuck and stopped | `stall = 15` in `buyer.toml` for a portal that makes you wait longer |
| bot checks stop the request | `solve = True`, or `meta={"solve": True}` on the requests that meet them |
| an attachment is a GET of its link, and a web page is not the file | override `file_request` and `file_problem` |

## Reading the errors

| What you see | What it means | Fix |
|---|---|---|
| `rate-limited N times and slowed to S s` | the portal answered 429 or `Retry-After`; the crawl slowed itself and retried | set `delay = S` on the portal's `[[portals]]` entry in `buyer.toml`, so the crawl starts at the pace that works; save it with `remember` |
| `(429)`, `(202)` with an empty body, or bodies that stop being JSON partway | the portal limits rate without saying so | set a `delay` in `buyer.toml` (start at 2) and rerun until it's gone |
| `N stopped by bot checks` | a check stood in front of those requests | the `challenges` skill: `solve`, then `solve = True` and pacing |
| `refused with 401/403` on the first request | a header the portal's own page sends is missing, or the portal really needs a sign-in | compare with the page's request in your recording; see `sign-in` |
| `(400)` or `(404)` on some records | the spider builds a URL the portal doesn't have for them (a closed bid, a missing tab) | skip what the portal doesn't have, without failing the listing |
| a crash or `KeyError` on a 200 | the parsing is wrong for some records | fix the parser; capture that record into the fixtures |
| `N failed` among the files | the downloads didn't return the file | the `attachments` skill |
| `0 listings`, no errors | the listing request returns nothing from the crawl | check its parameters against your recording |

Rerun after each change. A full crawl of a big portal can take many minutes when it has to go slowly; that is fine.

## Compare with the portal

A pass shows the crawl works, not that it is complete. Compare the count with what the page lists, and open two
listings to confirm titles, due dates (against the time the page shows, in the portal's time zone) and files.
