"""A heal that fails, or whose worker dies, leaves its source HEALING, and nothing else ever clears that: the
schedule's refresh is the way out, so a source left healing with no agent on it must still be crawled
(`Schedule.refresh_ready`), while one an agent is healing, or waiting on a person for, is left to that agent."""

import asyncio
import datetime as dt
import uuid
from typing import Any, cast

import pytest

from foundry.app.app import Schedule
from foundry.app.catalog.models import SourceView
from foundry.app.db.models.catalog import SourceStatus
from foundry.app.db.models.runs import RunKind

AT = dt.datetime(2026, 10, 22, tzinfo=dt.UTC)
SOURCE = uuid.UUID(int=1)


def source(status: SourceStatus) -> SourceView:
    return SourceView(
        id=SOURCE, url="https://portal.example", name="Portal", status=status, site="us/ca/x", heals=1,
        previous_valid=0, scraper_version=1, created_at=AT,
    )  # fmt: skip


class Runs:
    def __init__(self, working: bool, waiting: bool) -> None:
        self._working = working
        self._waiting = waiting

    async def working_sources(self) -> set[uuid.UUID]:
        return {SOURCE} if self._working else set()

    async def waiting_sources(self) -> set[uuid.UUID]:
        return {SOURCE} if self._waiting else set()


class Queue:
    def __init__(self) -> None:
        self.requested: list[tuple[uuid.UUID, RunKind]] = []

    async def revive(self) -> int:
        return 0

    async def request(self, source_id: uuid.UUID, kind: RunKind, requested_by: uuid.UUID | None) -> None:
        self.requested.append((source_id, kind))


class Catalog:
    def __init__(self, status: SourceStatus) -> None:
        self._status = status

    async def sources(self) -> list[SourceView]:
        return [source(self._status)]


def refreshed(status: SourceStatus, working: bool = False, waiting: bool = False) -> bool:
    queue = Queue()
    fakes: Any = (Runs(working, waiting), None, queue, Catalog(status), None, None)
    asyncio.run(Schedule(*cast(Any, fakes)).refresh_ready())
    assert queue.requested in ([], [(SOURCE, RunKind.REFRESH)])
    return bool(queue.requested)


def test_a_source_left_healing_with_no_agent_is_crawled_again() -> None:
    assert refreshed(SourceStatus.HEALING)


@pytest.mark.parametrize(("working", "waiting"), [(True, False), (False, True)], ids=["heal-running", "heal-asking"])
def test_a_source_an_agent_is_healing_is_left_to_it(working: bool, waiting: bool) -> None:
    assert not refreshed(SourceStatus.HEALING, working, waiting)


@pytest.mark.parametrize(
    ("status", "expected"),
    [(SourceStatus.READY, True), (SourceStatus.NEEDS_HUMAN, False), (SourceStatus.IN_REVIEW, False)],
    ids=["ready", "needs-human", "in-review"],
)
def test_other_sources_are_crawled_only_when_ready(status: SourceStatus, expected: bool) -> None:
    assert refreshed(status) is expected
