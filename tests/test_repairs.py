"""A test scrape that goes wrong must reach an agent without a person noticing: the agent that wrote a version in
review is told what failed (reopened if its run ended), a live version gets one heal, and after MAX_HEALS failures in
a row nobody is sent again, so a scraper that can't be fixed doesn't burn agent runs forever."""

import asyncio
import datetime as dt
import uuid

from foundry.app.db.models.catalog import ScraperState
from foundry.app.db.models.runs import RunKind, RunStatus
from foundry.app.repairs.repairs import AUTHOR, Repairs
from foundry.app.runs.models import EventKind, RunView
from foundry.app.scrapers.models.crawl import MAX_HEALS, Crawl
from foundry.app.scrapers.models.version import ScraperView
from foundry.app.settings import settings

AT = dt.datetime(2026, 10, 22, tzinfo=dt.UTC)
SOURCE = uuid.UUID(int=1)
BUILDER = uuid.UUID(int=2)
BROKEN = Crawl(auth_failed=False, crashed=True, invalid=0, valid=3, previous_valid=0, errors=("ValueError",))
HEALTHY = Crawl(auth_failed=False, crashed=False, invalid=0, valid=385, previous_valid=0)


def scraper(state: ScraperState) -> ScraperView:
    return ScraperView(
        source_id=SOURCE, version=1, site="us/ca/california", state=state, branch="agents/x", pull=27, commit=None,
        run_id=BUILDER, created_at=AT,
    )  # fmt: skip


def run(kind: RunKind, status: RunStatus, number: int, crawl: Crawl | None = None) -> RunView:
    stats = None
    if crawl is not None:
        stats = {
            "scraper": scraper(ScraperState.PROPOSED).model_dump(mode="json"),
            "crawl": crawl.model_dump(mode="json"),
            "listings": [],
        }
    return RunView(
        id=uuid.UUID(int=100 + number), source_id=SOURCE, kind=kind, status=status, requested_by=None, summary=None,
        stats=stats, created_at=AT, started_at=AT, finished_at=AT if crawl is not None else None, attempts=0,
        beat_at=None, live_view_url=None,
    )  # fmt: skip


class History:
    def __init__(self, trials: list[RunView], builder: RunStatus, working: bool = False) -> None:
        self._trials = trials  # newest first, as `of_source` returns them
        self._builder = run(RunKind.ONBOARD, builder, 0).model_copy(update={"id": BUILDER})
        self._working = working

    async def of_source(self, source_id: uuid.UUID) -> list[RunView]:
        return self._trials

    async def run(self, run_id: uuid.UUID) -> RunView:
        assert run_id == BUILDER
        return self._builder

    async def working_sources(self) -> set[uuid.UUID]:
        return {SOURCE} if self._working else set()


class Timeline:
    def __init__(self) -> None:
        self.written: list[tuple[uuid.UUID, EventKind, str, str]] = []
        self.logged: list[tuple[uuid.UUID, str]] = []

    async def wrote(self, run_id: uuid.UUID, kind: EventKind, author: str, text: str) -> None:
        self.written.append((run_id, kind, author, text))

    async def log(self, run_id: uuid.UUID, kind: EventKind, text: str) -> None:
        self.logged.append((run_id, text))


class Queue:
    def __init__(self) -> None:
        self.reopened: list[uuid.UUID] = []
        self.requested: list[RunKind] = []

    async def reopen(self, run_id: uuid.UUID) -> None:
        self.reopened.append(run_id)

    async def request(self, source_id: uuid.UUID, kind: RunKind, requested_by: uuid.UUID | None) -> None:
        self.requested.append(kind)


def repair(trials: list[RunView], state: ScraperState, builder: RunStatus = RunStatus.SUCCEEDED, working: bool = False):
    timeline, queue = Timeline(), Queue()
    repairs = Repairs(History(trials, builder, working), timeline, queue, settings)  # type: ignore[arg-type]
    newest = trials[0]
    crawl = Crawl.model_validate(newest.stats["crawl"]) if newest.stats else BROKEN
    asyncio.run(repairs.after_trial(newest, scraper(state), crawl))
    return timeline, queue


def failed(count: int) -> list[RunView]:
    return [run(RunKind.TRIAL, RunStatus.SUCCEEDED, n, BROKEN) for n in range(count)]


def test_a_healthy_test_sends_nobody() -> None:
    timeline, queue = repair([run(RunKind.TRIAL, RunStatus.SUCCEEDED, 0, HEALTHY)], ScraperState.PROPOSED)
    assert (timeline.written, queue.reopened, queue.requested) == ([], [], [])


def test_a_failed_test_of_a_version_in_review_tells_its_builder_and_reopens_it() -> None:
    timeline, queue = repair(failed(1) + [run(RunKind.TRIAL, RunStatus.SUCCEEDED, 9, HEALTHY)], ScraperState.PROPOSED)
    ((to, kind, author, text),) = timeline.written
    assert (to, kind, author) == (BUILDER, EventKind.PERSON, AUTHOR)
    assert "v1" in text and "ValueError" in text and f"1 of {MAX_HEALS}" in text
    assert queue.reopened == [BUILDER] and queue.requested == []


def test_a_builder_still_working_hears_it_mid_task_without_a_second_run() -> None:
    timeline, queue = repair(failed(1), ScraperState.PROPOSED, builder=RunStatus.RUNNING)
    assert len(timeline.written) == 1 and queue.reopened == []


def test_a_failed_test_of_the_live_version_queues_one_heal() -> None:
    timeline, queue = repair(failed(1), ScraperState.MERGED)
    assert queue.requested == [RunKind.HEAL] and timeline.written == []
    _, queue = repair(failed(1), ScraperState.MERGED, working=True)
    assert queue.requested == []


def test_after_max_heals_failures_in_a_row_nobody_is_sent() -> None:
    timeline, queue = repair(failed(MAX_HEALS), ScraperState.PROPOSED)
    assert f"{MAX_HEALS} of {MAX_HEALS}" in timeline.written[0][3]
    timeline, queue = repair(failed(MAX_HEALS + 1), ScraperState.PROPOSED)
    assert (timeline.written, queue.reopened, queue.requested) == ([], [], [])
    assert "no agent is sent again" in timeline.logged[0][1]
