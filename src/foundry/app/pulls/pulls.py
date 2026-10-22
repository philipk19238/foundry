"""Pull requests, the way scrapers reach `main`: an agent's submission becomes one (Proposals), people approve
one to make it live (Reviews), and pull requests people opened themselves are registered like an agent's (Imports).
Each follows a template in `templates/`."""

import logging
import uuid
from pathlib import Path

import jinja2

from foundry.app import exceptions
from foundry.app.agent.models import handoff, outcomes
from foundry.app.browser.browser import Accounts
from foundry.app.catalog.catalog import Catalog
from foundry.app.catalog.models import SourceView
from foundry.app.core.models import Lease
from foundry.app.db.models.runs import RunKind, RunStatus
from foundry.app.repo.client import GitHubPulls
from foundry.app.repo.models import BRANCHES, Pull
from foundry.app.repo.repo import Clones, Mirror
from foundry.app.runs.models import EventKind, RunView
from foundry.app.runs.runs import Leases, Queue, RunHistory, Runs, Timeline
from foundry.app.scrapers.models import CrawlReport, Verdict
from foundry.app.scrapers.scrapers import Crawls, Healths, Versions
from foundry.app.settings import Settings
from foundry.core.sites import Sites

logger = logging.getLogger(__name__)

SAMPLES = 5
PULLS = jinja2.Environment(
    loader=jinja2.FileSystemLoader(Path(__file__).parent / "templates"), trim_blocks=True, lstrip_blocks=True
)


def cell(text: str) -> str:
    """Text that fits in one markdown table cell: on one line, with its pipes escaped."""
    return " ".join(text.split()).replace("|", "\\|")


PULLS.filters.update(cell=cell)


class Proposals:
    """Turns what the agent submitted into a pull request: every site the branch touches must pass its replay, the
    submitted site gets a staging crawl against the live portal from the branch, and both go in the description so the
    reviewer sees the scraper work before merging."""

    def __init__(
        self, clones: Clones, pulls: GitHubPulls, crawls: Crawls, accounts: Accounts, catalog: Catalog,
        versions: Versions, timeline: Timeline, settings: Settings,
    ) -> None:  # fmt: skip
        self._clones = clones
        self._pulls = pulls
        self._crawls = crawls
        self._accounts = accounts
        self._catalog = catalog
        self._versions = versions
        self._timeline = timeline
        self._base_url = settings.base_url

    async def propose(self, lease: Lease, checkout: Path, submission: handoff.Submission) -> outcomes.Outcome:
        await self._clones.commit(checkout, submission.title)
        changed = await self._clones.changed(checkout)
        if not changed:
            return outcomes.Rejection(reason="the branch changes nothing")
        try:
            touched = {site.key for site in Sites(checkout).touched(changed)} | {submission.site}
        except (OSError, ValueError) as error:
            return outcomes.Rejection(reason=f"the buyer registry does not load: {error}")
        replays = {key: await self._crawls.replay(checkout, key) for key in sorted(touched)}
        failed = [key for key, report in replays.items() if not self.passed(report)]
        if failed:
            return outcomes.Rejection(reason=f"the replay fails for {', '.join(failed)}")
        await self._timeline.log(lease.run_id, EventKind.STATUS, f"Replays pass for {', '.join(replays)}.")
        access = await self._accounts.access(lease.source_id)
        staging = await self._crawls.live(checkout, submission.site, access, 0)
        await self._timeline.log(
            lease.run_id, EventKind.STATUS, f"Staging crawl: {staging.crawl.verdict}, {staging.crawl.valid} listings."
        )
        branch = await self._clones.push(checkout)
        source = await self._catalog.source(lease.source_id)
        body = self.describe(lease, source.url, branch, submission, replays, staging)
        pull = await self._pulls.open(branch, submission.title, body)
        version = await self._versions.propose(lease, submission.site, branch, pull.number)
        return outcomes.ScraperPull(version=version, url=pull.html_url)

    def passed(self, report: CrawlReport) -> bool:
        return report.crawl.verdict == Verdict.HEALTHY and report.crawl.valid > 0

    def describe(
        self, lease: Lease, portal: str, branch: str, submission: handoff.Submission, replays: dict[str, CrawlReport],
        staging: CrawlReport,
    ) -> str:  # fmt: skip
        """A scraper's pull request, from the template every one follows: what it does, how it reads the portal, how
        to test it, sample output, gaps, and what to check before merging."""
        return PULLS.get_template("scraper.md").render(
            portal=portal,
            branch=branch,
            run_id=lease.run_id,
            run_url=f"{self._base_url}/runs/{lease.run_id}",
            source_id=lease.source_id,
            source_url=f"{self._base_url}/sources/{lease.source_id}",
            submission=submission,
            replays=replays,
            staging=staging,
            samples=staging.listings[:SAMPLES],
            first=staging.listings[0].model_dump_json(indent=2, exclude_defaults=True) if staging.listings else "",
        )

    def describe_change(self, run_id: uuid.UUID, change: handoff.Change) -> str:
        """A pull request to foundry itself, from its template: what changes, the blocker, and how it was tested."""
        return PULLS.get_template("change.md").render(
            run_id=run_id, run_url=f"{self._base_url}/runs/{run_id}", change=change
        )


class Reviews:
    """A person's verdict on the version in review. Approving merges its pull request on GitHub and queues the check
    that registers it, so the scraper goes live at once instead of at the next scheduled check."""

    def __init__(self, versions: Versions, pulls: GitHubPulls, queue: Queue, history: RunHistory) -> None:
        self._versions = versions
        self._pulls = pulls
        self._queue = queue
        self._history = history

    async def approve(self, source_id: uuid.UUID, requested_by: uuid.UUID) -> RunView:
        """Refuses while an agent works on the source, so a half-finished branch is never merged."""
        agent = await self._history.last_agent(source_id)
        if agent is not None and agent.is_active:
            raise exceptions.AgentWorking(source_id)
        proposal = await self._versions.proposed(source_id)
        if proposal is None:
            raise exceptions.NothingToMerge(source_id)
        if not (await self._pulls.get(proposal.pull)).merged:
            await self._pulls.merge(proposal.pull)
        return await self._queue.request(source_id, RunKind.REVIEW, requested_by)


class Imports:
    """Pull requests people open themselves, registered the way an agent's are: each site a pull request adds or
    changes becomes a version in review on its portal's source, made if the portal is new, so it is test crawled,
    read and approved on the scraper's page. Agents' branches register themselves when they submit. The schedule
    imports every open pull request; `register` imports one at once."""

    def __init__(
        self, pulls: GitHubPulls, mirror: Mirror, catalog: Catalog, runs: Runs, leases: Leases, versions: Versions,
        healths: Healths,
    ) -> None:  # fmt: skip
        self._pulls = pulls
        self._mirror = mirror
        self._catalog = catalog
        self._runs = runs
        self._leases = leases
        self._versions = versions
        self._healths = healths

    async def sync(self) -> int:
        imported = 0
        for pull in await self._pulls.open_pulls():
            if pull.head.ref.startswith(BRANCHES):
                continue
            try:
                imported += len(await self.register(pull.number))
            except exceptions.Error as error:
                logger.info("skipped pull request #%s: %s", pull.number, error)
        return imported

    async def register(self, number: int) -> list[SourceView]:
        """The sources the pull request now has a version in review on; a site registered before is left alone."""
        pull = await self._pulls.get(number)
        if pull.state != "open":
            raise exceptions.NotImportable(number, "it is not open")
        if pull.head.ref.startswith(BRANCHES):
            raise exceptions.NotImportable(number, "an agent opened it, and agents register their own")
        changed = [file.filename for file in await self._pulls.files(number)]
        await self._mirror.fetch()
        async with self._mirror.tree(pull.head.sha) as tree:
            try:
                sites = Sites(tree).touched(changed)
            except (OSError, ValueError) as error:
                raise exceptions.NotImportable(number, f"its buyer registry does not load: {error}") from error
        if not sites:
            raise exceptions.NotImportable(number, "it changes no scraper")
        sources = []
        for site in sites:
            source = await self._catalog.find(site.url) or await self._catalog.add_source(site.url)
            if await self._versions.of_pull(source.id, number) is None:
                await self._propose(source, site.key, pull)
            sources.append(source)
        return sources

    async def _propose(self, source: SourceView, site: str, pull: Pull) -> None:
        run = await self._runs.add(source.id, RunKind.REVIEW, None)
        await self._runs.start(run.id)

        async def propose(lease: Lease) -> int:
            version = await self._versions.propose(lease, site, pull.head.ref, pull.number)
            await self._healths.set(lease, (await self._versions.standing(source.id)).settled())
            return version

        version = await self._leases.hold(source.id, run.id, propose)
        await self._runs.finish(run.id, RunStatus.SUCCEEDED, f"registered {pull.html_url} as version {version}")
