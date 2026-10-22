import datetime as dt
import enum
import uuid
from typing import assert_never

from pydantic import BaseModel, ConfigDict, TypeAdapter

from foundry.app.catalog.models import IngestCounts
from foundry.app.db.models.catalog import ScraperState, SourceStatus
from foundry.app.db.models.runs import RunKind
from foundry.core.models import CrawlJob, CrawlStats, Fixture, Listing

MAX_HEALS = 3
FIXTURES = TypeAdapter(list[Fixture])


class ScraperVersion(BaseModel):
    """A merged version of a source's spider: the `main` commit that merged it, and its site key."""

    model_config = ConfigDict(from_attributes=True)

    source_id: uuid.UUID
    version: int
    site: str
    commit: str
    created_at: dt.datetime


class Proposal(BaseModel):
    """A version still in review: the pull request a run opened for it."""

    model_config = ConfigDict(from_attributes=True)

    source_id: uuid.UUID
    version: int
    site: str
    branch: str
    pull: int


class ScraperView(BaseModel):
    """Any version of a source's spider, merged or not, and the run whose agent built it."""

    model_config = ConfigDict(from_attributes=True)

    source_id: uuid.UUID
    version: int
    site: str
    state: ScraperState
    branch: str
    pull: int
    commit: str | None
    run_id: uuid.UUID
    created_at: dt.datetime

    @property
    def ref(self) -> str:
        """Where its code is: the merge commit once merged, else the tip of the agent's branch."""
        return self.commit if self.commit is not None else f"refs/heads/{self.branch}"


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
    signed_in: bool = True
    browser: bool = False

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
            signed_in=job.session is not None,
            browser=job.browser is not None,
        )

    @property
    def verdict(self) -> Verdict:
        """formal/foundry_rules `verdict`. A portal that turns away a scraper with no session isn't asking it to
        sign in: it is blocking the scraper, which is the scraper's to fix. A scraper that used to find notices and
        now finds none is broken, even with no error: that is what a portal redesign usually looks like."""
        if self.auth_failed and self.signed_in:
            return Verdict.RELOGIN
        if self.auth_failed or self.crashed or self.invalid > 0 or (self.valid == 0 and self.previous_valid > 0):
            return Verdict.HEAL
        return Verdict.HEALTHY

    @property
    def blocked(self) -> bool:
        """The portal turned away a plain crawl. Bot checks answer the way an expired session does, so a stealth
        browser gets the next try either way."""
        return self.auth_failed and not self.browser


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


class CrawlReport(BaseModel, frozen=True):
    listings: tuple[Listing, ...]
    crawl: Crawl


class RefreshReport(BaseModel, frozen=True):
    crawl: Crawl
    ingested: IngestCounts
    health: Health

    @property
    def summary(self) -> str:
        return (
            f"{self.crawl.verdict}: {self.crawl.valid} listings, {self.ingested.new} new, {self.ingested.amended} "
            f"amended, {self.ingested.unchanged} unchanged; source is {self.health.status}"
        )


class Standing(BaseModel, frozen=True):
    """Which versions a source has, which decides its status around an agent job (formal/foundry_rules
    `Standing`). A source with a live version keeps running and crawling while an agent reworks it; a job that ends
    without proposing anything puts the source back where its versions say."""

    live: bool
    proposed: bool

    def working(self, kind: RunKind) -> SourceStatus | None:
        """The status while the agent works; None leaves it as it is."""
        if kind == RunKind.HEAL:
            return SourceStatus.HEALING
        return None if self.live else SourceStatus.ONBOARDING

    def settled(self) -> SourceStatus:
        """The status after a job that proposed nothing, or a pull request closed without merging."""
        if self.live:
            return SourceStatus.READY
        if self.proposed:
            return SourceStatus.IN_REVIEW
        return SourceStatus.NEEDS_HUMAN

    def rejected(self, kind: RunKind) -> SourceStatus:
        """The status after a job whose work was rejected: a rejected repair leaves a broken scraper to a person."""
        return SourceStatus.NEEDS_HUMAN if kind == RunKind.HEAL else self.settled()
