"""Runs and the source lease. Each lease change is one conditional UPDATE on the source row, so of two runs that race
for a source exactly one gets it, and a run whose lease lapsed can neither renew nor release it
(formal/runs `OneHolder`)."""

import datetime as dt
import uuid
from collections.abc import Sequence

from pydantic import JsonValue
from sqlalchemy import and_, bindparam, exists, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from foundry.app.db.models.catalog import Source
from foundry.app.db.models.help import HelpRequest, HelpStatus
from foundry.app.db.models.runs import Event, Run, RunKind, RunStatus
from foundry.app.runs.models import ACTIVE, EventKind, Revived

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
_SAID = (
    select(Event.text)
    .where(Event.run_id == bindparam("run"), Event.kind == EventKind.MESSAGE)
    .order_by(Event.id.desc())
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
_LIVE_HELP = (HelpStatus.OPEN, HelpStatus.CLAIMED)
_ACTIVE = (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.WAITING)
_LIVE_LEASE = exists().where(
    Source.id == Run.source_id, Source.lease_run_id == Run.id, Source.lease_expires_at > bindparam("now")
)
_CRAWLS = (RunKind.REFRESH, RunKind.TRIAL)
_REAP = (
    update(Run)
    .where(
        Run.status.in_(_ACTIVE),
        func.coalesce(Run.beat_at, Run.created_at) < bindparam("before"),
        ~_LIVE_LEASE,
        or_(Run.kind.not_in(_CRAWLS), Run.status == RunStatus.QUEUED),
    )
    .values(status=RunStatus.FAILED, summary="lost its worker before finishing", finished_at=bindparam("now"))
    .returning(Run.id)
)
_DEFER = (
    update(Run)
    .where(Run.id == bindparam("run"), Run.status == RunStatus.RUNNING)
    .values(status=RunStatus.DEFERRED, summary=bindparam("why"))
)
_WAKE = (
    update(Run)
    .where(Run.source_id == bindparam("source"), Run.status == RunStatus.DEFERRED)
    .values(status=RunStatus.QUEUED, summary=None)
    .returning(Run.id)
)
_FREE = select(Source.id).where(
    Source.id == bindparam("source"), or_(Source.lease_run_id.is_(None), Source.lease_expires_at <= bindparam("now"))
)
_STRANDED = (
    select(Run.source_id)
    .where(
        Run.status == RunStatus.DEFERRED,
        ~exists().where(Source.id == Run.source_id, Source.lease_expires_at > bindparam("now")),
    )
    .distinct()
)
_WORKING = (
    select(Run.source_id)
    .where(Run.kind.in_((RunKind.ONBOARD, RunKind.HEAL, RunKind.RELOGIN)), Run.status.in_(ACTIVE))
    .distinct()
)
_WAITING = (
    select(Run.source_id)
    .join(HelpRequest, HelpRequest.run_id == Run.id)
    .where(HelpRequest.status.in_(_LIVE_HELP), HelpRequest.expires_at > bindparam("now"))
    .distinct()
)
_AGENTS = (RunKind.ONBOARD, RunKind.HEAL, RunKind.RELOGIN)
_LAST_AGENT = (
    select(Run)
    .where(Run.source_id == bindparam("source"), Run.kind.in_(_AGENTS))
    .order_by(Run.status.in_(ACTIVE).desc(), Run.created_at.desc())
    .limit(1)
)
_LIVE_VIEW = update(Run).where(Run.id == bindparam("run")).values(live_view_url=bindparam("url"))
_SET_STATUS = update(Run).where(Run.id == bindparam("run")).values(status=bindparam("state"))
_ENDED = (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.SKIPPED)
_REOPEN = (
    update(Run)
    .where(Run.id == bindparam("run"), Run.status.in_(_ENDED))
    .values(status=RunStatus.QUEUED, summary=None, finished_at=None)
    .returning(Run.id)
)
_STARTABLE = (RunStatus.QUEUED, RunStatus.RESTARTING)
_START = (
    update(Run)
    .where(Run.id == bindparam("run"), Run.status.in_(_STARTABLE), Run.attempts == bindparam("attempt"))
    .values(status=RunStatus.RUNNING, started_at=bindparam("at"), beat_at=bindparam("at"))
    .returning(Run.id)
)
_FINISH = (
    update(Run)
    .where(Run.id == bindparam("run"), Run.status.not_in(_ENDED), Run.attempts == bindparam("attempt"))
    .values(
        status=bindparam("state"),
        summary=bindparam("text"),
        stats=bindparam("numbers"),
        finished_at=bindparam("at"),
    )
)


_BEAT = (
    update(Run)
    .where(Run.id == bindparam("run"), Run.status == RunStatus.RUNNING, Run.attempts == bindparam("attempt"))
    .values(beat_at=bindparam("at"), stats=bindparam("numbers"))
    .returning(Run.id)
)
_RETRY = (
    update(Run)
    .where(
        Run.id == bindparam("run"),
        Run.kind.in_(_CRAWLS),
        Run.status.in_((RunStatus.RUNNING, RunStatus.FAILED)),
        Run.attempts == bindparam("attempt"),
    )
    .values(status=RunStatus.QUEUED, attempts=Run.attempts + 1, beat_at=bindparam("at"), summary=None, finished_at=None)
    .returning(Run.id)
)
_STALE = and_(
    Run.kind.in_(_CRAWLS),
    Run.status == RunStatus.RUNNING,
    func.coalesce(Run.beat_at, Run.started_at) < bindparam("before"),
)
_REVIVE = (
    update(Run)
    .where(_STALE, Run.attempts < bindparam("most"))
    .values(status=RunStatus.QUEUED, attempts=Run.attempts + 1, beat_at=bindparam("now"))
    .returning(Run.id)
)
_GIVE_UP = (
    update(Run)
    .where(_STALE, Run.attempts >= bindparam("most"))
    .values(status=RunStatus.FAILED, summary="stopped making progress, tried too often", finished_at=bindparam("now"))
    .returning(Run.id)
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


async def reopen(session: AsyncSession, run_id: uuid.UUID) -> bool:
    """Queues an ended run again; False if it hasn't ended."""
    return await session.scalar(_REOPEN, {"run": run_id}) is not None


async def start(session: AsyncSession, run_id: uuid.UUID, attempt: int, at: dt.datetime) -> bool:
    """Starts a queued (or restarting) run's `attempt`; False when another dispatch already started it, or a newer
    attempt replaced it (formal/runs/crawls `FencedStart`)."""
    return await session.scalar(_START, {"run": run_id, "attempt": attempt, "at": at}) is not None


async def set_live_view(session: AsyncSession, run_id: uuid.UUID, url: str | None) -> None:
    await session.execute(_LIVE_VIEW, {"run": run_id, "url": url})


async def set_status(session: AsyncSession, run_id: uuid.UUID, status: RunStatus) -> None:
    await session.execute(_SET_STATUS, {"run": run_id, "state": status})


async def finish(
    session: AsyncSession,
    run_id: uuid.UUID,
    attempt: int,
    status: RunStatus,
    summary: str,
    stats: dict[str, JsonValue] | None,
    at: dt.datetime,
) -> None:
    """Ends `attempt` of the run; nothing when the run already ended or a newer attempt replaced this one
    (formal/runs/crawls `FencedFinish`)."""
    numbers = {"run": run_id, "attempt": attempt, "state": status, "text": summary, "numbers": stats, "at": at}
    await session.execute(_FINISH, numbers)


async def add_event(
    session: AsyncSession, run_id: uuid.UUID, kind: str, text: str, author: str | None, at: dt.datetime
) -> None:
    await session.execute(_ADD_EVENT, {"run": run_id, "sort": kind, "line": text, "by": author, "when": at})


async def people(session: AsyncSession, run_id: uuid.UUID, after: int) -> Sequence[Event]:
    return (await session.scalars(_PEOPLE, {"run": run_id, "after": after})).all()


async def events(session: AsyncSession, run_id: uuid.UUID, after: int, limit: int) -> Sequence[Event]:
    return (await session.scalars(_EVENTS, {"run": run_id, "after": after, "limit": limit})).all()


async def said(session: AsyncSession, run_id: uuid.UUID) -> str | None:
    """What the run's agent said last."""
    return await session.scalar(_SAID, {"run": run_id})


async def reap(session: AsyncSession, at: dt.datetime, before: dt.datetime) -> Sequence[uuid.UUID]:
    """Fails every active run older than ``before`` that holds no live lease: its worker died or restarted. A started
    crawl is left to `revive`, which goes by its beats."""
    return (await session.scalars(_REAP, {"now": at, "before": before})).all()


async def beat(
    session: AsyncSession, run_id: uuid.UUID, attempt: int, stats: dict[str, JsonValue], at: dt.datetime
) -> bool:
    """Records a crawl's progress; False once the attempt is no longer the run's newest, running one."""
    return await session.scalar(_BEAT, {"run": run_id, "attempt": attempt, "numbers": stats, "at": at}) is not None


async def retry(session: AsyncSession, run_id: uuid.UUID, attempt: int, at: dt.datetime) -> bool:
    """Queues a running or failed crawl's next attempt; False if `attempt` isn't its newest or it can't be retried."""
    return await session.scalar(_RETRY, {"run": run_id, "attempt": attempt, "at": at}) is not None


async def revive(session: AsyncSession, before: dt.datetime, most: int, at: dt.datetime) -> Revived:
    """Crawls that haven't beaten since ``before``, their worker gone or stuck: queued again while they have
    attempts left, failed after."""
    queued = list((await session.scalars(_REVIVE, {"before": before, "most": most, "now": at})).all())
    failed = list((await session.scalars(_GIVE_UP, {"before": before, "most": most, "now": at})).all())
    return Revived(queued=queued, failed=failed)


async def defer(session: AsyncSession, run_id: uuid.UUID, why: str) -> None:
    await session.execute(_DEFER, {"run": run_id, "why": why})


async def wake(session: AsyncSession, source_id: uuid.UUID) -> Sequence[uuid.UUID]:
    """Queues again every agent job deferred on the source; each is returned once, to whoever wakes it."""
    return (await session.scalars(_WAKE, {"source": source_id})).all()


async def last_agent(session: AsyncSession, source_id: uuid.UUID) -> Run | None:
    """The source's agent job people talk to: the one under way, else the newest."""
    return await session.scalar(_LAST_AGENT, {"source": source_id})


async def free(session: AsyncSession, source_id: uuid.UUID, at: dt.datetime) -> bool:
    """Whether no run holds the source's lease at ``at``."""
    return await session.scalar(_FREE, {"source": source_id, "now": at}) is not None


async def stranded(session: AsyncSession, at: dt.datetime) -> Sequence[uuid.UUID]:
    """Sources with deferred agent jobs that nothing holds any more, as when the holder's worker died."""
    return (await session.scalars(_STRANDED, {"now": at})).all()


async def working_sources(session: AsyncSession) -> Sequence[uuid.UUID]:
    """Sources an agent job is under way on, or waiting to start on."""
    return (await session.scalars(_WORKING)).all()


async def waiting_sources(session: AsyncSession, at: dt.datetime) -> Sequence[uuid.UUID]:
    """Sources whose agent waits on a person right now."""
    return (await session.scalars(_WAITING, {"now": at})).all()
