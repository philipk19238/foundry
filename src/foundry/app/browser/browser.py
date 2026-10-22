import contextlib
import uuid
from collections.abc import AsyncGenerator

from cryptography.fernet import Fernet
from playwright.async_api import async_playwright

from foundry.app import exceptions
from foundry.app.browser import models, store
from foundry.app.core.models import Lease
from foundry.app.db.db import Sessionmaker, now
from foundry.core.models import Cookie, Session


class Browsers:
    """A run's browser on a source, and the signed-in session read out of it for the scraper."""

    def __init__(self, api: models.BrowserApi) -> None:
        self._api = api

    @contextlib.asynccontextmanager
    async def opened(self, source_id: uuid.UUID) -> AsyncGenerator[models.Browser, None]:
        """A browser on the source's profile, deleted afterwards so the profile is saved."""
        browser = await self._api.open(source_id)
        try:
            yield browser
        finally:
            await self._api.close(browser)

    async def session(self, browser: models.Browser, headers: dict[str, str]) -> Session:
        async with async_playwright() as playwright:
            connected = await playwright.chromium.connect_over_cdp(browser.cdp_url)
            context = connected.contexts[0]
            cookies = [Cookie.model_validate(cookie) for cookie in await context.cookies()]
        return Session(cookies=tuple(cookies), headers=headers, captured_at=now())


class Accounts:
    """Each portal's login and signed-in session. Passwords are sealed with the credentials key; sessions are what
    the scraper sends."""

    def __init__(self, sessionmaker: Sessionmaker, fernet: Fernet) -> None:
        self._sessionmaker = sessionmaker
        self._fernet = fernet

    async def account(self, source_id: uuid.UUID) -> models.Account | None:
        async with self._sessionmaker() as session:
            stored = await store.account(session, source_id)
        if stored is None:
            return None
        username, sealed = stored
        return models.Account(username=username, password=self._fernet.decrypt(sealed.encode()).decode())

    async def save_account(self, lease: Lease, account: models.Account) -> None:
        sealed = self._fernet.encrypt(account.password.encode()).decode()
        async with self._sessionmaker.begin() as session:
            if not await store.set_account(session, lease, account.username, sealed):
                raise exceptions.LeaseLost()

    async def session(self, source_id: uuid.UUID) -> Session | None:
        async with self._sessionmaker() as session:
            raw = await store.portal_session(session, source_id)
        return Session.model_validate(raw) if raw is not None else None

    async def save_session(self, lease: Lease, portal: Session) -> None:
        async with self._sessionmaker.begin() as session:
            if not await store.set_session(session, lease, portal.model_dump(mode="json")):
                raise exceptions.LeaseLost()
