"""What a crawl saw and what it means for the source's health, and the seams a live crawl reports through."""

import enum
import uuid
from pathlib import Path
from typing import Protocol, assert_never

from pydantic import BaseModel, TypeAdapter

from foundry.app.catalog.models import IngestCounts
from foundry.app.db.models.catalog import SourceStatus
from foundry.core.models import CrawlJob, CrawlProgress, CrawlStats, Fixture, Listing, SavedFile, Session

MAX_HEALS = 3
FIXTURES = TypeAdapter(list[Fixture])


class Verdict(enum.StrEnum):
    HEALTHY = "healthy"
    RELOGIN = "relogin"
    HEAL = "heal"


class Crawl(BaseModel, frozen=True):
    """What one crawl saw. `previous_valid` is how many notices the last healthy crawl found. `refused` is the first
    response the portal turned the scraper away with; `signed_in` says whether the crawl sent a saved session, and
    `browser` whether it ran from a stealth browser."""

    auth_failed: bool
    crashed: bool
    invalid: int
    valid: int
    previous_valid: int
    errors: tuple[str, ...] = ()
    refused: str = ""
    challenged: int = 0
    rate_limited: int = 0
    slowed_to: float = 0
    signed_in: bool = True
    browser: bool = False
    files_saved: int = 0
    files_failed: int = 0
    renewed: int = 0

    @classmethod
    def broken(cls, why: str, previous_valid: int) -> "Crawl":
        """A crawl that never got to report: it stalled on every attempt, or its spider wouldn't load. The scraper's
        to fix, like a crash."""
        return cls(auth_failed=False, crashed=True, invalid=0, valid=0, previous_valid=previous_valid, errors=(why,))

    @classmethod
    def of(cls, stats: CrawlStats, previous_valid: int, job: CrawlJob) -> "Crawl":
        return cls(
            auth_failed=stats.logged_out,
            crashed=stats.crashes > 0,
            invalid=stats.invalid,
            valid=stats.valid,
            previous_valid=previous_valid,
            errors=stats.examples,
            refused=stats.refused,
            challenged=stats.challenged,
            rate_limited=stats.rate_limited,
            slowed_to=stats.slowed_to,
            signed_in=job.session is not None,
            browser=job.browser is not None,
            files_saved=stats.files_saved,
            files_failed=stats.files_failed,
            renewed=stats.renewed,
        )

    @property
    def verdict(self) -> Verdict:
        """formal/foundry_rules `verdict`. A portal that turns away a scraper with no session isn't asking it to
        sign in: it is blocking the scraper, which is the scraper's to fix, as is a bot check that stopped requests
        (the spider should `solve` it); no person is asked. A scraper that used to find notices and
        now finds none is broken, even with no error: that is what a portal redesign usually looks like."""
        if self.auth_failed and self.signed_in:
            return Verdict.RELOGIN
        if self.auth_failed or self.challenged or self.crashed or self.invalid > 0:
            return Verdict.HEAL
        if self.valid == 0 and self.previous_valid > 0:
            return Verdict.HEAL
        return Verdict.HEALTHY


class Health(BaseModel, frozen=True):
    status: SourceStatus
    heals: int

    def after(self, crawl: Crawl) -> "Health":
        """formal/foundry_rules `afterCrawl`: at most MAX_HEALS repairs (heals and relogins) in a row, then a person
        is asked. One healthy crawl clears the count."""
        match crawl.verdict:
            case Verdict.HEALTHY:
                return Health(status=SourceStatus.READY, heals=0)
            case _ if self.heals >= MAX_HEALS:
                return Health(status=SourceStatus.NEEDS_HUMAN, heals=self.heals)
            case Verdict.RELOGIN:
                return Health(status=SourceStatus.RELOGIN, heals=self.heals + 1)
            case Verdict.HEAL:
                return Health(status=SourceStatus.HEALING, heals=self.heals + 1)
            case _:
                assert_never(crawl.verdict)


class BronzeRecord(BaseModel, frozen=True):
    """Where a live crawl's bronze is: `har`, the SHA-256 its HAR is kept under in the bucket, beside the bodies its
    entries name by theirs, and how many responses it holds."""

    har: str
    entries: int


class CrawlReport(BaseModel, frozen=True):
    """What a crawl found, and for a live one, the browser's cookies when it finished: the bot checks it passed,
    kept as the source's session so the next crawl doesn't meet them again. `bronze` is everything the portal sent a
    live crawl; a replay has none."""

    listings: tuple[Listing, ...]
    crawl: Crawl
    session: Session | None = None
    files: dict[str, SavedFile] = {}
    bronze: BronzeRecord | None = None

    def from_browser(self, session: Session | None, files: dict[str, SavedFile], bronze: BronzeRecord) -> "CrawlReport":
        """The report of a live crawl, with what its browser held at the end, the attachments it downloaded, and
        everything the portal sent it."""
        return self.model_copy(update={"session": session, "files": files, "bronze": bronze})


class CrawlWatch(Protocol):
    """The run a live crawl belongs to, told how far the crawl has got every few seconds."""

    @property
    def run_id(self) -> uuid.UUID: ...

    @property
    def attempt(self) -> int: ...

    async def beat(self, progress: CrawlProgress) -> bool:
        """False once the crawl should stop: a person restarted it, or the run ended."""
        ...


class CrawlState(Protocol):
    """Where a live crawl keeps what must outlive its worker (the requests still to send, the listings so far, files
    not yet uploaded), one folder per run; each attempt works in a folder of its own inside it."""

    def folder(self, run_id: uuid.UUID) -> Path: ...

    async def keep(self) -> None:
        """Makes what was written so far survive this worker."""
        ...


class RefreshReport(BaseModel, frozen=True):
    crawl: Crawl
    ingested: IngestCounts
    health: Health
    bronze: BronzeRecord | None = None

    @property
    def summary(self) -> str:
        return (
            f"{self.crawl.verdict}: {self.crawl.valid} listings, {self.ingested.new} new, {self.ingested.amended} "
            f"amended, {self.ingested.unchanged} unchanged; source is {self.health.status}"
        )
