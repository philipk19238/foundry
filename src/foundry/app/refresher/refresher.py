from foundry.app.browser.browser import Accounts
from foundry.app.catalog.catalog import Catalog
from foundry.app.catalog.models import IngestCounts, ScrapedNotice, SourceView
from foundry.app.core.models import Lease
from foundry.app.repo.repo import Mirror
from foundry.app.runs.models import RunView
from foundry.app.runs.runs import Attempts
from foundry.app.scrapers.models.crawl import Crawl, CrawlReport, Health, RefreshReport
from foundry.app.scrapers.models.version import ScraperView
from foundry.app.scrapers.scrapers import Crawls, Healths, Versions


class Refresher:
    """One crawl of a portal with its current scraper, from `main` at the commit that merged it, which downloads the
    attachments as it goes: store what is new, judge the scraper's health."""

    def __init__(
        self, versions: Versions, mirror: Mirror, accounts: Accounts, crawls: Crawls, catalog: Catalog,
        healths: Healths, attempts: Attempts,
    ) -> None:  # fmt: skip
        self._attempts = attempts
        self._versions = versions
        self._mirror = mirror
        self._accounts = accounts
        self._crawls = crawls
        self._catalog = catalog
        self._healths = healths

    async def refresh(self, lease: Lease, source: SourceView, run: RunView) -> RefreshReport:
        watch = self._attempts.watch(run)
        scraper = await self._versions.current(source.id)
        access = await self._accounts.access(source.id)
        known = await self._catalog.stored(source.id)
        async with self._mirror.tree(scraper.commit) as tree:
            report = await self._crawls.live(tree, source.id, scraper.site, access, source.previous_valid, watch, known)
        if report.session is not None and report.session.cookies:
            await self._accounts.save_session(lease, report.session)
        notices = [ScrapedNotice(source_id=source.id, listing=listing) for listing in report.listings]
        saved = {**known, **report.files}
        files = {notice.notice_id: notice.stored_files(saved) for notice in await self._catalog.unseen(notices)}
        ingested = await self._catalog.ingest(lease, notices, files)
        health = await self._healths.record(lease, Health(status=source.status, heals=source.heals), report.crawl)
        return RefreshReport(crawl=report.crawl, ingested=ingested, health=health, bronze=report.bronze)

    async def broken(self, lease: Lease, source: SourceView, why: str) -> RefreshReport:
        """A refresh that never got to report (it stalled on its last attempt, or its spider wouldn't load), judged
        as a crashed crawl, so the source is healed like any broken one."""
        crawl = Crawl.broken(why, source.previous_valid)
        health = await self._healths.record(lease, Health(status=source.status, heals=source.heals), crawl)
        return RefreshReport(crawl=crawl, ingested=IngestCounts(new=0, amended=0, unchanged=0), health=health)

    async def trial(self, scraper: ScraperView, run: RunView) -> CrawlReport:
        """One live crawl with any version, from its own code, storing nothing but the attachments it downloads."""
        watch = self._attempts.watch(run)
        await self._mirror.fetch()
        access = await self._accounts.access(scraper.source_id)
        known = await self._catalog.stored(scraper.source_id)
        async with self._mirror.tree(scraper.ref) as tree:
            return await self._crawls.live(tree, scraper.source_id, scraper.site, access, 0, watch, known)
