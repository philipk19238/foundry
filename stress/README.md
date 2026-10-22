# Stress harness

Foundry's live crawl, run end to end through the real host code (`Crawls.live`) against a fake procurement portal
on this machine, under named failure scenarios: rate limits, firewall challenges, 5xx, hangs, dropped connections,
expiring and stale file links, viewer pages, a restart, a dead worker, a bucket outage, and a throughput sweep.

**It must never point at a real portal.** The portal (`portal.py`) listens on 127.0.0.1 and localhost only, and the
spider (`sites/stressbids/spider.py`) only knows the tenant the harness gives it.

```bash
uv run playwright install chromium   # once: the local browser that stands in for Kernel
just stress --list                   # every scenario and what it does
just stress                          # all of them (about a quarter of an hour)
just stress baseline hang            # just these
```

Each row reports wall time, requests per second the portal answered, listings per second, listings found against
the portal's total, files saved against the good ones, failed downloads, rate limits met, the slowest pace it came to,
challenges, errors, progress beats, the crawl process's peak memory, the verdict, and PASS or FAIL with why. The
command exits non-zero if any scenario fails.

| File | What |
|---|---|
| `portal.py` | the fake portal: listing API and details on one host, signed files on another, faults set by `Knobs` |
| `sites/` | the portal's `buyer.toml` and platform spider, copied into a fresh repo tree per scenario (`tree.py`) |
| `browsers.py` | `BrowserApi` on a local headless Chromium (no stealth, no proxy, no CAPTCHA solver) |
| `buckets.py` | an in-memory bucket that counts uploads, and one that is down |
| `watches.py` | fake `CrawlWatch`es: one that records beats, one that restarts, one whose worker dies |
| `scenarios.py` | every scenario: knobs and expectations |
| `run.py` | runs them and prints the table |
