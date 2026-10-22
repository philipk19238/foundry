"""The data every scraper and the crawl runner share: what a scraper yields, what it is checked against, how it signs
in, and how a site is registered."""

import datetime as dt
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class FileLink(BaseModel, frozen=True):
    """An attachment: a URL that downloads the file itself, and the name the portal shows."""

    url: str
    name: str


class Listing(BaseModel, frozen=True):
    """What a scraper yields for one opportunity on its portal. `external_id` is the portal's own id for it (a
    solicitation number, or the detail page's id): it must stay the same when the portal edits the listing. Leave it
    out only when the portal shows none; the title then stands in for it."""

    external_id: str | None = Field(default=None, min_length=1)
    url: str
    title: str = Field(min_length=1)
    buyer: str | None = None
    due_at: dt.datetime | None = None
    description: str = ""
    metadata: dict[str, JsonValue] = {}
    files: tuple[FileLink, ...] = ()


class Fixture(BaseModel, frozen=True):
    """One response captured from the portal, replayed to check a scraper without the network."""

    method: str = "GET"
    url: str
    status: int = 200
    headers: dict[str, str] = {}
    body: str


class Exchange(BaseModel, frozen=True):
    """One request a browser page sent and the response it got, as the recorder logs it. `kind` is what the page
    asked for (`document`, `xhr`, `fetch`, `script`, ...); `body` is None when the response isn't text."""

    method: str
    url: str
    kind: str
    request_headers: dict[str, str]
    request_body: str | None
    status: int
    headers: dict[str, str]
    body: str | None

    @property
    def fixture(self) -> Fixture:
        return Fixture(method=self.method, url=self.url, status=self.status, headers=self.headers, body=self.body or "")


class Cookie(BaseModel, frozen=True):
    """A cookie as Playwright reads it from the browser."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    name: str
    value: str
    domain: str
    path: str = "/"
    secure: bool = False
    http_only: bool = Field(default=False, alias="httpOnly")


class Session(BaseModel, frozen=True):
    """What a scraper sends to look signed in: the portal's cookies, plus any headers it needs (a bearer token, a
    CSRF header). Captured from the agent's browser after it signs in."""

    cookies: tuple[Cookie, ...] = ()
    headers: dict[str, str] = {}
    captured_at: dt.datetime

    @property
    def cookie_header(self) -> str:
        return "; ".join(f"{cookie.name}={cookie.value}" for cookie in self.cookies)


class PlatformPortal(BaseModel, frozen=True):
    """A buyer's page on a shared platform: the platform's spider, pointed at this buyer's tenant."""

    kind: Literal["platform"] = "platform"
    name: str
    platform: str
    tenant: str
    url: str


class SitePortal(BaseModel, frozen=True):
    """A buyer's own one-off site, with its spider in a folder of the same name beside `buyer.toml`."""

    kind: Literal["site"] = "site"
    name: str
    url: str


Portal = Annotated[PlatformPortal | SitePortal, Field(discriminator="kind")]


class Buyer(BaseModel, frozen=True):
    """`buyers/<country>/<state>/<buyer>/buyer.toml`: who buys, and every portal they post on."""

    name: str
    jurisdiction: str
    portals: tuple[Portal, ...]


class Site(BaseModel, frozen=True):
    """One registered portal, resolved: the spider to run, the arguments it takes, and the fixtures it is checked
    against. `key` is `<buyer folder>/<portal name>`, e.g. `us/tx/austin/financeonline`."""

    key: str
    url: str
    spider: Path
    arguments: dict[str, str]
    fixtures: Path


class Access(BaseModel, frozen=True):
    """Everything a live crawl signs in with: the session read from the agent's browser, and the portal's saved
    credentials by name (API keys, tokens, a second login) for spiders that sign in themselves."""

    session: Session | None = None
    credentials: dict[str, str] = {}


class CrawlJob(BaseModel, frozen=True):
    """What the crawl process is told: the spider to run, where to write, and either a session (live) or fixtures
    (replayed offline). A live crawl given `browser` (a stealth browser's CDP URL) sends every request from it,
    starting at the portal's page, `home`."""

    spider: Path
    arguments: dict[str, str]
    listings: Path
    stats: Path
    session: Session | None = None
    credentials: dict[str, str] = {}
    fixtures: list[Fixture] | None = None
    browser: str | None = None
    home: str = ""


class CrawlStats(BaseModel, frozen=True):
    """What the crawl process reports back, read from Scrapy's stats."""

    valid: int = 0
    invalid: int = 0
    logged_out: bool = False
    refused: str = ""
    crashes: int = 0
    examples: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        """A replay passes when it yields listings and nothing else went wrong."""
        return self.valid > 0 and self.invalid == 0 and self.crashes == 0 and not self.logged_out
