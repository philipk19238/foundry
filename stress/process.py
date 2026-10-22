"""The crawl's own process, found by its job file on the command line `Crawls` starts it with: how much memory it
holds, and how to kill it outright."""

import asyncio
import contextlib
import os
import signal
from pathlib import Path
from types import TracebackType

SAMPLE_SECONDS = 0.5


class CrawlProcess:
    """The `python -m foundry.core crawl <job>` process for the job file at `job`, while it runs."""

    def __init__(self, job: Path) -> None:
        self._job = job

    async def pid(self) -> int | None:
        found = await asyncio.create_subprocess_exec(
            "pgrep", "-f", f"foundry.core crawl {self._job}", stdout=asyncio.subprocess.PIPE
        )
        out, _ = await found.communicate()
        pids = [int(line) for line in out.split()]
        return pids[0] if pids else None

    async def rss_kb(self) -> int:
        pid = await self.pid()
        if pid is None:
            return 0
        found = await asyncio.create_subprocess_exec("ps", "-o", "rss=", "-p", str(pid), stdout=asyncio.subprocess.PIPE)
        out, _ = await found.communicate()
        return int(out.strip() or 0)

    async def kill(self) -> int | None:
        """SIGKILLs the crawl, as a dying machine would; returns its pid, or None if it wasn't running."""
        pid = await self.pid()
        if pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        return pid


class PeakMemory:
    """Samples the resident memory of every crawl process in `processes` every SAMPLE_SECONDS while the context is
    open, keeping the highest. `psutil` isn't a dependency, so it asks `ps`."""

    def __init__(self, processes: list[CrawlProcess]) -> None:
        self._processes = processes
        self.peak_kb = 0
        self._sampling: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "PeakMemory":
        self._sampling = asyncio.get_running_loop().create_task(self._sample())
        return self

    async def __aexit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        assert self._sampling is not None
        self._sampling.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._sampling

    async def _sample(self) -> None:
        while True:
            for process in self._processes:
                self.peak_kb = max(self.peak_kb, await process.rss_kb())
            await asyncio.sleep(SAMPLE_SECONDS)
