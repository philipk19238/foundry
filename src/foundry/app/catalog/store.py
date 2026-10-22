"""Catalog I/O. Notices and files are content-addressed and only ever inserted: a row, once stored, stays
(formal/foundry_rules `ingestAll_keeps`). The writer holds the source's lease, so two writers never race on one
source's rows."""

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence

from sqlalchemy import Uuid, bindparam, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from foundry.app.catalog import models
from foundry.app.core.models import Lease
from foundry.app.db.models.catalog import Buyer, File, Notice, Source

_SOURCES = select(Source).order_by(Source.created_at.desc())
_SOURCE = select(Source).where(Source.id == bindparam("source"))
_SOURCE_AT = select(Source).where(Source.url == bindparam("url"))

_FENCED = (
    update(Source)
    .where(
        Source.id == bindparam("source"), Source.lease_run_id == bindparam("run"), Source.fence == bindparam("token")
    )
    .values(fence=Source.fence)
    .returning(Source.id)
)

_KNOWN_NOTICES = select(Notice.notice_id).where(Notice.notice_id.in_(bindparam("notices", expanding=True)))
_KNOWN_OPPORTUNITIES = select(Notice.opportunity_id).where(
    Notice.opportunity_id.in_(bindparam("opportunities", expanding=True))
)
_KNOWN_BUYERS = select(Buyer.buyer_id).where(Buyer.buyer_id.in_(bindparam("buyers", expanding=True)))

_ADD_BUYERS = insert(Buyer)
_ADD_NOTICES = insert(Notice)
_ADD_FILES = insert(File)

_NOTICES = (
    select(Notice, Buyer.name)
    .outerjoin(Buyer, Buyer.buyer_id == Notice.buyer_id)
    .where(Notice.source_id == bindparam("source"))
    .order_by(Notice.first_seen_at.desc())
    .limit(bindparam("limit"))
)
_RANKED = (
    select(
        Notice.notice_id,
        func.row_number()
        .over(partition_by=Notice.opportunity_id, order_by=(Notice.first_seen_at.desc(), Notice.notice_id.desc()))
        .label("rank"),
        func.count().over(partition_by=Notice.opportunity_id).label("snapshots"),
    )
    .where(or_(bindparam("source", type_=Uuid).is_(None), Notice.source_id == bindparam("source", type_=Uuid)))
    .subquery()
)
_OPPORTUNITIES = (
    select(Notice, Buyer.name, Source.name, _RANKED.c.snapshots)
    .join(_RANKED, (_RANKED.c.notice_id == Notice.notice_id) & (_RANKED.c.rank == 1))
    .join(Source, Source.id == Notice.source_id)
    .outerjoin(Buyer, Buyer.buyer_id == Notice.buyer_id)
    .where(or_(Notice.title.ilike(bindparam("match"), escape="\\"), Buyer.name.ilike(bindparam("match"), escape="\\")))
    .order_by(Notice.first_seen_at.desc(), Notice.notice_id)
    .limit(bindparam("limit"))
    .offset(bindparam("skip"))
)
_FILES = select(File).where(File.notice_id.in_(bindparam("notices", expanding=True)))
_FILE = select(File).where(File.notice_id == bindparam("notice"), File.file_id == bindparam("file"))
_RUN_OF = select(Notice.run_id).where(Notice.notice_id == bindparam("notice"))
_STORED = (
    select(File)
    .join(Notice, Notice.notice_id == File.notice_id)
    .where(Notice.source_id == bindparam("source"), File.sha256.is_not(None))
)


async def source_at(session: AsyncSession, url: str) -> Source | None:
    return await session.scalar(_SOURCE_AT, {"url": url})


async def add_source(session: AsyncSession, url: str, name: str, created_at: dt.datetime) -> Source:
    source = Source(id=uuid.uuid4(), url=url, name=name, created_at=created_at, fence=0, heals=0, previous_valid=0)
    session.add(source)
    await session.flush()
    return source


async def sources(session: AsyncSession) -> Sequence[Source]:
    return (await session.scalars(_SOURCES)).all()


async def source(session: AsyncSession, source_id: uuid.UUID) -> Source | None:
    return await session.scalar(_SOURCE, {"source": source_id})


async def fenced(session: AsyncSession, lease: Lease) -> bool:
    """Locks the source row for this transaction if the lease still holds it."""
    binds = {"source": lease.source_id, "run": lease.run_id, "token": lease.fence}
    return await session.scalar(_FENCED, binds) is not None


async def known(session: AsyncSession, notices: Sequence[models.ScrapedNotice]) -> models.Known:
    notice_ids = await session.scalars(_KNOWN_NOTICES, {"notices": [notice.notice_id for notice in notices]})
    opportunity_ids = await session.scalars(
        _KNOWN_OPPORTUNITIES, {"opportunities": [notice.opportunity_id for notice in notices]}
    )
    return models.Known(notices=frozenset(notice_ids), opportunities=frozenset(opportunity_ids))


async def add_buyers(session: AsyncSession, buyers: dict[str, str]) -> None:
    present = set(await session.scalars(_KNOWN_BUYERS, {"buyers": list(buyers)}))
    missing = [{"buyer_id": buyer_id, "name": name} for buyer_id, name in buyers.items() if buyer_id not in present]
    if missing:
        await session.execute(_ADD_BUYERS, missing)


async def add_notices(
    session: AsyncSession,
    notices: Sequence[models.ScrapedNotice],
    files: Mapping[str, Sequence[models.StoredFile]],
    seen_at: dt.datetime,
    run_id: uuid.UUID,
) -> None:
    rows = [
        {
            "notice_id": notice.notice_id,
            "opportunity_id": notice.opportunity_id,
            "source_id": notice.source_id,
            "buyer_id": notice.buyer_id,
            "external_id": notice.listing.external_id,
            "url": notice.listing.url,
            "title": notice.listing.title,
            "due_at": notice.listing.due_at,
            "description": notice.listing.description,
            "metadata_": notice.listing.metadata,
            "first_seen_at": seen_at,
            "run_id": run_id,
        }
        for notice in notices
    ]
    attachments = [
        {"notice_id": notice.notice_id, **file.model_dump()}
        for notice in notices
        for file in {file.file_id: file for file in files.get(notice.notice_id, ())}.values()
    ]
    if rows:
        await session.execute(_ADD_NOTICES, rows)
    if attachments:
        await session.execute(_ADD_FILES, attachments)


async def stored(session: AsyncSession, source_id: uuid.UUID) -> Sequence[File]:
    return (await session.scalars(_STORED, {"source": source_id})).all()


async def file(session: AsyncSession, notice_id: str, file_id: str) -> File | None:
    return await session.scalar(_FILE, {"notice": notice_id, "file": file_id})


async def run_of(session: AsyncSession, notice_id: str) -> uuid.UUID | None:
    return await session.scalar(_RUN_OF, {"notice": notice_id})


async def notices(session: AsyncSession, source_id: uuid.UUID, limit: int) -> list[models.NoticeView]:
    rows = (await session.execute(_NOTICES, {"source": source_id, "limit": limit})).all()
    return await _with_files(session, [(notice, buyer) for notice, buyer in rows])


async def opportunities(
    session: AsyncSession, source_id: uuid.UUID | None, words: str, limit: int, skip: int
) -> list[models.OpportunityRow]:
    """The newest snapshot of every opportunity, newest first, from one source or all, whose title or buyer holds
    `words`; each with how many snapshots its portal's edits have made."""
    escaped = words.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    binds = {"source": source_id, "match": f"%{escaped}%", "limit": limit, "skip": skip}
    rows = (await session.execute(_OPPORTUNITIES, binds)).all()
    views = await _with_files(session, [(notice, buyer) for notice, buyer, _, _ in rows])
    return [
        models.OpportunityRow(notice=view, source_id=notice.source_id, source=source, snapshots=snapshots)
        for view, (notice, _, source, snapshots) in zip(views, rows, strict=True)
    ]


async def _with_files(session: AsyncSession, rows: list[tuple[Notice, str | None]]) -> list[models.NoticeView]:
    attached = await session.scalars(_FILES, {"notices": [notice.notice_id for notice, _ in rows]})
    files: dict[str, list[models.FileView]] = {}
    for file in attached:
        files.setdefault(file.notice_id, []).append(models.FileView.model_validate(file))
    return [
        models.NoticeView(
            notice_id=notice.notice_id,
            opportunity_id=notice.opportunity_id,
            external_id=notice.external_id,
            url=notice.url,
            title=notice.title,
            buyer=buyer,
            due_at=notice.due_at,
            description=notice.description,
            metadata=notice.metadata_,
            first_seen_at=notice.first_seen_at,
            run_id=notice.run_id,
            files=files.get(notice.notice_id, []),
        )
        for notice, buyer in rows
    ]
