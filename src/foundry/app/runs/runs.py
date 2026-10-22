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
from foundry.core.models import CrawlProgress

LEASE = dt.timedelta(minutes=2)
FEED_SECONDS = 0.5
FEED_HEARTBEAT = 15.0
BEAT = dt.timedelta(seconds=30)
PAGE = 100
EVENT_PAGE = 500
STALE = dt.timedelta(minutes=3)
MOST_ATTEMPTS = 3
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


class RunChanges:
    """Where runs have got to, compared look by look by whoever tells listeners what changed."""

    def __init__(self, sessionmaker: Sessionmaker) -> None:
        self._sessionmaker = sessionmaker

    async def marks(self, since: dt.datetime) -> list[models.RunMark]:
        """Every run that hasn't ended, or ended after `since`."""
        async with self._sessionmaker() as session:
            return list(await store.marks(session, since))

    async def written(self, after: int) -> dict[uuid.UUID, int]:
        """The runs with lines after event `after`, each with its newest line's id."""
        async with self._sessionmaker() as session:
            return await store.written(session, after)

    async def last_line(self) -> int:
        async with self._sessionmaker() as session:
            return await store.last_event(session)


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

    async def start(self, run: models.RunView) -> bool:
        """False when this dispatch is not the run's to carry out: another one started it, or it was replaced."""
        async with self._sessionmaker.begin() as session:
            started = await store.start(session, run.id, run.attempts, now())
        if started:
            await self._timeline.log(run.id, models.EventKind.STATUS, "Started.")
        return started

    async def reap(self) -> list[uuid.UUID]:
        """Fails the runs whose worker is gone, and queues again each crawl that stopped beating while it has
        attempts left, to carry on where it stopped; returns those, for the queue to dispatch. The schedule and every
        boot call this."""
        at = now()
        async with self._sessionmaker.begin() as session:
            reaped = await store.reap(session, at, at - LEASE)
            revived = await store.revive(session, at - STALE, MOST_ATTEMPTS, at)
        for run_id in [*reaped, *revived.failed]:
            await self._timeline.log(run_id, models.EventKind.ERROR, "Lost its worker before finishing.")
        for run_id in revived.queued:
            await self._timeline.log(
                run_id, models.EventKind.STATUS, "Stopped making progress; carrying on where it stopped."
            )
        return revived.queued

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

    async def watching(self, run_id: uuid.UUID, url: str | None) -> None:
        """The agent's browser, to watch on the run's page while it is open; None once it closes."""
        async with self._sessionmaker.begin() as session:
            await store.set_live_view(session, run_id, url)

    async def finish(
        self, run: models.RunView, status: RunStatus, summary: str, stats: dict[str, JsonValue] | None = None
    ) -> None:
        """Ends the attempt `run` was read at; a superseded attempt ends nothing."""
        async with self._sessionmaker.begin() as session:
            await store.finish(session, run.id, run.attempts, status, summary, stats, now())
        await self._timeline.log(run.id, models.EventKind.STATUS, f"{status.capitalize()}: {summary}")


class Attempts:
    """A crawl run's attempts: the newest one beats with its progress as the crawl goes, and one that stopped is
    queued again to carry on where it got to."""

    def __init__(self, sessionmaker: Sessionmaker, timeline: Timeline) -> None:
        self._sessionmaker = sessionmaker
        self._timeline = timeline

    async def beat(self, run: models.RunView, progress: CrawlProgress) -> bool:
        """Keeps a crawl's progress on its run; False once a person restarted it, or it ended."""
        stats: dict[str, JsonValue] = {"progress": progress.model_dump(mode="json")}
        async with self._sessionmaker.begin() as session:
            return await store.beat(session, run.id, run.attempts, stats, now())

    async def retry(self, run: models.RunView) -> bool:
        """Queues the crawl's next attempt, which carries on where this one got to; False if it can't be retried."""
        async with self._sessionmaker.begin() as session:
            retried = await store.retry(session, run.id, run.attempts, now())
        if retried:
            await self._timeline.log(run.id, models.EventKind.STATUS, "Restarting; carrying on where the crawl got to.")
        return retried

    def watch(self, run: models.RunView) -> "RunWatch":
        return RunWatch(self, run)


class RunWatch:
    """One attempt of a crawl run, beaten by the crawl as it goes."""

    def __init__(self, attempts: Attempts, run: models.RunView) -> None:
        self._attempts = attempts
        self._run = run

    @property
    def run_id(self) -> uuid.UUID:
        return self._run.id

    @property
    def attempt(self) -> int:
        return self._run.attempts

    async def beat(self, progress: CrawlProgress) -> bool:
        return await self._attempts.beat(self._run, progress)


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

    def __init__(self, runs: Runs, pauses: Pauses, attempts: Attempts, dispatcher: models.Dispatcher) -> None:
        self._runs = runs
        self._pauses = pauses
        self._attempts = attempts
        self._dispatcher = dispatcher

    async def request(self, source_id: uuid.UUID, kind: RunKind, requested_by: uuid.UUID | None) -> models.RunView:
        run = await self._runs.add(source_id, kind, requested_by)
        await self._dispatcher.dispatch(run.id)
        return run

    async def reopen(self, run_id: uuid.UUID) -> None:
        """Carries an ended agent run on, in the same conversation and workspace, because someone wrote to it."""
        if await self._pauses.reopen(run_id):
            await self._dispatcher.dispatch(run_id)

    async def dispatch(self, run_id: uuid.UUID) -> None:
        """Hands a run already queued to the dispatcher: a crawl's next attempt."""
        await self._dispatcher.dispatch(run_id)

    async def restart(self, run: models.RunView) -> None:
        """A person restarting a crawl: its next attempt carries on where this one got to. A worker still beating
        sees the new attempt within seconds, stops, and dispatches it; a quiet or failed run is dispatched now."""
        if not await self._attempts.retry(run):
            raise exceptions.CannotRestart(run.id)
        if run.quiet or run.status == RunStatus.FAILED:
            await self._dispatcher.dispatch(run.id)

    async def retry(self, run: models.RunView) -> bool:
        """Carries a stalled crawl on in its next attempt; False if it can't be retried."""
        retried = await self._attempts.retry(run)
        if retried:
            await self._dispatcher.dispatch(run.id)
        return retried

    async def revive(self) -> int:
        """Fails runs whose worker is gone and carries on the crawls that stopped beating."""
        revived = await self._runs.reap()
        for run_id in revived:
            await self._dispatcher.dispatch(run_id)
        return len(revived)

    async def wake(self, source_id: uuid.UUID) -> None:
        """Hands the source's deferred agent jobs to the dispatcher again; called whenever a run lets go of it."""
        for run_id in await self._runs.woken(source_id):
            await self._dispatcher.dispatch(run_id)

    async def redeploy(self, run_id: uuid.UUID) -> None:
        """Carries a restarting run on after a redeploy, on the new code. Called once the run let go of its lease."""
        await self._dispatcher.redeploy(run_id)
