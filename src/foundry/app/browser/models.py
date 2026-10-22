import datetime as dt
import uuid
from typing import Protocol

from pydantic import BaseModel, Field

from foundry.app.db.models.access import CredentialKind

HINT = 4


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

    async def open_bare(self, source_id: uuid.UUID) -> Browser:
        """A browser with no profile, for a crawl: it runs beside an agent's browser on the source's profile."""
        ...

    async def close(self, browser: Browser) -> None: ...


class Account(BaseModel, frozen=True):
    """The login the agent made on a portal (or was given). The password is stored sealed with the credentials key."""

    username: str
    password: str


class NewCredential(BaseModel, frozen=True):
    """A credential to save for a portal. `name` is how the spider reads it (`self.credentials["api_key"]`)."""

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    kind: CredentialKind
    value: str = Field(min_length=1)
    note: str = ""


class CredentialView(BaseModel, frozen=True):
    """A saved credential as a person sees it: everything but the value, of which only the last characters show."""

    name: str
    kind: CredentialKind
    note: str
    hint: str
    updated_at: dt.datetime

    @classmethod
    def of(cls, name: str, kind: CredentialKind, note: str, value: str, updated_at: dt.datetime) -> "CredentialView":
        hint = value[-HINT:] if len(value) > 2 * HINT else ""
        return cls(name=name, kind=kind, note=note, hint=hint, updated_at=updated_at)
