---
name: completeness
description: Prove a scraper gets every opportunity the portal page lists - pagination, filters, stages, "load more" buttons, page-size limits - and count the result against the page. Use before submit, and whenever a crawl finds fewer listings than the page shows.
---

# Get everything the page lists

A healthy replay proves the spider parses. It doesn't prove the spider found every opportunity. This skill does.

## Scope is the page you were given

Scrape what the given URL lists, as a visitor sees it there: its default filters, stages and sort, through every
page. Don't narrow it (only "open" bids) unless the page itself does, and don't widen it to the whole platform.
Example: Menifee's PlanetBids page lists 278 bids, all stages, newest first, with no open ones at the time. The
scraper takes all 278.

## Find how the list pages

With the recorder running (`find-the-api`), go to page 2 and the last page, and change the sort once. Read the
listing request's parameters: `page`, `offset`, `per_page`, `limit`, a cursor, `totalPages` or `total` in the
response's `meta`. Prefer the API's own total over counting pages.

Traps seen on real portals:

- **"View more" or "Load more" that makes no request.** DemandStar returned all 55 rows in the first response and
  only revealed them on click. Count the rows in the response, not the rows on screen.
- **A page size the API caps quietly.** Asking for `per_page=500` can return 100 with no error. Compare the rows
  returned with the total the API reports.
- **Filters in the URL fragment or local storage**, not the request. The default the page applies is what counts.
- **Several lists on one page** (open, closed, awarded tabs). Take what the given URL shows by default.

## Count it

After `live-crawl` passes, compare:

1. the total the portal shows (or its API's `total`), and
2. the crawl's `valid` count.

They should match, or differ by records the portal shows but can't be scraped (say which in `gaps`). Put both
numbers in `submit`'s `checks`: "the portal lists 278 bids; the crawl found 278".
