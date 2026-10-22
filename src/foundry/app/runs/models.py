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
    def is_active(self) -> bool:
        return self.status in (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.WAITING)


class EventView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: EventKind
    text: str
    at: dt.datetime


class Dispatcher(Protocol):
    """Where a queued run is carried out: in this process, or on a Modal container of its own."""

    async def dispatch(self, run_id: uuid.UUID) -> None: ...

    async def redeploy(self, run_id: uuid.UUID) -> None:
        """Deploys the latest `main`, then carries the run on there."""
        ...
