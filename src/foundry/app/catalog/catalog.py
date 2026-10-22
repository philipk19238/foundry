import uuid
from collections.abc import Mapping, Sequence
from urllib.parse import urlsplit

from foundry.app import exceptions
from foundry.app.catalog import models, store
from foundry.app.core.models import Lease
from foundry.app.db.db import Sessionmaker, now

NOTICE_PAGE = 200


class Catalog:
    """Portals, and the notices scraped from them."""

    def __init__(self, sessionmaker: Sessionmaker) -> None:
        self._sessionmaker = sessionmaker

    async def add_source(self, url: str) -> models.SourceView:
        async with self._sessionmaker.begin() as session:
            source = await store.add_source(session, url, urlsplit(url).hostname or url, now())
            return models.SourceView.model_validate(source)

    async def sources(self) -> list[models.SourceView]:
        async with self._sessionmaker() as session:
            return [models.SourceView.model_validate(source) for source in await store.sources(session)]

    async def source(self, source_id: uuid.UUID) -> models.SourceView:
        async with self._sessionmaker() as session:
            source = await store.source(session, source_id)
        if source is None:
            raise exceptions.SourceNotFound(source_id)
        return models.SourceView.model_validate(source)

    async def notices(self, source_id: uuid.UUID) -> list[models.NoticeView]:
        async with self._sessionmaker() as session:
            return await store.notices(session, source_id, NOTICE_PAGE)

    async def file(self, notice_id: str, file_id: str) -> models.FileView:
        async with self._sessionmaker() as session:
            found = await store.file(session, notice_id, file_id)
        if found is None:
            raise exceptions.FileNotFound(notice_id, file_id)
        return models.FileView.model_validate(found)

    async def unseen(self, notices: Sequence[models.ScrapedNotice]) -> list[models.ScrapedNotice]:
        """The notices of a crawl the catalog doesn't have yet; only these need their attachments downloaded."""
        async with self._sessionmaker() as session:
            known = await store.known(session, notices)
        return [notice for notice in notices if notice.notice_id not in known.notices]

    async def ingest(
        self,
        lease: Lease,
        notices: Sequence[models.ScrapedNotice],
        files: Mapping[str, Sequence[models.StoredFile]],
    ) -> models.IngestCounts:
        """Stores what is new, with ``files`` by notice id; a repeat stores nothing, so a crawl can run any number of
        times."""
        batch = list({notice.notice_id: notice for notice in notices}.values())
        async with self._sessionmaker.begin() as session:
            if not await store.fenced(session, lease):
                raise exceptions.LeaseLost()
            known = await store.known(session, batch)
            changes = [models.Change.of(notice, known) for notice in batch]
            fresh = [notice for notice, change in zip(batch, changes) if change != models.Change.UNCHANGED]
            buyers = {notice.buyer_id: notice.listing.buyer for notice in fresh}
            await store.add_buyers(session, {key: name for key, name in buyers.items() if key and name})
            await store.add_notices(session, fresh, files, now())
        return models.IngestCounts(
            new=changes.count(models.Change.NEW),
            amended=changes.count(models.Change.AMENDMENT),
            unchanged=changes.count(models.Change.UNCHANGED),
        )
