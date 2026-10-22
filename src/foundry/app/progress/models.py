import enum
import uuid

from pydantic import BaseModel

from foundry.app.catalog.models import SourceView
from foundry.app.db.models.catalog import ScraperState, SourceStatus
from foundry.app.db.models.runs import RunKind
from foundry.app.help.models import HelpView
from foundry.app.runs.models import RunView
from foundry.app.scrapers.models import Crawl, ScraperView
from foundry.core.models import Listing


class Stage(enum.StrEnum):
    """Where a scraper stands, as a person follows it (formal/foundry_rules `stageOf`): an agent building it or
    waiting on them, a version ready for review, live, or stopped with nothing under way."""

    BUILDING = "building"
    WAITING = "waiting"
    REVIEW = "review"
    LIVE = "live"
    STOPPED = "stopped"

    @classmethod
    def of(cls, status: SourceStatus, signals: "Signals") -> "Stage":
        """The one rule for every page: a question waiting beats an agent working, which beats a version in review;
        only a source the schedule still scrapes is live."""
        if signals.waiting:
            return cls.WAITING
        if signals.working:
            return cls.BUILDING
        if signals.reviewing:
            return cls.REVIEW
        if status == SourceStatus.READY:
            return cls.LIVE
        return cls.STOPPED

    @property
    def label(self) -> str:
        return LABELS[self]

    @property
    def step(self) -> int:
        """Which of the track's steps (Requested, Building, Ready for review, Live) it lights."""
        return STEPS[self]

    @property
    def opens_on(self) -> str:
        """The tab a scraper's page opens on at this stage."""
        return OPENS_ON[self]


LABELS = {
    Stage.BUILDING: "Building",
    Stage.WAITING: "Needs you",
    Stage.REVIEW: "Ready for review",
    Stage.LIVE: "Live",
    Stage.STOPPED: "Stopped",
}
STEPS = {Stage.BUILDING: 1, Stage.WAITING: 1, Stage.STOPPED: 1, Stage.REVIEW: 2, Stage.LIVE: 3}
OPENS_ON = {
    Stage.BUILDING: "chat",
    Stage.WAITING: "chat",
    Stage.STOPPED: "chat",
    Stage.REVIEW: "test",
    Stage.LIVE: "data",
}


class Signals(BaseModel, frozen=True):
    """What is under way on a source beside its status: an agent job working (or waiting its turn), the agent's
    question waiting on a person, a version waiting in its pull request."""

    working: bool
    waiting: bool
    reviewing: bool


class AgentJob(BaseModel, frozen=True):
    """The agent job a scraper's conversation is in, its live question, and what the agent said last."""

    run: RunView
    question: HelpView | None
    last_message: str | None


class TrialView(BaseModel):
    """What a test crawl found, read back from the stats it finished with."""

    scraper: ScraperView
    crawl: Crawl
    listings: list[Listing]

    @classmethod
    def of(cls, run: RunView) -> "TrialView | None":
        if run.kind != RunKind.TRIAL or run.stats is None:
            return None
        return cls.model_validate(run.stats)

    @property
    def described(self) -> str:
        """The test in a line, for the agent reading a person's request about it."""
        crawl = self.crawl
        found = f"{crawl.valid} listings, {crawl.invalid} rejected, verdict {crawl.verdict}"
        refused = f"; the portal turned it away with {crawl.refused}" if crawl.refused else ""
        browser = ", run from a stealth browser" if crawl.browser else ""
        errors = "".join(f"\n- {error}" for error in crawl.errors[:3])
        return f"(About my test scrape of v{self.scraper.version}: {found}{refused}{browser}.){errors}"


class TrialState(BaseModel):
    """A source's latest finished test crawl and what it found, and one still running, if one is."""

    finished: RunView | None
    trial: TrialView | None
    running: RunView | None

    @classmethod
    def of(cls, finished: RunView | None, runs: list[RunView]) -> "TrialState":
        running = next((run for run in runs if run.kind == RunKind.TRIAL and run.is_active), None)
        return cls(finished=finished, trial=None if finished is None else TrialView.of(finished), running=running)


class Progress(BaseModel):
    """One scraper's page: its stage, the agent job its conversation is in, its newest version, the agent's live
    question, and a merge check or crawl under way."""

    source: SourceView
    stage: Stage
    agent: RunView | None
    newest: ScraperView | None
    question: HelpView | None
    merging: bool
    crawling: bool
    last_message: str | None
    runs: list[RunView]
    scrapers: list[ScraperView]

    @property
    def source_id(self) -> uuid.UUID:
        return self.source.id

    @classmethod
    def of(
        cls, source: SourceView, job: AgentJob | None, runs: list[RunView], scrapers: list[ScraperView]
    ) -> "Progress":
        newest = next((scraper for scraper in scrapers if scraper.state != ScraperState.CLOSED), None)
        signals = Signals(
            working=job is not None and job.run.is_active,
            waiting=job is not None and job.question is not None,
            reviewing=newest is not None and newest.state == ScraperState.PROPOSED,
        )
        return cls(
            source=source,
            stage=Stage.of(source.status, signals),
            agent=None if job is None else job.run,
            newest=newest,
            question=None if job is None else job.question,
            merging=any(run.kind == RunKind.REVIEW and run.is_active for run in runs),
            crawling=any(run.kind == RunKind.REFRESH and run.is_active for run in runs),
            last_message=None if job is None else job.last_message,
            runs=runs,
            scrapers=scrapers,
        )
