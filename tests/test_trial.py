"""A test scrape's run page and MCP's get_run read what it found from its stats, which hold only its progress until it
finishes (and stay that way when the reaper gives up on it). Reading progress as findings broke both for every running
test scrape; a finished one must still read back."""

import datetime as dt
import uuid

from foundry.app.db.models.runs import RunKind, RunStatus
from foundry.app.progress.models import TrialView
from foundry.app.runs.models import RunView

AT = dt.datetime(2026, 10, 22, tzinfo=dt.UTC)
PROGRESS = {"progress": {"at": AT.isoformat(), "listings": 12, "requests": 40}}
FOUND = {
    "scraper": {
        "source_id": str(uuid.UUID(int=1)), "version": 3, "site": "us/tx/austin", "state": "proposed",
        "branch": "agent/austin", "pull": 7, "commit": None, "run_id": str(uuid.UUID(int=2)),
        "created_at": AT.isoformat(),
    },
    "crawl": {"auth_failed": False, "crashed": False, "invalid": 1, "valid": 12, "previous_valid": 0},
    "listings": [],
}  # fmt: skip


def trial(status: RunStatus, stats: dict[str, object] | None) -> RunView:
    return RunView(
        id=uuid.UUID(int=3), source_id=uuid.UUID(int=1), kind=RunKind.TRIAL, status=status, requested_by=None,
        summary=None, stats=stats, created_at=AT, started_at=AT, finished_at=None, attempts=0, beat_at=AT,
        live_view_url=None,
    )  # fmt: skip


def test_a_running_test_scrape_has_found_nothing_yet() -> None:
    assert TrialView.of(trial(RunStatus.RUNNING, PROGRESS)) is None


def test_a_test_scrape_given_up_on_has_found_nothing() -> None:
    assert TrialView.of(trial(RunStatus.FAILED, PROGRESS)) is None


def test_a_finished_test_scrape_reads_back_what_it_found() -> None:
    found = TrialView.of(trial(RunStatus.SUCCEEDED, FOUND))
    assert found is not None
    assert (found.scraper.version, found.crawl.valid, found.crawl.invalid) == (3, 12, 1)
