"""The foundry.core command line, run from the repo root:

- `python -m foundry.core crawl <job.json>`: one crawl, as Foundry runs it, in a process of its own.
- `python -m foundry.core check <site key>...`: replay each site's fixtures through its spider; fails unless every
  site passes. With `--changed <base ref>` instead of keys, checks the sites the branch changed (the PR check); with
  `--all`, every site (the check on `main`).
- `python -m foundry.core live <site key> --browser <cdp url>`: one crawl of the live portal exactly as Foundry
  runs it, from that stealth browser; fails unless it is healthy with listings.
- `python -m foundry.core record <out.jsonl> --browser <cdp url>`: logs every request the browser's pages make
  until stopped (or `--seconds`), printing the ones that could be the portal's API.
- `python -m foundry.core fixtures <recording.jsonl> <url part>...`: prints, as fixtures JSON, the recorded
  responses whose URL holds any of the parts.
- `python -m foundry.core needs <recording.jsonl> <url part> --browser <cdp url>`: which cookies and headers the
  latest recorded request matching the part needs, by sending it again without each, and where each came from.
- `python -m foundry.core solve <url> --browser <cdp url>`: opens the URL in that browser and waits (`--seconds`,
  90 by default) for its bot check to clear, saying whether it did and how long it took."""

import argparse
import asyncio
import importlib.util
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path

from playwright.async_api import async_playwright
from pydantic import TypeAdapter
from scrapy.crawler import AsyncCrawlerRunner, Crawler
from scrapy.settings import Settings, default_settings
from scrapy.utils.log import configure_logging

from foundry.core import challenges, pace, recorder, spider
from foundry.core.models import CrawlJob, CrawlProgress, CrawlStats, Fixture, Site
from foundry.core.recorder import Recorder
from foundry.core.sites import Sites

STOPPED_CLEANLY = "stopped-cleanly"
DOWNLOAD_TIMEOUT = 60
MEMORY_WARNING_MB = 768
MEMORY_LIMIT_MB = 1536


async def stop(crawler: Crawler) -> None:
    """SIGTERM: tell the kit the crawl is stopping, so nothing waits on a pace, then close it the way Scrapy saves
    its queue."""
    crawler.signals.send_catch_log(signal=spider.STOPPING)
    await crawler.stop_async()


def resumable(job: CrawlJob) -> None:
    """Scrapy saves the requests still to send only when a crawl closes, as SIGTERM closes it. A crawl killed outright
    (its machine died) left a queue that reads as empty, and carrying it on would send nothing: start it over, keeping
    the attachments already downloaded."""
    assert job.state is not None
    if job.state.exists() and not (job.state / STOPPED_CLEANLY).exists():
        shutil.rmtree(job.state)
        job.listings.unlink(missing_ok=True)
        if job.progress is not None:
            job.progress.unlink(missing_ok=True)
    (job.state / STOPPED_CLEANLY).unlink(missing_ok=True)


async def crawl(job: CrawlJob) -> None:
    found = importlib.util.spec_from_file_location("spider", job.spider)
    assert found is not None and found.loader is not None
    module = importlib.util.module_from_spec(found)
    found.loader.exec_module(module)
    (portal,) = spider.spiders(module)
    middlewares = {"foundry.core.middlewares.SessionMiddleware": 500}
    if job.fixtures is not None:
        middlewares["foundry.core.middlewares.ReplayMiddleware"] = 950
    if job.browser is not None:
        middlewares["foundry.core.middlewares.BrowserMiddleware"] = 960
    settings = Settings(
        {
            "TWISTED_REACTOR_ENABLED": False,
            "DOWNLOADER_MIDDLEWARES": middlewares,
            "SPIDER_MIDDLEWARES": {
                "foundry.core.middlewares.ResumeMiddleware": 50,
                "foundry.core.middlewares.ErrorsMiddleware": 100,
            },
            "ITEM_PIPELINES": {
                "foundry.core.pipelines.FilesPipeline": 50,
                "foundry.core.pipelines.ListingPipeline": 100,
            },
            "EXTENSIONS": {"foundry.core.extensions.ProgressExtension": 500},
            "FOUNDRY_FILES": str(job.files) if job.files is not None else "",
            "FOUNDRY_KNOWN_FILES": job.known_files,
            "FOUNDRY_PROGRESS": str(job.progress) if job.progress is not None else "",
            "FOUNDRY_LISTINGS": str(job.listings),
            "FOUNDRY_SESSION": job.session.model_dump_json() if job.session is not None else "",
            "FOUNDRY_CREDENTIALS": job.credentials,
            "FOUNDRY_BROWSER": job.browser or "",
            "FOUNDRY_HOME": job.home,
            "FOUNDRY_FIXTURES": [fixture.model_dump() for fixture in job.fixtures or []],
            "LOG_LEVEL": "INFO",
            "DOWNLOAD_TIMEOUT": DOWNLOAD_TIMEOUT,
            "MEMUSAGE_ENABLED": True,
            "MEMUSAGE_WARNING_MB": MEMORY_WARNING_MB,
            "MEMUSAGE_LIMIT_MB": MEMORY_LIMIT_MB,
        }
    )
    settings.set("RETRY_EXCEPTIONS", [*default_settings.RETRY_EXCEPTIONS, "builtins.TimeoutError"])
    if job.state is not None:
        settings.set("JOBDIR", str(job.state))
    if job.delay:
        settings.set("DOWNLOAD_DELAY", job.delay, priority="cmdline")
        settings.set("CONCURRENT_REQUESTS_PER_DOMAIN", 1, priority="cmdline")
        settings.set("AUTOTHROTTLE_ENABLED", False, priority="cmdline")
    if job.state is not None:
        resumable(job)
    configure_logging(settings)
    runner = AsyncCrawlerRunner(settings)
    crawler = runner.create_crawler(portal)
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, lambda: asyncio.ensure_future(stop(crawler)))
    await runner.crawl(crawler, **job.arguments)
    if job.state is not None:
        (job.state / STOPPED_CLEANLY).touch()
    assert crawler.stats is not None
    stats = crawler.stats.get_stats()
    report = CrawlStats(
        valid=stats.get(spider.VALID, 0),
        invalid=stats.get(spider.INVALID, 0),
        logged_out=stats.get(spider.LOGGED_OUT, False),
        refused=stats.get(spider.REFUSED, ""),
        challenged=stats.get(spider.CHALLENGED, 0),
        rate_limited=stats.get(pace.RATE_LIMITED, 0),
        slowed_to=stats.get(pace.SLOWED_TO, 0),
        files_saved=stats.get(spider.FILES_SAVED, 0),
        files_failed=stats.get(spider.FILES_FAILED, 0),
        crashes=stats.get(spider.CRASHES, 0) + stats.get("log_count/ERROR", 0),
        examples=tuple(str(value) for key, value in stats.items() if key.startswith(spider.EXAMPLE)),
    )
    job.stats.write_text(report.model_dump_json())


PROGRESS_SECONDS = 15.0


async def check(root: Path, keys: Iterable[str]) -> bool:
    passed = True
    for site in (Sites(root).get(key) for key in keys):
        fixtures = TypeAdapter(list[Fixture]).validate_json(site.fixtures.read_bytes())
        passed = await run(root, site, live=False, fixtures=fixtures) and passed
    return passed


async def live(root: Path, key: str, browser: str) -> bool:
    """One crawl of the live portal exactly as Foundry runs it, from the stealth browser at `browser`."""
    site = Sites(root).get(key)
    return await run(root, site, live=True, browser=browser, home=site.url, delay=site.delay)


async def run(root: Path, site: Site, live: bool, **job: object) -> bool:
    """Crawls `site` in a process of its own, as Foundry does, and prints what it found; True if it passed. A live
    crawl also downloads the attachments, as foundry's do, and prints its progress every PROGRESS_SECONDS."""
    with tempfile.TemporaryDirectory(prefix="foundry-run-") as folder:
        out = Path(folder)
        extra = {"files": out / "files", "progress": out / "progress.json"} if live else {}
        crawl_job = CrawlJob.model_validate(
            {"spider": site.spider, "arguments": site.arguments, "listings": out / "listings.jsonl",
             "stats": out / "stats.json", **extra, **job}
        )  # fmt: skip
        if crawl_job.files is not None:
            crawl_job.files.mkdir()
        (out / "job.json").write_text(crawl_job.model_dump_json())
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "foundry.core", "crawl", str(out / "job.json"), cwd=root
        )
        while True:
            try:
                await asyncio.wait_for(process.wait(), PROGRESS_SECONDS)
                break
            except TimeoutError:
                if crawl_job.progress is not None and crawl_job.progress.exists():
                    now = CrawlProgress.model_validate_json(crawl_job.progress.read_text())
                    print(f"... {now.listings} listings, {now.files_saved} files, {now.requests} requests; "
                          f"last {now.last_url[:100]}", flush=True)  # fmt: skip
        stats = (
            CrawlStats.model_validate_json(crawl_job.stats.read_text())
            if crawl_job.stats.exists()
            else CrawlStats(crashes=1)
        )
        listings = crawl_job.listings.read_text().splitlines() if crawl_job.listings.exists() else []
    refused = f", refused with {stats.refused}" if stats.refused else ""
    refused += f", {stats.challenged} stopped by bot checks" if stats.challenged else ""
    refused += (
        f", rate-limited {stats.rate_limited} times and slowed to {stats.slowed_to:g} s between requests"
        if stats.rate_limited
        else ""
    )
    if live:
        refused += f", {stats.files_saved} files downloaded, {stats.files_failed} failed"
    print(f"{'PASS' if stats.passed else 'FAIL'} {site.key}: {stats.valid} listings, {stats.invalid} invalid, "
          f"{stats.crashes} crashes, logged out {stats.logged_out}{refused}")  # fmt: skip
    for example in stats.examples:
        print(f"    {example}")
    for line in listings[:2]:
        print(f"    {line[:300]}")
    return stats.passed


async def solve(url: str, browser: str, seconds: float) -> bool:
    """Opens `url` in a new tab of the browser and reports what its bot check did."""
    async with async_playwright() as playwright:
        connected = await playwright.chromium.connect_over_cdp(browser)
        page = await connected.contexts[0].new_page()
        took = await challenges.clear(page, url, seconds)
        names = sorted(cookie["name"] for cookie in await connected.contexts[0].cookies(url))
        landed = page.url
    if took is None:
        print(f"STILL CHALLENGED after {seconds:.0f} s at {landed}. Cookies: {', '.join(names) or 'none'}")
        return False
    if took == 0:
        print(f"NO CHECK: the page loaded at {landed}. Cookies: {', '.join(names) or 'none'}")
    else:
        print(f"CLEARED after {took} s, now at {landed}. Cookies: {', '.join(names) or 'none'}")
    return True


def changed(root: Path, base: str) -> list[str]:
    diff = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD"], cwd=root, capture_output=True, text=True)
    return [site.key for site in Sites(root).touched(diff.stdout.split())]


def main() -> None:
    parser = argparse.ArgumentParser(prog="foundry.core")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("crawl").add_argument("job", type=Path)
    checking = commands.add_parser("check")
    checking.add_argument("keys", nargs="*")
    checking.add_argument("--changed", metavar="BASE")
    checking.add_argument("--all", action="store_true")
    crawling = commands.add_parser("live")
    crawling.add_argument("key")
    crawling.add_argument("--browser", required=True, metavar="CDP_URL")
    recording = commands.add_parser("record")
    recording.add_argument("out", type=Path)
    recording.add_argument("--browser", required=True, metavar="CDP_URL")
    recording.add_argument("--seconds", type=float)
    needing = commands.add_parser("needs")
    needing.add_argument("recording", type=Path)
    needing.add_argument("match")
    needing.add_argument("--browser", required=True, metavar="CDP_URL")
    solving = commands.add_parser("solve")
    solving.add_argument("url")
    solving.add_argument("--browser", required=True, metavar="CDP_URL")
    solving.add_argument("--seconds", type=float, default=90.0)
    picking = commands.add_parser("fixtures")
    picking.add_argument("recording", type=Path)
    picking.add_argument("matches", nargs="+")
    options = parser.parse_args()
    root = Path.cwd()
    match options.command:
        case "crawl":
            asyncio.run(crawl(CrawlJob.model_validate_json(options.job.read_text())))
        case "check":
            if options.all:
                keys = [site.key for site in Sites(root).all()]
            elif options.changed:
                keys = changed(root, options.changed)
            else:
                keys = options.keys
            if not keys:
                print("No sites to check.")
                return
            sys.exit(0 if asyncio.run(check(root, keys)) else 1)
        case "live":
            sys.exit(0 if asyncio.run(live(root, options.key, options.browser)) else 1)
        case "record":
            asyncio.run(Recorder(options.out).record(options.browser, options.seconds))
        case "needs":
            asyncio.run(recorder.Needs(options.recording).report(options.match, options.browser))
        case "solve":
            sys.exit(0 if asyncio.run(solve(options.url, options.browser, options.seconds)) else 1)
        case "fixtures":
            chosen = recorder.fixtures(options.recording, options.matches)
            print(TypeAdapter(list[Fixture]).dump_json(chosen, indent=2).decode())


if __name__ == "__main__":
    main()
