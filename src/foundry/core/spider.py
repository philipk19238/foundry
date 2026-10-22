"""The scraper kit. Every portal's spider is one module with one `Spider(PortalSpider)` class: it walks listings to
detail pages and yields `Listing`s. Signing in, retries, throttling, replay and validation are here, the same for
every portal, so a spider holds only what is particular to its site or platform."""

import asyncio
import base64
from collections.abc import Iterable, Iterator
from pathlib import Path
from types import ModuleType
from typing import TextIO
from urllib.parse import urlsplit

import scrapy
from playwright.async_api import Browser, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError
from pydantic import ValidationError
from scrapy import signals
from scrapy.crawler import Crawler
from scrapy.exceptions import IgnoreRequest, NotConfigured
from scrapy.http import Headers, Request, Response, TextResponse
from scrapy.responsetypes import responsetypes
from scrapy.statscollectors import StatsCollector

from foundry.core.models import Fixture, Listing, Session

LOGGED_OUT = "foundry/logged_out"
REFUSED = "foundry/refused"
VALID = "foundry/valid"
INVALID = "foundry/invalid"
UNMATCHED = "foundry/unmatched"
CRASHES = "foundry/crashes"
EXAMPLE = "foundry/example/"


class PortalSpider(scrapy.Spider):
    """The base of every scraper. A subclass sets `name` and `start_urls`, parses listing responses in `parse`
    (yielding detail requests with `callback=self.parse_detail`, and the next page), and yields one `Listing` per
    opportunity from `parse_detail`. Override `logged_out` when the portal signals a lost session another way. The
    signed-in session is sent with every request; `self.credentials` holds the portal's other saved secrets.

    A platform spider (`platforms/<name>/spider.py`) serves many buyers: each buyer's `buyer.toml` names its `tenant`,
    which arrives as `self.tenant`."""

    tenant: str = ""

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": 1.0,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "RETRY_TIMES": 3,
        "HTTPERROR_ALLOW_ALL": True,
    }

    @property
    def credentials(self) -> dict[str, str]:
        """The portal's saved credentials by name, for a spider that signs in itself (an API key in a header, a token
        exchange, a login form). Empty when replaying fixtures, so a spider must not need them to parse."""
        return dict(self.settings.getdict("FOUNDRY_CREDENTIALS"))

    def parse_detail(self, response: Response) -> Iterable[Listing | Request]:
        raise NotImplementedError

    def logged_out(self, response: Response) -> bool:
        return response.status in (401, 403) or "login" in response.url.lower()


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


class BrowserMiddleware:
    """Sends every request from inside a stealth browser, when the crawl was given one: a page on the portal passes
    its bot checks once, then each request runs as that page's `fetch`, with the browser's address, fingerprint and
    cookies. The response is what plain HTTP would have returned, so spiders and their fixtures don't change. A
    request the portal page may not fetch (another site without CORS) runs from a page on its own site."""

    def __init__(self, cdp_url: str, home: str, session: Session | None) -> None:
        self._cdp_url = cdp_url
        self._home = home
        self._session = session
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._pages: dict[str, Page] = {}
        self._lock = asyncio.Lock()

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "BrowserMiddleware":
        cdp_url = crawler.settings.get("FOUNDRY_BROWSER")
        if not cdp_url:
            raise NotConfigured("no browser for this crawl")
        raw = crawler.settings.get("FOUNDRY_SESSION")
        middleware = cls(
            cdp_url, crawler.settings.get("FOUNDRY_HOME"), Session.model_validate_json(raw) if raw else None
        )
        crawler.signals.connect(middleware.close, signal=signals.spider_closed)
        return middleware

    async def process_request(self, request: Request) -> Response:
        sent = {
            "url": request.url,
            "method": request.method,
            "headers": {key: value for key, value in request.headers.to_unicode_dict().items()
                        if key.lower() not in {"cookie", "user-agent", "accept-encoding"}},
            "body": base64.b64encode(request.body).decode() if request.body else None,
        }  # fmt: skip
        try:
            got = await (await self._page(self._home)).evaluate(FETCH, sent)
        except PlaywrightError:
            got = await (await self._page(request.url)).evaluate(FETCH, sent)
        headers = Headers([(key, value) for key, value in got["headers"] if key.lower() not in DROPPED])
        body = base64.b64decode(got["body"])
        kind = responsetypes.from_args(headers=headers, url=got["url"], body=body)
        return kind(url=got["url"], status=got["status"], headers=headers, body=body, request=request)

    async def _page(self, url: str) -> Page:
        """The page fetches run from for `url`'s site, opened on first use and kept for the crawl."""
        site = "{0.scheme}://{0.netloc}".format(urlsplit(url))
        async with self._lock:
            if site not in self._pages:
                context = (await self._connected()).contexts[0]
                page = await context.new_page()
                await page.goto(url if url == self._home else f"{site}/", wait_until="load")
                self._pages[site] = page
            return self._pages[site]

    async def _connected(self) -> Browser:
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.connect_over_cdp(self._cdp_url)
            if self._session is not None and self._session.cookies:
                await self._browser.contexts[0].add_cookies(
                    [cookie.model_dump(by_alias=True) for cookie in self._session.cookies]
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
            self._stats.set_value(f"{EXAMPLE}{self._stats.get_value(INVALID)}", f"{response.url}: {exception}"[:500])
            return ()
        self._stats.inc_value(CRASHES)
        self._stats.set_value(
            f"{EXAMPLE}crash/{self._stats.get_value(CRASHES)}", f"{response.url}: {exception!r}"[:500]
        )
        return None


class ListingPipeline:
    """Validates every item as a `Listing` and writes the good ones out, one JSON line each; a bad item is counted,
    never stored."""

    def __init__(self, stats: StatsCollector, output: TextIO) -> None:
        self._stats = stats
        self._output = output

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "ListingPipeline":
        assert crawler.stats is not None
        return cls(crawler.stats, Path(crawler.settings.get("FOUNDRY_LISTINGS")).open("w"))

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
        return listing


def spiders(module: ModuleType) -> Iterator[type[PortalSpider]]:
    """The spider classes a module defines (not ones it imports)."""
    for value in vars(module).values():
        if isinstance(value, type) and issubclass(value, PortalSpider) and value.__module__ == module.__name__:
            yield value
