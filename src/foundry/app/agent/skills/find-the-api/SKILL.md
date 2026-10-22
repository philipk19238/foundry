---
name: find-the-api
description: Find the requests behind a procurement portal's listing, detail pages and downloads by recording the browser's network traffic. Use at the start of every new scraper, and whenever a scraper breaks because the portal changed.
---

# Find the portal's API

The page is a view over requests. Find those requests before you parse any HTML.

## Record while you click

Start the recorder before you open anything, in the background:

```bash
"$FOUNDRY_PYTHON" -m foundry.core record traffic.jsonl --browser "$FOUNDRY_BROWSER" > traffic.log 2>&1 &
```

It logs every request every tab makes (new tabs too), with request and response bodies, one JSON line each in
`traffic.jsonl`, and prints each document, XHR and fetch to `traffic.log` as it happens. Then, in the browser:

1. open the listing page the portal URL points at;
2. go to its second page, and change its sort or filter once, so the paging and filter parameters show up;
3. open two opportunities, and the documents or addenda tab of each;
4. click one attachment's download.

Stop with `kill %1` (or `pkill -f "foundry.core record"`). Keep `traffic.jsonl` and `traffic.log` out of commits.

## Read the recording

```bash
"$FOUNDRY_PYTHON" "$FOUNDRY_SKILLS/find-the-api/scripts/endpoints.py" traffic.jsonl
```

It lists each endpoint once (method, host and path, with ids replaced by `{id}`), how often it was called, its
statuses, its content type, and one full example URL, with analytics and trackers left out. The listing, detail,
documents and download requests are usually obvious from their paths. For a candidate, read its full exchange:

```bash
grep -F '<path part>' traffic.jsonl | head -1 | "$FOUNDRY_PYTHON" -m json.tool
```

## What each request really needs

Don't copy every header and cookie the browser sent. Find the ones that matter:

```bash
"$FOUNDRY_PYTHON" -m foundry.core needs traffic.jsonl "<url part>" --browser "$FOUNDRY_BROWSER"
```

It sends the latest matching request again from the page it came from, then without each header and each cookie
of its site in turn, and lists the ones whose absence changes the answer, with where each came from: the response
that set a cookie, the localStorage key a header's value is kept under, or the earlier response it first appeared in.
On PlanetBids it says `bid-details` needs only the `company-id` header, and no cookie.

Put exactly those in the spider: fixed headers as constants built from `self.tenant`, a token read from an earlier
response in the callback that receives it, and a token the portal's scripts keep in storage with
`self.session.stored("<key>")`. Cookies the browser holds are sent for you. Name the needs in `submit`'s `access`.

## Pick how the scraper reads the portal

In this order:

1. **The API**: JSON (or HTML fragments) behind the listing, details and files. Most reliable, and cheapest.
2. **The server's HTML**: when no request returns the data, parse the pages the server sends.
3. **A real browser**: only when the data exists only after JavaScript runs. Live crawls already send every request
   from a stealth browser on the portal, with its cookies, so an API that needs a browser session works as plain
   requests in the spider. Propose a kit change only when no request returns the data at all.

Prefer one request that lists everything (a big `per_page`, an export endpoint) over many small ones, but check the
portal honors it: count the rows it returns against what the page shows.
