import datetime as dt
import enum
import uuid

from pydantic import JsonValue
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from foundry.app.db.db import Base, Json, UtcDateTime


class RunKind(enum.StrEnum):
    ONBOARD = "onboard"
    REFRESH = "refresh"
    HEAL = "heal"
    RELOGIN = "relogin"
    REVIEW = "review"
    TRIAL = "trial"


class RunStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    RESTARTING = "restarting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class Run(Base):
    """One job on one source: an agent session (onboard, heal, relogin) or a scheduled crawl (refresh)."""

    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), index=True)
    kind: Mapped[RunKind] = mapped_column(String(16))
    status: Mapped[RunStatus] = mapped_column(String(16))
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    summary: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict[str, JsonValue] | None] = mapped_column(Json)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)
    started_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    finished_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)


class Event(Base):
    """A line on a run's timeline: what the agent said, ran, or called, and what people wrote to it, in order.
    `author` is the person's email on a line a person wrote."""

    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(320))
    at: Mapped[dt.datetime] = mapped_column(UtcDateTime)
