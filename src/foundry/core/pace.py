"""How fast a live crawl may ask a portal for things. Scrapy's own DOWNLOAD_DELAY and per-domain concurrency apply in
its download queue, which requests sent from the stealth browser never enter, so the browser middleware paces them
here instead, with the same settings, and slows a site down when it says so."""

import asyncio
import contextlib
from collections import defaultdict
from collections.abc import AsyncGenerator

from scrapy.statscollectors import StatsCollector

from foundry.core import challenges

RATE_LIMITED = "foundry/rate_limited"
SLOWED_TO = "foundry/slowed_to"
SLOWEST = 60.0
FIRST_SLOWDOWN = 0.25
EASING = 0.5


class Pace:
    """Per site: at most `concurrency` requests at once, at least `delay` seconds apart. A rate limit (a 429, a
    `Retry-After`, a firewall's challenge header) doubles that site's delay, or takes the wait the portal asked for,
    up to a minute, and every request that goes through eases it back toward `delay`, so one burst of limits doesn't
    slow the rest of the crawl. The crawl's stats keep how often it happened and the slowest delay it came to, which
    is the pace to set in `buyer.toml`."""

    def __init__(self, delay: float, concurrency: int, stats: StatsCollector) -> None:
        self._base = delay
        self._delays: defaultdict[str, float] = defaultdict(lambda: delay)
        self._turns: defaultdict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(concurrency))
        self._next: defaultdict[str, float] = defaultdict(float)
        self._spacing: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._stats = stats
        self._hurrying = False

    def hurry(self) -> None:
        """The crawl is stopping: stop waiting, so the requests under way finish and the crawl closes in seconds."""
        self._hurrying = True

    @property
    def hurrying(self) -> bool:
        return self._hurrying

    @contextlib.asynccontextmanager
    async def turn(self, site: str) -> AsyncGenerator[None, None]:
        """Waits for the site's next free slot, then holds it while the request runs."""
        async with self._turns[site]:
            async with self._spacing[site]:
                loop = asyncio.get_running_loop()
                if not self._hurrying:
                    await asyncio.sleep(max(0.0, self._next[site] - loop.time()))
                self._next[site] = loop.time() + self._delays[site]
            yield

    def went_through(self, site: str) -> None:
        """A request the site answered without a rate limit: ease its delay back toward the base pace."""
        self._delays[site] = max(self._base, self._delays[site] * EASING)

    def limited(self, site: str, retry_after: float | None) -> float:
        """Slows `site` down after a rate limit: its next turn waits, for every request to it, and the seconds are
        returned. A `Retry-After` is a pause the portal asked for, once: the site waits that long, and its pace only
        doubles."""
        slower = min(SLOWEST, max(self._delays[site] * 2, FIRST_SLOWDOWN))
        self._delays[site] = slower
        wait = min(SLOWEST, retry_after) if retry_after else slower
        self._next[site] = max(self._next[site], asyncio.get_running_loop().time() + wait)
        self._stats.inc_value(RATE_LIMITED)
        self._stats.max_value(SLOWED_TO, slower)
        return wait


def retry_after(status: int, headers: dict[str, str]) -> float | None:
    """The wait a response asks for, if it is a rate limit: `Retry-After` in seconds, or 0 for a bare 429 or a
    firewall's challenge header, which a portal mostly raises mid-crawl because the requests came too fast."""
    raw = next((value for key, value in headers.items() if key.lower() == "retry-after"), None)
    if raw is not None and raw.strip().isdigit():
        return float(raw)
    if status == 429 or challenges.HEADERS & {(key.lower(), value.lower()) for key, value in headers.items()}:
        return 0.0
    return None
