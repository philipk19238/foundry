import asyncio
import datetime as dt
import functools
import uuid
from collections.abc import Awaitable, Callable
from typing import TypeVar

from pydantic import JsonValue

from foundry.app import exceptions
from foundry.app.core.models import Lease
from foundry.app.db.db import Sessionmaker, now
from foundry.app.db.models.runs import RunKind, RunStatus
from foundry.app.runs import models, store

LEASE = dt.timedelta(minutes=2)
BEAT = dt.timedelta(seconds=30)
PAGE = 100
EVENT_PAGE = 500

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

    async def last(self, source_id: uuid.UUID, kind: RunKind) -> models.RunView | None:
        async with self._sessionmaker() as session:
            run = await store.last(session, source_id, kind)
        return models.RunView.model_validate(run) if run is not None else None


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

    async def reap(self) -> int:
        """Fails the runs whose worker is gone; the schedule and every boot call this."""
        at = now()
        async with self._sessionmaker.begin() as session:
            reaped = await store.reap(session, at, at - LEASE)
        for run_id in reaped:
            await self._timeline.log(run_id, models.EventKind.ERROR, "Lost its worker before finishing.")
        return len(reaped)

    async def finish(
        self, run_id: uuid.UUID, status: RunStatus, summary: str, stats: dict[str, JsonValue] | None = None
    ) -> None:
        async with self._sessionmaker.begin() as session:
            await store.finish(session, run_id, status, summary, stats, now())
        await self._timeline.log(run_id, models.EventKind.STATUS, f"{status.capitalize()}: {summary}")


class Queue:
    """Where runs are asked for, by a person, the schedule or the healing loop, and handed to the dispatcher."""

    def __init__(self, runs: Runs, dispatcher: models.Dispatcher) -> None:
        self._runs = runs
        self._dispatcher = dispatcher

    async def request(self, source_id: uuid.UUID, kind: RunKind, requested_by: uuid.UUID | None) -> models.RunView:
        run = await self._runs.add(source_id, kind, requested_by)
        await self._dispatcher.dispatch(run.id)
        return run

    async def redeploy(self, run_id: uuid.UUID) -> None:
        """Carries a restarting run on after a redeploy, on the new code. Called once the run let go of its lease."""
        await self._dispatcher.redeploy(run_id)
