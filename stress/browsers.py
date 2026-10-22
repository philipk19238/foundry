"""`BrowserApi` on this machine, so `Crawls.live` runs end to end without Kernel: each browser is Playwright's
headless Chromium with remote debugging on a free port, which the crawl connects to over CDP as it does to Kernel's.
It has no stealth, no proxy and no CAPTCHA solver, so a bot check it meets stays unsolved."""

import uuid
from types import TracebackType

from playwright.async_api import Browser as Chromium
from playwright.async_api import Playwright, async_playwright

from foundry.app.browser.models import Browser
from stress.portal import free_port


class LocalBrowserApi:
    """Launches local headless Chromiums while the context is open, and closes any still running when it ends."""

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._running: dict[str, Chromium] = {}

    async def open(self, source_id: uuid.UUID) -> Browser:
        return await self.open_bare(source_id)

    async def open_bare(self, source_id: uuid.UUID) -> Browser:
        assert self._playwright is not None
        port = free_port()
        chromium = await self._playwright.chromium.launch(args=[f"--remote-debugging-port={port}"])
        session_id = uuid.uuid4().hex
        self._running[session_id] = chromium
        return Browser(session_id=session_id, cdp_url=f"http://127.0.0.1:{port}", live_view_url="", profile="")

    async def close(self, browser: Browser) -> None:
        await self._running.pop(browser.session_id).close()

    async def __aenter__(self) -> "LocalBrowserApi":
        self._playwright = await async_playwright().start()
        return self

    async def __aexit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        for chromium in self._running.values():
            await chromium.close()
        self._running.clear()
        if self._playwright is not None:
            await self._playwright.stop()
