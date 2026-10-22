"""What MCP clients hear over `subscriptions/listen`: a `ResourceUpdated` for a run, its chat, its scraper, and the
open questions whenever any of them changes, so an agent following a job waits on events instead of polling. Runs
change in whichever process carries them out, so the web process reads where each run has got to from the database,
and only while someone listens."""

import asyncio
import datetime as dt
from collections.abc import Callable

import svcs
from mcp.server.subscriptions import InMemorySubscriptionBus, ResourceUpdated, ServerEvent

from foundry.app.db.db import now
from foundry.app.runs.runs import RunChanges
from foundry.server.changes.models import Look

POLL_SECONDS = 1.0
ENDED_WITHIN = dt.timedelta(minutes=10)


class ListenerBus(InMemorySubscriptionBus):
    """The listen streams open on this process; `someone` is set while there is one."""

    def __init__(self) -> None:
        super().__init__()
        self.someone = asyncio.Event()

    def subscribe(self, listener: Callable[[ServerEvent], None]) -> Callable[[], None]:
        unsubscribe = super().subscribe(listener)
        self.someone.set()

        def leave() -> None:
            unsubscribe()
            if not self._listeners:
                self.someone.clear()

        return leave


class Changes:
    """Compares each look with the last and publishes what changed: a new line is a change to its run's chat; a new
    status or beat, to the run and its scraper (and to the questions, on a new status). A look after nobody listened
    only takes note: a client that starts listening reads the state once, then waits for changes."""

    def __init__(self, listeners: ListenerBus) -> None:
        self._listeners = listeners

    async def publish(self, registry: svcs.Registry) -> None:
        seen: Look | None = None
        while True:
            if not self._listeners.someone.is_set():
                seen = None
                await self._listeners.someone.wait()
            async with svcs.Container(registry) as services:
                runs = await services.aget(RunChanges)
                marks = {mark.id: mark for mark in await runs.marks(now() - ENDED_WITHIN)}
                written = await runs.written(seen.after) if seen is not None else {}
                after = await runs.last_line() if seen is None else max(written.values(), default=seen.after)
            look = Look(marks=marks, after=after)
            if seen is not None:
                for uri in seen.changed(look, written):
                    await self._listeners.publish(ResourceUpdated(uri=uri))
            seen = look
            await asyncio.sleep(POLL_SECONDS)
