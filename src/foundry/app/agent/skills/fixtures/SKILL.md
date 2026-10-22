---
name: fixtures
description: Capture a site's fixtures, the saved portal responses its spider is replayed against by check_scraper and the pull request check. Use after the spider's requests are known, and whenever check_scraper says a request is not in the sample.
---

# Capture fixtures

Fixtures are a JSON list of `{"method", "url", "status", "headers", "body"}` objects, `body` as text, saved as the
site's `fixtures.json`. Replay answers the spider's requests from them, offline, so they decide what
`check_scraper` can prove.

## What to capture

- At least one listing page.
- For two opportunities, every response the spider needs before it yields their `Listing`: the detail, the
  documents or attachments list, addenda, anything a callback requests on the way.
- Not binary files: keep downloads out, and check one real download by hand instead.
- Nothing private beyond what the portal shows any visitor (or any signed-in user, if it needs a sign-in).

Requests for opportunities you didn't capture are skipped on replay, not failed, so two complete opportunities are
enough.

## From your recording

Click through those pages with the recorder running (see the `find-the-api` skill), then:

```bash
"$FOUNDRY_PYTHON" -m foundry.core fixtures traffic.jsonl <url part> <url part> ... > <site folder>/fixtures.json
```

It keeps every recorded response whose URL holds any of the parts, the latest of each method and URL. Pick parts
that match only the portal's own requests (`/papi/bid-details/`, not `planetbids`).

## Replay matches method and URL only

Not the request body. When several records POST to one URL with different bodies, the command warns, and replay
would answer them all with one response. Find a URL that tells them apart, such as a query parameter the API
accepts (`?bidId=123`), make the spider send that, and capture again. Never invent a URL the portal doesn't answer.

## Then

`check_scraper` with the site key. A healthy replay shows the spider parses what you captured; the `live-crawl` skill
shows it works against the portal.
