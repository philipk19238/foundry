import asyncio
import datetime as dt
import hashlib
import shutil
import sys
import tempfile
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import BinaryIO

from pydantic import ValidationError

import foundry
from foundry.app import exceptions
from foundry.app.browser.browser import Browsers
from foundry.app.core.models import Lease
from foundry.app.db.db import Sessionmaker, now
from foundry.app.db.models.catalog import SourceStatus
from foundry.app.files.models import Bucket
from foundry.app.scrapers import store
from foundry.app.scrapers.models.crawl import (
    FIXTURES,
    BronzeRecord,
    Crawl,
    CrawlReport,
    CrawlState,
    CrawlWatch,
    Health,
    Verdict,
)
from foundry.app.scrapers.models.version import Proposal, ScraperVersion, ScraperView, Standing
from foundry.core.har import Har, HarEntry, HarLog
from foundry.core.models import Access, CrawlJob, CrawlProgress, CrawlStats, Listing, SavedFile, Site
from foundry.core.sites import Sites

REPLAY_TIMEOUT = 600
POLL_SECONDS = 5
KEEP_SECONDS = 60
STOP_SECONDS = 60
KIT = str(Path(foundry.__file__).parents[1])
STDERR_TAIL = 2000
HAR_TYPE = "application/json"


class Versions:
    """Each source's spider, as versions. A run proposes one with a pull request; it becomes the version the source
    runs only once a person merged it."""

    def __init__(self, sessionmaker: Sessionmaker) -> None:
        self._sessionmaker = sessionmaker

    async def current(self, source_id: uuid.UUID) -> ScraperVersion:
        async with self._sessionmaker() as session:
            scraper = await store.current(session, source_id)
        if scraper is None:
            raise exceptions.NoScraper(source_id)
        return ScraperVersion.model_validate(scraper)

    async def proposed(self, source_id: uuid.UUID) -> Proposal | None:
        async with self._sessionmaker() as session:
            scraper = await store.proposed(session, source_id)
        return Proposal.model_validate(scraper) if scraper is not None else None

    async def of_pull(self, source_id: uuid.UUID, pull: int) -> int | None:
        async with self._sessionmaker() as session:
            return await store.of_pull(session, source_id, pull)

    async def standing(self, source_id: uuid.UUID) -> Standing:
        async with self._sessionmaker() as session:
            live = await store.current(session, source_id) is not None
            proposed = await store.proposed(session, source_id) is not None
        return Standing(live=live, proposed=proposed)

    async def propose(self, lease: Lease, site: str, branch: str, pull: int) -> int:
        async with self._sessionmaker.begin() as session:
            version = await store.propose(session, lease, site, branch, pull, now())
        if version is None:
            raise exceptions.LeaseLost()
        return version

    async def merged(self, lease: Lease, proposal: Proposal, commit: str) -> None:
        async with self._sessionmaker.begin() as session:
            if not await store.merged(session, lease, proposal.version, proposal.site, commit):
                raise exceptions.LeaseLost()

    async def closed(self, lease: Lease, proposal: Proposal) -> None:
        async with self._sessionmaker.begin() as session:
            if not await store.closed(session, lease, proposal.version):
                raise exceptions.LeaseLost()


class ScraperHistory:
    """Every version of each source's spider, merged or not, and which run's agent built it."""

    def __init__(self, sessionmaker: Sessionmaker) -> None:
        self._sessionmaker = sessionmaker

    async def built_by(self, run_id: uuid.UUID) -> list[ScraperView]:
        async with self._sessionmaker() as session:
            return [ScraperView.model_validate(scraper) for scraper in await store.built_by(session, run_id)]

    async def of_source(self, source_id: uuid.UUID) -> list[ScraperView]:
        async with self._sessionmaker() as session:
            return [ScraperView.model_validate(scraper) for scraper in await store.of_source(session, source_id)]

    async def reviewing_sources(self) -> set[uuid.UUID]:
        async with self._sessionmaker() as session:
            return set(await store.reviewing_sources(session))

    async def newest(self, source_id: uuid.UUID) -> ScraperView:
        async with self._sessionmaker() as session:
            scraper = await store.newest(session, source_id)
        if scraper is None:
            raise exceptions.NoScraper(source_id)
        return ScraperView.model_validate(scraper)


class Crawls:
    """Runs a site's spider in a process of its own, from a tree of the repo (a run's checkout, or `main` at the
    commit that merged it): live with a session, or offline against the site's fixtures. The spider comes from the
    tree and the kit it runs on (`foundry.core`: sessions, the stealth browser, replay) from this app, so a version
    merged long ago still crawls with today's kit; spiders import only the kit's public names."""

    def __init__(self, browsers: Browsers, bucket: Bucket, state: CrawlState) -> None:
        self._browsers = browsers
        self._bucket = bucket
        self._state = state
        self._python = sys.executable

    async def live(
        self, tree: Path, source_id: uuid.UUID, key: str, access: Access, previous_valid: int,
        watch: CrawlWatch, known: Mapping[str, SavedFile],
    ) -> CrawlReport:  # fmt: skip
        """A crawl of the live portal, always from a stealth browser on a residential address, which gets past most
        bot checks. It downloads every attachment not in `known` as it goes and uploads it to the bucket at once, keeps
        everything the portal sent as bronze (uploaded the same way, and a HAR of it at the end), and keeps its state
        in the run's folder, so a crawl whose worker died, that stalled, or that a person restarted carries on where
        it got to. One that sends no new request for its site's `stall` minutes is stopped."""
        site = self.site(tree, key)
        root = self._state.folder(watch.run_id)
        folder = self._taken_over(root, watch.attempt)
        (folder / "files").mkdir(parents=True, exist_ok=True)
        (folder / "bronze").mkdir(exist_ok=True)
        (folder / "stats.json").unlink(missing_ok=True)
        uploads = Uploads(self._bucket, folder / "files")
        bronze = BronzeUploads(self._bucket, folder / "bronze")
        async with self._browsers.bare(source_id) as browser:
            job = CrawlJob(
                spider=site.spider,
                arguments=site.arguments,
                listings=folder / "listings.jsonl",
                stats=folder / "stats.json",
                session=access.session,
                credentials=access.credentials,
                browser=browser.cdp_url,
                home=site.url,
                delay=site.delay,
                files=folder / "files",
                known_files=sorted(known),
                progress=folder / "progress.json",
                state=folder / "scrapy",
                bronze=folder / "bronze",
            )
            stall = dt.timedelta(minutes=site.stall)
            returncode = await self._watched(tree, job, folder / "stderr.log", watch, uploads, bronze, stall)
            headers = access.session.headers if access.session is not None else {}
            session = await self._browsers.session(browser, headers)
        await uploads.upload_new()
        await bronze.upload_new()
        record = await bronze.har()
        report = self._report(job, returncode, folder / "stderr.log", previous_valid)
        await asyncio.to_thread(shutil.rmtree, root, ignore_errors=True)
        await self._state.keep()
        return report.from_browser(session, uploads.saved, record)

    def _taken_over(self, root: Path, attempt: int) -> Path:
        """This attempt's folder, holding the newest earlier attempt's state: renamed, not shared, so a worker that
        only went quiet and is still stopping writes to a folder no attempt reads again."""
        folder = root / str(attempt)
        earlier = sorted((int(older.name) for older in root.glob("*") if older.name.isdigit()), reverse=True)
        newest = next((number for number in earlier if number < attempt), None)
        if newest is not None and not folder.exists():
            (root / str(newest)).rename(folder)
        return folder

    async def replay(self, tree: Path, key: str) -> CrawlReport:
        site = self.site(tree, key)
        try:
            fixtures = FIXTURES.validate_json(site.fixtures.read_bytes())
        except (OSError, ValidationError) as error:
            raise exceptions.SiteMissing(key, f"its fixtures don't load: {error}") from error
        with tempfile.TemporaryDirectory(prefix="foundry-crawl-") as folder:
            root = Path(folder)
            job = CrawlJob(
                spider=site.spider,
                arguments=site.arguments,
                listings=root / "listings.jsonl",
                stats=root / "stats.json",
                fixtures=fixtures,
                home=site.url,
            )
            (root / "job.json").write_text(job.model_dump_json())
            with (root / "stderr.log").open("wb") as stderr:
                process = await self._spawn(tree, root / "job.json", stderr)
                try:
                    async with asyncio.timeout(REPLAY_TIMEOUT):
                        await process.wait()
                except TimeoutError:
                    process.kill()
                    await process.wait()
            return self._report(job, process.returncode, root / "stderr.log", 0)

    def site(self, tree: Path, key: str) -> Site:
        try:
            return Sites(tree).get(key)
        except (OSError, ValueError, ValidationError) as error:
            raise exceptions.SiteMissing(key, str(error)) from error

    async def _spawn(self, tree: Path, job: Path, stderr: BinaryIO) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            self._python, "-m", "foundry.core", "crawl", str(job),
            cwd=tree, env={"PYTHONPATH": KIT, "PATH": "/usr/bin:/bin"},
            stdout=asyncio.subprocess.DEVNULL, stderr=stderr,
        )  # fmt: skip

    async def _watched(
        self, tree: Path, job: CrawlJob, log: Path, watch: CrawlWatch, uploads: "Uploads", bronze: "BronzeUploads",
        stall: dt.timedelta,
    ) -> int | None:  # fmt: skip
        """Runs the crawl, and every POLL_SECONDS uploads what it saved and beats the run with its progress; every
        KEEP_SECONDS, keeps its state. Stops it when the run says so, or when it sent no request for `stall`: SIGTERM,
        so it finishes what it has under way and saves its queue, then a kill after STOP_SECONDS."""
        assert job.progress is not None
        job_file = log.with_name("job.json")
        job_file.write_text(job.model_dump_json())
        loop = asyncio.get_running_loop()
        moved_at = kept_at = loop.time()
        requests = -1
        with log.open("ab") as stderr:
            process = await self._spawn(tree, job_file, stderr)
            try:
                while True:
                    try:
                        return await asyncio.wait_for(process.wait(), POLL_SECONDS)
                    except TimeoutError:
                        pass
                    await uploads.upload_new()
                    await bronze.upload_new()
                    if job.progress.exists():
                        progress = CrawlProgress.model_validate_json(job.progress.read_text())
                        if progress.requests != requests:
                            requests, moved_at = progress.requests, loop.time()
                        if not await watch.beat(progress):
                            raise exceptions.CrawlStopped()
                    if loop.time() - moved_at > stall.total_seconds():
                        raise exceptions.CrawlStalled(f"sent no request for {stall}")
                    if loop.time() - kept_at > KEEP_SECONDS:
                        await self._state.keep()
                        kept_at = loop.time()
            finally:
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), STOP_SECONDS)
                    except TimeoutError:
                        process.kill()
                        await process.wait()
                    await self._state.keep()

    def _report(self, job: CrawlJob, returncode: int | None, log: Path, previous_valid: int) -> CrawlReport:
        if returncode != 0 or not job.stats.exists():
            tail = log.read_bytes()[-STDERR_TAIL:].decode(errors="replace") if log.exists() else ""
            crashed = CrawlStats(crashes=1, examples=(tail,))
            return CrawlReport(listings=(), crawl=Crawl.of(crashed, previous_valid, job))
        stats = CrawlStats.model_validate_json(job.stats.read_text())
        lines = job.listings.read_text().splitlines() if job.listings.exists() else []
        listings = tuple(Listing.model_validate_json(line) for line in lines)
        return CrawlReport(listings=listings, crawl=Crawl.of(stats, previous_valid, job))


class Uploads:
    """The attachments a crawl saved to its files folder, by unsigned URL, uploaded to the bucket as they appear (each
    distinct file once) and then deleted.
    `uploaded` in the folder counts the lines of `files.jsonl` already done, so a crawl carried on by another worker
    doesn't upload them again."""

    def __init__(self, bucket: Bucket, folder: Path) -> None:
        self._bucket = bucket
        self._folder = folder
        self.saved: dict[str, SavedFile] = {}
        self._put: set[str] = set()

    async def upload_new(self) -> None:
        log = self._folder / "files.jsonl"
        cursor = self._folder / "uploaded"
        lines = log.read_text().split("\n")[:-1] if log.exists() else []
        done = int(cursor.read_text()) if cursor.exists() else 0
        for number, line in enumerate(lines):
            saved = SavedFile.model_validate_json(line)
            self.saved[saved.key] = saved
            if number < done:
                continue
            content = self._folder / saved.sha256
            if content.exists() and saved.sha256 not in self._put:
                await self._bucket.put(saved.sha256, await asyncio.to_thread(content.read_bytes), saved.content_type)
                self._put.add(saved.sha256)
            content.unlink(missing_ok=True)
            cursor.write_text(str(number + 1))


class BronzeUploads:
    """Everything the portal sent a crawl, as the crawl wrote it to its bronze folder: each response's body, uploaded
    to the bucket under its SHA-256 as it appears (each distinct body once) and then deleted, and at the end the HAR
    of every response, put beside them. `uploaded` in the folder is how far into `entries.jsonl` the bodies are done,
    in bytes, so a crawl carried on by another worker doesn't upload them again."""

    def __init__(self, bucket: Bucket, folder: Path) -> None:
        self._bucket = bucket
        self._folder = folder
        self._put: set[str] = set()

    async def upload_new(self) -> None:
        log = self._folder / "entries.jsonl"
        cursor = self._folder / "uploaded"
        done = int(cursor.read_text()) if cursor.exists() else 0
        if not log.exists():
            return
        with log.open("rb") as entries:
            entries.seek(done)
            written = entries.read()
        for line in written.split(b"\n")[:-1]:
            content = HarEntry.model_validate_json(line).response.content
            body = self._folder / content.sha256
            if body.exists() and content.sha256 not in self._put:
                await self._bucket.put(content.sha256, await asyncio.to_thread(body.read_bytes), content.mime_type)
                self._put.add(content.sha256)
            body.unlink(missing_ok=True)
            done += len(line) + 1
            cursor.write_text(str(done))

    async def har(self) -> BronzeRecord:
        """The HAR of every response the crawl got, put in the bucket under the SHA-256 of its bytes."""
        har = await asyncio.to_thread(self.read)
        document = (await asyncio.to_thread(har.model_dump_json, exclude_none=True)).encode()
        digest = hashlib.sha256(document).hexdigest()
        await self._bucket.put(digest, document, HAR_TYPE)
        return BronzeRecord(har=digest, entries=len(har.log.entries))

    def read(self) -> Har:
        """The crawl's entries so far, as one HAR document."""
        log = self._folder / "entries.jsonl"
        lines = log.read_bytes().split(b"\n")[:-1] if log.exists() else []
        return Har(log=HarLog(entries=tuple(HarEntry.model_validate_json(line) for line in lines)))


class CrawlFolders:
    """Live crawls' state in a folder on this machine's disk, which outlives the process but not the machine."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def folder(self, run_id: uuid.UUID) -> Path:
        return self._root / str(run_id)

    async def keep(self) -> None:
        return None


class Healths:
    """Where each source stands after a crawl: ready, logged out, being healed, or waiting for a person."""

    def __init__(self, sessionmaker: Sessionmaker) -> None:
        self._sessionmaker = sessionmaker

    async def record(self, lease: Lease, health: Health, crawl: Crawl) -> Health:
        updated = health.after(crawl)
        found = crawl.valid if crawl.verdict == Verdict.HEALTHY else crawl.previous_valid
        async with self._sessionmaker.begin() as session:
            if not await store.set_health(session, lease, updated.status, updated.heals, found):
                raise exceptions.LeaseLost()
        return updated

    async def set(self, lease: Lease, status: SourceStatus) -> None:
        async with self._sessionmaker.begin() as session:
            if not await store.set_status(session, lease, status):
                raise exceptions.LeaseLost()
