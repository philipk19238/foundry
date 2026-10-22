"""A scrape that goes wrong sends an agent to fix it, so a failure never waits on a person to notice. A production
scrape does it through the source's health (`Health.after`): a heal, then a test of the fix, then scrapes again once
it is merged. A test scrape does it here."""

import uuid

from foundry.app.db.models.catalog import ScraperState
from foundry.app.db.models.runs import RunKind
from foundry.app.progress.models import TrialView
from foundry.app.runs.models import EventKind, RunView
from foundry.app.runs.runs import Queue, RunHistory, Timeline
from foundry.app.scrapers.models.crawl import MAX_HEALS, Crawl, Verdict
from foundry.app.scrapers.models.version import ScraperView
from foundry.app.settings import Settings

AUTHOR = "foundry"


class Repairs:
    """After a test scrape that went wrong: the agent that wrote a version in review is told what failed, and fixes
    it on its branch (Foundry tests its next version when it hands it in); a live version gets a heal. After
    MAX_HEALS failed tests of the source in a row, it is left to a person, and the run says so."""

    def __init__(self, history: RunHistory, timeline: Timeline, queue: Queue, settings: Settings) -> None:
        self._history = history
        self._timeline = timeline
        self._queue = queue
        self._base_url = settings.base_url

    async def after_trial(self, run: RunView, scraper: ScraperView, crawl: Crawl) -> None:
        if not self.went_wrong(crawl):
            return
        failed = await self.failed_in_a_row(run.source_id)
        if failed > MAX_HEALS:
            await self._timeline.log(
                run.id, EventKind.NOTE, f"The last {failed} test scrapes failed; no agent is sent again until a person "
                "looks at it."
            )  # fmt: skip
            return
        if scraper.state == ScraperState.PROPOSED:
            await self._tell(scraper, run, crawl, failed)
        elif run.source_id not in await self._history.working_sources():
            await self._queue.request(run.source_id, RunKind.HEAL, run.requested_by)
            await self._timeline.log(run.id, EventKind.STATUS, "Queued a heal run next.")

    async def failed_in_a_row(self, source_id: uuid.UUID) -> int:
        """How many of the source's newest finished test scrapes went wrong, back to its last healthy one."""
        failed = 0
        for trial in await self._history.of_source(source_id):
            found = TrialView.of(trial) if trial.finished_at is not None else None
            if found is None:
                continue
            if not self.went_wrong(found.crawl):
                break
            failed += 1
        return failed

    @staticmethod
    def went_wrong(crawl: Crawl) -> bool:
        """A test went wrong when it wasn't healthy, or found nothing: a test has no earlier count to fall from, so
        an empty one reads as healthy to the verdict."""
        return crawl.verdict != Verdict.HEALTHY or crawl.valid == 0

    async def _tell(self, scraper: ScraperView, run: RunView, crawl: Crawl, failed: int) -> None:
        """Writes to the agent run that built the version, as a person would, which reaches it mid-task or reopens
        it."""
        found = TrialView(scraper=scraper, crawl=crawl, listings=[])
        text = (
            f"Foundry's test scrape of your v{scraper.version} went wrong ({self._base_url}/runs/{run.id}). "
            f"{found.described}\nFix the spider on your branch and hand it in again; Foundry tests it again then. "
            f"This is automatic repair {failed} of {MAX_HEALS}."
        )
        await self._timeline.wrote(scraper.run_id, EventKind.PERSON, AUTHOR, text)
        builder = await self._history.run(scraper.run_id)
        if not builder.is_active:
            await self._queue.reopen(builder.id)
        await self._timeline.log(
            run.id, EventKind.STATUS, f"Sent the failure to the agent that wrote v{scraper.version}."
        )
