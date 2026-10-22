---
name: attachments
description: Make every attachment link one foundry can actually download - check a real download the way foundry fetches files, find public copies of files behind a login, and keep name and link when a file can't be fetched. Use whenever a listing has documents.
---

# Attachments

Each `FileLink(url, name)` the spider yields is downloaded during the crawl itself and uploaded to foundry's bucket
at once, so links that expire (presigned URLs) are fetched while they still work. Name is what the portal shows; url
must download the file itself, not a page about it.

## How the crawl downloads files

The kit does it for every spider (`FilesPipeline`): each link of each good listing is sent as
`spider.file_request(link, listing)`, a GET with the listing as `Referer` by default. It goes as plain HTTP first,
which is fast and costs no proxy traffic (file hosts are mostly plain CDNs), and from the stealth browser, with its
session and pace, only when that didn't get the file. `spider.file_problem(link, response)` decides whether a
response is the file; by default an error status, or a web page for a link that isn't one, counts as a failure.

The live crawl (`live-crawl`) prints `N files downloaded, M failed`, and the first failures with their reason. That
is the proof: every file downloaded, or each failure explained in `gaps`. For a quick look at one link:

```bash
"$FOUNDRY_PYTHON" "$FOUNDRY_SKILLS/attachments/scripts/download.py" "<file url>" "<listing url>"
```

A PDF starts with `%PDF`; an HTML page means the link is a viewer or a login, not the file.

## Site quirks: override the hooks

The defaults are the best practice; override them in the spider when the portal needs otherwise:

- **A download takes more than a GET** (a POST, a token from the page, a second request for a signed URL):
  override `file_request` to return that request. For a two-step download, return the first request and have the
  link point at what hands out the file.
- **The portal serves files oddly** (a download page that is the file, a 200 with an error page in the body):
  override `file_problem` to say what a good download looks like there.
- **Files need the browser** (a bot check, cookies the browser earned): the plain try fails and the browser one
  follows by itself; return `meta={"browser": True}` from `file_request` to skip the plain try, and `solve = True`
  covers bot checks on file requests too.

## When it doesn't download

- **The link is a viewer page.** Find the request it makes for the bytes (record it with `find-the-api` while you
  click Download) and yield that URL.
- **The link expires** (a signed URL with an `Expires` or `X-Amz-Signature` parameter). Yield it as it is: the crawl
  downloads it minutes after reading it. If even that is too late, make `file_request` ask for a fresh one.
- **The file needs a login the listing doesn't.** Don't sign up for it. Look for a public copy: Paso Robles' bid
  PDFs behind DemandStar's login were public on the city's own site (`prcity.com/DocumentCenter/View/...`). Check it
  is the same document, then yield the public URL.
- **Nothing public exists.** Keep each file's name and the portal's link, and list what's missing in `gaps`.

Never put file bytes in the fixtures; the live crawl's file count is the proof.
