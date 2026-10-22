import uuid

from foundry.app.catalog.catalog import Catalog
from foundry.app.db.db import now
from foundry.app.db.models.runs import RunKind
from foundry.app.help.help import HelpQueue
from foundry.app.progress import models
from foundry.app.runs.runs import RunHistory, Timeline
from foundry.app.scrapers.scrapers import ScraperHistory


class Tracker:
    """Follows each scraper through its stages, for the pages that show them: one source in full, or every source's
    stage at once."""

    def __init__(
        self, catalog: Catalog, history: RunHistory, scrapers: ScraperHistory, help_queue: HelpQueue,
        timeline: Timeline,
    ) -> None:  # fmt: skip
        self._catalog = catalog
        self._history = history
        self._scrapers = scrapers
        self._help_queue = help_queue
        self._timeline = timeline

    async def progress(self, source_id: uuid.UUID) -> models.Progress:
        return models.Progress.of(
            await self._catalog.source(source_id),
            await self.agent_job(source_id),
            await self._history.of_source(source_id),
            await self._scrapers.of_source(source_id),
        )

    async def agent_job(self, source_id: uuid.UUID) -> models.AgentJob | None:
        run = await self._history.last_agent(source_id)
        if run is None:
            return None
        at = now()
        question = next((ask for ask in await self._help_queue.of_run(run.id) if ask.is_live(at)), None)
        return models.AgentJob(run=run, question=question, last_message=await self._timeline.said(run.id))

    async def trials(self, progress: models.Progress) -> models.TrialState:
        finished = await self._history.last(progress.source_id, RunKind.TRIAL)
        return models.TrialState.of(finished, progress.runs)

    async def stages(self) -> dict[uuid.UUID, models.Stage]:
        """Every source's stage, from three queries however many sources there are."""
        working = await self._history.working_sources()
        waiting = await self._history.waiting_sources()
        reviewing = await self._scrapers.reviewing_sources()
        return {
            source.id: models.Stage.of(
                source.status,
                models.Signals(
                    working=source.id in working, waiting=source.id in waiting, reviewing=source.id in reviewing
                ),
            )
            for source in await self._catalog.sources()
        }
