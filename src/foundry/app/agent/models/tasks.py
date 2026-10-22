from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from foundry.app.scrapers.models import Crawl, ScraperVersion

PROMPTS = Path(__file__).parents[1] / "prompts"


class Onboard(BaseModel, frozen=True):
    """A portal we have never scraped: sign up or sign in, find its API, write the first scraper."""

    task: Literal["onboard"] = "onboard"
    url: str

    @property
    def prompt(self) -> str:
        return (PROMPTS / "onboard.md").read_text().format(url=self.url)


class Heal(BaseModel, frozen=True):
    """A scraper that stopped working: see what the portal does now and fix it."""

    task: Literal["heal"] = "heal"
    url: str
    scraper: ScraperVersion
    crawl: Crawl

    @property
    def prompt(self) -> str:
        crawl = self.crawl
        seen = (
            f"valid listings {crawl.valid} (the last healthy crawl found {crawl.previous_valid}), "
            f"invalid {crawl.invalid}, crashed {crawl.crashed}"
            + (f", turned away with {crawl.refused}" if crawl.refused else "")
            + (" even from a stealth browser" if crawl.browser else "")
        )
        errors = "\n".join(crawl.errors) or "(none)"
        return (
            (PROMPTS / "heal.md")
            .read_text()
            .format(url=self.url, site=self.scraper.site, version=self.scraper.version, seen=seen, errors=errors)
        )


class Relogin(BaseModel, frozen=True):
    """The scraper's session was refused: sign in again so the next crawl can use the fresh session."""

    task: Literal["relogin"] = "relogin"
    url: str

    @property
    def prompt(self) -> str:
        return (PROMPTS / "relogin.md").read_text().format(url=self.url)


Task = Annotated[Onboard | Heal | Relogin, Field(discriminator="task")]
