import datetime as dt
import enum
import uuid
from typing import assert_never

from pydantic import BaseModel, ConfigDict, TypeAdapter

from foundry.app.catalog.models import IngestCounts
from foundry.app.db.models.catalog import ScraperState, SourceStatus
from foundry.core.models import CrawlStats, Fixture, Listing

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
    """What one crawl saw. `previous_valid` is how many notices the last healthy crawl found."""

    auth_failed: bool
    crashed: bool
    invalid: int
    valid: int
    previous_valid: int
    errors: tuple[str, ...] = ()

    @classmethod
    def of(cls, stats: CrawlStats, previous_valid: int) -> "Crawl":
        return cls(
            auth_failed=stats.logged_out,
            crashed=stats.crashes > 0,
            invalid=stats.invalid,
            valid=stats.valid,
            previous_valid=previous_valid,
            errors=stats.examples,
        )

    @property
    def verdict(self) -> Verdict:
        """formal/foundry_rules `verdict`. A scraper that used to find notices and now finds none is broken, even
        with no error: that is what a portal redesign usually looks like."""
        if self.auth_failed:
            return Verdict.RELOGIN
        if self.crashed or self.invalid > 0 or (self.valid == 0 and self.previous_valid > 0):
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
