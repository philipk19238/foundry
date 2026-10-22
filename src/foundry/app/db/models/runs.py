import datetime as dt
import enum
import uuid

from pydantic import JsonValue
from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from foundry.app.db.db import Base, Json, UtcDateTime


class RunKind(enum.StrEnum):
    """What a run does. Agent jobs (onboard, heal, relogin) have an agent a person talks to; the rest run code: a
    crawl that stores what is new, a test crawl that stores nothing, and the check for a merged pull request."""

    ONBOARD = "onboard"
    REFRESH = "refresh"
    HEAL = "heal"
    RELOGIN = "relogin"
    REVIEW = "review"
    TRIAL = "trial"

    @property
    def is_agent(self) -> bool:
        return self in (RunKind.ONBOARD, RunKind.HEAL, RunKind.RELOGIN)

    @property
    def crawls(self) -> bool:
        """A crawl of the live portal, which beats as it goes and carries on where it stopped when run again."""
        return self in (RunKind.REFRESH, RunKind.TRIAL)

    @property
    def leased(self) -> bool:
        """Whether the run holds the source while it works. A test crawl writes nothing, so it runs beside others."""
        return self != RunKind.TRIAL

    @property
    def label(self) -> str:
        return LABELS[self]


LABELS = {
    RunKind.ONBOARD: "Agent: build",
    RunKind.HEAL: "Agent: repair",
    RunKind.RELOGIN: "Agent: sign in",
    RunKind.REFRESH: "Scrape",
    RunKind.TRIAL: "Test scrape",
    RunKind.REVIEW: "Merge check",
}


class RunStatus(enum.StrEnum):
    """Where a run stands. A deferred run is an agent job waiting for the run that holds its source to finish."""

    QUEUED = "queued"
    DEFERRED = "deferred"
    RUNNING = "running"
    WAITING = "waiting"
    RESTARTING = "restarting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class Run(Base):
    """One job on one source: an agent session (onboard, heal, relogin) or a scheduled crawl (refresh). A crawl
    records `beat_at` as it goes, and `attempts` counts how often it was carried on after its worker died or it
    stalled; only the newest attempt may beat. `live_view_url` is the agent's browser while it has one open."""

    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_source_id_created_at", "source_id", "created_at"),
        Index("ix_runs_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"))
    kind: Mapped[RunKind] = mapped_column(String(16))
    status: Mapped[RunStatus] = mapped_column(String(16))
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    summary: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict[str, JsonValue] | None] = mapped_column(Json)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)
    started_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    finished_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    beat_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    live_view_url: Mapped[str | None] = mapped_column(String(2048))


class Event(Base):
    """A line on a run's timeline: what the agent said, ran, or called, and what people wrote to it, in order.
    `author` is the person's email on a line a person wrote."""

    __tablename__ = "run_events"
    __table_args__ = (Index("ix_run_events_run_id_id", "run_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"))
    kind: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(320))
    at: Mapped[dt.datetime] = mapped_column(UtcDateTime)
