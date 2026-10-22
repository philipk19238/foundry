---
name: attachments
description: Make every attachment link one foundry can actually download - check a real download the way foundry fetches files, find public copies of files behind a login, and keep name and link when a file can't be fetched. Use whenever a listing has documents.
---

# Attachments

Each `FileLink(url, name)` the spider yields is downloaded by foundry after the crawl and kept in its bucket. Name
is what the portal shows; url must download the file itself, not a page about it.

## Foundry's download is not the spider's crawl

Files are fetched separately, with a plain HTTP GET: browser-like headers, the listing's URL as `Referer`, and the
portal's saved session cookies. Not the spider's custom headers, and not through the stealth browser. A link that
works in the spider can fail here (PlanetBids answered 405 until the request looked like a browser's). Check one
real file the way foundry fetches it:

```bash
"$FOUNDRY_PYTHON" "$FOUNDRY_SKILLS/attachments/scripts/download.py" "<file url>" "<listing url>"
```

It prints the status, content type, size and the first bytes. A PDF starts with `%PDF`; an HTML page means the link
is a viewer or a login, not the file.

## When it doesn't download

- **The link is a viewer page.** Find the request it makes for the bytes (record it with `find-the-api` while you
  click Download) and yield that URL.
- **The link expires** (a signed URL with an `Expires` or `X-Amz-Signature` parameter). Yield the stable URL that
  hands out the signed one, if the portal has one; otherwise say so in `gaps`.
- **The file needs a login the listing doesn't.** Don't sign up for it. Look for a public copy: Paso Robles' bid
  PDFs behind DemandStar's login were public on the city's own site (`prcity.com/DocumentCenter/View/...`). Check it
  is the same document, then yield the public URL.
- **Nothing public exists.** Keep each file's name and the portal's link, and list what's missing in `gaps`.

Never put file bytes in the fixtures; the download check above is the proof.
