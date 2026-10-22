"""The kit's middlewares: requests sent from the stealth browser, replayed fixtures, errors counted instead of
crashing the crawl, and start requests sent again when a crawl carries on. `foundry.core.session` sends the portal's
session and renews it, and `foundry.core.bronze` keeps everything the portal sent."""

import asyncio
import base64
import datetime as dt
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
from foundry.core.models import Cookie, Fixture, Session
from foundry.core.pace import Pace
from foundry.core.spider import (
    CHALLENGED,
    CRASHES,
    EXAMPLE,
    FILE,
    INVALID,
    PLAIN,
    RENEW,
    STOPPING,
    UNMATCHED,
    PortalSpider,
)

BLANK_PATH = "__foundry__"
PAGE_SLACK = 10.0
BLANK_PAGE = "<!doctype html><title>foundry</title>"
TEXT = ("json", "text", "html", "xml", "javascript", "x-www-form-urlencoded")
LATENCY = "download_latency"


FETCH = """async ({url, method, headers, body, timeout}) => {
  const response = await fetch(url, {
    method, headers, credentials: "include", signal: AbortSignal.timeout(timeout),
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
  if (sessionStorage.getItem(RENEWED_KEY)) return;
  const kept = KEPT;
  for (const [name, store] of [["local", localStorage], ["session", sessionStorage]]) {
    for (const [key, value] of Object.entries(kept[name][location.origin] || {})) {
      if (store.getItem(key) === null) store.setItem(key, value);
    }
  }
})();"""
FORGET = """(key) => { localStorage.clear(); sessionStorage.clear(); sessionStorage.setItem(key, "1"); }"""
RENEWED_KEY = "foundry-renewed"
CHALLENGES_KEPT = 5
SOLVE_SECONDS = 90.0
SOLVED_FOR = 5.0


class Sent(TypedDict):
    """A request as the browser's `fetch` sends it; `body` is base64."""

    url: str
    method: str
    headers: dict[str, str]
    body: str | None
    timeout: int


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
    watched until the check clears, and sent again. Otherwise it is counted and the request dropped. How long each
    request took, its pace included, goes in its `download_latency`, as Scrapy's own downloads report it."""

    def __init__(self, crawler: Crawler, cdp_url: str, home: str, session: Session | None) -> None:
        assert crawler.stats is not None
        self._crawler = crawler
        self._timeout = crawler.settings.getfloat("DOWNLOAD_TIMEOUT")
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
        crawler.signals.connect(middleware.stopping, signal=STOPPING)
        crawler.signals.connect(middleware.renew, signal=RENEW)
        return middleware

    def stopping(self) -> None:
        self._pace.hurry()

    async def renew(self) -> Session:
        """A fresh session for a lost one: the browser forgets every cookie, and the portal's stored values (which the
        kept session no longer puts back), loads the portal's page again, a new guest session, where the spider's
        `sign_in` signs in, and returns what it then holds, which plain-HTTP requests send from then on."""
        context = (await self._connected()).contexts[0]
        await context.clear_cookies()
        page = await self._page(self._home)
        await page.evaluate(FORGET, RENEWED_KEY)
        await page.goto(self._home, wait_until="load")
        if isinstance(spider := self._crawler.spider, PortalSpider):
            await spider.sign_in(page)
        kept = self._session
        return Session(
            cookies=tuple(Cookie.model_validate(cookie) for cookie in await context.cookies()),
            headers=kept.headers if kept is not None else {},
            user_agent=await page.evaluate("navigator.userAgent"),
            captured_at=dt.datetime.now(dt.UTC),
        )

    async def process_request(self, request: Request) -> Response | None:
        if request.meta.get(PLAIN):
            return None
        began = asyncio.get_running_loop().time()
        sent = Sent(
            url=request.url,
            method=request.method,
            headers={str(key): value for key, value in request.headers.to_unicode_dict().items()
                     if str(key).lower() not in {"cookie", "user-agent", "accept-encoding"}},
            body=base64.b64encode(request.body).decode() if request.body else None,
            timeout=int(self._timeout * 1000),
        )  # fmt: skip
        solve = request.meta.get("solve", getattr(self._crawler.spider, "solve", False))
        got = await self._paced(sent, request.url if request.meta.get(FILE) else self._home, solve)
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
        request.meta[LATENCY] = asyncio.get_running_loop().time() - began
        return kind(url=got["url"], status=got["status"], headers=headers, body=body, request=request)

    async def _paced(self, sent: Sent, at: str, solve: bool) -> Fetched:
        """`sent` fetched from the page on `at`'s site in the site's turn, and again, slower, while the portal answers
        with a rate limit. A bot check that is still there after one slower try is handed back to a spider that
        can't solve it, rather than slowing the whole site. A crawl that is stopping hands the rate limit back
        instead, and Scrapy's retry queues the request again, where the stopping crawl saves it."""
        site = "{0.scheme}://{0.netloc}".format(urlsplit(sent["url"]))
        for tries in range(RATE_TRIES):
            async with self._pace.turn(site):
                got = await self._fetch(at, sent)
            wait = pace.retry_after(got["status"], dict(got["headers"]))
            if wait is None:
                self._pace.went_through(site)
                return got
            if self._pace.hurrying or (tries > 0 and not solve and self._challenged(sent, got)):
                return got
            self._pace.limited(site, wait)
        return got

    async def _fetch(self, at: str, sent: Sent) -> Fetched:
        """`sent` fetched from the page on `at`'s site, or from one on its own site if that page may not fetch it. A
        request that runs past DOWNLOAD_TIMEOUT is a TimeoutError, which Scrapy retries."""
        async with asyncio.timeout(self._timeout + PAGE_SLACK):
            try:
                return await (await self._page(at)).evaluate(FETCH, sent)
            except PlaywrightError as error:
                if "timeout" in str(error).lower() or "abort" in str(error).lower():
                    raise TimeoutError(f"{sent['url']} didn't answer in {self._timeout:g} s") from error
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
                await context.add_init_script(
                    RESTORE.replace("KEPT", json.dumps(kept)).replace("RENEWED_KEY", json.dumps(RENEWED_KEY))
                )
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
