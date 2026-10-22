from pydantic import BaseModel

from foundry.app.catalog.models import SourceView
from foundry.app.db.models.runs import RunKind
from foundry.app.runs.models import RunView
from foundry.app.scrapers.models import Crawl, ScraperView
from foundry.core.models import Listing


class NewSource(BaseModel):
    url: str


class Onboarding(BaseModel):
    source: SourceView
    run: RunView


class TrialView(BaseModel):
    """What a trial run found, read back from the stats it finished with."""

    scraper: ScraperView
    crawl: Crawl
    listings: list[Listing]

    @classmethod
    def of(cls, run: RunView) -> "TrialView | None":
        if run.kind != RunKind.TRIAL or run.stats is None:
            return None
        return cls.model_validate(run.stats)
