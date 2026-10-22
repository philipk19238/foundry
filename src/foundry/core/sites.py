"""The registry is the folder tree: every `buyers/**/buyer.toml` registers its buyer's portals. A site key is the
buyer's folder plus the portal's name (`us/tx/austin/financeonline`)."""

import tomllib
from collections.abc import Iterable, Iterator
from pathlib import Path

from foundry.core.models import Buyer, PlatformPortal, Site, SitePortal

BUYERS = "src/foundry/buyers"
PLATFORMS = "src/foundry/platforms"
CORE = "src/foundry/core/"


class Sites:
    """The sites registered in one checkout of the foundry repo."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def buyers(self) -> Iterator[tuple[str, Buyer]]:
        for found in sorted((self._root / BUYERS).rglob("buyer.toml")):
            folder = found.parent.relative_to(self._root / BUYERS).as_posix()
            yield folder, Buyer.model_validate(tomllib.loads(found.read_text()))

    def all(self) -> Iterator[Site]:
        for folder, buyer in self.buyers():
            for portal in buyer.portals:
                yield self.resolve(folder, portal)

    def touched(self, paths: Iterable[str]) -> list[Site]:
        """The sites a change to ``paths`` can affect: a buyer's folder touches its portals, a platform's folder
        touches every buyer on it, and `src/foundry/core/` touches everything."""
        changed = list(paths)
        if any(path.startswith(CORE) for path in changed):
            return list(self.all())
        return [site for site in self.all() if any(path.startswith(prefix) for prefix in self.sources(site)
                                                   for path in changed)]  # fmt: skip

    def sources(self, site: Site) -> tuple[str, ...]:
        """The repo paths a site is built from."""
        folder = site.key.rpartition("/")[0]
        return (f"{BUYERS}/{folder}/", site.spider.parent.relative_to(self._root).as_posix() + "/")

    def get(self, key: str) -> Site:
        folder, _, name = key.rpartition("/")
        buyer = Buyer.model_validate(tomllib.loads((self._root / BUYERS / folder / "buyer.toml").read_text()))
        (portal,) = (portal for portal in buyer.portals if portal.name == name)
        return self.resolve(folder, portal)

    def resolve(self, folder: str, portal: PlatformPortal | SitePortal) -> Site:
        buyer = self._root / BUYERS / folder
        match portal:
            case PlatformPortal(platform=platform, tenant=tenant):
                code = self._root / PLATFORMS / platform
                own = buyer / f"{portal.name}.json"
                return Site(
                    key=f"{folder}/{portal.name}",
                    url=portal.url,
                    spider=code / "spider.py",
                    arguments={"tenant": tenant},
                    fixtures=own if own.exists() else code / "fixtures.json",
                    delay=portal.delay,
                )
            case SitePortal():
                return Site(
                    key=f"{folder}/{portal.name}",
                    url=portal.url,
                    spider=buyer / portal.name / "scraper.py",
                    arguments={},
                    fixtures=buyer / portal.name / "fixtures.json",
                    delay=portal.delay,
                )
