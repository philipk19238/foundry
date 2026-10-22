import asyncio
import hashlib
import sys
import tempfile
import uuid
from pathlib import Path

import httpx
from pydantic import ValidationError

from foundry.app import exceptions
from foundry.app.catalog.models import ScrapedNotice, StoredFile
from foundry.app.core.models import Lease
from foundry.app.db.db import Sessionmaker, now
from foundry.app.db.models.catalog import SourceStatus
from foundry.app.files.models import Bucket
from foundry.app.scrapers import models, store
from foundry.core.models import CrawlJob, CrawlStats, Fixture, Listing, Session, Site
from foundry.core.sites import Sites

CRAWL_TIMEOUT = 1800
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


class Crawls:
    """Runs a site's spider in a process of its own, from a tree of the repo (a run's checkout, or `main` at the
    commit that merged it), so the spider runs with that tree's core: live with a session, or offline against the
    site's fixtures."""

    def __init__(self) -> None:
        self._python = sys.executable

    async def live(self, tree: Path, key: str, session: Session | None, previous_valid: int) -> models.CrawlReport:
        return await self._run(tree, self.site(tree, key), session, None, previous_valid)

    async def replay(self, tree: Path, key: str) -> models.CrawlReport:
        site = self.site(tree, key)
        try:
            fixtures = models.FIXTURES.validate_json(site.fixtures.read_bytes())
        except (OSError, ValidationError) as error:
            raise exceptions.SiteMissing(key, f"its fixtures don't load: {error}") from error
        return await self._run(tree, site, None, fixtures, 0)

    def site(self, tree: Path, key: str) -> Site:
        try:
            return Sites(tree).get(key)
        except (OSError, ValueError, ValidationError) as error:
            raise exceptions.SiteMissing(key, str(error)) from error

    async def _run(
        self, tree: Path, site: Site, session: Session | None, fixtures: list[Fixture] | None, previous_valid: int
    ) -> models.CrawlReport:
        with tempfile.TemporaryDirectory(prefix="foundry-crawl-") as folder:
            root = Path(folder)
            job = CrawlJob(
                spider=site.spider,
                arguments=site.arguments,
                listings=root / "listings.jsonl",
                stats=root / "stats.json",
                session=session,
                fixtures=fixtures,
            )
            (root / "job.json").write_text(job.model_dump_json())
            process = await asyncio.create_subprocess_exec(
                self._python, "-m", "foundry.core", "crawl", str(root / "job.json"),
                cwd=tree, env={"PYTHONPATH": str(tree / "src"), "PATH": "/usr/bin:/bin"},
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
                return models.CrawlReport(listings=(), crawl=models.Crawl.of(crashed, previous_valid))
            stats = CrawlStats.model_validate_json(job.stats.read_text())
            lines = job.listings.read_text().splitlines() if job.listings.exists() else []
            listings = tuple(Listing.model_validate_json(line) for line in lines)
            return models.CrawlReport(listings=listings, crawl=models.Crawl.of(stats, previous_valid))


class Downloads:
    """Fetches attachments with the portal session and keeps each in the bucket under the SHA-256 of its bytes. A
    file that will not download is stored without a hash rather than failing the crawl."""

    def __init__(self, http: httpx.AsyncClient, bucket: Bucket) -> None:
        self._http = http
        self._bucket = bucket

    async def fetch(self, listing: Listing, session: Session | None) -> tuple[StoredFile, ...]:
        headers = {**session.headers, "Cookie": session.cookie_header} if session is not None else {}
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
