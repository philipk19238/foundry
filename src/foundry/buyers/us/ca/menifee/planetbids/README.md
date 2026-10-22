# City of Menifee — PlanetBids 41576

This site is scoped to the visitor listing at
https://vendors.planetbids.com/portal/41576/bo/bo-search.
Its default search includes every stage, so the spider retains closed, awarded,
canceled and rejected bids as well as any future open bids. It never requests another tenant.

## Requests

All API requests use `https://api-external.prod.planetbids.com/papi/`:

1. `bids?bid_type_id=0&cid=41576&dept_id=0&due_date_from=&due_date_to=&keyword=&page=1&per_page=30&sort_by=&sort_order=-1&stage_id=0`
   returns the visitor search results and `meta.totalPages`. Increment `page` through the final page.
2. `bid-details/{bidId}` returns the title, invitation number, scope, due date,
   department, contact, categories, bonds and other opportunity fields.
3. `bid-downloadable-files?bid_id={bidId}` returns the Documents tab.
4. `bid-addenda?bid_id={bidId}`, `bid-q-and-a-items?bid_id={bidId}` and
   `bid-email-history-items?bid_id={bidId}` return supplemental information and
   attachment records in their `included` arrays.
5. The portal builds file links as
   `https://files-prod01.planetbids.com/fileHandling.cfm?path=/Menifee/BMfiles/&filename={encoded serverFilename}&download=1`.
   Clicking the public addendum attachment for bid 146087 confirmed this handler
   (`download=0` for the portal's View action); it redirected to the PDF. The
   `download=1` URL returned a 153,226-byte PDF with browser request headers.
   A legacy attachment with `www.planetbids.com` in its metadata also returned
   a 351,650-byte PDF through the current file server; metadata supplies only
   the path, while the portal configuration supplies the file server host.

The scraper uses the JSON API without signing in or setting authentication headers.
The API requires a browser user agent and a vendor-portal Referer, even for public
requests; `company-id: 41576` identifies this buyer. Each API request explicitly
retains these headers so Scrapy's Referer middleware does not replace the portal origin.
Naive API dates are interpreted in `America/Los_Angeles`, matching the displayed PDT/PST.
The stable external identifier is the bid ID; the invitation number is kept in metadata.

## Checks

- On 2026-10-22 the visitor page reported **278 bids**. The ten API listing pages
  contained **278 unique IDs**: 54 Closed, 195 Awarded, 3 Award Pending,
  16 Canceled and 10 Rejected. There were no open bids.
- Opened bids **146108** and **146087** in the browser. Their titles were
  **As Needed Event/Party Equipment Rental Services** (RFQ 2027-02) and
  **Special Event Portable Restroom Rental Services** (RFQ 2027-01).
  Both displayed **October 12, 2026, 3:00 PM PDT**, matching the parsed
  `2026-10-12T15:00:00-07:00`.
- Their document tabs displayed respectively
  `RFP 2027-02 Event Equipment Rentals- Final.pdf` and
  `RFQ 2027-01 Portable Restrooms- Final.pdf`.
  The second bid also displayed the public `Addendum No. 1.pdf` attachment.
- Fixtures contain all ten listing pages and the detail/document/addenda/Q&A/email
  responses for seven bids, including all three reported failure cases,
  awarded construction bid 145127 and
  legacy bid 97403 with a recalled addendum.
- Replay: `python -m foundry.core check us/ca/menifee/planetbids`
  yields seven listings, zero invalid items and zero crashes.

- The repaired scraper's full **stealth-browser live crawl** completed
  **1,400 HTTP 200 API responses** in about **37 minutes**, printing **PASS**:
  **278 valid listings, zero invalid, zero crashes, no lost session**.
  All 278 unique IDs matched the visitor search results across all ten pages.
- Every live listing's title, invitation number, stage and local deadline matched
  the search responses. The crawl produced **1,349 attachment links**.
- Foundry's `check_scraper` reported **healthy** for seven complete fixture bids.
  All **10 regression tests** passed, including a full fixture replay that injects
  empty HTTP 202, HTML HTTP 200, and HTTP 429 responses at the three reported
  failing URLs and recovers all seven expected listings with their files.
- Ruff lint/format and the spider's type check passed.

## Access limits

Both sampled main bid documents are marked **requires login** by the visitor UI.
Their names and handler links are retained without creating an account. Public
addendum attachments can be opened as a visitor. An earlier manual download
succeeded with browser request headers; the latest HTTP check is recorded below.

## Intermittent response repair

The review crawl returned only 233 listings and reported JSON decoding failures
on details 129606 and 129759 and the documents endpoint for 142701. All three
requests subsequently returned HTTP 200 with JSON in the visitor browser, so
these were intermittent transport responses rather than missing bid records.

Scrapy's normal download delay does not pace responses returned directly by
Foundry's browser middleware. This site therefore installs a small downloader
middleware before the browser transport, preserving Foundry's other middlewares.
It spaces all requests, including browser fetches and retries, by at least
1.5 seconds and limits global concurrency to one. Transient HTTP statuses
(including empty 202 challenges and 429 rate limits) and malformed HTTP 200 JSON
are retried up to six times, retaining the original callback and its state.
Retries impose a shared exponential cooldown (5 to 120 seconds), honoring a
longer numeric Retry-After. Exhaustion raises an explicit API error and fails
the crawl; it never silently discards an opportunity or treats a WAF challenge
as an expired login.

Regression tests cover empty challenges, rate limits, empty/HTML/truncated
HTTP 200 bodies, retry exhaustion, callback state, shared pacing, and preservation
of Foundry's browser and session middleware configuration.

The current attachment check with Foundry's browser-like HTTP headers returned
HTTP 405 for both the file handler and the direct public PDF path. Public links
remain attached to their listings, but the plain HTTP downloader's access to
the file server is a gap distinct from the API response repair.
