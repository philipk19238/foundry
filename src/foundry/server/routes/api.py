"""The JSON API, for scripts and the pipeline: send `Authorization: Bearer <token>` (made on the Tokens page)."""

import uuid

from fastapi import APIRouter

from foundry.app.auth.models import UserView
from foundry.app.catalog.catalog import Catalog
from foundry.app.catalog.models import NoticeView, SourceView
from foundry.app.db.models.runs import RunKind
from foundry.app.runs.models import EventView, RunView
from foundry.app.runs.runs import Queue, RunHistory, Timeline
from foundry.server.models import NewSource, Onboarding
from foundry.server.routes.auth import Admin, SignedIn

router = APIRouter(prefix="/api")


@router.post("/sources")
async def add_source(body: NewSource, services: Admin) -> Onboarding:
    """Adds a portal and starts the agent on it. Poll the run, then call refresh whenever you want fresh data."""
    user, catalog, queue = await services.aget(UserView, Catalog, Queue)
    source = await catalog.add_source(body.url.strip())
    return Onboarding(source=source, run=await queue.request(source.id, RunKind.ONBOARD, user.id))


@router.get("/sources")
async def sources(services: SignedIn) -> list[SourceView]:
    catalog = await services.aget(Catalog)
    return await catalog.sources()


@router.post("/sources/{source_id}/refresh")
async def refresh(source_id: uuid.UUID, services: Admin) -> RunView:
    """Crawls the portal now with its registered scraper; a broken scraper queues a heal by itself."""
    user, catalog, queue = await services.aget(UserView, Catalog, Queue)
    source = await catalog.source(source_id)
    return await queue.request(source.id, RunKind.REFRESH, user.id)


@router.get("/sources/{source_id}/notices")
async def notices(source_id: uuid.UUID, services: SignedIn) -> list[NoticeView]:
    catalog = await services.aget(Catalog)
    return await catalog.notices(source_id)


@router.get("/runs/{run_id}")
async def run(run_id: uuid.UUID, services: SignedIn) -> RunView:
    runs = await services.aget(RunHistory)
    return await runs.run(run_id)


@router.get("/runs/{run_id}/events")
async def events(run_id: uuid.UUID, services: SignedIn, after: int = 0) -> list[EventView]:
    timeline = await services.aget(Timeline)
    return await timeline.since(run_id, after)
