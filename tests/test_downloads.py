"""Attachment fetches honor the browser-only hook and the ordinary HTTP fallback."""

from types import SimpleNamespace

import pytest
from scrapy import Request
from scrapy.crawler import Crawler
from scrapy.http import Response
from scrapy.settings import Settings
from scrapy.statscollectors import MemoryStatsCollector

from foundry.core.models import FileLink, Listing
from foundry.core.pipelines import FilesPipeline
from foundry.core.spider import FILES_FAILED, FILES_SAVED, PLAIN, PortalSpider


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("browser", "fail_first", "expected"),
    [(False, False, [True]), (False, True, [True, False]), (True, False, [False]), (True, True, [False])],
)
async def test_download_transport(tmp_path, browser, fail_first, expected):
    class Spider(PortalSpider):
        name = "downloads"

        def file_request(self, link, listing):
            return Request(link.url, meta={"browser": browser})

    crawler = Crawler(Spider, Settings())
    crawler.stats = MemoryStatsCollector(crawler)
    crawler.spider = Spider.from_crawler(crawler)
    sent = []

    async def download(request):
        sent.append(bool(request.meta.get(PLAIN)))
        failed = fail_first and len(sent) == 1
        return Response(
            request.url,
            headers={"Content-Type": "text/html" if failed else "application/pdf"},
            body=b"<html>sign in</html>" if failed else b"%PDF-1.7\nfile",
        )

    crawler.engine = SimpleNamespace(download_async=download)
    pipeline = FilesPipeline(crawler, tmp_path, set())
    listing = Listing(url="https://portal.example/event/1", title="Event")
    link = FileLink(url="https://portal.example/file.pdf", name="file.pdf")
    await pipeline._download(link, listing)
    pipeline.close_spider()
    assert sent == expected
    failed = browser and fail_first
    assert crawler.stats.get_value(FILES_SAVED, 0) == int(not failed)
    assert crawler.stats.get_value(FILES_FAILED, 0) == int(failed)
    assert bool((tmp_path / "files.jsonl").read_text()) is not failed
