"""The kit's middlewares: the portal session on every request, requests sent from the stealth browser, replayed
fixtures, errors counted instead of crashing the crawl, and start requests sent again when a crawl carries on."""

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Iterable
from typing import TypedDict
from urllib.parse import urlsplit

from playwright.async_api import Browser, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError
from pydantic import ValidationError
from scrapy import signals
from scrapy.crawler import Crawler
from scrapy.exceptions import IgnoreRequest, NotConfigured
from scrapy.http import Headers, Request, Response, TextResponse
from scrapy.responsetypes import responsetypes
from scrapy.statscollectors import StatsCollector

from foundry.core import challenges, pace
from foundry.core.models import Fixture, Session
from foundry.core.pace import Pace
from foundry.core.spider import (
    CHALLENGED,
    CRASHES,
    EXAMPLE,
    INVALID,
    LOGGED_OUT,
    PLAIN,
    REFUSED,
    UNMATCHED,
    PortalSpider,
)

BLANK_PATH = "__foundry__"
BLANK_PAGE = "<!doctype html><title>foundry</title>"


class SessionMiddleware:
    """Sends the portal's signed-in session with every request, and stops the crawl as logged out the first time a
    response says the session is gone."""

    def __init__(self, session: Session | None, crawler: Crawler) -> None:
        self._session = session
        self._crawler = crawler

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "SessionMiddleware":
        raw = crawler.settings.get("FOUNDRY_SESSION")
        return cls(Session.model_validate_json(raw) if raw else None, crawler)

    def process_request(self, request: Request) -> None:
        if self._session is None:
            return
        for key, value in self._session.headers.items():
            request.headers.setdefault(key, value)
        if self._session.cookies:
            request.headers.setdefault("Cookie", self._session.cookie_header)

    def process_response(self, request: Request, response: Response) -> Response:
        spider = self._crawler.spider
        if isinstance(spider, PortalSpider) and spider.logged_out(response) and self._crawler.stats is not None:
            self._crawler.stats.set_value(LOGGED_OUT, True)
            if self._crawler.stats.get_value(REFUSED) is None:
                self._crawler.stats.set_value(REFUSED, f"{response.status} at {response.url}")
            raise IgnoreRequest(f"logged out at {response.url}")
        return response


FETCH = """async ({url, method, headers, body}) => {
  const response = await fetch(url, {
    method, headers, credentials: "include",
    body: body === null ? undefined : Uint8Array.from(atob(body), (c) => c.charCodeAt(0)),
  });
  const bytes = new Uint8Array(await response.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return {url: response.url, status: response.status, headers: [...response.headers], body: btoa(binary)};
}"""
DROPPED = {"content-encoding", "content-length", "transfer-encoding"}
SOLVE_TRIES = 3
RATE_TRIES = 5
RESTORE = """(() => {
  const kept = KEPT;
  for (const [name, store] of [["local", localStorage], ["session", sessionStorage]]) {
    for (const [key, value] of Object.entries(kept[name][location.origin] || {})) {
      if (store.getItem(key) === null) store.setItem(key, value);
    }
  }
})();"""
CHALLENGES_KEPT = 5
SOLVE_SECONDS = 90.0
SOLVED_FOR = 5.0


class Sent(TypedDict):
    """A request as the browser's `fetch` sends it; `body` is base64."""

    url: str
    method: str
    headers: dict[str, str]
    body: str | None


class Fetched(TypedDict):
    """What the browser's `fetch` got back; `body` is base64."""

    url: str
    status: int
    headers: list[tuple[str, str]]
    body: str


class BrowserMiddleware:
    """Sends every request from inside a stealth browser, when the crawl was given one: a page on the portal passes
    its bot checks once, then each request runs as that page's `fetch`, with the browser's address, fingerprint and
    cookies. The response is what plain HTTP would have returned, so spiders and their fixtures don't change. A
    request the portal page may not fetch (another site without CORS) runs from a page on its own site. A bot check
    (`challenges.challenged`) is cleared only for a spider that asks to `solve`: the request is opened as a page,
    watched until the check clears, and sent again. Otherwise it is counted and the request dropped."""

    def __init__(self, crawler: Crawler, cdp_url: str, home: str, session: Session | None) -> None:
        assert crawler.stats is not None
        self._crawler = crawler
        self._pace = Pace(
            crawler.settings.getfloat("DOWNLOAD_DELAY"),
            crawler.settings.getint("CONCURRENT_REQUESTS_PER_DOMAIN"),
            crawler.stats,
        )
        self._cdp_url = cdp_url
        self._home = home
        self._session = session
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._pages: dict[str, Page] = {}
        self._lock = asyncio.Lock()
        self._solving = asyncio.Lock()
        self._solved_at = 0.0

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "BrowserMiddleware":
        cdp_url = crawler.settings.get("FOUNDRY_BROWSER")
        if not cdp_url:
            raise NotConfigured("no browser for this crawl")
        raw = crawler.settings.get("FOUNDRY_SESSION")
        middleware = cls(
            crawler, cdp_url, crawler.settings.get("FOUNDRY_HOME"), Session.model_validate_json(raw) if raw else None
        )
        crawler.signals.connect(middleware.close, signal=signals.spider_closed)
        return middleware

    async def process_request(self, request: Request) -> Response | None:
        if request.meta.get(PLAIN):
            return None
        sent = Sent(
            url=request.url,
            method=request.method,
            headers={str(key): value for key, value in request.headers.to_unicode_dict().items()
                     if str(key).lower() not in {"cookie", "user-agent", "accept-encoding"}},
            body=base64.b64encode(request.body).decode() if request.body else None,
        )  # fmt: skip
        got = await self._paced(sent)
        solve = request.meta.get("solve", getattr(self._crawler.spider, "solve", False))
        for _ in range(SOLVE_TRIES if solve else 0):
            if not self._challenged(sent, got):
                break
            await self._solve(request.url)
            got = await self._fetch(request.url, sent)
        if self._challenged(sent, got):
            assert self._crawler.stats is not None
            stats = self._crawler.stats
            stats.inc_value(CHALLENGED)
            if (count := stats.get_value(CHALLENGED, 0)) <= CHALLENGES_KEPT:
                example = f"{request.url} ({got['status']}): a bot check at {got['url']}"
                stats.set_value(f"{EXAMPLE}challenge/{count}", example[:500])
            raise IgnoreRequest(f"challenged at {request.url}")
        headers = Headers([(key, value) for key, value in got["headers"] if key.lower() not in DROPPED])
        body = base64.b64decode(got["body"])
        kind = responsetypes.from_args(headers=headers, url=got["url"], body=body)
        return kind(url=got["url"], status=got["status"], headers=headers, body=body, request=request)

    async def _paced(self, sent: Sent) -> Fetched:
        """`sent` fetched in the site's turn, and again, slower, while the portal answers with a rate limit."""
        site = "{0.scheme}://{0.netloc}".format(urlsplit(sent["url"]))
        for _ in range(RATE_TRIES):
            async with self._pace.turn(site):
                got = await self._fetch(self._home, sent)
            wait = pace.retry_after(got["status"], dict(got["headers"]))
            if wait is None:
                self._pace.went_through(site)
                return got
            await asyncio.sleep(self._pace.limited(site, wait))
        return got

    async def _fetch(self, at: str, sent: Sent) -> Fetched:
        """`sent` fetched from the page on `at`'s site, or from one on its own site if that page may not fetch it."""
        try:
            return await (await self._page(at)).evaluate(FETCH, sent)
        except PlaywrightError:
            return await (await self._page(sent["url"])).evaluate(FETCH, sent)

    def _challenged(self, sent: Sent, got: Fetched) -> bool:
        headers = dict(got["headers"])
        text = base64.b64decode(got["body"])[:20_000].decode(errors="replace")
        return challenges.challenged(sent["url"], got["url"], got["status"], headers, text)

    async def _solve(self, url: str) -> None:
        """Opens a challenged `url` as a page and waits for the browser's solver to clear it, so the browser keeps the
        token it earns. Requests challenged together wait for one solve."""
        async with self._solving:
            if asyncio.get_running_loop().time() - self._solved_at < SOLVED_FOR:
                return
            await challenges.clear(await self._page(url), url, SOLVE_SECONDS)
            self._solved_at = asyncio.get_running_loop().time()

    async def _page(self, url: str) -> Page:
        """The page fetches run from for `url`'s site, opened on first use and kept for the crawl: the portal's own
        page for its site, so its bot checks are passed, and for any other site (a file host, a CDN) an empty page
        foundry serves at that site's address, which no redirect can take away and which never touches the site."""
        site = "{0.scheme}://{0.netloc}".format(urlsplit(url))
        async with self._lock:
            if site not in self._pages:
                context = (await self._connected()).contexts[0]
                page = await context.new_page()
                if url == self._home:
                    await page.goto(url, wait_until="load")
                else:
                    blank = f"{site}/{BLANK_PATH}"
                    await page.route(blank, lambda route: route.fulfill(content_type="text/html", body=BLANK_PAGE))
                    await page.goto(blank)
                self._pages[site] = page
            return self._pages[site]

    async def _connected(self) -> Browser:
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.connect_over_cdp(self._cdp_url)
            if self._session is not None:
                context = self._browser.contexts[0]
                if self._session.cookies:
                    await context.add_cookies([cookie.model_dump(by_alias=True) for cookie in self._session.cookies])
                kept = {"local": self._session.local_storage, "session": self._session.session_storage}
                await context.add_init_script(RESTORE.replace("KEPT", json.dumps(kept)))
        return self._browser

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()


class ReplayMiddleware:
    """Answers requests from captured fixtures instead of the network, so a spider is checked offline. Fixtures are
    a sample (a listing page and a few details), so a request outside it is skipped, not an error."""

    def __init__(self, fixtures: dict[tuple[str, str], Fixture], stats: StatsCollector) -> None:
        self._fixtures = fixtures
        self._stats = stats

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "ReplayMiddleware":
        fixtures = [Fixture.model_validate(raw) for raw in crawler.settings.getlist("FOUNDRY_FIXTURES")]
        assert crawler.stats is not None
        return cls({(fixture.method.upper(), fixture.url): fixture for fixture in fixtures}, crawler.stats)

    def process_request(self, request: Request) -> Response:
        fixture = self._fixtures.get((request.method.upper(), request.url))
        if fixture is None:
            self._stats.inc_value(UNMATCHED)
            raise IgnoreRequest(f"not in the sample: {request.method} {request.url}")
        return TextResponse(
            url=fixture.url, status=fixture.status, headers=fixture.headers, body=fixture.body, encoding="utf-8"
        )


class ErrorsMiddleware:
    """A `Listing` the scraper could not build counts as invalid, not as a crash; any other exception is a crash.
    Either way its message is kept, so the healing agent sees what broke."""

    def __init__(self, stats: StatsCollector) -> None:
        self._stats = stats

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "ErrorsMiddleware":
        assert crawler.stats is not None
        return cls(crawler.stats)

    def process_spider_exception(self, response: Response, exception: Exception) -> Iterable[object] | None:
        if isinstance(exception, ValidationError):
            self._stats.inc_value(INVALID)
            example = f"{response.url} ({response.status}): {exception}"
            self._stats.set_value(f"{EXAMPLE}{self._stats.get_value(INVALID)}", example[:500])
            return ()
        self._stats.inc_value(CRASHES)
        example = f"{response.url} ({response.status}): {exception!r}"
        self._stats.set_value(f"{EXAMPLE}crash/{self._stats.get_value(CRASHES)}", example[:500])
        return None


class ResumeMiddleware:
    """A resumed crawl skips every request it already sent, start requests included, so a spider that learns what it
    needs on its first page (a tenant's name, its time zone) would never learn it again. Start requests are always
    sent, and what follows them is skipped as before."""

    async def process_start(self, start: AsyncIterator[object]) -> AsyncIterator[object]:
        async for item in start:
            yield item.replace(dont_filter=True) if isinstance(item, Request) else item
