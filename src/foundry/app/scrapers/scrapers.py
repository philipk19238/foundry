import asyncio
import hashlib
import sys
import tempfile
import uuid
from pathlib import Path

import httpx
from pydantic import ValidationError

import foundry
from foundry.app import exceptions
from foundry.app.browser.browser import Browsers
from foundry.app.catalog.models import ScrapedNotice, StoredFile
from foundry.app.core.models import Lease
from foundry.app.db.db import Sessionmaker, now
from foundry.app.db.models.catalog import SourceStatus
from foundry.app.files.models import Bucket
from foundry.app.scrapers import models, store
from foundry.core.models import Access, CrawlJob, CrawlStats, Fixture, Listing, Session, Site
from foundry.core.sites import Sites

CRAWL_TIMEOUT = 1800
KIT = str(Path(foundry.__file__).parents[1])
STDERR_TAIL = 2000


class Versions:
    """Each source's spider, as versions. A run proposes one with a pull request; it becomes the version the source
    runs only once a person merged it."""

    def __init__(self, sessionmaker: Sessionmaker) -> None:
        self._sessionmaker = sessionmaker

    async def current(self, source_id: uuid.UUID) -> models.ScraperVersion:
        async with self._sessionmaker() as session:
            scraper = await store.current(session, source_id)
        if scraper is None:
            raise exceptions.NoScraper(source_id)
        return models.ScraperVersion.model_validate(scraper)

    async def proposed(self, source_id: uuid.UUID) -> models.Proposal | None:
        async with self._sessionmaker() as session:
            scraper = await store.proposed(session, source_id)
        return models.Proposal.model_validate(scraper) if scraper is not None else None

    async def of_pull(self, source_id: uuid.UUID, pull: int) -> int | None:
        async with self._sessionmaker() as session:
            return await store.of_pull(session, source_id, pull)

    async def standing(self, source_id: uuid.UUID) -> models.Standing:
        async with self._sessionmaker() as session:
            live = await store.current(session, source_id) is not None
            proposed = await store.proposed(session, source_id) is not None
        return models.Standing(live=live, proposed=proposed)

    async def propose(self, lease: Lease, site: str, branch: str, pull: int) -> int:
        async with self._sessionmaker.begin() as session:
            version = await store.propose(session, lease, site, branch, pull, now())
        if version is None:
            raise exceptions.LeaseLost()
        return version

    async def merged(self, lease: Lease, proposal: models.Proposal, commit: str) -> None:
        async with self._sessionmaker.begin() as session:
            if not await store.merged(session, lease, proposal.version, proposal.site, commit):
                raise exceptions.LeaseLost()

    async def closed(self, lease: Lease, proposal: models.Proposal) -> None:
        async with self._sessionmaker.begin() as session:
            if not await store.closed(session, lease, proposal.version):
                raise exceptions.LeaseLost()


class ScraperHistory:
    """Every version of each source's spider, merged or not, and which run's agent built it."""

    def __init__(self, sessionmaker: Sessionmaker) -> None:
        self._sessionmaker = sessionmaker

    async def built_by(self, run_id: uuid.UUID) -> list[models.ScraperView]:
        async with self._sessionmaker() as session:
            return [models.ScraperView.model_validate(scraper) for scraper in await store.built_by(session, run_id)]

    async def of_source(self, source_id: uuid.UUID) -> list[models.ScraperView]:
        async with self._sessionmaker() as session:
            return [models.ScraperView.model_validate(scraper) for scraper in await store.of_source(session, source_id)]

    async def reviewing_sources(self) -> set[uuid.UUID]:
        async with self._sessionmaker() as session:
            return set(await store.reviewing_sources(session))

    async def newest(self, source_id: uuid.UUID) -> models.ScraperView:
        async with self._sessionmaker() as session:
            scraper = await store.newest(session, source_id)
        if scraper is None:
            raise exceptions.NoScraper(source_id)
        return models.ScraperView.model_validate(scraper)


class Crawls:
    """Runs a site's spider in a process of its own, from a tree of the repo (a run's checkout, or `main` at the
    commit that merged it): live with a session, or offline against the site's fixtures. The spider comes from the
    tree and the kit it runs on (`foundry.core`: sessions, the stealth browser, replay) from this app, so a version
    merged long ago still crawls with today's kit; spiders import only the kit's public names."""

    def __init__(self, browsers: Browsers) -> None:
        self._browsers = browsers
        self._python = sys.executable

    async def live(
        self, tree: Path, source_id: uuid.UUID, key: str, access: Access, previous_valid: int
    ) -> models.CrawlReport:
        """A crawl of the live portal: plain first, and if the portal turns it away, the same crawl again from a
        stealth browser on a residential address, which gets past most bot checks. Only a refusal the browser gets
        too, with a saved session sent, asks for a new sign-in."""
        site = self.site(tree, key)
        report = await self._run(tree, site, access, None, None, previous_valid)
        if not report.crawl.blocked:
            return report
        async with self._browsers.bare(source_id) as browser:
            return await self._run(tree, site, access, None, browser.cdp_url, previous_valid)

    async def replay(self, tree: Path, key: str) -> models.CrawlReport:
        site = self.site(tree, key)
        try:
            fixtures = models.FIXTURES.validate_json(site.fixtures.read_bytes())
        except (OSError, ValidationError) as error:
            raise exceptions.SiteMissing(key, f"its fixtures don't load: {error}") from error
        return await self._run(tree, site, Access(), fixtures, None, 0)

    def site(self, tree: Path, key: str) -> Site:
        try:
            return Sites(tree).get(key)
        except (OSError, ValueError, ValidationError) as error:
            raise exceptions.SiteMissing(key, str(error)) from error

    async def _run(
        self, tree: Path, site: Site, access: Access, fixtures: list[Fixture] | None, browser: str | None,
        previous_valid: int,
    ) -> models.CrawlReport:  # fmt: skip
        with tempfile.TemporaryDirectory(prefix="foundry-crawl-") as folder:
            root = Path(folder)
            job = CrawlJob(
                spider=site.spider,
                arguments=site.arguments,
                listings=root / "listings.jsonl",
                stats=root / "stats.json",
                session=access.session,
                credentials=access.credentials,
                fixtures=fixtures,
                browser=browser,
                home=site.url,
            )
            (root / "job.json").write_text(job.model_dump_json())
            process = await asyncio.create_subprocess_exec(
                self._python, "-m", "foundry.core", "crawl", str(root / "job.json"),
                cwd=tree, env={"PYTHONPATH": KIT, "PATH": "/usr/bin:/bin"},
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )  # fmt: skip
            try:
                async with asyncio.timeout(CRAWL_TIMEOUT):
                    _, stderr = await process.communicate()
            except TimeoutError:
                process.kill()
                stderr = b"the crawl ran past its time limit"
            if process.returncode != 0 or not job.stats.exists():
                tail = stderr.decode(errors="replace")[-STDERR_TAIL:]
                crashed = CrawlStats(crashes=1, examples=(tail,))
                return models.CrawlReport(listings=(), crawl=models.Crawl.of(crashed, previous_valid, job))
            stats = CrawlStats.model_validate_json(job.stats.read_text())
            lines = job.listings.read_text().splitlines() if job.listings.exists() else []
            listings = tuple(Listing.model_validate_json(line) for line in lines)
            return models.CrawlReport(listings=listings, crawl=models.Crawl.of(stats, previous_valid, job))


BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0 Safari/537.36"
    ),
    "Accept": "*/*",
}


class Downloads:
    """Fetches attachments with the portal session and keeps each in the bucket under the SHA-256 of its bytes. A
    file that will not download is stored without a hash rather than failing the crawl."""

    def __init__(self, http: httpx.AsyncClient, bucket: Bucket) -> None:
        self._http = http
        self._bucket = bucket

    async def fetch(self, listing: Listing, session: Session | None) -> tuple[StoredFile, ...]:
        signed_in = {**session.headers, "Cookie": session.cookie_header} if session is not None else {}
        headers = {**BROWSER_HEADERS, "Referer": listing.url, **signed_in}
        stored = []
        for link in listing.files:
            try:
                response = await self._http.get(link.url, headers=headers, follow_redirects=True)
                response.raise_for_status()
            except httpx.HTTPError:
                stored.append(
                    StoredFile(
                        file_id=ScrapedNotice.file_id(link),
                        name=link.name,
                        url=link.url,
                        sha256=None,
                        etag=None,
                        content_type=None,
                        size=0,
                    )  # fmt: skip
                )
                continue
            digest = hashlib.sha256(response.content).hexdigest()
            await self._bucket.put(digest, response.content, response.headers.get("content-type"))
            stored.append(
                StoredFile(
                    file_id=ScrapedNotice.file_id(link),
                    name=link.name,
                    url=link.url,
                    sha256=digest,
                    etag=response.headers.get("etag"),
                    content_type=response.headers.get("content-type"),
                    size=len(response.content),
                )
            )
        return tuple(stored)


class Healths:
    """Where each source stands after a crawl: ready, logged out, being healed, or waiting for a person."""

    def __init__(self, sessionmaker: Sessionmaker) -> None:
        self._sessionmaker = sessionmaker

    async def record(self, lease: Lease, health: models.Health, crawl: models.Crawl) -> models.Health:
        updated = health.after(crawl)
        found = crawl.valid if crawl.verdict == models.Verdict.HEALTHY else crawl.previous_valid
        async with self._sessionmaker.begin() as session:
            if not await store.set_health(session, lease, updated.status, updated.heals, found):
                raise exceptions.LeaseLost()
        return updated

    async def set(self, lease: Lease, status: SourceStatus) -> None:
        async with self._sessionmaker.begin() as session:
            if not await store.set_status(session, lease, status):
                raise exceptions.LeaseLost()
