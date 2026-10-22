"""The foundry MCP server Codex starts for one run (`python -m foundry.app.agent.tools`). Its tools act for that run
only: the run, source, fence, live view and workspace come from FOUNDRY_TOOLS, set by `Codex.work`."""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import assert_never

import svcs
from mcp.server.mcpserver import Context, MCPServer

from foundry.app import app as foundry
from foundry.app import exceptions
from foundry.app.agent.agent import Proposals, Workspace
from foundry.app.agent.models import handoff
from foundry.app.browser.browser import Accounts
from foundry.app.browser.models import Account
from foundry.app.core.models import Lease
from foundry.app.help import models as help_models
from foundry.app.help.help import HelpDesk
from foundry.app.repo.client import GitHubPulls
from foundry.app.repo.repo import Clones, Mirror
from foundry.app.scrapers.models import CrawlReport
from foundry.app.scrapers.scrapers import Crawls
from foundry.app.settings import Settings

CONTEXT = handoff.ToolsContext.model_validate_json(os.environ["FOUNDRY_TOOLS"])
LEASE = Lease(source_id=CONTEXT.source_id, run_id=CONTEXT.run_id, fence=CONTEXT.fence)
WORKSPACE = Workspace(CONTEXT.workspace)
LISTINGS_SHOWN = 3


@asynccontextmanager
async def lifespan(server: MCPServer) -> AsyncGenerator[svcs.Registry, None]:
    async with svcs.Registry() as registry:
        foundry.register(registry)
        yield registry


server = MCPServer("foundry", lifespan=lifespan)


def described(report: CrawlReport) -> str:
    crawl = report.crawl
    lines = [
        f"verdict: {crawl.verdict}",
        f"valid listings: {crawl.valid}, invalid: {crawl.invalid}, crashed: {crawl.crashed}",
        *(f"error: {error}" for error in crawl.errors),
        *(listing.model_dump_json(indent=2) for listing in report.listings[:LISTINGS_SHOWN]),
    ]
    return "\n".join(lines)


@server.tool()
async def request_help(reason: str, ctx: Context[svcs.Registry]) -> str:
    """Ask a person for something and wait for it (up to 45 minutes): an answer, which they give in the run's chat, or
    a hand in your browser, which they take over and give back. Also how you ask them to review and merge a pull
    request you opened. Returns what they said."""
    async with svcs.Container(ctx.request_context.lifespan_context) as services:
        desk = await services.aget(HelpDesk)
        answer = await desk.ask(CONTEXT.run_id, reason, CONTEXT.live_view_url)
    match answer:
        case help_models.HumanHelp(note=note):
            answered = note or "(no note)"
            return f"A person answered: {answered}. If they used your browser, look at the page before going on."
        case help_models.NoAnswer():
            return "Nobody answered in time. Stop, and end with a summary of where you got stuck."
        case _:
            assert_never(answer)


@server.tool()
async def check_scraper(site: str, ctx: Context[svcs.Registry]) -> str:
    """Replay a site's fixtures through its spider in your checkout and report what it yields. `site` is the site
    key: the buyer's folder under src/foundry/buyers plus the portal's name, like `us/tx/austin/financeonline`."""
    async with svcs.Container(ctx.request_context.lifespan_context) as services:
        crawls = await services.aget(Crawls)
        try:
            return described(await crawls.replay(WORKSPACE.checkout, site))
        except exceptions.Error as error:
            return str(error)


@server.tool()
async def submit(site: str, title: str, summary: str, ctx: Context[svcs.Registry]) -> str:
    """Hand in your scraper for `site` when `check_scraper` passes. `title` and `summary` become the pull request's;
    say what the portal is, how it signs in, and what the scraper reads. After this, end your turn: foundry checks
    every site your branch touches, crawls the live portal from your branch, and opens the pull request."""
    async with svcs.Container(ctx.request_context.lifespan_context) as services:
        crawls, proposals = await services.aget(Crawls, Proposals)
        try:
            report = await crawls.replay(WORKSPACE.checkout, site)
        except exceptions.Error as error:
            return str(error)
    if not proposals.passed(report):
        return f"Not submitted: the replay must be healthy with listings.\n{described(report)}"
    WORKSPACE.handoff.submit(handoff.Submission(site=site, title=title, summary=summary))
    return "Submitted. End your turn now with a short summary."


@server.tool()
async def propose_change(title: str, summary: str, ctx: Context[svcs.Registry]) -> str:
    """Open a pull request with everything your branch changes, for a change to foundry itself: your tools, prompts,
    the browser or agent code, or the shared core. A person reviews it. Ask them to merge it with `request_help`
    (include the link), and once it is merged call `restart` to carry on with the new code."""
    async with svcs.Container(ctx.request_context.lifespan_context) as services:
        clones, pulls = await services.aget(Clones, GitHubPulls)
        await clones.commit(WORKSPACE.checkout, title)
        branch = await clones.push(WORKSPACE.checkout)
        pull = await pulls.open(branch, title, f"{summary}\n\nOpened by foundry run `{CONTEXT.run_id}`.")
    WORKSPACE.handoff.keep_pull(handoff.ChangePull(number=pull.number, url=pull.html_url))
    return f"Opened {pull.html_url}."


@server.tool()
async def restart(ctx: Context[svcs.Registry]) -> str:
    """Restart this run on the latest `main`, so changes to foundry merged since it started take effect. You keep
    this conversation, your checkout (moved onto the new `main`, with uncommitted work kept) and the portal's
    browser profile; the browser itself is a new one. After this, end your turn."""
    async with svcs.Container(ctx.request_context.lifespan_context) as services:
        clones, mirror, pulls, settings = await services.aget(Clones, Mirror, GitHubPulls, Settings)
        change = WORKSPACE.handoff.pull()
        if change is not None and not (await pulls.get(change.number)).merged:
            return f"{change.url} is not merged yet. Ask a person to merge it with `request_help`, then try again."
        if change is None and await clones.ahead(WORKSPACE.checkout):
            return "Your branch has commits main doesn't. Open a pull request with `propose_change` first."
        main = await mirror.main()
    if main == settings.deployed_commit:
        return str(exceptions.NothingToDeploy(main))
    try:
        WORKSPACE.restarts.ask(main)
    except exceptions.TooManyRestarts as error:
        return str(error)
    return f"Restarting on {main[:12]}. End your turn now with one line on what you'll do next."


@server.tool()
async def account(ctx: Context[svcs.Registry]) -> str:
    """The login this portal already has, if any."""
    async with svcs.Container(ctx.request_context.lifespan_context) as services:
        accounts = await services.aget(Accounts)
        login = await accounts.account(CONTEXT.source_id)
    if login is None:
        return "This portal has no saved login yet."
    return f"username: {login.username}\npassword: {login.password}"


@server.tool()
async def save_account(username: str, password: str, ctx: Context[svcs.Registry]) -> str:
    """Save the login you made on this portal, so later runs can sign in again."""
    async with svcs.Container(ctx.request_context.lifespan_context) as services:
        accounts = await services.aget(Accounts)
        await accounts.save_account(LEASE, Account(username=username, password=password))
    return "Saved."


if __name__ == "__main__":
    server.run("stdio")
