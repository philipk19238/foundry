"""Fake `CrawlWatch`es: the run a live crawl reports to, played by the harness. Each counts the beats it gets; the
interrupting ones end the attempt as a person or a dying worker would."""

import uuid

from foundry.core.models import CrawlProgress
from stress.exceptions import WorkerDied
from stress.process import CrawlProcess


class RecordingWatch:
    """A run that lets its crawl go on, and keeps every progress beat."""

    def __init__(self, run_id: uuid.UUID, attempt: int) -> None:
        self._run_id = run_id
        self._attempt = attempt
        self.beats: list[CrawlProgress] = []

    @property
    def run_id(self) -> uuid.UUID:
        return self._run_id

    @property
    def attempt(self) -> int:
        return self._attempt

    async def beat(self, progress: CrawlProgress) -> bool:
        self.beats.append(progress)
        return True


class StoppingWatch(RecordingWatch):
    """A run a person restarts after `after` beats: the beat says stop, and the host stops the crawl cleanly."""

    def __init__(self, run_id: uuid.UUID, attempt: int, after: int) -> None:
        super().__init__(run_id, attempt)
        self._after = after

    async def beat(self, progress: CrawlProgress) -> bool:
        await super().beat(progress)
        return len(self.beats) < self._after


class KillingWatch(RecordingWatch):
    """A worker that dies after `after` beats: its crawl is killed outright, and the host's call ends with
    `WorkerDied` instead of cleaning up, as it would on a machine that went away."""

    def __init__(self, run_id: uuid.UUID, attempt: int, after: int, crawl: CrawlProcess) -> None:
        super().__init__(run_id, attempt)
        self._after = after
        self._crawl = crawl

    async def beat(self, progress: CrawlProgress) -> bool:
        await super().beat(progress)
        if len(self.beats) >= self._after:
            raise WorkerDied(await self._crawl.kill() or 0)
        return True
