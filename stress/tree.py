"""A repo tree for one scenario's crawl, holding only the fake portal's site, laid out as `Sites` reads a checkout:
the platform spider under `src/foundry/platforms/`, the buyer under `src/foundry/buyers/`."""

import json
import shutil
import tomllib
from pathlib import Path

from foundry.core.models import Buyer, PlatformPortal
from foundry.core.sites import BUYERS, PLATFORMS

SITES = Path(__file__).with_name("sites")
BUYER = "zz/local/stress"
PORTAL = "bids"


def toml(buyer: Buyer) -> str:
    """`buyer.toml` for `buyer`; every value the harness writes is a string or a number, which JSON spells as TOML."""
    lines = [f"name = {json.dumps(buyer.name)}", f"jurisdiction = {json.dumps(buyer.jurisdiction)}"]
    for portal in buyer.portals:
        lines += ["", "[[portals]]", *(f"{key} = {json.dumps(value)}" for key, value in portal.model_dump().items())]
    return "\n".join(lines) + "\n"


class Tree:
    """The tree at `root`, built for the portal at `tenant`."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def key(self) -> str:
        return f"{BUYER}/{PORTAL}"

    def build(self, tenant: str, stall: float, tuning: dict[str, int]) -> Path:
        template = Buyer.model_validate(tomllib.loads((SITES / "buyer.toml").read_text()))
        (portal,) = template.portals
        assert isinstance(portal, PlatformPortal)
        here = portal.model_copy(update={"tenant": tenant, "url": f"http://{tenant}/", "stall": stall})
        spider = self._root / PLATFORMS / portal.platform
        shutil.copytree(SITES / portal.platform, spider, ignore=shutil.ignore_patterns("__pycache__"))
        if tuning:
            (spider / "tuning.json").write_text(json.dumps(tuning))
        buyer = self._root / BUYERS / BUYER
        buyer.mkdir(parents=True)
        (buyer / "buyer.toml").write_text(toml(template.model_copy(update={"portals": (here,)})))
        return self._root
