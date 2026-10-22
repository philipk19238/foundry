---
name: kit-hooks
description: Every behaviour the scraper kit gives a spider for free (sending from the stealth browser, pacing, rate limits, bot checks, downloads, sessions, resuming a killed crawl, progress), its best-practice default, and the hook that changes it for one portal. Use whenever a portal has a quirk the defaults don't handle, before writing a workaround inside a callback.
---

# Adapting the kit to a portal

The kit (`foundry.core`) does the same things for every spider, and each has a default that is the best practice.
When a portal needs otherwise, change it **at its hook**, in the spider or in `buyer.toml`. Don't work around the kit
inside a callback: a hook keeps the quirk in one named place, so the next agent sees it, and the kit can keep
improving underneath it.

## The hooks

| What | Default | Hook |
|---|---|---|
| How requests are sent | from a page on the portal in the stealth browser, as `fetch` | nothing to change; a request to another site runs from a blank page on that site |
| Pace | the spider's `DOWNLOAD_DELAY`; on a 429, `Retry-After` or a firewall header, slow that site down, then ease back | `delay = S` in `buyer.toml` (seconds between requests, one at a time) |
| How many at once | Scrapy's `CONCURRENT_REQUESTS_PER_DOMAIN` | `custom_settings = {"CONCURRENT_REQUESTS_PER_DOMAIN": 2}` on the spider |
| Bot checks | counted, and the request dropped | `solve = True` on the spider, or `meta={"solve": True}` on the requests that meet one (`challenges`) |
| A lost session | 401, 403 or a URL with "login" | override `logged_out(response)` |
| Saved session | cookies, localStorage, sessionStorage and the user agent, sent with every request | read a token with `self.session.stored("key")`; other secrets with `self.credentials["name"]` (`sign-in`) |
| Downloading an attachment | a GET of the link with the listing as `Referer`: plain HTTP first, the browser if that fails | override `file_request(link, listing)`; `meta={"browser": True}` skips the plain try |
| Whether a download is the file | not an error status, and not a web page unless the link is one | override `file_problem(link, response)`, returning why it isn't, or None |
| A stuck crawl | stopped after 5 minutes without a request | `stall = 15` in `buyer.toml` |
| Resuming a killed crawl | its queue is kept; `start` is sent again and everything already sent is skipped | set what the spider learns once in `start`'s callbacks or `cb_kwargs`, never only in a later callback |
| Anything Scrapy does | Scrapy's defaults | `custom_settings` on the spider (Scrapy's own setting names) |

## How to use one

1. Find the quirk in the live crawl's output (`live-crawl`): the status, the URL, the stat.
2. Pick the narrowest hook: a `meta` key on the requests that need it before a spider attribute, and a spider
   attribute before a `buyer.toml` setting that changes the whole portal.
3. Write it with a one-line comment saying what the portal does, for example
   `# Bonfire serves each file from a signed URL its download button asks for`.
4. Run the live crawl again and check the stat that showed the quirk.
5. Save the quirk with `remember`, named for the platform, so the next portal on it starts with the fix.

## When no hook fits

Say so in `submit`'s `gaps`, and propose the hook with `propose_change` (`changing-foundry`): a new hook in the kit,
with its default, is better than a spider that reimplements the kit. Never copy kit code into a spider.

## Example

```python
class Spider(PortalSpider):
    solve = True  # eMMA puts a browser check in front of every page
    custom_settings = {"CONCURRENT_REQUESTS_PER_DOMAIN": 2}  # more than two at once gets 429s

    def file_request(self, link, listing):
        # Each file's link is a page whose Download button asks for a signed URL.
        return Request(link.url + "/download-url", meta={"browser": True})

    def file_problem(self, link, response):
        # The portal answers 200 with a "file not found" page.
        if b"File not found" in response.body[:2000]:
            return "the portal says the file is gone"
        return super().file_problem(link, response)
```
