import asyncio
import contextlib
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import assert_never

from openai_codex import ApprovalMode, AsyncCodex, AsyncTurnHandle, CodexConfig, Sandbox
from openai_codex.errors import CodexError
from openai_codex.generated.v2_all import (
    AgentMessageThreadItem,
    CommandExecutionThreadItem,
    FileChangeThreadItem,
    ItemCompletedNotification,
    McpToolCallThreadItem,
    ReasoningEffort,
    TurnCompletedNotification,
)

from foundry.app import exceptions
from foundry.app.agent.models import handoff, outcomes, tasks
from foundry.app.auth.auth import Users
from foundry.app.browser.browser import Accounts, Browsers
from foundry.app.browser.models import Browser
from foundry.app.catalog.catalog import Catalog
from foundry.app.core.models import Lease
from foundry.app.help.models import Notifier
from foundry.app.repo.client import GitHubPulls
from foundry.app.repo.models import BRANCHES
from foundry.app.repo.repo import CHECKOUT, Clones
from foundry.app.runs.models import EventKind
from foundry.app.runs.runs import Timeline
from foundry.app.scrapers.models import CrawlReport, Verdict
from foundry.app.scrapers.scrapers import Crawls, Versions
from foundry.app.settings import Settings
from foundry.core.sites import Sites

logger = logging.getLogger(__name__)

KERNEL_MCP = "https://mcp.onkernel.com/mcp"
TOOL_SECONDS = 3000
INBOX_SECONDS = 3
MAX_RESTARTS = 3
SAMPLES = 5


class Narrator:
    """Puts what the agent says, runs, edits and calls on its run's timeline. What it says also goes to the run's
    chat, and people are emailed a link to it; a failed email is logged, not fatal."""

    def __init__(self, timeline: Timeline, notifier: Notifier, users: Users, settings: Settings) -> None:
        self._timeline = timeline
        self._notifier = notifier
        self._users = users
        self._base_url = settings.base_url

    async def tell(self, run_id: uuid.UUID, completed: ItemCompletedNotification) -> None:
        match completed.item.root:
            case AgentMessageThreadItem(text=text):
                await self._timeline.log(run_id, EventKind.MESSAGE, text)
                try:
                    await self._notifier.agent_said(
                        run_id, await self._users.all(), text, f"{self._base_url}/runs/{run_id}"
                    )
                except exceptions.NotifyFailed as error:
                    logger.warning("run %s: %s", run_id, error)
            case CommandExecutionThreadItem(command=command, exit_code=code):
                await self._timeline.log(run_id, EventKind.COMMAND, f"$ {command}  (exit {code})")
            case McpToolCallThreadItem(server=server, tool=tool, error=error):
                status = f"failed: {error}" if error is not None else "ok"
                await self._timeline.log(run_id, EventKind.TOOL, f"{server}.{tool}: {status}")
            case FileChangeThreadItem(changes=changes):
                paths = ", ".join(sorted({change.path for change in changes}))
                await self._timeline.log(run_id, EventKind.FILE, f"edited {paths}")
            case _:
                return


class Handoff:
    """What the agent's tools leave for its run: the scraper it submitted, and the pull request it opened for a change
    to foundry itself."""

    def __init__(self, folder: Path) -> None:
        self._submission = folder / "submission.json"
        self._pull = folder / "pull.json"

    def submission(self) -> handoff.Submission | None:
        if not self._submission.exists():
            return None
        return handoff.Submission.model_validate_json(self._submission.read_text())

    def submit(self, submission: handoff.Submission) -> None:
        self._submission.write_text(submission.model_dump_json())

    def pull(self) -> handoff.ChangePull | None:
        return handoff.ChangePull.model_validate_json(self._pull.read_text()) if self._pull.exists() else None

    def keep_pull(self, pull: handoff.ChangePull) -> None:
        self._pull.write_text(pull.model_dump_json())

    def forget_pull(self) -> None:
        self._pull.unlink(missing_ok=True)


class Restarts:
    """A restart the agent asked for, and how many the run has had (at most MAX_RESTARTS)."""

    def __init__(self, folder: Path) -> None:
        self._asked = folder / "restart.txt"
        self._count = folder / "restarts.txt"

    def asked(self) -> str | None:
        """The commit the agent asked to restart on, if it asked."""
        return self._asked.read_text() if self._asked.exists() else None

    def ask(self, commit: str) -> None:
        if self.count() >= MAX_RESTARTS:
            raise exceptions.TooManyRestarts(MAX_RESTARTS)
        self._asked.write_text(commit)

    def came_back(self) -> None:
        """The run is back on the new deployment: the request is spent and counted."""
        self._asked.unlink(missing_ok=True)
        self._count.write_text(str(self.count() + 1))

    def count(self) -> int:
        return int(self._count.read_text()) if self._count.exists() else 0


class Workspace:
    """One run's folder, which outlives a restart: its clone of the repo, Codex's home with the thread, and the notes
    its tools leave."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.checkout = folder / CHECKOUT
        self.home = folder / ".codex"
        self.handoff = Handoff(folder)
        self.restarts = Restarts(folder)
        self._thread = folder / "thread.txt"
        self._read = folder / "chat.txt"

    @property
    def branch(self) -> str:
        return f"{BRANCHES}{self.folder.name}"

    def thread(self) -> str | None:
        return self._thread.read_text() if self._thread.exists() else None

    def keep_thread(self, thread_id: str) -> None:
        self._thread.write_text(thread_id)

    def read_up_to(self) -> int:
        """The last chat line handed to the agent."""
        return int(self._read.read_text()) if self._read.exists() else 0

    def read(self, event_id: int) -> None:
        self._read.write_text(str(event_id))


class Workspaces:
    """Each run's workspace, cloned on its own branch the first time and found again when the run resumes."""

    def __init__(self, settings: Settings, clones: Clones) -> None:
        self._root = settings.workspaces_dir.resolve()
        self._clones = clones

    def of(self, run_id: uuid.UUID) -> Workspace:
        return Workspace(self._root / str(run_id))

    async def prepare(self, lease: Lease) -> tuple[Workspace, handoff.Start]:
        workspace = self.of(lease.run_id)
        start = handoff.Start.FRESH if workspace.thread() is None else handoff.Start.REOPENED
        workspace.folder.mkdir(parents=True, exist_ok=True)
        workspace.home.mkdir(exist_ok=True)
        await self._clones.clone(workspace.folder, workspace.branch)
        if workspace.restarts.asked() is not None:
            await self._clones.onto_main(workspace.checkout)
            workspace.handoff.forget_pull()
            workspace.restarts.came_back()
            start = handoff.Start.RESTARTED
        return workspace, start


class Inbox:
    """Hands what people write in a run's chat to the agent while its turn runs, each line once, even across a
    restart (the workspace keeps the last line handed over)."""

    def __init__(self, timeline: Timeline) -> None:
        self._timeline = timeline

    async def deliver(self, run_id: uuid.UUID, workspace: Workspace, turn: AsyncTurnHandle) -> None:
        while True:
            for line in await self._timeline.from_people(run_id, workspace.read_up_to()):
                await turn.steer(f"{line.author} wrote in this run's chat: {line.text}")
                workspace.read(line.id)
            await asyncio.sleep(INBOX_SECONDS)


class Codex:
    """One Codex thread per run, in the run's checkout, with Kernel's MCP tools and our own. A run that restarted
    goes on in the same thread. People's chat messages reach the running turn as they arrive."""

    def __init__(self, settings: Settings, narrator: Narrator, inbox: Inbox) -> None:
        self._settings = settings
        self._narrator = narrator
        self._inbox = inbox

    async def work(self, browser: Browser, context: handoff.ToolsContext, workspace: Workspace, prompt: str) -> None:
        instructions = (
            (Path(__file__).parent / "prompts" / "agent.md")
            .read_text()
            .format(session_id=browser.session_id, cdp_url=browser.cdp_url)
        )
        overrides = (
            f"mcp_servers.kernel.url={json.dumps(KERNEL_MCP)}",
            'mcp_servers.kernel.bearer_token_env_var="KERNEL_API_KEY"',
            'mcp_servers.kernel.default_tools_approval_mode="approve"',
            f"mcp_servers.foundry.command={json.dumps(sys.executable)}",
            'mcp_servers.foundry.args=["-m", "foundry.app.agent.tools"]',
            f"mcp_servers.foundry.cwd={json.dumps(str(Path.cwd()))}",
            f"mcp_servers.foundry.env={{FOUNDRY_TOOLS={json.dumps(context.model_dump_json())}}}",
            f"mcp_servers.foundry.env_vars={json.dumps(self._forwarded())}",
            f"mcp_servers.foundry.tool_timeout_sec={TOOL_SECONDS}",
            "mcp_servers.foundry.startup_timeout_sec=60",
            'mcp_servers.foundry.default_tools_approval_mode="approve"',
            "sandbox_workspace_write.network_access=true",
            'shell_environment_policy.inherit="core"',
        )
        environment = {
            **os.environ,
            "CODEX_HOME": str(workspace.home),
            "KERNEL_API_KEY": self._settings.kernel_api_key.get_secret_value(),
        }
        cwd = str(workspace.checkout)
        config = CodexConfig(config_overrides=overrides, cwd=cwd, env=environment)
        async with AsyncCodex(config) as codex:
            await codex.login_api_key(self._settings.openai_api_key.get_secret_value())
            kept = workspace.thread()
            if kept is None:
                thread = await codex.thread_start(
                    approval_mode=ApprovalMode.deny_all,
                    cwd=cwd,
                    developer_instructions=instructions,
                    model=self._settings.codex_model,
                    sandbox=Sandbox.workspace_write,
                )
                workspace.keep_thread(thread.id)
            else:
                thread = await codex.thread_resume(
                    kept,
                    approval_mode=ApprovalMode.deny_all,
                    cwd=cwd,
                    developer_instructions=instructions,
                    model=self._settings.codex_model,
                    sandbox=Sandbox.workspace_write,
                )
            turn = await thread.turn(prompt, effort=ReasoningEffort(self._settings.codex_effort))
            delivery = asyncio.create_task(self._inbox.deliver(context.run_id, workspace, turn))
            try:
                async for event in turn.stream():
                    match event.payload:
                        case ItemCompletedNotification():
                            await self._narrator.tell(context.run_id, event.payload)
                        case TurnCompletedNotification(turn=done) if done.error is not None:
                            raise exceptions.AgentFailed(done.error.message)
                        case _:
                            pass
            finally:
                delivery.cancel()
                with contextlib.suppress(asyncio.CancelledError, CodexError):
                    await delivery

    def _forwarded(self) -> list[str]:
        names = [name.upper() for name in Settings.model_fields]
        return [name for name in names if name in os.environ]


class Proposals:
    """Turns what the agent submitted into a pull request: every site the branch touches must pass its replay, the
    submitted site gets a staging crawl against the live portal from the branch, and both go in the description so the
    reviewer sees the scraper work before merging."""

    def __init__(
        self, clones: Clones, pulls: GitHubPulls, crawls: Crawls, accounts: Accounts, catalog: Catalog,
        versions: Versions, timeline: Timeline,
    ) -> None:  # fmt: skip
        self._clones = clones
        self._pulls = pulls
        self._crawls = crawls
        self._accounts = accounts
        self._catalog = catalog
        self._versions = versions
        self._timeline = timeline

    async def propose(self, lease: Lease, workspace: Workspace, submission: handoff.Submission) -> outcomes.Outcome:
        checkout = workspace.checkout
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
        session = await self._accounts.session(lease.source_id)
        staging = await self._crawls.live(checkout, submission.site, session, 0)
        await self._timeline.log(
            lease.run_id, EventKind.STATUS, f"Staging crawl: {staging.crawl.verdict}, {staging.crawl.valid} listings."
        )
        branch = await self._clones.push(checkout)
        source = await self._catalog.source(lease.source_id)
        body = self.describe(lease, source.url, submission, replays, staging)
        pull = await self._pulls.open(branch, submission.title, body)
        version = await self._versions.propose(lease, submission.site, branch, pull.number)
        return outcomes.ScraperPull(version=version, url=pull.html_url)

    def passed(self, report: CrawlReport) -> bool:
        return report.crawl.verdict == Verdict.HEALTHY and report.crawl.valid > 0

    def describe(
        self, lease: Lease, url: str, submission: handoff.Submission, replays: dict[str, CrawlReport],
        staging: CrawlReport,
    ) -> str:  # fmt: skip
        crawl = staging.crawl
        lines = [
            f"Opened by foundry run `{lease.run_id}` for {url}.",
            "",
            submission.summary,
            "",
            "### Replay of the captured fixtures",
            *(f"- `{key}`: {report.crawl.valid} listings" for key, report in replays.items()),
            "",
            f"### Staging crawl of `{submission.site}` from this branch, against the live portal",
            f"{crawl.verdict}: {crawl.valid} listings, {crawl.invalid} invalid, crashed {crawl.crashed}",
            *(f"- error: {error}" for error in crawl.errors),
            "",
            "| Title | Due | Files |",
            "|---|---|---|",
            *(
                f"| [{listing.title}]({listing.url}) | {listing.due_at or ''} | {len(listing.files)} |"
                for listing in staging.listings[:SAMPLES]
            ),
            "",
            "Merging registers this scraper; foundry picks the merge up and refreshes the source from `main`.",
        ]
        return "\n".join(lines)


class Agent:
    """Carries out one agent task on a source, in a Kernel browser on the source's profile, and turns what the agent
    hands in into a pull request, a fresh session, or a restart on newly merged code."""

    def __init__(
        self, browsers: Browsers, accounts: Accounts, workspaces: Workspaces, codex: Codex, proposals: Proposals,
        timeline: Timeline, settings: Settings,
    ) -> None:  # fmt: skip
        self._browsers = browsers
        self._accounts = accounts
        self._workspaces = workspaces
        self._codex = codex
        self._proposals = proposals
        self._timeline = timeline
        self._commit = settings.deployed_commit or "(local)"

    def prompt(self, task: tasks.Task, start: handoff.Start, browser: Browser) -> str:
        """What begins the turn: the task itself the first time; after a restart or a reopen, where the run stands."""
        match start:
            case handoff.Start.FRESH:
                return task.prompt
            case handoff.Start.RESTARTED | handoff.Start.REOPENED:
                text = (tasks.PROMPTS / f"{start}.md").read_text()
                return text.format(commit=self._commit, session_id=browser.session_id)
            case _:
                assert_never(start)

    async def run(self, lease: Lease, task: tasks.Task) -> outcomes.Outcome:
        workspace, start = await self._workspaces.prepare(lease)
        async with self._browsers.opened(lease.source_id) as browser:
            await self._timeline.log(lease.run_id, EventKind.STATUS, "Opened a Kernel browser.")
            context = handoff.ToolsContext(
                run_id=lease.run_id,
                source_id=lease.source_id,
                fence=lease.fence,
                live_view_url=browser.live_view_url,
                workspace=workspace.folder,
            )
            await self._codex.work(browser, context, workspace, self.prompt(task, start, browser))
            await self._accounts.save_session(lease, await self._browsers.session(browser, {}))
        commit = workspace.restarts.asked()
        if commit is not None:
            return outcomes.Restart(commit=commit)
        if isinstance(task, tasks.Relogin):
            return outcomes.FreshSession()
        submission = workspace.handoff.submission()
        if submission is None:
            return outcomes.Rejection(reason="the agent ended without calling submit")
        return await self._proposals.propose(lease, workspace, submission)
