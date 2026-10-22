"""The kit's extensions: the crawl's progress, written as it goes."""

import asyncio
import datetime as dt
from pathlib import Path

from scrapy import signals
from scrapy.crawler import Crawler
from scrapy.exceptions import NotConfigured
from scrapy.http import Response

from foundry.core import pace
from foundry.core.models import CrawlProgress
from foundry.core.spider import (
    CHALLENGED,
    FILES_FAILED,
    FILES_SAVED,
    INVALID,
    VALID,
)

PROGRESS_SECONDS = 5.0
REQUESTS = "downloader/request_count"


class ProgressExtension:
    """Writes the crawl's progress (`CrawlProgress`) every PROGRESS_SECONDS while it runs, and once more at the end."""

    def __init__(self, crawler: Crawler, path: Path) -> None:
        assert crawler.stats is not None
        self._stats = crawler.stats
        self._path = path
        self._last_url = ""
        self._beating: asyncio.Task[None] | None = None
        crawler.signals.connect(self.opened, signal=signals.spider_opened)
        crawler.signals.connect(self.closed, signal=signals.spider_closed)
        crawler.signals.connect(self.received, signal=signals.response_received)

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "ProgressExtension":
        path = crawler.settings.get("FOUNDRY_PROGRESS")
        if not path:
            raise NotConfigured("no progress file for this crawl")
        return cls(crawler, Path(path))

    def opened(self) -> None:
        """A resumed crawl counts on from where the killed one got to, so its totals cover the whole crawl."""
        if self._path.exists():
            before = CrawlProgress.model_validate_json(self._path.read_text())
            counted = {
                VALID: before.listings, INVALID: before.invalid, REQUESTS: before.requests,
                FILES_SAVED: before.files_saved, FILES_FAILED: before.files_failed,
                pace.RATE_LIMITED: before.rate_limited, CHALLENGED: before.challenged,
            }  # fmt: skip
            for key, value in counted.items():
                self._stats.inc_value(key, value)
        self._beating = asyncio.get_running_loop().create_task(self._beat())

    def received(self, response: Response) -> None:
        self._last_url = response.url

    def closed(self) -> None:
        if self._beating is not None:
            self._beating.cancel()
        self._write()

    async def _beat(self) -> None:
        while True:
            self._write()
            await asyncio.sleep(PROGRESS_SECONDS)

    def _write(self) -> None:
        stats = self._stats
        progress = CrawlProgress(
            listings=stats.get_value(VALID, 0),
            invalid=stats.get_value(INVALID, 0),
            requests=stats.get_value(REQUESTS, 0),
            files_saved=stats.get_value(FILES_SAVED, 0),
            files_failed=stats.get_value(FILES_FAILED, 0),
            rate_limited=stats.get_value(pace.RATE_LIMITED, 0),
            challenged=stats.get_value(CHALLENGED, 0),
            last_url=self._last_url,
            at=dt.datetime.now(dt.UTC),
        )
        self._path.write_text(progress.model_dump_json())
