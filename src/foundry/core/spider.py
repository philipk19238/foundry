"""The scraper kit's spider. Every portal's spider is one module with one `Spider(PortalSpider)` class: it walks
listings to detail pages and yields `Listing`s. Signing in, retries, throttling, replay and validation live beside it
(`middlewares`, `pipelines`, `extensions`), the same for every portal, so a spider holds only what is particular to
its site or platform. The stats keys here are what the crawl reports."""

from collections.abc import Iterable, Iterator
from types import ModuleType

import scrapy
from scrapy.http import Request, Response

from foundry.core.models import FileLink, Listing, Session

LOGGED_OUT = "foundry/logged_out"
REFUSED = "foundry/refused"
CHALLENGED = "foundry/challenged"
FILES_SAVED = "foundry/files_saved"
FILES_FAILED = "foundry/files_failed"
BRONZE = "foundry/bronze"
HTTP_ERROR = 400
VALID = "foundry/valid"
INVALID = "foundry/invalid"
UNMATCHED = "foundry/unmatched"
CRASHES = "foundry/crashes"
EXAMPLE = "foundry/example/"
PLAIN = "foundry_plain"
FILE = "foundry_file"
STOPPING = object()  # the signal a crawl sends when told to stop, before Scrapy starts closing it


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
        it for a portal whose files take something else (a POST, a token from the page, a two-step download). The
        crawl sends it as plain HTTP first, which is fast and costs no proxy traffic, and from the stealth browser
        only if that doesn't get the file; `meta={"browser": True}` sends it from the browser straight away."""
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


def spiders(module: ModuleType) -> Iterator[type[PortalSpider]]:
    """The spider classes a module defines (not ones it imports)."""
    for value in vars(module).values():
        if isinstance(value, type) and issubclass(value, PortalSpider) and value.__module__ == module.__name__:
            yield value
