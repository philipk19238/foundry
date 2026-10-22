import asyncio
import datetime as dt
import functools
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import TypeVar

from pydantic import JsonValue

from foundry.app import exceptions
from foundry.app.core.models import Lease
from foundry.app.db.db import Sessionmaker, now
from foundry.app.db.models.runs import RunKind, RunStatus
from foundry.app.runs import models, store

LEASE = dt.timedelta(minutes=2)
FEED_SECONDS = 0.5
FEED_HEARTBEAT = 15.0
BEAT = dt.timedelta(seconds=30)
PAGE = 100
EVENT_PAGE = 500
TRIAL_LIFETIME = dt.timedelta(minutes=40)
DEFERRED = "waiting for the job using the portal to finish"

T = TypeVar("T")


async def kept_alive(work: Awaitable[T], beat: Callable[[], Awaitable[None]]) -> T:
    """Awaits ``work``, calling ``beat`` every BEAT meanwhile. A beat that raises stops the work."""
    task = asyncio.ensure_future(work)
    try:
        while not (await asyncio.wait({task}, timeout=BEAT.total_seconds()))[0]:
            await beat()
        return task.result()
    finally:
        task.cancel()
        await asyncio.wait({task}, timeout=BEAT.total_seconds())


class Timeline:
    """What happened in each run, in order: the lines the run page streams."""

    def __init__(self, sessionmaker: Sessionmaker) -> None:
        self._sessionmaker = sessionmaker

    async def log(self, run_id: uuid.UUID, kind: models.EventKind, text: str) -> None:
        async with self._sessionmaker.begin() as session:
            await store.add_event(session, run_id, kind, text, None, now())

    async def wrote(self, run_id: uuid.UUID, kind: models.EventKind, author: str, text: str) -> None:
        """A line a person wrote in the run's chat."""
        async with self._sessionmaker.begin() as session:
            await store.add_event(session, run_id, kind, text, author, now())

    async def from_people(self, run_id: uuid.UUID, after: int) -> list[models.EventView]:
        """What people wrote to the agent after event ``after``, for it to read."""
        async with self._sessionmaker() as session:
            return [models.EventView.model_validate(event) for event in await store.people(session, run_id, after)]

    async def said(self, run_id: uuid.UUID) -> str | None:
        async with self._sessionmaker() as session:
            return await store.said(session, run_id)

    async def since(self, run_id: uuid.UUID, after: int) -> list[models.EventView]:
        async with self._sessionmaker() as session:
            events = await store.events(session, run_id, after, EVENT_PAGE)
            return [models.EventView.model_validate(event) for event in events]


class Leases:
    """Which run works on each source: one at a time, and a run whose lease lapsed can no longer act for it."""

    def __init__(self, sessionmaker: Sessionmaker) -> None:
        self._sessionmaker = sessionmaker

    async def hold(self, source_id: uuid.UUID, run_id: uuid.UUID, work: Callable[[Lease], Awaitable[T]]) -> T:
        """Runs ``work`` under the source's lease, renewing it while the work runs (a person helping the agent
        included) and releasing it after."""
        lease = await self.claim(source_id, run_id)
        try:
            return await kept_alive(work(lease), functools.partial(self.renew, lease))
        finally:
            await self.release(lease)

    async def claim(self, source_id: uuid.UUID, run_id: uuid.UUID) -> Lease:
        at = now()
        async with self._sessionmaker.begin() as session:
            fence = await store.claim(session, source_id, run_id, at, at + LEASE)
        if fence is None:
            raise exceptions.SourceBusy(source_id)
        return Lease(source_id=source_id, run_id=run_id, fence=fence)

    async def renew(self, lease: Lease) -> None:
        at = now()
        async with self._sessionmaker.begin() as session:
            if not await store.renew(session, lease.source_id, lease.run_id, lease.fence, at, at + LEASE):
                raise exceptions.LeaseLost()

    async def release(self, lease: Lease) -> None:
        async with self._sessionmaker.begin() as session:
            await store.release(session, lease.source_id, lease.run_id, lease.fence)

    async def free(self, source_id: uuid.UUID) -> bool:
        async with self._sessionmaker() as session:
            return await store.free(session, source_id, now())

    async def stranded(self) -> list[uuid.UUID]:
        async with self._sessionmaker() as session:
            return list(await store.stranded(session, now()))


class RunHistory:
    """The runs so far, for the pages and the healing loop."""

    def __init__(self, sessionmaker: Sessionmaker) -> None:
        self._sessionmaker = sessionmaker

    async def run(self, run_id: uuid.UUID) -> models.RunView:
        async with self._sessionmaker() as session:
            run = await store.run(session, run_id)
        if run is None:
            raise exceptions.RunNotFound(run_id)
        return models.RunView.model_validate(run)

    async def recent(self) -> list[models.RunView]:
        async with self._sessionmaker() as session:
            return [models.RunView.model_validate(run) for run in await store.runs(session, PAGE)]

    async def of_source(self, source_id: uuid.UUID) -> list[models.RunView]:
        async with self._sessionmaker() as session:
            return [models.RunView.model_validate(run) for run in await store.source_runs(session, source_id, PAGE)]

    async def working_sources(self) -> set[uuid.UUID]:
        async with self._sessionmaker() as session:
            return set(await store.working_sources(session))

    async def waiting_sources(self) -> set[uuid.UUID]:
        async with self._sessionmaker() as session:
            return set(await store.waiting_sources(session, now()))

    async def last_agent(self, source_id: uuid.UUID) -> models.RunView | None:
        async with self._sessionmaker() as session:
            run = await store.last_agent(session, source_id)
            return None if run is None else models.RunView.model_validate(run)

    async def last(self, source_id: uuid.UUID, kind: RunKind) -> models.RunView | None:
        async with self._sessionmaker() as session:
            run = await store.last(session, source_id, kind)
        return models.RunView.model_validate(run) if run is not None else None


class RunFeed:
    """A run's new lines as they are written, for a page to stream. The server reads the timeline every half second,
    so a browser hears of a line about as soon as it exists; when nothing changes it hears a heartbeat now and then."""

    def __init__(self, timeline: Timeline, history: RunHistory) -> None:
        self._timeline = timeline
        self._history = history

    async def follow(
        self, run_id: uuid.UUID, after: int, stopped: Callable[[], Awaitable[bool]]
    ) -> AsyncGenerator[models.FeedUpdate, None]:
        status = None
        quiet = 0.0
        while not await stopped():
            run = await self._history.run(run_id)
            events = await self._timeline.since(run_id, after)
            if events or run.status != status or quiet >= FEED_HEARTBEAT:
                yield models.FeedUpdate(run=run, events=events, after=events[-1].id if events else after)
                after, status, quiet = (events[-1].id if events else after), run.status, 0.0
            await asyncio.sleep(FEED_SECONDS)
            quiet += FEED_SECONDS


class Runs:
    """Jobs on sources: recorded when asked for, then started, paused for a person, resumed and finished."""

    def __init__(self, sessionmaker: Sessionmaker, timeline: Timeline) -> None:
        self._sessionmaker = sessionmaker
        self._timeline = timeline

    async def add(self, source_id: uuid.UUID, kind: RunKind, requested_by: uuid.UUID | None) -> models.RunView:
        async with self._sessionmaker.begin() as session:
            run = models.RunView.model_validate(await store.add_run(session, source_id, kind, requested_by, now()))
        await self._timeline.log(run.id, models.EventKind.STATUS, f"Queued a {kind} run.")
        return run

    async def start(self, run_id: uuid.UUID) -> None:
        async with self._sessionmaker.begin() as session:
            await store.start(session, run_id, now())
        await self._timeline.log(run_id, models.EventKind.STATUS, "Started.")

    async def reap(self) -> int:
        """Fails the runs whose worker is gone; the schedule and every boot call this."""
        at = now()
        async with self._sessionmaker.begin() as session:
            reaped = await store.reap(session, at, at - LEASE, at - TRIAL_LIFETIME)
        for run_id in reaped:
            await self._timeline.log(run_id, models.EventKind.ERROR, "Lost its worker before finishing.")
        return len(reaped)

    async def woken(self, source_id: uuid.UUID) -> list[uuid.UUID]:
        async with self._sessionmaker.begin() as session:
            return list(await store.wake(session, source_id))

    async def defer(self, run_id: uuid.UUID) -> None:
        """Puts an agent job back to wait for the run holding its source, instead of skipping it, so what a person
        asked for still happens once that run ends."""
        async with self._sessionmaker.begin() as session:
            await store.defer(session, run_id, DEFERRED)
        await self._timeline.log(
            run_id, models.EventKind.STATUS, "Another job is using the portal; I'll start when it ends."
        )

    async def finish(
        self, run_id: uuid.UUID, status: RunStatus, summary: str, stats: dict[str, JsonValue] | None = None
    ) -> None:
        async with self._sessionmaker.begin() as session:
            await store.finish(session, run_id, status, summary, stats, now())
        await self._timeline.log(run_id, models.EventKind.STATUS, f"{status.capitalize()}: {summary}")


class Pauses:
    """A run stepping aside and coming back: waiting for a person, restarting on newly deployed code, or reopened by a
    message after it ended."""

    def __init__(self, sessionmaker: Sessionmaker, timeline: Timeline) -> None:
        self._sessionmaker = sessionmaker
        self._timeline = timeline

    async def pause(self, run_id: uuid.UUID) -> None:
        """The run is waiting for a person."""
        async with self._sessionmaker.begin() as session:
            await store.set_status(session, run_id, RunStatus.WAITING)

    async def resume(self, run_id: uuid.UUID) -> None:
        async with self._sessionmaker.begin() as session:
            await store.set_status(session, run_id, RunStatus.RUNNING)

    async def restart(self, run_id: uuid.UUID) -> None:
        """The run's worker stops so a new deployment can carry it on; the reaper leaves it alone meanwhile."""
        async with self._sessionmaker.begin() as session:
            await store.set_status(session, run_id, RunStatus.RESTARTING)

    async def reopen(self, run_id: uuid.UUID) -> bool:
        async with self._sessionmaker.begin() as session:
            reopened = await store.reopen(session, run_id)
        if reopened:
            await self._timeline.log(run_id, models.EventKind.NOTE, "Reopened to answer a new message")
        return reopened


class Queue:
    """Where runs are asked for, by a person, the schedule or the healing loop, and handed to the dispatcher."""

    def __init__(self, runs: Runs, pauses: Pauses, dispatcher: models.Dispatcher) -> None:
        self._runs = runs
        self._pauses = pauses
        self._dispatcher = dispatcher

    async def request(self, source_id: uuid.UUID, kind: RunKind, requested_by: uuid.UUID | None) -> models.RunView:
        run = await self._runs.add(source_id, kind, requested_by)
        await self._dispatcher.dispatch(run.id)
        return run

    async def reopen(self, run_id: uuid.UUID) -> None:
        """Carries an ended agent run on, in the same conversation and workspace, because someone wrote to it."""
        if await self._pauses.reopen(run_id):
            await self._dispatcher.dispatch(run_id)

    async def wake(self, source_id: uuid.UUID) -> None:
        """Hands the source's deferred agent jobs to the dispatcher again; called whenever a run lets go of it."""
        for run_id in await self._runs.woken(source_id):
            await self._dispatcher.dispatch(run_id)

    async def redeploy(self, run_id: uuid.UUID) -> None:
        """Carries a restarting run on after a redeploy, on the new code. Called once the run let go of its lease."""
        await self._dispatcher.redeploy(run_id)
