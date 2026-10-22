"""The data every scraper and the crawl runner share: what a scraper yields, what it is checked against, how it signs
in, and how a site is registered."""

import datetime as dt
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue

SIGNING = ("x-amz-", "x-goog-")
SIGNED = {"expires", "signature", "key-pair-id", "policy", "sig", "se", "sp", "sv", "sr", "st", "skoid", "sktid", "skt",
          "ske", "sks", "skv"}  # fmt: skip


def unsigned(url: str) -> str:
    """`url` without the signature a presigned link carries (S3, GCS, CloudFront, Azure), which changes every time the
    portal hands the link out: the same file has the same unsigned URL from crawl to crawl."""
    parts = urlsplit(url)
    kept = [
        (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith(SIGNING) and key.lower() not in SIGNED
    ]  # fmt: skip
    return urlunsplit(parts._replace(query=urlencode(kept)))


class FileLink(BaseModel, frozen=True):
    """An attachment: a URL that downloads the file itself, and the name the portal shows. `key` is the URL without
    a presigned link's signature, so the same file is known again however the portal signs it."""

    url: str
    name: str

    @property
    def key(self) -> str:
        return unsigned(self.url)


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
    """Everything a portal's browser held, kept so the next crawl starts where the last one left off: its cookies
    (on every domain), each origin's localStorage and sessionStorage (where many portals keep an API token), the
    browser's user agent, and any headers named for it. Captured from the agent's browser after each run and from a
    scheduled crawl's browser after each crawl; every live crawl's browser starts with all of it."""

    cookies: tuple[Cookie, ...] = ()
    headers: dict[str, str] = {}
    local_storage: dict[str, dict[str, str]] = {}
    session_storage: dict[str, dict[str, str]] = {}
    user_agent: str = ""
    captured_at: dt.datetime

    def stored(self, key: str) -> str | None:
        """A value the portal's pages kept in localStorage or sessionStorage under `key`, from any origin: how a
        spider reads a token the portal's own JavaScript would send as a header."""
        for kept in (*self.local_storage.values(), *self.session_storage.values()):
            if key in kept:
                return kept[key]
        return None

    @property
    def cookie_header(self) -> str:
        return "; ".join(f"{cookie.name}={cookie.value}" for cookie in self.cookies)


STALL_MINUTES = 5.0


class PlatformPortal(BaseModel, frozen=True):
    """A buyer's page on a shared platform: the platform's spider, pointed at this buyer's tenant. `delay` is the
    seconds between requests the portal tolerates (0 leaves the spider's own pace); `stall` how many minutes a crawl
    may go without sending a request before foundry stops it as stuck (raise it for a portal whose bot check or rate
    limit makes it wait longer)."""

    kind: Literal["platform"] = "platform"
    name: str
    platform: str
    tenant: str
    url: str
    delay: float = 0
    stall: float = STALL_MINUTES


class SitePortal(BaseModel, frozen=True):
    """A buyer's own one-off site, with its spider in a folder of the same name beside `buyer.toml`. `delay` is the
    seconds between requests the portal tolerates (0 leaves the spider's own pace); `stall` how many minutes a crawl
    may go without sending a request before foundry stops it as stuck (raise it for a portal whose bot check or rate
    limit makes it wait longer)."""

    kind: Literal["site"] = "site"
    name: str
    url: str
    delay: float = 0
    stall: float = STALL_MINUTES


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
    delay: float = 0
    stall: float = STALL_MINUTES


class Access(BaseModel, frozen=True):
    """Everything a live crawl signs in with: the session read from the agent's browser, and the portal's saved
    credentials by name (API keys, tokens, a second login) for spiders that sign in themselves."""

    session: Session | None = None
    credentials: dict[str, str] = {}


class CrawlJob(BaseModel, frozen=True):
    """What the crawl process is told: the spider to run, where to write, and either a session (live) or fixtures
    (replayed offline). A live crawl given `browser` (a stealth browser's CDP URL) sends every request from it,
    starting at the portal's page, `home`. Given `files`, it downloads every attachment it finds into that folder as
    it goes, except those at `known_files` URLs; given `progress`, it writes how far it has got there every few
    seconds. Given `state`, a folder that outlives the process, it keeps the requests still to send and those already
    sent there (Scrapy's JOBDIR), so the same job run again carries on where a killed one stopped. Given `bronze`, it
    keeps everything the portal sent there, as HAR entries and the bodies they point to."""

    spider: Path
    arguments: dict[str, str]
    listings: Path
    stats: Path
    session: Session | None = None
    credentials: dict[str, str] = {}
    fixtures: list[Fixture] | None = None
    browser: str | None = None
    home: str = ""
    delay: float = 0
    files: Path | None = None
    known_files: list[str] = []
    progress: Path | None = None
    state: Path | None = None
    bronze: Path | None = None


class CrawlStats(BaseModel, frozen=True):
    """What the crawl process reports back, read from Scrapy's stats."""

    valid: int = 0
    invalid: int = 0
    logged_out: bool = False
    renewed: int = 0
    refused: str = ""
    challenged: int = 0
    rate_limited: int = 0
    slowed_to: float = 0
    files_saved: int = 0
    files_failed: int = 0
    crashes: int = 0
    examples: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        """A replay passes when it yields listings and nothing else went wrong."""
        return (
            self.valid > 0 and self.invalid == 0 and self.crashes == 0 and not self.logged_out and not self.challenged
        )


class SavedFile(BaseModel, frozen=True):
    """An attachment a live crawl downloaded, kept in its files folder under `sha256`."""

    url: str
    name: str
    sha256: str
    content_type: str | None
    size: int

    @property
    def key(self) -> str:
        return unsigned(self.url)


class CrawlProgress(BaseModel, frozen=True):
    """How far a running crawl has got, written every few seconds, so a person (and foundry) can see it is alive:
    `at` is when it was written, `last_url` the last response it got."""

    listings: int = 0
    invalid: int = 0
    requests: int = 0
    files_saved: int = 0
    files_failed: int = 0
    rate_limited: int = 0
    challenged: int = 0
    last_url: str = ""
    at: dt.datetime
