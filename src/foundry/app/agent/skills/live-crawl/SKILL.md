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

Each request runs as a `fetch` from a page on the portal, so responses are what plain HTTP returns and the spider
needs no browser code. `fetch` drops the `Cookie`, `User-Agent` and `Referer` headers a spider sets; the page sends
its own, with the browser's cookies. Other headers (`Accept`, a tenant id, a bearer token) go through. A firewall
challenge (AWS WAF, Cloudflare) is answered by opening the request as a page and sending it again.

## Reading the errors

| What you see | What it means | Fix |
|---|---|---|
| `(429)`, `(202)` with an empty body, or bodies that stop being JSON partway | the spider is faster than the portal allows | slow it in `custom_settings`: `DOWNLOAD_DELAY` (start at 1-2 s), `CONCURRENT_REQUESTS_PER_DOMAIN: 1`, `AUTOTHROTTLE_TARGET_CONCURRENCY: 1`; retry them with `RETRY_HTTP_CODES` including 429 and 202 |
| `refused with 401/403` on the first request | a header the portal's own page sends is missing, or the portal really needs a sign-in | compare with the page's request in your recording; see `sign-in` |
| `(400)` or `(404)` on some records | the spider builds a URL the portal doesn't have for them (a closed bid, a missing tab) | skip what the portal doesn't have, without failing the listing |
| a crash or `KeyError` on a 200 | the parsing is wrong for some records | fix the parser; capture that record into the fixtures |
| `0 listings`, no errors | the listing request returns nothing from the crawl | check its parameters against your recording |

Rerun after each change. A full crawl of a big portal can take many minutes when it has to go slowly; that is fine.

## Compare with the portal

A pass shows the crawl works, not that it is complete. Compare the count with what the page lists, and open two
listings to confirm titles, due dates (against the time the page shows, in the portal's time zone) and files.
