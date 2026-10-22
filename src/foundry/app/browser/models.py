import uuid
from typing import Protocol

from pydantic import BaseModel


class Browser(BaseModel, frozen=True):
    """A Kernel browser held by one run. `live_view_url` is interactive: whoever opens it drives the browser."""

    session_id: str
    cdp_url: str
    live_view_url: str
    profile: str


class BrowserApi(Protocol):
    """Kernel: stealth browsers with a CAPTCHA solver, and profiles that keep a portal's login between sessions.
    A profile is saved when its browser is deleted; only one browser should hold a profile at a time."""

    async def open(self, source_id: uuid.UUID) -> Browser: ...

    async def close(self, browser: Browser) -> None: ...


class Account(BaseModel, frozen=True):
    """The login the agent made on a portal (or was given). The password is stored sealed with the credentials key."""

    username: str
    password: str
