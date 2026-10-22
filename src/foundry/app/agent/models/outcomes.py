from typing import Annotated, Literal

from pydantic import BaseModel, Field


class ScraperPull(BaseModel, frozen=True):
    outcome: Literal["proposed"] = "proposed"
    version: int
    url: str


class FreshSession(BaseModel, frozen=True):
    outcome: Literal["signed_in"] = "signed_in"


class Rejection(BaseModel, frozen=True):
    outcome: Literal["rejected"] = "rejected"
    reason: str


class Reply(BaseModel, frozen=True):
    """A reopened run whose agent answered without handing in a new scraper."""

    outcome: Literal["replied"] = "replied"


class Restart(BaseModel, frozen=True):
    outcome: Literal["restart"] = "restart"
    commit: str


Outcome = Annotated[ScraperPull | FreshSession | Rejection | Reply | Restart, Field(discriminator="outcome")]
