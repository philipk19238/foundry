import datetime as dt
import uuid

from pydantic import BaseModel, TypeAdapter


class Recording(BaseModel, frozen=True):
    """A browser recording an agent made in its clone (`foundry.core record`'s JSON lines, or a HAR), kept in the
    bucket under `sha256`; `name` is its path in the clone."""

    name: str
    sha256: str
    size: int


RECORDINGS = TypeAdapter(list[Recording])


class BronzeLink(BaseModel, frozen=True):
    """A crawl's bronze, for a person or a script: `url`, a short-lived link to its HAR, how many responses the HAR
    holds, and the run that crawled, finished at `crawled_at`."""

    run_id: uuid.UUID
    url: str
    entries: int
    crawled_at: dt.datetime


class RecordingLink(BaseModel, frozen=True):
    """An agent's browser recording, with a short-lived link to it."""

    run_id: uuid.UUID
    name: str
    sha256: str
    size: int
    url: str
