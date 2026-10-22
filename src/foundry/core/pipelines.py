"""The kit's item pipelines: every attachment downloaded while the crawl runs, and every listing validated and
written out."""

import asyncio
import hashlib
from pathlib import Path
from typing import TextIO

from pydantic import ValidationError
from scrapy.crawler import Crawler
from scrapy.exceptions import NotConfigured
from scrapy.http import Request, Response
from scrapy.statscollectors import StatsCollector

from foundry.core.models import FileLink, Listing, SavedFile
from foundry.core.spider import EXAMPLE, FILE, FILES_FAILED, FILES_SAVED, INVALID, PLAIN, VALID, PortalSpider

FAILURES_KEPT = 5


class FilesPipeline:
    """Downloads every attachment of every good listing while the crawl runs, so links that expire (presigned URLs)
    are fetched while they still work: as plain HTTP first, and from the stealth browser, with its pace, only when
    that didn't get the file. Each file is written
    to the crawl's files folder under the SHA-256 of its bytes and logged in `files.jsonl` for foundry to upload as it
    goes. A listing's files download together. Files foundry already has (by unsigned URL) are skipped; a download that
    fails is counted and the listing kept."""

    def __init__(self, crawler: Crawler, folder: Path, known: set[str]) -> None:
        assert crawler.stats is not None
        self._crawler = crawler
        self._stats = crawler.stats
        self._folder = folder
        log = folder / "files.jsonl"
        saved = log.read_text().splitlines() if log.exists() else []
        self._known = known | {SavedFile.model_validate_json(line).key for line in saved if line.strip()}
        self._log = log.open("a")

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "FilesPipeline":
        folder = crawler.settings.get("FOUNDRY_FILES")
        if not folder:
            raise NotConfigured("no files folder for this crawl")
        return cls(crawler, Path(folder), set(crawler.settings.getlist("FOUNDRY_KNOWN_FILES")))

    def close_spider(self) -> None:
        self._log.close()

    async def process_item(self, item: Listing | dict[str, object]) -> Listing | dict[str, object]:
        try:
            listing = item if isinstance(item, Listing) else Listing.model_validate(item)
        except ValidationError:
            return item
        fresh = {link.key: link for link in listing.files if link.key not in self._known}
        self._known |= set(fresh)
        await asyncio.gather(*(self._download(link, listing) for link in fresh.values()))
        return item

    async def _download(self, link: FileLink, listing: Listing) -> None:
        """One attachment, as plain HTTP and then from the browser, written to the folder and logged."""
        spider = self._crawler.spider
        assert isinstance(spider, PortalSpider)
        request = spider.file_request(link, listing)
        request = request.replace(meta={**request.meta, FILE: True})
        response, problem = await self._fetched(request.replace(meta={**request.meta, PLAIN: True}), link)
        if problem is not None and not request.meta.get("browser"):
            response, problem = await self._fetched(request, link)
        if problem is not None or response is None:
            self._failed(link, problem or "no response")
            return
        digest = hashlib.sha256(response.body).hexdigest()
        await asyncio.to_thread((self._folder / digest).write_bytes, response.body)
        kind = response.headers.get("Content-Type")
        saved = SavedFile(
            url=link.url, name=link.name, sha256=digest, content_type=kind.decode() if kind else None,
            size=len(response.body),
        )  # fmt: skip
        self._log.write(saved.model_dump_json() + "\n")
        self._log.flush()
        self._stats.inc_value(FILES_SAVED)

    async def _fetched(self, request: Request, link: FileLink) -> tuple[Response | None, str | None]:
        """The response to `request`, and why it isn't the file, if it isn't."""
        spider = self._crawler.spider
        assert isinstance(spider, PortalSpider) and self._crawler.engine is not None
        try:
            response = await self._crawler.engine.download_async(request)
        except Exception as error:  # noqa: BLE001 - any failed download is counted, never fatal
            return None, repr(error)
        return response, spider.file_problem(link, response)

    def _failed(self, link: FileLink, why: str) -> None:
        self._stats.inc_value(FILES_FAILED)
        if (count := self._stats.get_value(FILES_FAILED, 0)) <= FAILURES_KEPT:
            self._stats.set_value(f"{EXAMPLE}file/{count}", f"{link.url}: download failed, {why}"[:500])


class ListingPipeline:
    """Validates every item as a `Listing` and writes the good ones out, one JSON line each; a bad item is counted,
    never stored."""

    def __init__(self, stats: StatsCollector, output: TextIO) -> None:
        self._stats = stats
        self._output = output

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "ListingPipeline":
        assert crawler.stats is not None
        return cls(crawler.stats, Path(crawler.settings.get("FOUNDRY_LISTINGS")).open("a"))

    def close_spider(self) -> None:
        self._output.close()

    def process_item(self, item: Listing | dict[str, object]) -> Listing | None:
        try:
            listing = item if isinstance(item, Listing) else Listing.model_validate(item)
        except ValidationError as error:
            self._stats.inc_value(INVALID)
            self._stats.set_value(f"{EXAMPLE}{self._stats.get_value(INVALID)}", str(error)[:500])
            return None
        self._stats.inc_value(VALID)
        self._output.write(listing.model_dump_json() + "\n")
        self._output.flush()
        return listing
