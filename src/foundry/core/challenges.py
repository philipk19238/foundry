"""Bot checks a portal puts in front of its pages: Cloudflare, AWS WAF, reCAPTCHA and hCaptcha browser checks. A
response is recognized as one here, and a stealth browser page is watched until the check clears, so the kit, the
agent's `solve` command and a spider that sets `solve` all agree on what a challenge is and when it is over."""

import asyncio
import contextlib

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

HEADERS = {("x-amzn-waf-action", "challenge"), ("cf-mitigated", "challenge")}
PATHS = ("browser_check", "captcha", "/cdn-cgi/challenge-platform", "challenge")
WIDGETS = ("g-recaptcha", "cf-turnstile", "challenges.cloudflare.com", "hcaptcha.com", "challenge-form")
REFUSALS = {202, 403, 429, 503}
ON_PAGE = """() => {
  const path = location.pathname + location.search;
  const widget = document.querySelector(
    '.g-recaptcha, .cf-turnstile, .h-captcha, #challenge-form, iframe[src*="recaptcha/api2"], '
    + 'iframe[src*="challenges.cloudflare.com"], iframe[src*="hcaptcha.com"]');
  return [path, widget !== null];
}"""
WATCH_SECONDS = 2.0


def challenged(asked: str, url: str, status: int, headers: dict[str, str], body: str) -> bool:
    """Whether a response is a bot check rather than the page asked for: a challenge header, a redirect to a check
    page, or a refusal whose page holds a challenge widget. A normal page with a reCAPTCHA form isn't one."""
    if HEADERS & {(key.lower(), value.lower()) for key, value in headers.items()}:
        return True
    if url != asked and any(part in url.lower() for part in PATHS):
        return True
    return status in REFUSALS and any(widget in body for widget in WIDGETS)


async def on_page(page: Page) -> bool:
    """Whether the page is showing a bot check right now."""
    with contextlib.suppress(PlaywrightError):
        path, widget = await page.evaluate(ON_PAGE)
        return widget or any(part in path.lower() for part in PATHS)
    return True


async def clear(page: Page, url: str, seconds: float) -> float | None:
    """Opens `url` on `page` and waits for its check to clear, as the browser's solver (or a person in the live
    view) passes it: the seconds it took once the page loaded (0 when the page showed no check), or None if it was
    still there after `seconds`."""
    loop = asyncio.get_running_loop()
    with contextlib.suppress(PlaywrightError):
        await page.goto(url, wait_until="load")
    began = loop.time()
    while loop.time() - began < seconds:
        if not await on_page(page):
            return round(loop.time() - began, 1)
        await asyncio.sleep(WATCH_SECONDS)
    return None
