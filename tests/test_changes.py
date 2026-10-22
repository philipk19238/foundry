"""An agent following a job over `subscriptions/listen` reads again only on the updates it hears, so a change the
publisher misses leaves it waiting forever, and one it hears too often makes it reread everything. These pin which
resources each change updates (`foundry.server.changes.models.Look`)."""

import datetime as dt
import uuid

from foundry.app.db.models.runs import RunStatus
from foundry.app.runs.models import RunMark
from foundry.server.changes.models import QUESTIONS, Look

RUN = uuid.UUID(int=1)
OTHER = uuid.UUID(int=2)
SOURCE = uuid.UUID(int=10)
AT = dt.datetime(2026, 10, 22, tzinfo=dt.UTC)


def mark(status: RunStatus, beat_at: dt.datetime | None = None, run_id: uuid.UUID = RUN) -> RunMark:
    return RunMark(id=run_id, source_id=SOURCE, status=status, beat_at=beat_at)


def look(*marks: RunMark, after: int = 5) -> Look:
    return Look(marks={m.id: m for m in marks}, after=after)


def test_nothing_new_updates_nothing() -> None:
    before = look(mark(RunStatus.RUNNING, AT))
    assert before.changed(look(mark(RunStatus.RUNNING, AT)), {}) == []


def test_a_new_line_updates_only_its_runs_chat() -> None:
    before = look(mark(RunStatus.RUNNING, AT))
    later = look(mark(RunStatus.RUNNING, AT), after=7)
    assert before.changed(later, {RUN: 7}) == [f"foundry://runs/{RUN}/chat"]


def test_a_line_on_an_ended_run_still_reaches_its_chat() -> None:
    assert look().changed(look(after=6), {OTHER: 6}) == [f"foundry://runs/{OTHER}/chat"]


def test_a_beat_updates_the_run_and_its_scraper_but_not_the_questions() -> None:
    before = look(mark(RunStatus.RUNNING, AT))
    later = look(mark(RunStatus.RUNNING, AT + dt.timedelta(seconds=5)))
    assert before.changed(later, {}) == [f"foundry://runs/{RUN}", f"foundry://sources/{SOURCE}"]


def test_a_new_status_also_updates_the_questions() -> None:
    before = look(mark(RunStatus.RUNNING))
    later = look(mark(RunStatus.WAITING))
    assert before.changed(later, {}) == [f"foundry://runs/{RUN}", f"foundry://sources/{SOURCE}", QUESTIONS]


def test_a_run_that_appears_counts_as_a_new_status() -> None:
    later = look(mark(RunStatus.QUEUED))
    assert look().changed(later, {}) == [f"foundry://runs/{RUN}", f"foundry://sources/{SOURCE}", QUESTIONS]
