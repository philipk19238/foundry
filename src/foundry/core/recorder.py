"""The network recorder: what a browser's pages send and receive while someone clicks through a portal, so the
portal's API shows up in its traffic and the responses a spider needs become its fixtures."""

import asyncio
import contextlib
import signal
import sys
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Response, async_playwright

from foundry.core.models import Exchange, Fixture

SKIPPED = {"image", "font", "stylesheet", "media", "manifest", "texttrack"}
BROWSER_HOSTS = (".kernel.sh",)
TEXT = ("json", "text", "html", "xml", "javascript", "x-www-form-urlencoded")


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
        if request.resource_type in SKIPPED or (urlsplit(request.url).hostname or "").endswith(BROWSER_HOSTS):
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
