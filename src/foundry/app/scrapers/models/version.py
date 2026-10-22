"""A source's spider as versions: proposed in a pull request, then merged or closed."""

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict

from foundry.app.db.models.catalog import ScraperState, SourceStatus
from foundry.app.db.models.runs import RunKind


class ScraperVersion(BaseModel):
    """A merged version of a source's spider: the `main` commit that merged it, and its site key."""

    model_config = ConfigDict(from_attributes=True)

    source_id: uuid.UUID
    version: int
    site: str
    commit: str
    created_at: dt.datetime


class Proposal(BaseModel):
    """A version still in review: the pull request a run opened for it."""

    model_config = ConfigDict(from_attributes=True)

    source_id: uuid.UUID
    version: int
    site: str
    branch: str
    pull: int


class ScraperView(BaseModel):
    """Any version of a source's spider, merged or not, and the run whose agent built it."""

    model_config = ConfigDict(from_attributes=True)

    source_id: uuid.UUID
    version: int
    site: str
    state: ScraperState
    branch: str
    pull: int
    commit: str | None
    run_id: uuid.UUID
    created_at: dt.datetime

    @property
    def ref(self) -> str:
        """Where its code is: the merge commit once merged, else the tip of the agent's branch."""
        return self.commit if self.commit is not None else f"refs/heads/{self.branch}"


class Standing(BaseModel, frozen=True):
    """Which versions a source has, which decides its status around an agent job (formal/foundry_rules
    `Standing`). A source with a live version keeps running and crawling while an agent reworks it; a job that ends
    without proposing anything puts the source back where its versions say."""

    live: bool
    proposed: bool

    def working(self, kind: RunKind) -> SourceStatus | None:
        """The status while the agent works; None leaves it as it is."""
        if kind == RunKind.HEAL:
            return SourceStatus.HEALING
        return None if self.live else SourceStatus.ONBOARDING

    def settled(self) -> SourceStatus:
        """The status after a job that proposed nothing, or a pull request closed without merging."""
        if self.live:
            return SourceStatus.READY
        if self.proposed:
            return SourceStatus.IN_REVIEW
        return SourceStatus.NEEDS_HUMAN

    def rejected(self, kind: RunKind) -> SourceStatus:
        """The status after a job whose work was rejected: a rejected repair leaves a broken scraper to a person."""
        return SourceStatus.NEEDS_HUMAN if kind == RunKind.HEAL else self.settled()
