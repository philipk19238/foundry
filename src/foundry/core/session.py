"""The portal's session on every request of a crawl, and a fresh one when the portal drops it mid-crawl."""

import asyncio

from scrapy.crawler import Crawler
from scrapy.exceptions import IgnoreRequest
from scrapy.http import Request, Response

from foundry.core.models import Session
from foundry.core.spider import FILE, LOGGED_OUT, REFUSED, RENEW, RENEWED, PortalSpider

MOST_RENEWALS = 3
SENT_WITH = "foundry_session"


class SessionMiddleware:
    """Sends the portal's session with every request. When a response says the session is gone, a live crawl renews
    it (`RENEW`, which the stealth browser answers with a fresh one) and sends the request again, up to MOST_RENEWALS
    times a crawl; requests that met the same lost session wait for that one renewal. With no browser to renew from,
    or past that many, the crawl stops as logged out."""

    def __init__(self, session: Session | None, crawler: Crawler) -> None:
        self._session = session
        self._crawler = crawler
        self._renewals = 0
        self._renewing = asyncio.Lock()

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "SessionMiddleware":
        raw = crawler.settings.get("FOUNDRY_SESSION")
        return cls(Session.model_validate_json(raw) if raw else None, crawler)

    def process_request(self, request: Request) -> None:
        request.meta[SENT_WITH] = self._renewals
        if self._session is None:
            return
        for key, value in self._session.headers.items():
            request.headers.setdefault(key, value)
        if self._session.cookies and "Cookie" not in request.headers:
            request.headers["Cookie"] = self._session.cookie_header
            request.meta[f"{SENT_WITH}_cookies"] = True

    async def process_response(self, request: Request, response: Response) -> Request | Response:
        """An attachment refused (an expired link, a file host's 403) is a failed download, never a lost session."""
        spider = self._crawler.spider
        if request.meta.get(FILE) or not (isinstance(spider, PortalSpider) and spider.logged_out(response)):
            return response
        if await self._renewed(request.meta.get(SENT_WITH, 0)):
            again = spider.renewed(self._unsent(request))
            if again is None:
                raise IgnoreRequest(f"dropped after renewing the session at {response.url}")
            return again
        assert self._crawler.stats is not None
        self._crawler.stats.set_value(LOGGED_OUT, True)
        if self._crawler.stats.get_value(REFUSED) is None:
            self._crawler.stats.set_value(REFUSED, f"{response.status} at {response.url}")
        raise IgnoreRequest(f"logged out at {response.url}")

    async def _renewed(self, sent_with: int) -> bool:
        """Whether the session is fresh again: renewed already since the request was sent, or now."""
        async with self._renewing:
            if sent_with < self._renewals:
                return True
            if self._renewals >= MOST_RENEWALS:
                return False
            answers = await self._crawler.signals.send_catch_log_async(signal=RENEW)
            fresh = next((answer for _, answer in answers if isinstance(answer, Session)), None)
            if fresh is None:
                return False
            self._session = fresh
            self._renewals += 1
            assert self._crawler.stats is not None
            self._crawler.stats.inc_value(RENEWED)
            return True

    def _unsent(self, request: Request) -> Request:
        """The request as the spider made it, so it goes out again with the new session's cookies."""
        again = request.replace(
            dont_filter=True, meta={key: value for key, value in request.meta.items() if not key.startswith(SENT_WITH)}
        )
        if request.meta.get(f"{SENT_WITH}_cookies"):
            again.headers.pop("Cookie", None)
        return again
