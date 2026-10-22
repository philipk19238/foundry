"""Kernel closes a browser that sits idle, and an agent may spend that long in its shell. Its turn's work must survive
that: a browser that has closed is `BrowserGone` (which the agent and the crawl answer by keeping the session saved
last time), never a crash of the run, and keeping one alive around a turn never fails the turn."""

import asyncio
import socket
import uuid

import pytest
from playwright.async_api import async_playwright

from foundry.app import exceptions
from foundry.app.browser.browser import Browsers
from foundry.app.browser.models import Browser


def closed_browser() -> Browser:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return Browser(session_id="gone", cdp_url=f"http://127.0.0.1:{port}", live_view_url="", profile="")


def test_a_closed_browser_is_browser_gone() -> None:
    with pytest.raises(exceptions.BrowserGone):
        asyncio.run(Browsers(None).session(closed_browser(), {}))  # type: ignore[arg-type]


def test_keeping_a_closed_browser_alive_lets_the_turn_finish() -> None:
    async def turn() -> str:
        async with Browsers(None).kept_alive(closed_browser()):  # type: ignore[arg-type]
            return "done"

    assert asyncio.run(turn()) == "done"


def test_letting_go_after_the_turn_leaves_the_browser_open() -> None:
    """The browser is still needed after the turn: its session is read, then it is deleted so its profile saves."""

    async def turn() -> bool:
        async with async_playwright() as playwright:
            port = 9300 + uuid.uuid4().int % 500
            chromium = await playwright.chromium.launch(args=[f"--remote-debugging-port={port}"])
            browser = Browser(session_id="live", cdp_url=f"http://127.0.0.1:{port}", live_view_url="", profile="")
            async with Browsers(None).kept_alive(browser):  # type: ignore[arg-type]
                await asyncio.sleep(0.1)
            again = await playwright.chromium.connect_over_cdp(browser.cdp_url)
            alive = len(again.contexts) == 1
            await chromium.close()
            return alive

    assert asyncio.run(turn())
