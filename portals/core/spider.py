"""The scraper kit. Every portal's spider is one module with one `Spider(PortalSpider)` class: it walks listings to
detail pages and yields `Listing`s. Signing in, retries, throttling, replay and validation are here, the same for
every portal, so a spider holds only what is particular to its site or platform."""

from collections.abc import Iterable, Iterator
from pathlib import Path
from types import ModuleType
from typing import TextIO

import scrapy
from pydantic import ValidationError
from scrapy.crawler import Crawler
from scrapy.exceptions import IgnoreRequest
from scrapy.http import Request, Response, TextResponse
from scrapy.statscollectors import StatsCollector

from portals.core.models import Fixture, Listing, Session

LOGGED_OUT = "portals/logged_out"
VALID = "portals/valid"
INVALID = "portals/invalid"
UNMATCHED = "portals/unmatched"
CRASHES = "portals/crashes"
EXAMPLE = "portals/example/"


class PortalSpider(scrapy.Spider):
    """The base of every scraper. A subclass sets `name` and `start_urls`, parses listing responses in `parse`
    (yielding detail requests with `callback=self.parse_detail`, and the next page), and yields one `Listing` per
    opportunity from `parse_detail`. Override `logged_out` when the portal signals a lost session another way.

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
        raw = crawler.settings.get("PORTALS_SESSION")
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
            raise IgnoreRequest(f"logged out at {response.url}")
        return response


class ReplayMiddleware:
    """Answers requests from captured fixtures instead of the network, so a spider is checked offline. Fixtures are
    a sample (a listing page and a few details), so a request outside it is skipped, not an error."""

    def __init__(self, fixtures: dict[tuple[str, str], Fixture], stats: StatsCollector) -> None:
        self._fixtures = fixtures
        self._stats = stats

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "ReplayMiddleware":
        fixtures = [Fixture.model_validate(raw) for raw in crawler.settings.getlist("PORTALS_FIXTURES")]
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
        return cls(crawler.stats, Path(crawler.settings.get("PORTALS_LISTINGS")).open("w"))

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
