"""Runs and the source lease. Each lease change is one conditional UPDATE on the source row, so of two runs that race
for a source exactly one gets it, and a run whose lease lapsed can neither renew nor release it
(formal/runs `OneHolder`)."""

import datetime as dt
import uuid
from collections.abc import Sequence

from pydantic import JsonValue
from sqlalchemy import and_, bindparam, exists, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from foundry.app.db.models.catalog import Source
from foundry.app.db.models.runs import Event, Run, RunKind, RunStatus
from foundry.app.runs.models import EventKind

_HELD = and_(
    Source.id == bindparam("source"),
    Source.lease_run_id == bindparam("run"),
    Source.fence == bindparam("token"),
)

_CLAIM = (
    update(Source)
    .where(
        Source.id == bindparam("source"),
        or_(Source.lease_run_id.is_(None), Source.lease_expires_at <= bindparam("now")),
    )
    .values(lease_run_id=bindparam("run"), lease_expires_at=bindparam("until"), fence=Source.fence + 1)
    .returning(Source.fence)
)

_RENEW = (
    update(Source)
    .where(_HELD, Source.lease_expires_at > bindparam("now"))
    .values(lease_expires_at=bindparam("until"))
    .returning(Source.id)
)

_RELEASE = update(Source).where(_HELD).values(lease_run_id=None, lease_expires_at=None)

_RUN = select(Run).where(Run.id == bindparam("run"))
_RUNS = select(Run).order_by(Run.created_at.desc()).limit(bindparam("limit"))
_SOURCE_RUNS = (
    select(Run).where(Run.source_id == bindparam("source")).order_by(Run.created_at.desc()).limit(bindparam("limit"))
)
_LAST = (
    select(Run)
    .where(Run.source_id == bindparam("source"), Run.kind == bindparam("sort"), Run.finished_at.is_not(None))
    .order_by(Run.finished_at.desc())
    .limit(1)
)
_EVENTS = (
    select(Event)
    .where(Event.run_id == bindparam("run"), Event.id > bindparam("after"))
    .order_by(Event.id)
    .limit(bindparam("limit"))
)
_ADD_EVENT = insert(Event).values(
    run_id=bindparam("run"),
    kind=bindparam("sort"),
    text=bindparam("line"),
    author=bindparam("by"),
    at=bindparam("when"),
)
_PEOPLE = (
    select(Event)
    .where(Event.run_id == bindparam("run"), Event.kind == EventKind.PERSON, Event.id > bindparam("after"))
    .order_by(Event.id)
)
_ACTIVE = (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.WAITING)
_LIVE_LEASE = exists().where(
    Source.id == Run.source_id, Source.lease_run_id == Run.id, Source.lease_expires_at > bindparam("now")
)
_REAP = (
    update(Run)
    .where(Run.status.in_(_ACTIVE), Run.created_at < bindparam("before"), ~_LIVE_LEASE)
    .values(status=RunStatus.FAILED, summary="lost its worker before finishing", finished_at=bindparam("now"))
    .returning(Run.id)
)
_SET_STATUS = update(Run).where(Run.id == bindparam("run")).values(status=bindparam("state"))
_START = update(Run).where(Run.id == bindparam("run")).values(status=RunStatus.RUNNING, started_at=bindparam("at"))
_FINISH = (
    update(Run)
    .where(Run.id == bindparam("run"))
    .values(
        status=bindparam("state"),
        summary=bindparam("text"),
        stats=bindparam("numbers"),
        finished_at=bindparam("at"),
    )
)


async def add_run(
    session: AsyncSession, source_id: uuid.UUID, kind: RunKind, requested_by: uuid.UUID | None, at: dt.datetime
) -> Run:
    run = Run(
        id=uuid.uuid4(),
        source_id=source_id,
        kind=kind,
        status=RunStatus.QUEUED,
        requested_by=requested_by,
        created_at=at,
    )
    session.add(run)
    await session.flush()
    return run


async def run(session: AsyncSession, run_id: uuid.UUID) -> Run | None:
    return await session.scalar(_RUN, {"run": run_id})


async def runs(session: AsyncSession, limit: int) -> Sequence[Run]:
    return (await session.scalars(_RUNS, {"limit": limit})).all()


async def source_runs(session: AsyncSession, source_id: uuid.UUID, limit: int) -> Sequence[Run]:
    return (await session.scalars(_SOURCE_RUNS, {"source": source_id, "limit": limit})).all()


async def last(session: AsyncSession, source_id: uuid.UUID, kind: RunKind) -> Run | None:
    return await session.scalar(_LAST, {"source": source_id, "sort": kind})


async def claim(
    session: AsyncSession, source_id: uuid.UUID, run_id: uuid.UUID, at: dt.datetime, until: dt.datetime
) -> int | None:
    """The new fence, or None while another run holds a live lease."""
    return await session.scalar(_CLAIM, {"source": source_id, "run": run_id, "now": at, "until": until})


async def renew(
    session: AsyncSession, source_id: uuid.UUID, run_id: uuid.UUID, fence: int, at: dt.datetime, until: dt.datetime
) -> bool:
    binds = {"source": source_id, "run": run_id, "token": fence, "now": at, "until": until}
    return await session.scalar(_RENEW, binds) is not None


async def release(session: AsyncSession, source_id: uuid.UUID, run_id: uuid.UUID, fence: int) -> None:
    await session.execute(_RELEASE, {"source": source_id, "run": run_id, "token": fence})


async def start(session: AsyncSession, run_id: uuid.UUID, at: dt.datetime) -> None:
    await session.execute(_START, {"run": run_id, "at": at})


async def set_status(session: AsyncSession, run_id: uuid.UUID, status: RunStatus) -> None:
    await session.execute(_SET_STATUS, {"run": run_id, "state": status})


async def finish(
    session: AsyncSession,
    run_id: uuid.UUID,
    status: RunStatus,
    summary: str,
    stats: dict[str, JsonValue] | None,
    at: dt.datetime,
) -> None:
    await session.execute(_FINISH, {"run": run_id, "state": status, "text": summary, "numbers": stats, "at": at})


async def add_event(
    session: AsyncSession, run_id: uuid.UUID, kind: str, text: str, author: str | None, at: dt.datetime
) -> None:
    await session.execute(_ADD_EVENT, {"run": run_id, "sort": kind, "line": text, "by": author, "when": at})


async def people(session: AsyncSession, run_id: uuid.UUID, after: int) -> Sequence[Event]:
    return (await session.scalars(_PEOPLE, {"run": run_id, "after": after})).all()


async def events(session: AsyncSession, run_id: uuid.UUID, after: int, limit: int) -> Sequence[Event]:
    return (await session.scalars(_EVENTS, {"run": run_id, "after": after, "limit": limit})).all()


async def reap(session: AsyncSession, at: dt.datetime, before: dt.datetime) -> Sequence[uuid.UUID]:
    """Fails every active run older than ``before`` that holds no live lease: its worker died or restarted."""
    return (await session.scalars(_REAP, {"now": at, "before": before})).all()
