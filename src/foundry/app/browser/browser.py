import contextlib
import uuid
from collections.abc import AsyncGenerator

from cryptography.fernet import Fernet
from playwright.async_api import async_playwright

from foundry.app import exceptions
from foundry.app.browser import models, store
from foundry.app.core.models import Lease
from foundry.app.db.db import Sessionmaker, now
from foundry.app.db.models.access import Credential
from foundry.core.models import Access, Cookie, Session


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

    @contextlib.asynccontextmanager
    async def bare(self, source_id: uuid.UUID) -> AsyncGenerator[models.Browser, None]:
        """A browser with no profile, for one crawl, deleted afterwards."""
        browser = await self._api.open_bare(source_id)
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


class Credentials:
    """Each portal's saved secrets beyond its login, by name: what a person or the agent saves, sealed with the
    credentials key, and handed to the spider on every live crawl."""

    def __init__(self, sessionmaker: Sessionmaker, fernet: Fernet) -> None:
        self._sessionmaker = sessionmaker
        self._fernet = fernet

    async def shown(self, source_id: uuid.UUID) -> list[models.CredentialView]:
        async with self._sessionmaker() as session:
            stored = await store.credentials(session, source_id)
        return [
            models.CredentialView.of(row.name, row.kind, row.note, self._open(row.sealed), row.updated_at)
            for row in stored
        ]

    async def values(self, source_id: uuid.UUID) -> dict[str, str]:
        async with self._sessionmaker() as session:
            stored = await store.credentials(session, source_id)
        return {row.name: self._open(row.sealed) for row in stored}

    async def save(self, source_id: uuid.UUID, credential: models.NewCredential) -> None:
        row = Credential(
            source_id=source_id,
            name=credential.name,
            kind=credential.kind,
            sealed=self._fernet.encrypt(credential.value.encode()).decode(),
            note=credential.note,
            updated_at=now(),
        )
        async with self._sessionmaker.begin() as session:
            await store.put_credential(session, row)

    async def forget(self, source_id: uuid.UUID, name: str) -> None:
        async with self._sessionmaker.begin() as session:
            await store.forget_credential(session, source_id, name)

    def _open(self, sealed: str) -> str:
        return self._fernet.decrypt(sealed.encode()).decode()


class Accounts:
    """Each portal's login and signed-in session. Passwords are sealed with the credentials key; sessions are what
    the scraper sends, with the portal's other credentials."""

    def __init__(self, sessionmaker: Sessionmaker, fernet: Fernet, credentials: Credentials) -> None:
        self._sessionmaker = sessionmaker
        self._fernet = fernet
        self._credentials = credentials

    async def access(self, source_id: uuid.UUID) -> Access:
        """What a live crawl of the portal signs in with."""
        return Access(session=await self.session(source_id), credentials=await self._credentials.values(source_id))

    async def username(self, source_id: uuid.UUID) -> str | None:
        """The login's username, for showing without its password."""
        async with self._sessionmaker() as session:
            stored = await store.account(session, source_id)
        return None if stored is None else stored[0]

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
