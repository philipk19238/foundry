import datetime as dt
from typing import Literal

from pydantic import BaseModel

BRANCHES = "agents/"


class Head(BaseModel, frozen=True):
    ref: str
    sha: str


class PullFile(BaseModel, frozen=True):
    """One file a pull request changes, with its unified diff; GitHub leaves `patch` out for binary or huge files."""

    filename: str
    status: str
    additions: int
    deletions: int
    patch: str | None = None


class Pull(BaseModel, frozen=True):
    """A pull request as GitHub's REST API returns it, with only the fields foundry reads."""

    number: int
    html_url: str
    state: Literal["open", "closed"]
    merged_at: dt.datetime | None = None
    merge_commit_sha: str | None = None
    head: Head

    @property
    def merged(self) -> bool:
        return self.merged_at is not None
