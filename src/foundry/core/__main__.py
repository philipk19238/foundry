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
  responses whose URL holds any of the parts."""

import argparse
import asyncio
import importlib.util
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path

from pydantic import TypeAdapter
from scrapy.crawler import AsyncCrawlerRunner
from scrapy.settings import Settings
from scrapy.utils.log import configure_logging

from foundry.core import recorder, spider
from foundry.core.models import CrawlJob, CrawlStats, Fixture, Site
from foundry.core.recorder import Recorder
from foundry.core.sites import Sites


async def crawl(job: CrawlJob) -> None:
    found = importlib.util.spec_from_file_location("spider", job.spider)
    assert found is not None and found.loader is not None
    module = importlib.util.module_from_spec(found)
    found.loader.exec_module(module)
    (portal,) = spider.spiders(module)
    middlewares = {"foundry.core.spider.SessionMiddleware": 500}
    if job.fixtures is not None:
        middlewares["foundry.core.spider.ReplayMiddleware"] = 950
    if job.browser is not None:
        middlewares["foundry.core.spider.BrowserMiddleware"] = 960
    settings = Settings(
        {
            "TWISTED_REACTOR_ENABLED": False,
            "DOWNLOADER_MIDDLEWARES": middlewares,
            "SPIDER_MIDDLEWARES": {"foundry.core.spider.ErrorsMiddleware": 100},
            "ITEM_PIPELINES": {"foundry.core.spider.ListingPipeline": 100},
            "FOUNDRY_LISTINGS": str(job.listings),
            "FOUNDRY_SESSION": job.session.model_dump_json() if job.session is not None else "",
            "FOUNDRY_CREDENTIALS": job.credentials,
            "FOUNDRY_BROWSER": job.browser or "",
            "FOUNDRY_HOME": job.home,
            "FOUNDRY_FIXTURES": [fixture.model_dump() for fixture in job.fixtures or []],
            "LOG_LEVEL": "INFO",
        }
    )
    configure_logging(settings)
    runner = AsyncCrawlerRunner(settings)
    crawler = runner.create_crawler(portal)
    await runner.crawl(crawler, **job.arguments)
    assert crawler.stats is not None
    stats = crawler.stats.get_stats()
    report = CrawlStats(
        valid=stats.get(spider.VALID, 0),
        invalid=stats.get(spider.INVALID, 0),
        logged_out=stats.get(spider.LOGGED_OUT, False),
        refused=stats.get(spider.REFUSED, ""),
        crashes=stats.get(spider.CRASHES, 0) + stats.get("log_count/ERROR", 0),
        examples=tuple(str(value) for key, value in stats.items() if key.startswith(spider.EXAMPLE)),
    )
    job.stats.write_text(report.model_dump_json())


async def check(root: Path, keys: Iterable[str]) -> bool:
    passed = True
    for site in (Sites(root).get(key) for key in keys):
        fixtures = TypeAdapter(list[Fixture]).validate_json(site.fixtures.read_bytes())
        passed = await run(root, site, fixtures=fixtures) and passed
    return passed


async def live(root: Path, key: str, browser: str) -> bool:
    """One crawl of the live portal exactly as Foundry runs it, from the stealth browser at `browser`."""
    site = Sites(root).get(key)
    return await run(root, site, browser=browser, home=site.url)


async def run(root: Path, site: Site, **job: object) -> bool:
    """Crawls `site` in a process of its own, as Foundry does, and prints what it found; True if it passed."""
    with tempfile.TemporaryDirectory(prefix="foundry-run-") as folder:
        out = Path(folder)
        crawl_job = CrawlJob.model_validate(
            {"spider": site.spider, "arguments": site.arguments, "listings": out / "listings.jsonl",
             "stats": out / "stats.json", **job}
        )  # fmt: skip
        (out / "job.json").write_text(crawl_job.model_dump_json())
        subprocess.run([sys.executable, "-m", "foundry.core", "crawl", str(out / "job.json")], cwd=root, check=False)
        stats = (
            CrawlStats.model_validate_json(crawl_job.stats.read_text())
            if crawl_job.stats.exists()
            else CrawlStats(crashes=1)
        )
        listings = crawl_job.listings.read_text().splitlines() if crawl_job.listings.exists() else []
    refused = f", refused with {stats.refused}" if stats.refused else ""
    print(f"{'PASS' if stats.passed else 'FAIL'} {site.key}: {stats.valid} listings, {stats.invalid} invalid, "
          f"{stats.crashes} crashes, logged out {stats.logged_out}{refused}")  # fmt: skip
    for example in stats.examples:
        print(f"    {example}")
    for line in listings[:2]:
        print(f"    {line[:300]}")
    return stats.passed


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
        case "fixtures":
            chosen = recorder.fixtures(options.recording, options.matches)
            print(TypeAdapter(list[Fixture]).dump_json(chosen, indent=2).decode())


if __name__ == "__main__":
    main()
