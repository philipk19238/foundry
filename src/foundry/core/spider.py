"""The scraper kit. Every portal's spider is one module with one `Spider(PortalSpider)` class: it walks listings to
detail pages and yields `Listing`s. Signing in, retries, throttling, replay and validation are here, the same for
every portal, so a spider holds only what is particular to its site or platform."""

import asyncio
import base64
import datetime as dt
import hashlib
import json
from collections.abc import AsyncIterator, Iterable, Iterator
from pathlib import Path
from types import ModuleType
from typing import TextIO, TypedDict
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

from foundry.core import challenges, pace
from foundry.core.models import CrawlProgress, FileLink, Fixture, Listing, SavedFile, Session
from foundry.core.pace import Pace

LOGGED_OUT = "foundry/logged_out"
REFUSED = "foundry/refused"
CHALLENGED = "foundry/challenged"
FILES_SAVED = "foundry/files_saved"
FILES_FAILED = "foundry/files_failed"
FAILURES_KEPT = 5
PROGRESS_SECONDS = 5.0
HTTP_ERROR = 400
REQUESTS = "downloader/request_count"
BLANK_PATH = "__foundry__"
BLANK_PAGE = "<!doctype html><title>foundry</title>"
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
    which arrives as `self.tenant`.

    A portal that puts bot checks in front of its pages sets `solve = True` (or `meta={"solve": True}` on the
    requests that meet them): a live crawl then waits for the stealth browser to clear each check and sends the
    request again. Without it a check stops that request, and the crawl reports where.

    Everything else the kit does for every portal (pace, downloads, resuming a killed crawl) has a default that is
    the best practice, and a hook for a portal that needs otherwise:

    - `file_request` and `file_problem`: how an attachment is downloaded, and what counts as having got it;
    - `custom_settings`: Scrapy's settings, such as `CONCURRENT_REQUESTS_PER_DOMAIN` for how many requests the
      portal takes at once (the pace between them is `delay` in `buyer.toml`, and `stall` there is how long a crawl
      may wait before it counts as stuck);
    - `start`: sent again whenever a killed crawl carries on, so set what the spider learns once (a tenant's name,
      its time zone) there or in `cb_kwargs`, never only in a callback a resumed crawl skips."""

    tenant: str = ""
    solve: bool = False

    def file_request(self, link: FileLink, listing: Listing) -> Request:
        """How a live crawl downloads one attachment: a GET of its link from the listing's page, by default. Override
        it for a portal whose files take something else (a POST, a token from the page, a two-step download)."""
        return Request(link.url, headers={"Referer": listing.url}, dont_filter=True, meta={"solve": self.solve})

    def file_problem(self, link: FileLink, response: Response) -> str | None:
        """Why a download is not the file, or None when it is. By default an error status, or a web page for a link
        that isn't one (a viewer, a login, an expired-link notice). Override it for a portal that serves files oddly,
        such as a download page that is the file, or a 200 with an error in the body."""
        if response.status >= HTTP_ERROR:
            return f"status {response.status}"
        kind = (response.headers.get("Content-Type") or b"").decode().lower()
        if "html" in kind and not link.name.lower().endswith((".htm", ".html")):
            return "got a web page, not the file"
        return None

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": 1.0,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "RETRY_TIMES": 3,
        "HTTPERROR_ALLOW_ALL": True,
    }

    @property
    def session(self) -> Session | None:
        """Everything the portal's browser held at the last run or crawl: cookies, each origin's localStorage and
        sessionStorage, its user agent. `self.session.stored("auth.token")` reads a token the portal's own scripts
        would send as a header. None when replaying fixtures, so a spider must not need it to parse."""
        raw = self.settings.get("FOUNDRY_SESSION")
        return Session.model_validate_json(raw) if raw else None

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

    async def process_request(self, request: Request) -> Response:
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


class FilesPipeline:
    """Downloads every attachment of every good listing while the crawl runs, through the same stealth browser and
    pace as its pages, so links that expire (presigned URLs) are fetched while they still work. Each file is written
    to the crawl's files folder under the SHA-256 of its bytes and logged in `files.jsonl` for foundry to upload as it
    goes. Files foundry already has are skipped; a download that fails is counted and the listing kept."""

    def __init__(self, crawler: Crawler, folder: Path, known: set[str]) -> None:
        assert crawler.stats is not None
        self._crawler = crawler
        self._stats = crawler.stats
        self._folder = folder
        log = folder / "files.jsonl"
        saved = log.read_text().splitlines() if log.exists() else []
        self._known = known | {SavedFile.model_validate_json(line).url for line in saved if line.strip()}
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
        spider = self._crawler.spider
        assert isinstance(spider, PortalSpider) and self._crawler.engine is not None
        for link in listing.files:
            if link.url in self._known:
                continue
            self._known.add(link.url)
            try:
                response = await self._crawler.engine.download_async(spider.file_request(link, listing))
            except Exception as error:  # noqa: BLE001 - any failed download is counted, never fatal
                self._failed(link, repr(error))
                continue
            if (problem := spider.file_problem(link, response)) is not None:
                self._failed(link, problem)
                continue
            digest = hashlib.sha256(response.body).hexdigest()
            (self._folder / digest).write_bytes(response.body)
            kind = response.headers.get("Content-Type")
            saved = SavedFile(
                url=link.url, name=link.name, sha256=digest, content_type=kind.decode() if kind else None,
                size=len(response.body),
            )  # fmt: skip
            self._log.write(saved.model_dump_json() + "\n")
            self._log.flush()
            self._stats.inc_value(FILES_SAVED)
        return item

    def _failed(self, link: FileLink, why: str) -> None:
        self._stats.inc_value(FILES_FAILED)
        if (count := self._stats.get_value(FILES_FAILED, 0)) <= FAILURES_KEPT:
            self._stats.set_value(f"{EXAMPLE}file/{count}", f"{link.url}: download failed, {why}"[:500])


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


def spiders(module: ModuleType) -> Iterator[type[PortalSpider]]:
    """The spider classes a module defines (not ones it imports)."""
    for value in vars(module).values():
        if isinstance(value, type) and issubclass(value, PortalSpider) and value.__module__ == module.__name__:
            yield value
