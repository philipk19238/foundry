"""Scraper versions and a source's health. Every write is fenced by the writer's lease."""

import datetime as dt
import uuid

from sqlalchemy import and_, bindparam, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from foundry.app.core.models import Lease
from foundry.app.db.models.catalog import Scraper, ScraperState, Source, SourceStatus

_HELD = and_(
    Source.id == bindparam("source"),
    Source.lease_run_id == bindparam("run"),
    Source.fence == bindparam("token"),
)

_CURRENT = (
    select(Scraper)
    .join(Source, and_(Source.id == Scraper.source_id, Source.scraper_version == Scraper.version))
    .where(Source.id == bindparam("source"))
)
_PROPOSED = (
    select(Scraper)
    .where(Scraper.source_id == bindparam("source"), Scraper.state == ScraperState.PROPOSED)
    .order_by(Scraper.version.desc())
    .limit(1)
)
_LATEST = select(func.max(Scraper.version)).where(Scraper.source_id == bindparam("source"))
_FENCE = update(Source).where(_HELD).values(fence=Source.fence).returning(Source.id)
_MERGED = (
    update(Scraper)
    .where(Scraper.source_id == bindparam("source"), Scraper.version == bindparam("number"))
    .values(state=ScraperState.MERGED, commit=bindparam("sha"))
)
_CLOSED = (
    update(Scraper)
    .where(Scraper.source_id == bindparam("source"), Scraper.version == bindparam("number"))
    .values(state=ScraperState.CLOSED)
)
_POINT = (
    update(Source).where(_HELD).values(scraper_version=bindparam("number"), site=bindparam("key")).returning(Source.id)
)
_HEALTH = (
    update(Source)
    .where(_HELD)
    .values(status=bindparam("state"), heals=bindparam("count"), previous_valid=bindparam("found"))
    .returning(Source.id)
)
_STATUS = update(Source).where(_HELD).values(status=bindparam("state")).returning(Source.id)


def _held(lease: Lease) -> dict[str, uuid.UUID | int]:
    return {"source": lease.source_id, "run": lease.run_id, "token": lease.fence}


async def current(session: AsyncSession, source_id: uuid.UUID) -> Scraper | None:
    """The merged version the source runs."""
    return await session.scalar(_CURRENT, {"source": source_id})


async def proposed(session: AsyncSession, source_id: uuid.UUID) -> Scraper | None:
    """The newest version still waiting in its pull request."""
    return await session.scalar(_PROPOSED, {"source": source_id})


async def propose(
    session: AsyncSession, lease: Lease, site: str, branch: str, pull: int, at: dt.datetime
) -> int | None:
    """Records a pull request as the source's next version; None if the lease no longer holds the source."""
    if await session.scalar(_FENCE, _held(lease)) is None:
        return None
    version = (await session.scalar(_LATEST, {"source": lease.source_id}) or 0) + 1
    session.add(
        Scraper(
            id=uuid.uuid4(),
            source_id=lease.source_id,
            version=version,
            site=site,
            state=ScraperState.PROPOSED,
            branch=branch,
            pull=pull,
            commit=None,
            run_id=lease.run_id,
            created_at=at,
        )
    )
    return version


async def merged(session: AsyncSession, lease: Lease, version: int, site: str, commit: str) -> bool:
    """Makes a merged version the one the source runs."""
    if await session.scalar(_POINT, {**_held(lease), "number": version, "key": site}) is None:
        return False
    await session.execute(_MERGED, {"source": lease.source_id, "number": version, "sha": commit})
    return True


async def closed(session: AsyncSession, lease: Lease, version: int) -> bool:
    if await session.scalar(_FENCE, _held(lease)) is None:
        return False
    await session.execute(_CLOSED, {"source": lease.source_id, "number": version})
    return True


async def set_health(session: AsyncSession, lease: Lease, status: SourceStatus, heals: int, found: int) -> bool:
    binds = {**_held(lease), "state": status, "count": heals, "found": found}
    return await session.scalar(_HEALTH, binds) is not None


async def set_status(session: AsyncSession, lease: Lease, status: SourceStatus) -> bool:
    return await session.scalar(_STATUS, {**_held(lease), "state": status}) is not None
