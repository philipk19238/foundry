"""A portal that drops a crawl's session mid-crawl must not end the crawl: the kit gets a fresh session from the
stealth browser once for all the requests that met the lost one, sends each again with the new cookies, and only
after MOST_RENEWALS (or with no browser to renew from) stops the crawl as logged out."""

import asyncio
import datetime as dt

import pytest
from scrapy.exceptions import IgnoreRequest
from scrapy.http import HtmlResponse, Request
from scrapy.utils.test import get_crawler

from foundry.core.models import Cookie, Session
from foundry.core.session import MOST_RENEWALS, SessionMiddleware
from foundry.core.spider import LOGGED_OUT, RENEW, RENEWED, PortalSpider

AT = dt.datetime(2026, 10, 22, tzinfo=dt.UTC)
PAGE = "https://portal.test/bids"
LOGIN = "https://portal.test/login?expired=1"


class Spider(PortalSpider):
    name = "portal"


def session(value: str) -> Session:
    return Session(cookies=(Cookie(name="sid", value=value, domain="portal.test"),), captured_at=AT)


class Browser:
    """Answers RENEW as the stealth browser does: a new session each time, counted."""

    def __init__(self) -> None:
        self.renewals = 0

    async def renew(self) -> Session:
        self.renewals += 1
        await asyncio.sleep(0)
        return session(f"fresh-{self.renewals}")


def kit(browser: Browser | None) -> tuple[SessionMiddleware, object]:
    crawler = get_crawler(Spider)
    crawler.spider = crawler._create_spider()
    crawler.stats.open_spider()
    if browser is not None:
        crawler.signals.connect(browser.renew, signal=RENEW)
    return SessionMiddleware(session("stale"), crawler), crawler


def sent(middleware: SessionMiddleware, url: str = PAGE) -> Request:
    request = Request(url)
    middleware.process_request(request)
    return request


def logged_out(request: Request) -> HtmlResponse:
    return HtmlResponse(LOGIN, body=b"<form>sign in</form>", request=request)


def test_requests_that_meet_one_lost_session_share_one_renewal_and_go_again_with_its_cookies() -> None:
    browser = Browser()
    middleware, crawler = kit(browser)
    first, second = sent(middleware), sent(middleware, PAGE + "?page=2")

    async def both() -> list[Request]:
        return list(await asyncio.gather(*(middleware.process_response(r, logged_out(r)) for r in (first, second))))

    again = asyncio.run(both())
    assert browser.renewals == 1
    assert [request.url for request in again] == [PAGE, PAGE + "?page=2"]
    for request in again:
        middleware.process_request(request)
        assert request.headers["Cookie"] == b"sid=fresh-1"
    assert crawler.stats.get_value(RENEWED) == 1 and not crawler.stats.get_value(LOGGED_OUT)


def test_a_portal_that_keeps_dropping_the_session_stops_the_crawl_as_logged_out() -> None:
    browser = Browser()
    middleware, crawler = kit(browser)
    request = sent(middleware)
    for _ in range(MOST_RENEWALS):
        request = asyncio.run(middleware.process_response(request, logged_out(request)))
        middleware.process_request(request)
    with pytest.raises(IgnoreRequest):
        asyncio.run(middleware.process_response(request, logged_out(request)))
    assert browser.renewals == MOST_RENEWALS
    assert crawler.stats.get_value(LOGGED_OUT) is True


def test_with_no_browser_to_renew_from_a_lost_session_stops_the_crawl() -> None:
    middleware, crawler = kit(None)
    request = sent(middleware)
    with pytest.raises(IgnoreRequest):
        asyncio.run(middleware.process_response(request, logged_out(request)))
    assert crawler.stats.get_value(LOGGED_OUT) is True
