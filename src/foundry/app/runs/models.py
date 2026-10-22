import datetime as dt
import enum
import uuid
from typing import Protocol

from pydantic import BaseModel, ConfigDict, JsonValue

from foundry.app.db.models.runs import RunKind, RunStatus


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

    @property
    def has_agent(self) -> bool:
        """Runs an agent talks in: onboarding, healing, signing in again."""
        return self.kind in (RunKind.ONBOARD, RunKind.HEAL, RunKind.RELOGIN)

    @property
    def is_active(self) -> bool:
        return self.status in (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.WAITING, RunStatus.RESTARTING)


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
