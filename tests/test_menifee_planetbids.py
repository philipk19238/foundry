"""Regression coverage for intermittent empty PlanetBids responses and browser pacing."""

import asyncio
import json

import pytest
from scrapy import Request
from scrapy.crawler import Crawler
from scrapy.http import TextResponse
from scrapy.settings import Settings
from scrapy.statscollectors import StatsCollector

from foundry.buyers.us.ca.menifee.planetbids.scraper import PacedJSONMiddleware, Spider, api_payload


def middleware() -> PacedJSONMiddleware:
    crawler = Crawler(Spider, Settings({"RETRY_TIMES": 3, "RETRY_PRIORITY_ADJUST": -1}))
    crawler.stats = StatsCollector(crawler)
    crawler.spider = Spider.from_crawler(crawler)
    return PacedJSONMiddleware(crawler)


@pytest.mark.parametrize(
    ("status", "body"),
    [(202, b""), (429, b""), (200, b""), (200, b"<html>challenge</html>"), (200, b'{"data":')],
)
def test_transient_response_retries_with_callback_context(status: int, body: bytes) -> None:
    async def run() -> None:
        handler = middleware()
        request = Request(
            "https://api-external.prod.planetbids.com/papi/bid-downloadable-files?bid_id=142701",
            callback=handler.crawler.spider.parse_related,
            cb_kwargs={"detail": {"data": {"id": "142701"}}, "related": {}, "index": 0},
        )
        response = TextResponse(request.url, status=status, body=body, request=request)
        retry = handler.process_response(request, response)
        assert isinstance(retry, Request)
        assert retry.url == request.url
        assert retry.cb_kwargs == request.cb_kwargs
        assert retry.callback == request.callback
        assert retry.dont_filter
        assert retry.meta["retry_times"] == 1
        assert handler.next_request > asyncio.get_running_loop().time()

    asyncio.run(run())


def test_exhausted_challenge_fails_without_becoming_login_or_empty_listing() -> None:
    handler = middleware()
    request = Request("https://api-external.prod.planetbids.com/papi/bid-details/129606", meta={"retry_times": 6})
    response = TextResponse(request.url, status=202, body=b"", request=request)
    assert handler.process_response(request, response) is response
    assert not handler.crawler.spider.logged_out(response)
    with pytest.raises(ValueError, match="HTTP 202 after retries"):
        api_payload(response)


def test_valid_response_is_not_retried_and_access_refusal_is_preserved() -> None:
    handler = middleware()
    request = Request("https://api-external.prod.planetbids.com/papi/bid-details/129759")
    response = TextResponse(request.url, status=200, body=json.dumps({"data": {}}).encode())
    assert handler.process_response(request, response) is response
    assert api_payload(response) == {"data": {}}
    denied = TextResponse(request.url, status=403)
    assert handler.process_response(request, denied) is denied
    assert handler.crawler.spider.logged_out(denied)


def test_browser_middleware_pacing_is_shared_even_for_concurrent_requests(monkeypatch) -> None:
    async def run() -> None:
        handler = middleware()
        sleeps = []

        async def sleep(delay: float) -> None:
            sleeps.append(delay)

        monkeypatch.setattr(asyncio, "sleep", sleep)
        await asyncio.gather(*(handler.process_request(Request(f"https://example.test/{index}")) for index in range(3)))
        assert sleeps[0] == 0
        assert sleeps[1] > 1
        assert sleeps[2] > 1

    asyncio.run(run())


def test_installing_pacing_preserves_foundry_browser_and_session_middlewares() -> None:
    settings = Settings(
        {
            "DOWNLOADER_MIDDLEWARES": {
                "foundry.core.spider.BrowserMiddleware": 960,
                "foundry.core.spider.SessionMiddleware": 500,
            }
        }
    )
    Spider.update_settings(settings)
    installed = settings.getdict("DOWNLOADER_MIDDLEWARES")
    assert installed["foundry.core.spider.BrowserMiddleware"] == 960
    assert installed["foundry.core.spider.SessionMiddleware"] == 500
    assert installed[PacedJSONMiddleware] == 560


def test_transient_responses_recover_without_losing_fixture_listings(tmp_path, monkeypatch) -> None:
    from pathlib import Path

    from foundry.core.__main__ import crawl
    from foundry.core.models import CrawlJob, Fixture
    from foundry.core.spider import ReplayMiddleware

    root = Path(__file__).parents[1]
    folder = root / "src/foundry/buyers/us/ca/menifee/planetbids"
    fixtures = [Fixture.model_validate(row) for row in json.loads((folder / "fixtures.json").read_text())]
    failures = {
        "https://api-external.prod.planetbids.com/papi/bid-details/129606": (202, b""),
        "https://api-external.prod.planetbids.com/papi/bid-details/129759": (200, b"<html>challenge</html>"),
        "https://api-external.prod.planetbids.com/papi/bid-downloadable-files?bid_id=142701": (429, b""),
    }
    attempts = dict.fromkeys(failures, 0)
    original = ReplayMiddleware.process_request

    def answer(self, request):
        if request.url in failures:
            attempts[request.url] += 1
            if attempts[request.url] == 1:
                status, body = failures[request.url]
                return TextResponse(request.url, status=status, body=body, request=request)
        return original(self, request)

    monkeypatch.setattr(ReplayMiddleware, "process_request", answer)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    job = CrawlJob(
        spider=folder / "scraper.py",
        arguments={},
        listings=tmp_path / "listings.jsonl",
        stats=tmp_path / "stats.json",
        fixtures=fixtures,
    )
    asyncio.run(crawl(job))
    stats = json.loads(job.stats.read_text())
    listings = [json.loads(line) for line in job.listings.read_text().splitlines()]
    assert stats["invalid"] == stats["crashes"] == 0
    assert not stats["logged_out"]
    assert {row["external_id"] for row in listings} == {
        "146108",
        "146087",
        "145127",
        "97403",
        "129606",
        "129759",
        "142701",
    }
    assert all(row["files"] for row in listings)
    assert set(attempts.values()) == {2}
