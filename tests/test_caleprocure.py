"""Replay captured PeopleSoft responses through failure and session-renewal paths."""

import importlib.util
import json
import pickle
from collections import deque
from pathlib import Path

import pytest
from scrapy import Request
from scrapy.crawler import Crawler
from scrapy.exceptions import IgnoreRequest
from scrapy.http import HtmlResponse
from scrapy.settings import Settings
from scrapy.statscollectors import MemoryStatsCollector
from scrapy.utils.request import request_from_dict
from twisted.python.failure import Failure

from foundry.core.middlewares import ReplayMiddleware
from foundry.core.models import FileLink, Fixture, Listing
from foundry.core.session import SessionMiddleware

SITE = Path(__file__).parents[1] / "src/foundry/buyers/us/ca/california/caleprocure"
spec = importlib.util.spec_from_file_location("caleprocure", SITE / "scraper.py")
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def portal():
    crawler = Crawler(module.Spider, Settings())
    crawler.stats = MemoryStatsCollector(crawler)
    spider = module.Spider.from_crawler(crawler)
    crawler.spider = spider
    fixtures = [Fixture.model_validate(f) for f in json.loads((SITE / "fixtures.json").read_text())]
    replay = ReplayMiddleware({(f.method, f.url): f for f in fixtures}, crawler.stats)
    return spider, replay


def failure(request, error):
    result = Failure(error)
    result.request = request
    return result


def queued(request):
    """Serialize exactly the request data JOBDIR writes, including cb_kwargs."""
    spider = request.callback.__self__
    return request_from_dict(pickle.loads(pickle.dumps(request.to_dict(spider=spider))), spider=spider)


def run_event(replay, request):
    """Replay a complete event, returning its listing and its window's next request."""
    output = []
    while request:
        request = queued(request)
        response = replay.process_request(request)
        values = list(request.callback(response, **request.cb_kwargs))
        output.extend(value for value in values if isinstance(value, Listing))
        requests = [value for value in values if isinstance(value, Request)]
        request = requests[0] if requests else None
    return output


@pytest.mark.asyncio
async def test_logout_fixture_renewal_rebuilds_each_affected_window(portal):
    spider, replay = portal
    spider.events = deque([("2720", "0000040585"), ("SS246", "0000040527")])
    interrupted = queued(next(spider.next_event(3)))
    other = queued(next(spider.next_event(4)))
    captured = Fixture.model_validate(json.loads((SITE / "logout.json").read_text())[0])
    response = HtmlResponse(url=captured.url, body=captured.body, encoding="utf8", request=interrupted)
    assert spider.logged_out(response)
    # The form alone identifies a sign-out even if fetch preserved the detail URL.
    assert spider.logged_out(response.replace(url=interrupted.url))
    assert spider.logged_out(response.replace(url="https://caleprocure.ca.gov/?errorCode=127", body=b""))
    with pytest.raises(IgnoreRequest) as expired:
        await SessionMiddleware(None, spider.crawler).process_response(interrupted, response)
    assert list(interrupted.errback(failure(interrupted, expired.value))) == []
    assert not spider.done

    reset = spider.renewed(interrupted)
    assert "psfpd1_3/" in reset.url and "AUC_RESP_INQ_AUC.GBL" in reset.url
    assert spider.current(other.cb_kwargs["state"])
    assert list(interrupted.callback(response, **interrupted.cb_kwargs)) == []
    # Repeated renewal of a window's search preserves its interrupted event.
    reset = spider.renewed(reset)
    search = replay.process_request(Request(module.SEARCH))
    restarted = next(reset.callback(search.replace(url=reset.url), **reset.cb_kwargs))
    listings = run_event(replay, restarted)
    # The other window shares the lost session, but gets its own replacement.
    other_reset = spider.renewed(other)
    assert "psfpd1_4/" in other_reset.url
    other_restart = next(other_reset.callback(search.replace(url=other_reset.url), **other_reset.cb_kwargs))
    listings.extend(run_event(replay, other_restart))
    assert {listing.external_id for listing in listings} == {"2720/0000040585", "SS246/0000040527"}
    # A stale refresh must never requeue completed events.
    assert spider.renewed(interrupted) is None
    assert spider.renewed(other) is None
    assert not spider.events


@pytest.mark.parametrize("phase", ["detail", "package", "file"])
def test_request_failure_keeps_window_moving_and_preserves_resolved_files(portal, phase):
    spider, replay = portal
    spider.events = deque([("broken", "event"), ("SS246", "0000040527")])
    request = queued(next(spider.next_event(4)))
    state = request.cb_kwargs["state"]
    if phase != "detail":
        state["current"] = {
            "external_id": "broken/event",
            "url": "https://caleprocure.ca.gov/event/broken/event",
            "title": "Partial event",
            "metadata": {},
        }
        state["files"] = [FileLink(name="already-resolved.pdf", url="https://caleprocure.ca.gov/file.pdf")]
        response = HtmlResponse(url=request.url, body=b'<input type="hidden" name="ICStateNum" value="2">')
        callback = spider.parse_package if phase == "package" else spider.parse_file
        request = spider.action(response, "failed-action", callback, state)
    request = queued(request)
    assert request.errback is not None
    result = list(request.errback(failure(request, OSError("connection lost"))))
    partial = [value for value in result if isinstance(value, Listing)]
    if phase == "detail":
        assert not partial
        assert spider.crawler.stats.get_value("caleprocure/events_failed") == 1
    else:
        assert partial[0].files[0].name == "already-resolved.pdf"
        assert spider.crawler.stats.get_value("caleprocure/events_partial") == 1
    following = next(value for value in result if isinstance(value, Request))
    listings = run_event(replay, following)
    assert listings[0].external_id == "SS246/0000040527"
    assert len(listings[0].files) == 2


def test_missing_title_skips_event_and_replays_the_next(portal):
    spider, replay = portal
    spider.events = deque([("broken", "event"), ("SS246", "0000040527")])
    request = queued(next(spider.next_event(4)))
    response = HtmlResponse(url=request.url, body=b"<html>Event unavailable</html>")
    following = list(request.callback(response, **request.cb_kwargs))
    assert spider.crawler.stats.get_value("caleprocure/events_failed") == 1
    assert run_event(replay, following[0])[0].title == "Guest Survey"


def test_missing_download_link_retains_first_file_and_finishes_other_files(portal):
    spider, replay = portal
    spider.events = deque([("SS246", "0000040527")])
    detail = next(spider.next_event(4))
    package = next(detail.callback(replay.process_request(detail), **detail.cb_kwargs))
    first = next(package.callback(replay.process_request(package), **package.cb_kwargs))
    second = next(first.callback(replay.process_request(first), **first.cb_kwargs))
    # Remove only the captured resolver's window.open, leaving its real form state.
    response = replay.process_request(second)
    response = response.replace(body=response.body.replace(b"window.open(", b"unavailable("))
    result = list(second.callback(response, **second.cb_kwargs))
    assert len(result) == 1 and isinstance(result[0], Listing)
    assert len(result[0].files) == 1
    assert result[0].files[0].name == "2026_Guest_Survey_IFB_Final_for_Release.pdf"
    assert "No download link" in result[0].metadata["scrape_errors"][0]
    assert spider.crawler.stats.get_value("caleprocure/attachments_failed") == 1


def test_missing_first_link_still_resolves_the_next_attachment(portal):
    spider, replay = portal
    spider.events = deque([("SS246", "0000040527")])
    detail = next(spider.next_event(4))
    package = next(detail.callback(replay.process_request(detail), **detail.cb_kwargs))
    first = next(package.callback(replay.process_request(package), **package.cb_kwargs))
    response = replay.process_request(first)
    response = response.replace(body=response.body.replace(b"window.open(", b"unavailable("))
    second = next(first.callback(response, **first.cb_kwargs))
    listing = run_event(replay, second)[0]
    assert [link.name for link in listing.files] == ["RFI__1_and_STD1000_Guest_Survey.pdf"]
    assert spider.crawler.stats.get_value("caleprocure/events_partial") == 1


def test_both_windows_replay_complete_chains_from_pickled_requests(portal):
    spider, replay = portal
    spider.events = deque([("2720", "0000040585"), ("SS246", "0000040527")])
    requests = [next(spider.next_event(window)) for window in (3, 4)]
    listings = []
    for request in requests:
        restored = queued(request)
        assert restored.cb_kwargs["state"] is not request.cb_kwargs["state"]
        assert spider.current(restored.cb_kwargs["state"])
        assert spider.active[restored.cb_kwargs["state"]["window"]] == restored.cb_kwargs["state"]["token"]
        listings.extend(run_event(replay, restored))
    assert {listing.external_id for listing in listings} == {"2720/0000040585", "SS246/0000040527"}
    assert sorted(len(listing.files) for listing in listings) == [1, 2]
    assert not spider.active
    assert len(spider.done) == 2
