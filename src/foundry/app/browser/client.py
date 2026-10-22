import contextlib
import uuid

from kernel import AsyncKernel, ConflictError, NotFoundError

from foundry.app import exceptions
from foundry.app.browser import models

IDLE_SECONDS = 3600
PROXY = "foundry-residential-us"


class KernelBrowserApi:
    """Kernel's browsers API. Each source has one profile, `foundry-<source id>`, made on first use, and every
    browser leaves through one US residential proxy, also made on first use: state and local portals often refuse
    datacenter and foreign addresses."""

    def __init__(self, kernel: AsyncKernel) -> None:
        self._kernel = kernel

    async def open(self, source_id: uuid.UUID) -> models.Browser:
        profile = f"foundry-{source_id}"
        with contextlib.suppress(ConflictError):
            await self._kernel.profiles.create(name=profile)
        browser = await self._kernel.browsers.create(
            stealth=True,
            headless=False,
            timeout_seconds=IDLE_SECONDS,
            profile={"name": profile, "save_changes": True},
            proxy_id=await self.proxy(),
            tags={"app": "foundry", "source": str(source_id)},
        )
        return models.Browser(
            session_id=browser.session_id,
            cdp_url=browser.cdp_ws_url,
            live_view_url=browser.browser_live_view_url or "",
            profile=profile,
        )

    async def open_bare(self, source_id: uuid.UUID) -> models.Browser:
        browser = await self._kernel.browsers.create(
            stealth=True,
            headless=False,
            timeout_seconds=IDLE_SECONDS,
            proxy_id=await self.proxy(),
            tags={"app": "foundry", "source": str(source_id), "use": "crawl"},
        )
        return models.Browser(
            session_id=browser.session_id,
            cdp_url=browser.cdp_ws_url,
            live_view_url=browser.browser_live_view_url or "",
            profile="",
        )

    async def proxy(self) -> str:
        async for proxy in self._kernel.proxies.list():
            if proxy.name == PROXY and proxy.id is not None:
                return proxy.id
        created = await self._kernel.proxies.create(type="residential", name=PROXY, config={"country": "US"})
        if created.id is None:
            raise exceptions.KernelRefused("make a proxy")
        return created.id

    async def close(self, browser: models.Browser) -> None:
        with contextlib.suppress(NotFoundError):
            await self._kernel.browsers.delete_by_id(browser.session_id)
