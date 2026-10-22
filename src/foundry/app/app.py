"""The services foundry offers, and `Jobs`, which carries out one run from start to finish."""

import functools
import logging
import uuid
from typing import assert_never

import httpx
import svcs
from argon2 import PasswordHasher
from cryptography.fernet import Fernet
from kernel import AsyncKernel
from knockapi import AsyncKnock
from pydantic import JsonValue

from foundry.app import exceptions
from foundry.app.agent.agent import Agent, Codex, Inbox, Narrator, Workspaces
from foundry.app.agent.models import outcomes, tasks
from foundry.app.auth.auth import Logins, Users
from foundry.app.bronze.bronze import Bronze, Recordings
from foundry.app.browser.browser import Accounts, Browsers, Credentials
from foundry.app.browser.client import KernelBrowserApi
from foundry.app.browser.models import BrowserApi
from foundry.app.catalog.catalog import Catalog, Opportunities
from foundry.app.core.models import Lease
from foundry.app.db import db
from foundry.app.db.models.catalog import ScraperState, SourceStatus
from foundry.app.db.models.runs import RunKind, RunStatus
from foundry.app.files.client import FolderBucket, R2Bucket
from foundry.app.files.models import Bucket
from foundry.app.help.client import KnockNotifier
from foundry.app.help.help import HelpDesk, HelpQueue, RunChat
from foundry.app.help.models import Notifier
from foundry.app.learnings.learnings import Learnings
from foundry.app.progress.progress import Tracker
from foundry.app.pulls.pulls import Imports, Proposals, Reviews
from foundry.app.refresher.refresher import Refresher
from foundry.app.repairs.repairs import Repairs
from foundry.app.repo.client import GitHubPulls
from foundry.app.repo.repo import Clones, Git, Mirror
from foundry.app.runs.changes import RunChanges
from foundry.app.runs.models import EventKind, RunView
from foundry.app.runs.runs import (
    MOST_ATTEMPTS,
    Attempts,
    Leases,
    Pauses,
    Queue,
    RunFeed,
    RunHistory,
    Runs,
    Timeline,
)
from foundry.app.scrapers.models.crawl import Crawl, CrawlReport, CrawlState
from foundry.app.scrapers.models.version import Proposal
from foundry.app.scrapers.scrapers import CrawlFolders, Crawls, Healths, ScraperHistory, Versions
from foundry.app.settings import Settings, settings

logger = logging.getLogger(__name__)

NEXT = {SourceStatus.RELOGIN: RunKind.RELOGIN, SourceStatus.HEALING: RunKind.HEAL}


class Jobs:
    """Carries out a queued run under its source's lease, records how it ended, and queues what comes next: a heal
    or relogin after a broken crawl, a refresh after a merged scraper or a new session, the same run again after a
    restart. A test scrape that went wrong sends an agent to fix it (`Repairs`)."""

    def __init__(
        self,
        runs: Runs,
        history: RunHistory,
        leases: Leases,
        queue: Queue,
        timeline: Timeline,
        catalog: Catalog,
        refresher: Refresher,
        agent: Agent,
        versions: Versions,
        healths: Healths,
        pulls: GitHubPulls,
        pauses: Pauses,
        scrapers: ScraperHistory,
        proposals: Proposals,
        repairs: Repairs,
    ) -> None:
        self._runs = runs
        self._history = history
        self._leases = leases
        self._queue = queue
        self._timeline = timeline
        self._catalog = catalog
        self._refresher = refresher
        self._agent = agent
        self._versions = versions
        self._healths = healths
        self._pulls = pulls
        self._pauses = pauses
        self._scrapers = scrapers
        self._proposals = proposals
        self._repairs = repairs

    async def carry_out(self, run_id: uuid.UUID) -> None:
        """Carries a run out. An agent job that finds its source held waits for it instead of being skipped; whenever
        a run lets go of its source, the jobs waiting on it are woken, and an agent whose run ended with messages it
        never read is reopened to read them."""
        run = await self._history.run(run_id)
        if not await self._runs.start(run):
            return
        try:
            await self._carry(run)
        finally:
            if run.kind.leased:
                await self._queue.wake(run.source_id)
        if run.has_agent and await self._replies_owed(run.id):
            await self._queue.reopen(run.id)

    async def _replies_owed(self, run_id: uuid.UUID) -> bool:
        """An agent run that ended well but left messages unread; a failed one is not reopened, or a run that fails
        before it reads would reopen forever."""
        ended = await self._history.run(run_id)
        return ended.status == RunStatus.SUCCEEDED and await self._agent.unread(run_id)

    async def _carry(self, run: RunView) -> None:
        try:
            if run.kind.leased:
                follow = await self._leases.hold(run.source_id, run.id, functools.partial(self._work, run))
            else:
                follow = await self._trial(run)
        except exceptions.SourceBusy as error:
            if run.kind.crawls and run.attempts > 0:
                # The attempt it replaces still holds the source: leave this one running, unbeaten, for the reaper to
                # carry on once that lease lapses (formal/runs/crawls `BusyWaits`).
                return
            if run.has_agent:
                await self._runs.defer(run.id)
                if await self._leases.free(run.source_id):
                    await self._queue.wake(run.source_id)
            else:
                await self._runs.finish(run, RunStatus.SKIPPED, str(error))
            return
        except exceptions.CrawlStopped:
            if (await self._history.run(run.id)).status == RunStatus.QUEUED:
                await self._queue.dispatch(run.id)
            return
        except exceptions.CrawlStalled as error:
            if run.attempts + 1 >= MOST_ATTEMPTS or not await self._queue.retry(run):
                await self._runs.finish(run, RunStatus.FAILED, str(error))
            return
        except exceptions.Error as error:
            await self._runs.finish(run, RunStatus.FAILED, str(error))
            return
        except Exception as error:
            logger.exception("run %s crashed", run.id)
            await self._runs.finish(run, RunStatus.FAILED, f"crashed: {type(error).__name__}: {error}")
            return
        if (await self._history.run(run.id)).status == RunStatus.RESTARTING:
            await self._queue.redeploy(run.id)
            return
        if follow is not None:
            await self._timeline.log(run.id, EventKind.STATUS, f"Queued a {follow} run next.")
            await self._queue.request(run.source_id, follow, run.requested_by)

    async def _work(self, run: RunView, lease: Lease) -> RunKind | None:
        match run.kind:
            case RunKind.REFRESH:
                return await self._refresh(run, lease)
            case RunKind.ONBOARD:
                source = await self._catalog.source(run.source_id)
                return await self._build(run, lease, tasks.Onboard(url=source.url))
            case RunKind.HEAL:
                return await self._build(run, lease, await self._heal_task(run))
            case RunKind.RELOGIN:
                source = await self._catalog.source(run.source_id)
                return await self._build(run, lease, tasks.Relogin(url=source.url))
            case RunKind.REVIEW:
                return await self._review(run, lease)
            case RunKind.TRIAL:
                return await self._trial(run)
            case _:
                assert_never(run.kind)

    async def _refresh(self, run: RunView, lease: Lease) -> RunKind | None:
        source = await self._catalog.source(run.source_id)
        try:
            refreshed = await self._refresher.refresh(lease, source, run)
        except (exceptions.CrawlStalled, exceptions.SiteMissing) as error:
            if not self._last_try(run, error):
                raise
            refreshed = await self._refresher.broken(lease, source, str(error))
        stats: dict[str, JsonValue] = {
            "crawl": refreshed.crawl.model_dump(mode="json"),
            **refreshed.ingested.model_dump(),
            "bronze": refreshed.bronze.model_dump() if refreshed.bronze is not None else None,
        }
        await self._runs.finish(run, RunStatus.SUCCEEDED, refreshed.summary, stats)
        return NEXT.get(refreshed.health.status)

    async def _build(self, run: RunView, lease: Lease, task: tasks.Task) -> RunKind | None:
        standing = await self._versions.standing(run.source_id)
        working = standing.working(run.kind)
        if working is not None:
            await self._healths.set(lease, working)
        outcome = await self._agent.run(lease, task)
        stats: dict[str, JsonValue] = {
            "recordings": [recording.model_dump() for recording in self._agent.recordings(run.id)]
        }
        match outcome:
            case outcomes.ScraperPull(version=version, url=url):
                await self._healths.set(lease, SourceStatus.READY if standing.live else SourceStatus.IN_REVIEW)
                await self._runs.finish(run, RunStatus.SUCCEEDED, f"proposed scraper version {version}: {url}", stats)
                return RunKind.TRIAL
            case outcomes.Restart(commit=commit):
                await self._timeline.log(run.id, EventKind.STATUS, f"Restarting on main at {commit[:12]}.")
                await self._pauses.restart(run.id)
                return None
            case outcomes.FreshSession():
                await self._runs.finish(run, RunStatus.SUCCEEDED, "signed in again and saved the session", stats)
                return RunKind.REFRESH
            case outcomes.Reply():
                await self._healths.set(lease, standing.settled())
                await self._runs.finish(run, RunStatus.SUCCEEDED, "replied without changing the scraper", stats)
                return None
            case outcomes.Rejection(reason=reason):
                await self._healths.set(lease, standing.rejected(run.kind))
                await self._runs.finish(run, RunStatus.FAILED, reason, stats)
                return None
            case _:
                assert_never(outcome)

    async def _trial(self, run: RunView) -> RunKind | None:
        """Crawls the portal live with the newest version, merged or still in review, and keeps every listing on the
        run to read; nothing is stored in the catalog and the source's health is untouched, so a trial needs no lease
        and runs while an agent works on the source."""
        scraper = await self._scrapers.newest(run.source_id)
        try:
            report = await self._refresher.trial(scraper, run)
        except (exceptions.CrawlStalled, exceptions.SiteMissing) as error:
            if not self._last_try(run, error):
                raise
            report = CrawlReport(listings=(), crawl=Crawl.broken(str(error), 0))
        stats: dict[str, JsonValue] = {
            "scraper": scraper.model_dump(mode="json"),
            "crawl": report.crawl.model_dump(mode="json"),
            "listings": [listing.model_dump(mode="json") for listing in report.listings],
            "bronze": report.bronze.model_dump() if report.bronze is not None else None,
        }
        crawl = report.crawl
        summary = f"tried v{scraper.version} ({scraper.state}): {crawl.verdict}, {crawl.valid} listings"
        await self._runs.finish(run, RunStatus.SUCCEEDED, summary, stats)
        if scraper.state == ScraperState.PROPOSED:
            try:
                await self._proposals.report_test(scraper, run.id, report)
            except exceptions.Error as error:
                logger.warning("couldn't comment test %s on pull %s: %s", run.id, scraper.pull, error)
        await self._repairs.after_trial(run, scraper, crawl)
        return None

    @staticmethod
    def _last_try(run: RunView, error: Exception) -> bool:
        """Whether a crawl that failed this way is the scraper's to fix now: a spider that won't load always is; a
        stalled crawl only on its last attempt, as earlier ones carry on in the next (`_carry`)."""
        return not isinstance(error, exceptions.CrawlStalled) or run.attempts + 1 >= MOST_ATTEMPTS

    async def _review(self, run: RunView, lease: Lease) -> RunKind | None:
        """Registers a merged scraper and refreshes from it, or hands a closed one to a person."""
        proposal = await self._versions.proposed(run.source_id)
        if proposal is None:
            await self._runs.finish(run, RunStatus.SKIPPED, "no scraper is waiting for review")
            return None
        pull = await self._pulls.get(proposal.pull)
        if pull.merged and pull.merge_commit_sha is not None:
            return await self._merged(run, lease, proposal, pull.merge_commit_sha)
        if pull.state == "closed":
            await self._versions.closed(lease, proposal)
            standing = await self._versions.standing(run.source_id)
            await self._healths.set(lease, standing.settled())
            await self._runs.finish(run, RunStatus.SUCCEEDED, f"{pull.html_url} was closed without merging")
            return None
        await self._runs.finish(run, RunStatus.SKIPPED, f"{pull.html_url} is still open")
        return None

    async def _merged(self, run: RunView, lease: Lease, proposal: Proposal, commit: str) -> RunKind:
        await self._versions.merged(lease, proposal, commit)
        await self._healths.set(lease, SourceStatus.READY)
        await self._runs.finish(
            run, RunStatus.SUCCEEDED, f"registered scraper version {proposal.version} at {commit[:12]}"
        )
        return RunKind.REFRESH

    async def _heal_task(self, run: RunView) -> tasks.Heal:
        """What the agent is told about the breakage: the newest crawl of the live version, a refresh's or a test's."""
        source = await self._catalog.source(run.source_id)
        crawls = [await self._history.last(source.id, kind) for kind in (RunKind.REFRESH, RunKind.TRIAL)]
        finished = [last for last in crawls if last is not None and last.stats and "crawl" in last.stats]
        if not finished:
            raise exceptions.NoScraper(source.id)
        newest = max(finished, key=lambda last: last.finished_at or last.created_at)
        assert newest.stats is not None
        crawl = Crawl.model_validate(newest.stats["crawl"])
        return tasks.Heal(url=source.url, scraper=await self._versions.current(source.id), crawl=crawl)


class Schedule:
    """What the clock starts: a scrape of every healthy source every few hours, and every few minutes, the agent
    jobs stranded waiting on a source nothing holds and the pull requests decided on GitHub."""

    def __init__(
        self, runs: Runs, leases: Leases, queue: Queue, catalog: Catalog, versions: Versions, pulls: GitHubPulls
    ) -> None:
        self._runs = runs
        self._leases = leases
        self._queue = queue
        self._catalog = catalog
        self._versions = versions
        self._pulls = pulls

    async def refresh_ready(self) -> int:
        """Fails runs whose worker is gone, then queues a refresh of every source whose scraper is healthy; the
        schedule calls this."""
        await self._queue.revive()
        ready = [source for source in await self._catalog.sources() if source.status == SourceStatus.READY]
        for source in ready:
            await self._queue.request(source.id, RunKind.REFRESH, None)
        return len(ready)

    async def review_pending(self) -> int:
        """Carries on crawls that stopped beating, wakes agent jobs left deferred on a source nothing holds, then
        queues a review of every source whose pull request was merged or closed, on GitHub or here; the schedule
        calls this every few minutes."""
        await self._queue.revive()
        for source_id in await self._leases.stranded():
            await self._queue.wake(source_id)
        decided = 0
        for source in await self._catalog.sources():
            proposal = await self._versions.proposed(source.id)
            if proposal is None:
                continue
            if (await self._pulls.get(proposal.pull)).state == "closed":
                await self._queue.request(source.id, RunKind.REVIEW, None)
                decided += 1
        return decided


def register(registry: svcs.Registry) -> None:
    """Every service except the dispatcher, which each process registers for where it carries out runs."""
    db.register(registry)
    http = httpx.AsyncClient(timeout=60)
    registry.register_value(httpx.AsyncClient, http, on_registry_close=http.aclose)
    kernel = AsyncKernel(api_key=settings.kernel_api_key.get_secret_value())
    registry.register_value(AsyncKernel, kernel, on_registry_close=kernel.close)
    knock = AsyncKnock(api_key=settings.knock_secret_key.get_secret_value())
    registry.register_value(AsyncKnock, knock, on_registry_close=knock.close)
    registry.register_value(Settings, settings)
    registry.register_value(PasswordHasher, PasswordHasher())
    registry.register_value(Fernet, Fernet(settings.credentials_key.get_secret_value()))
    registry.register_value(Notifier, KnockNotifier(knock, settings))
    match settings.files_storage:
        case "folder":
            registry.register_value(Bucket, FolderBucket(settings))
        case "r2":
            registry.register_value(Bucket, R2Bucket(settings))
    git = Git(settings)
    mirror = Mirror(git, settings)
    registry.register_value(Mirror, mirror)
    registry.register_value(CrawlState, CrawlFolders(settings.workspaces_dir / "crawls"))
    registry.register_value(Clones, Clones(git, mirror, settings))
    services = (
        Users, Logins, Catalog, Timeline, Leases, Runs, Attempts, Pauses, RunHistory, RunChanges, RunFeed, HelpQueue,
        HelpDesk, Browsers, Crawls, Credentials, Accounts, Workspaces, Narrator, Inbox, Codex, Versions, ScraperHistory,
        Healths, GitHubPulls, Proposals, Agent, Refresher, Opportunities, Tracker, Imports, Learnings, Bronze,
        Recordings, Repairs,
    )  # fmt: skip
    for service in services:
        registry.register_factory(service, svcs.aautowire(service))
    registry.register_factory(BrowserApi, svcs.aautowire(KernelBrowserApi))


def register_queue(registry: svcs.Registry) -> None:
    """The queue and the jobs it feeds, for processes that registered a dispatcher."""
    registry.register_factory(Queue, svcs.aautowire(Queue))
    registry.register_factory(Jobs, svcs.aautowire(Jobs))
    registry.register_factory(Schedule, svcs.aautowire(Schedule))
    registry.register_factory(Reviews, svcs.aautowire(Reviews))
    registry.register_factory(RunChat, svcs.aautowire(RunChat))
