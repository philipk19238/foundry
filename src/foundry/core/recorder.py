"""The network recorder: what a browser's pages send and receive while someone clicks through a portal, so the
portal's API shows up in its traffic, the responses a spider needs become its fixtures, and each request's real
needs (the cookies and headers without which the portal answers differently) can be found."""

import asyncio
import base64
import contextlib
import functools
import json
import signal
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, Response, async_playwright

from foundry.core.middlewares import FETCH, Fetched, Sent
from foundry.core.models import Exchange, Fixture

if TYPE_CHECKING:
    from playwright._impl._api_structures import SetCookieParam

SKIPPED = {"image", "font", "stylesheet", "media", "manifest", "texttrack"}
BROWSER_HOSTS = (".kernel.sh",)
TEXT = ("json", "text", "html", "xml", "javascript", "x-www-form-urlencoded")
UNSENDABLE = {
    "cookie", "user-agent", "accept-encoding", "host", "content-length", "connection", "origin", "referer", "priority",
    "accept-language", "pragma", "cache-control",
}  # fmt: skip
HTTP_ERROR = 400
MEANINGFUL = 12


class Recorder:
    """Logs every exchange of every page in a browser, new tabs included, one JSON line each, and prints a line per
    request that could be the portal's API (documents, XHR and fetch). Images, fonts, styles and the browser
    service's own traffic are left out, and scripts are logged without their bodies."""

    def __init__(self, out: Path) -> None:
        self._out = out

    async def record(self, cdp_url: str, seconds: float | None) -> None:
        """Records until `seconds` pass, or until the process is interrupted or terminated."""
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sign in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sign, stopped.set)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(cdp_url)
            browser.contexts[0].on("response", self._save)
            print(f"Recording to {self._out}. Stop with Ctrl-C or kill.", flush=True)
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(seconds):
                    await stopped.wait()

    async def _save(self, response: Response) -> None:
        request = response.request
        parts = urlsplit(request.url)
        if parts.scheme not in ("http", "https") or request.resource_type in SKIPPED:
            return
        if (parts.hostname or "").endswith(BROWSER_HOSTS):
            return
        headers = await response.all_headers()
        sent = None
        with contextlib.suppress(UnicodeDecodeError):
            sent = request.post_data
        body = None
        if request.resource_type != "script" and any(kind in headers.get("content-type", "") for kind in TEXT):
            with contextlib.suppress(PlaywrightError, UnicodeDecodeError):
                body = await response.text()
        exchange = Exchange(
            method=request.method,
            url=request.url,
            kind=request.resource_type,
            request_headers=await request.all_headers(),
            request_body=sent,
            status=response.status,
            headers=headers,
            body=body,
        )
        with self._out.open("a") as out:
            out.write(exchange.model_dump_json() + "\n")
        if exchange.kind in ("document", "xhr", "fetch"):
            shown = f" body {exchange.request_body[:120]!r}" if exchange.request_body else ""
            print(f"{exchange.method} {exchange.status} {exchange.kind} {exchange.url}{shown}", flush=True)


def fixtures(recording: Path, matches: Iterable[str]) -> list[Fixture]:
    """The recorded responses whose URL holds any of `matches`, the latest of each method and URL. Replay tells
    requests apart by method and URL only, so requests that differ only in their body are reported on stderr."""
    chosen: dict[tuple[str, str], Exchange] = {}
    bodies: dict[tuple[str, str], set[str | None]] = {}
    for line in filter(None, recording.read_text().split("\n")):
        exchange = Exchange.model_validate_json(line)
        if exchange.body is None or not any(match in exchange.url for match in matches):
            continue
        key = (exchange.method, exchange.url)
        chosen[key] = exchange
        bodies.setdefault(key, set()).add(exchange.request_body)
    for (method, url), sent in bodies.items():
        if len(sent) > 1:
            print(f"{method} {url} was sent with {len(sent)} different bodies; replay keeps only the last",
                  file=sys.stderr)  # fmt: skip
    return [exchange.fixture for exchange in chosen.values()]


class Needs:
    """What one recorded request needs to get the same answer: sends it again from the browser as it was, then
    without each of its headers and each of its site's cookies in turn, and keeps the ones whose absence changes the
    answer. Says where each came from: the response that set a cookie, the localStorage key a header's value is
    kept under, or the earlier response a value first appeared in."""

    def __init__(self, recording: Path) -> None:
        self._exchanges = [
            Exchange.model_validate_json(line) for line in filter(None, recording.read_text().split("\n"))
        ]

    def find(self, match: str) -> Exchange:
        """The latest recorded request whose URL holds `match` and that got an answer with a body."""
        found = [e for e in self._exchanges if match in e.url and e.status < HTTP_ERROR and e.body is not None]
        if not found:
            raise SystemExit(f"No answered request in the recording matches {match!r}.")
        return found[-1]

    async def report(self, match: str, cdp_url: str) -> None:
        asked = self.find(match)
        sent = Sent(
            url=asked.url,
            method=asked.method,
            headers={key: value for key, value in asked.request_headers.items() if self.sendable(key)},
            body=base64.b64encode(asked.request_body.encode()).decode() if asked.request_body else None,
        )
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(cdp_url)
            context = browser.contexts[0]
            page = await context.new_page()
            await page.goto(self.page_of(asked), wait_until="domcontentloaded")
            fetch = functools.partial(self.fetch, page)
            base = await fetch(sent)
            print(f"{asked.method} {asked.url}\n  as recorded: {self.answer(base)}")
            if base["status"] == 0 or base["status"] >= HTTP_ERROR:
                print("  the request doesn't get its answer from this page now; record it again and retry")
                return
            headers = [key for key in sent["headers"] if self.differs(base, await fetch(self.without(sent, key)))]
            cookies = []
            for cookie in await context.cookies(asked.url):
                await context.clear_cookies(name=cookie["name"], domain=cookie["domain"])
                if self.differs(base, await fetch(sent)):
                    cookies.append(cookie["name"])
                await context.add_cookies([cast("SetCookieParam", cookie)])
            local = json.loads(await page.evaluate("JSON.stringify(localStorage)"))
        for name in cookies:
            setters = [e.url for e in self._exchanges if f"{name}=" in e.headers.get("set-cookie", "")]
            print(f"  needs cookie {name}" + (f", set by {setters[0]}" if setters else ", set before the recording"))
        for key in headers:
            value = sent["headers"][key]
            print(f"  needs header {key}: {value[:60]}" + self.origin(value, local, asked))
        if not headers and not cookies:
            print("  needs no cookie or header beyond what any browser sends")

    def page_of(self, asked: Exchange) -> str:
        """The page the request was sent from: the last page on its site (`api.example.gov` and `www.example.gov`
        share one) the recording opened before it, not a third party's frame inside it."""
        before = self._exchanges[: self._exchanges.index(asked)]
        site = ".".join((urlsplit(asked.url).hostname or "").split(".")[-2:])
        pages = [
            e.url for e in before
            if e.kind == "document" and e.status < HTTP_ERROR and (urlsplit(e.url).hostname or "").endswith(site)
        ]  # fmt: skip
        return pages[-1] if pages else "{0.scheme}://{0.netloc}/".format(urlsplit(asked.url))

    def origin(self, value: str, local: dict[str, str], asked: Exchange) -> str:
        """Where a header's value comes from, as far as the recording shows."""
        kept = next((key for key, stored in local.items() if value and value in stored), None)
        if kept is not None:
            return f" (kept in localStorage {kept!r}; read it with self.session.stored({kept!r}))"
        earlier = next((e.url for e in self._exchanges if e is not asked and e.body and value in e.body), None)
        if earlier is not None and len(value) >= MEANINGFUL:
            return f" (first seen in the response of {earlier})"
        return ""

    @staticmethod
    async def fetch(page: Page, sent: Sent) -> Fetched:
        """`sent` fetched from `page`; a request the browser refuses to send (CORS, a network error) is status 0."""
        try:
            return await page.evaluate(FETCH, sent)
        except PlaywrightError:
            return Fetched(url=sent["url"], status=0, headers=[], body="")

    @staticmethod
    def without(sent: Sent, key: str) -> Sent:
        return Sent(**{**sent, "headers": {name: value for name, value in sent["headers"].items() if name != key}})

    @staticmethod
    def sendable(key: str) -> bool:
        return not key.startswith((":", "sec-")) and key.lower() not in UNSENDABLE

    @staticmethod
    def answer(got: Fetched) -> str:
        return f"{got['status']}, {len(base64.b64decode(got['body']))} bytes"

    @staticmethod
    def differs(base: Fetched, got: Fetched) -> bool:
        """A different status, or a body less than half or more than twice as long: a refusal or an error page."""
        if got["status"] != base["status"]:
            return True
        size, other = len(base["body"]) or 1, len(got["body"])
        return not size / 2 <= other <= size * 2
