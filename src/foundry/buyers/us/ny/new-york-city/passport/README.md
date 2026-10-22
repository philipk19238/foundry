# NYC PASSPort public RFx

Scope: the unfiltered visitor results of
`https://passport.cityofnewyork.us/page.aspx/en/rfp/request_browse_public`, including all displayed stages and
separate lots/rounds. This is NYC's customized public site, not a general Ivalua platform integration.

The spider reads server HTML. Listing pagination posts the current `mainForm`, including its fresh viewstate and
CSRF token, with `__EVENTTARGET=body_x_grid_grd` and `__EVENTARGUMENT=Page|<zero-based index>`. The initial count
("More than 150") is an estimate: follow the actual Next control until it is disabled. Saved visitor sessions can
remember a later page; the spider first returns to page zero without changing filters.

Each row links to `/page.aspx/en/bpm/process_manage_extranet/<record id>`. That page contains summary input values,
the HTML description, dates, contact information, conference/location grids, and the Documents grid. Each detail
grid is paginated with its own event target and the same form post mechanism. File links use
`https://webserver-mocs-prd.lfr.cloud/c/public_document/download?documentId=<id>` and download publicly, including
addenda. The record id identifies a listing because multiple rounds can share an EPIN and title. Due timestamps
come from the visible listing columns in America/New_York; hidden internal timestamps are not deadlines. Blank
visible deadlines remain null.

No account or authorization header is needed. The spider reads CSRF tokens from each form rather than storing
them as credentials. No rendered browser is required by the spider.

## Verification on 2026-10-22

- Portal precise count: 4,139 results. Live enumeration through this spider's pagination: 276 pages, 4,139 distinct
  record ids, no duplicates.
- Compared public listing/detail titles and deadlines for 39123 (Printed Election Supplies, November 9, 2026,
  11:00 AM EST) and 39078 (151-20 Jamaica Roof Replacement, December 2, 2026, 10:00 AM EST).
- Verified all six files for 39123 and all 26 files across two document pages for 39078 against the browser.
- Downloaded document 255097317: HTTP 200, application/pdf, PDF signature, 369,197 bytes, filename
  `2600039-Addendum 3.pdf`. No binary content is in fixtures.
- A preliminary live Scrapy run parsed 145 opportunities and 1,337 distinct attachment links across all five
  stages, including 13 blank deadlines and 24 opportunities with more than 15 attachments. Duplicate document
  URLs are retained once. This was a sample detail crawl; the count verification enumerated the entire list.
- Exercised page-zero resets against real listing and document form responses. The document reset still yielded
  all 26 files.

Fixtures contain one listing GET, two detail GETs, and the second document-page POST for 39078. Only one POST
response per URL is captured, so method/URL replay is unambiguous. Further listing POSTs are outside the fixture
sample and skipped during replay; full listing pagination was checked live. The site rejects adding pagination
query parameters to an existing form's action, so the spider uses the actual form URLs without invented keys.

Run the fixture check with:

```sh
PYTHONPATH=src /.uv/.venv/bin/python -m foundry.core check us/ny/new-york-city/passport
```
