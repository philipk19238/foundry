import uuid

import svcs
from pydantic import BaseModel

from foundry.app.auth.models import UserView
from foundry.app.catalog.models import SourceView
from foundry.app.db.models.runs import RunStatus
from foundry.app.help.help import HelpQueue
from foundry.app.help.models import HelpView
from foundry.app.progress.models import Progress, Stage, TrialView
from foundry.app.progress.progress import Tracker
from foundry.app.runs.models import EventView, RunView
from foundry.app.runs.runs import Timeline
from foundry.app.scrapers.models import ScraperView
from foundry.app.settings import Settings


class NewSource(BaseModel):
    url: str


class Onboarding(BaseModel):
    source: SourceView
    run: RunView


class ScraperSummary(BaseModel):
    """A portal and its scraper's stage, as the scrapers list shows it."""

    source: SourceView
    stage: Stage


class SourceDetail(BaseModel):
    """A scraper in full, and its page's address for a person."""

    progress: Progress
    url: str


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


class ScraperPage(BaseModel):
    """What every tab of a scraper's page shows around its own content: who is looking, where the scraper stands,
    which panel tab is open, the repo its pull requests are on, and the conversation with its agent, which sits
    beside every tab."""

    user: UserView
    progress: Progress
    tab: str
    repo: str
    events: list[EventView] = []
    help: list[HelpView] = []

    @classmethod
    async def from_container(cls, container: svcs.Container, source_id: uuid.UUID, tab: str) -> "ScraperPage":
        user, tracker, settings, timeline, desk = await container.aget(UserView, Tracker, Settings, Timeline, HelpQueue)
        progress = await tracker.progress(source_id)
        page = cls(user=user, progress=progress, tab=tab, repo=settings.repo)
        if progress.agent is None:
            return page
        events = await timeline.since(progress.agent.id, 0)
        return page.model_copy(update={"events": events, "help": await desk.of_run(progress.agent.id)})
