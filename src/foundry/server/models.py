from pydantic import BaseModel

from foundry.app.catalog.models import SourceView
from foundry.app.db.models.runs import RunKind, RunStatus
from foundry.app.help.models import HelpView
from foundry.app.runs.models import EventView, RunView
from foundry.app.scrapers.models import Crawl, ScraperView
from foundry.core.models import Listing


class NewSource(BaseModel):
    url: str


class Onboarding(BaseModel):
    source: SourceView
    run: RunView


class TrialView(BaseModel):
    """What a trial run found, read back from the stats it finished with."""

    scraper: ScraperView
    crawl: Crawl
    listings: list[Listing]

    @classmethod
    def of(cls, run: RunView) -> "TrialView | None":
        if run.kind != RunKind.TRIAL or run.stats is None:
            return None
        return cls.model_validate(run.stats)


class TrialState(BaseModel):
    """A source's latest finished trial and what it found, and a trial still running, if one is."""

    finished: RunView | None
    trial: TrialView | None
    running: RunView | None

    @classmethod
    def of(cls, finished: RunView | None, runs: list[RunView]) -> "TrialState":
        running = next((run for run in runs if run.kind == RunKind.TRIAL and run.is_active), None)
        return cls(finished=finished, trial=None if finished is None else TrialView.of(finished), running=running)


class SourceDetail(BaseModel):
    """A source with every version of its scraper and its recent runs."""

    source: SourceView
    scrapers: list[ScraperView]
    runs: list[RunView]


class RunDetail(BaseModel):
    """A run as its page shows it: its state, open help requests, the scrapers its agent built, what a trial found,
    and the page's address for a person."""

    run: RunView
    help: list[HelpView]
    built: list[ScraperView]
    trial: TrialView | None
    url: str


class RunLines(BaseModel):
    """A run's lines after a cursor. Pass `after` back to read on; `waiting` is true while the agent waits on a
    person."""

    status: RunStatus
    summary: str | None
    waiting: bool
    lines: list[EventView]
    after: int
