---
name: challenges
description: Get a scraper past a portal's bot checks - Cloudflare, AWS WAF, reCAPTCHA or hCaptcha "browser check" pages - by testing whether the stealth browser clears them, then building that into the spider with solve, pacing and saved clearances, so scheduled crawls never need a person. Use when a page shows a check, when the recorder or live crawl reports "stopped by bot checks", and on repair jobs that mention them.
---

# Bot checks

Scheduled crawls run with nobody watching, so a check has to be something the scraper handles. A person clearing it
for you proves the portal works; it doesn't make the scraper work.

## 1. See what kind it is, and when it fires

- **On entry** (every new session gets a "Browser check" page, like eMMA): one clearance per session.
- **Per request** (a download popup, each file asking for its own token): one per file.
- **When you go fast** (pages past some count redirect to a check, or 429s and challenge headers appear): a rate rule.
- **By address** (datacenter IPs refused, residential ones fine): crawls already run from a residential address.

The recorder shows it: a request redirected to a `browser_check`, `captcha` or `/cdn-cgi/challenge` URL, or answered
with `cf-mitigated: challenge` or `x-amzn-waf-action: challenge`.

## 2. Test whether the browser clears it, before asking anyone

```bash
"$FOUNDRY_PYTHON" -m foundry.core solve "<the challenged url>" --browser "$FOUNDRY_BROWSER"
```

It opens the URL in a new tab and watches until the check is gone (up to 90 s, `--seconds` for more). The stealth
browser's own solver often clears it on its own; this tells you when it has, so you don't keep waiting on a page that
is already through. `NO CHECK` means this page has none. `CLEARED after 12.3 s` means the crawl can do the same
(eMMA's reCAPTCHA Enterprise entry check clears on its own in 20 to 30 s). `STILL CHALLENGED` means it can't.

## 3. Build it into the spider

When `solve` clears it:

- Set `solve = True` on the spider (or `meta={"solve": True}` on just the requests that meet the check). A live crawl
  then waits for the browser to clear each check and sends the request again. Without it, a check stops the request
  and the crawl reports `N stopped by bot checks`.
- If it fires when you go fast, pace the portal in `buyer.toml`: `delay = 2` (seconds between requests) on its
  `[[portals]]` entry. That wins over the spider's settings and keeps the check from firing at all. Prefer this over
  writing pacing code in the spider.
- After a scheduled crawl, foundry keeps the browser's cookies as the source's session, so a clearance carries into
  the next crawl.

Then the `live-crawl` skill: it must pass with no requests stopped by bot checks.

## 4. When it still doesn't clear

- Look for a way around the check: the same data from a request that isn't checked (an API host, an export, a
  print view), or a public copy of the files elsewhere (`attachments`).
- Per-file checks on downloads you can't pass: keep each file's name and link, and say so in `gaps`.
- Ask a person with `request_help` only to see the portal behind the check while you build; say in `gaps` and in a
  learning that scheduled crawls will stop at it, because nobody clears it for them.

Save what you found with `remember`: which check, when it fires, whether `solve` cleared it, and the pace that works.
