import datetime as dt
import enum
import uuid
from typing import Protocol

from pydantic import BaseModel, ConfigDict, JsonValue

from foundry.app.db.db import now
from foundry.app.db.models.runs import RunKind, RunStatus
from foundry.core.models import CrawlProgress


class EventKind(enum.StrEnum):
    STATUS = "status"
    MESSAGE = "message"
    COMMAND = "command"
    TOOL = "tool"
    FILE = "file"
    HELP = "help"
    PERSON = "person"
    ANSWER = "answer"
    NOTE = "note"
    CRAWL = "crawl"
    ERROR = "error"


QUIET = dt.timedelta(minutes=1)
ACTIVE = (RunStatus.QUEUED, RunStatus.DEFERRED, RunStatus.RUNNING, RunStatus.WAITING, RunStatus.RESTARTING)


class RunView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: uuid.UUID
    kind: RunKind
    status: RunStatus
    requested_by: uuid.UUID | None
    summary: str | None
    stats: dict[str, JsonValue] | None
    created_at: dt.datetime
    started_at: dt.datetime | None
    finished_at: dt.datetime | None
    attempts: int
    beat_at: dt.datetime | None
    live_view_url: str | None

    @property
    def has_agent(self) -> bool:
        """Runs an agent talks in: onboarding, healing, signing in again."""
        return self.kind.is_agent

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE

    @property
    def progress(self) -> CrawlProgress | None:
        """How far a crawl has got: written as it goes, until the run's final stats replace it."""
        found = (self.stats or {}).get("progress")
        return CrawlProgress.model_validate(found) if found is not None else None

    @property
    def quiet(self) -> bool:
        """A running crawl that stopped beating: its worker is likely gone or stuck, and a person may restart it."""
        return (
            self.kind.crawls and self.status == RunStatus.RUNNING and self.beat_at is not None
            and now() - self.beat_at > QUIET
        )  # fmt: skip


CHAT = (EventKind.MESSAGE, EventKind.PERSON, EventKind.ANSWER, EventKind.HELP, EventKind.NOTE)


class EventView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: EventKind
    text: str
    author: str | None
    at: dt.datetime

    @property
    def in_chat(self) -> bool:
        """What the chat shows: what the agent said, what people wrote, and the help requests between them."""
        return self.kind in CHAT


class FeedUpdate(BaseModel, frozen=True):
    """One push to a page following a run: its state now, and the lines written since the last push."""

    run: RunView
    events: list[EventView]
    after: int


class Dispatcher(Protocol):
    """Where a queued run is carried out: in this process, or on a Modal container of its own."""

    async def dispatch(self, run_id: uuid.UUID) -> None: ...

    async def redeploy(self, run_id: uuid.UUID) -> None:
        """Deploys the latest `main`, then carries the run on there."""
        ...
