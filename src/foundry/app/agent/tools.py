"""The foundry MCP server Codex starts for one run (`python -m foundry.app.agent.tools`). Its tools act for that run
only: the run, source, fence, live view and workspace come from FOUNDRY_TOOLS, set by `Codex.work`."""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import assert_never

import svcs
from mcp.server.mcpserver import Context, MCPServer
from pydantic import ValidationError

from foundry.app import app as foundry
from foundry.app import exceptions
from foundry.app.agent.agent import Workspace
from foundry.app.agent.models import handoff
from foundry.app.browser.browser import Accounts, Credentials
from foundry.app.browser.models import Account, NewCredential
from foundry.app.core.models import Lease
from foundry.app.db.models.access import CredentialKind
from foundry.app.help import models as help_models
from foundry.app.help.help import HelpDesk
from foundry.app.learnings.learnings import Learnings
from foundry.app.learnings.models import NewLearning
from foundry.app.pulls.pulls import Proposals
from foundry.app.repo.client import GitHubPulls
from foundry.app.repo.repo import Clones, Mirror
from foundry.app.scrapers.models.crawl import CrawlReport
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
        f"fixture replay verdict: {crawl.verdict} (this checks parsing, not that the scraper finds everything)",
        f"valid listings: {crawl.valid}, invalid: {crawl.invalid}, crashed: {crawl.crashed}",
        *(f"error: {error}" for error in crawl.errors),
        *(listing.model_dump_json(indent=2) for listing in report.listings[:LISTINGS_SHOWN]),
    ]
    return "\n".join(lines)


@server.tool()
async def request_help(reason: str, ctx: Context[svcs.Registry]) -> str:
    """Ask a person for something and wait for it (up to 15 minutes): an answer, which they give in the run's chat, or
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
            return (
                "Nobody answered in time. Don't ask again: end your turn now with a summary of where you got stuck. "
                "Ending frees your browser; a person's message in the chat carries this run on."
            )
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
async def submit(
    site: str,
    title: str,
    summary: str,
    method: handoff.Method,
    access: str,
    process: list[str],
    checks: list[str],
    gaps: list[str],
    ctx: Context[svcs.Registry],
) -> str:
    """Hand in your scraper for `site` when `check_scraper` passes. Foundry fills the pull request's template from
    what you give here, so write it for a reviewer who has never seen the portal:
    - `title`: the buyer and portal, such as "Add City of Paso Robles (DemandStar)".
    - `summary`: what the scraper reads, in two or three sentences.
    - `method`: `api`, `html` or `browser`, whichever the scraper uses.
    - `access`: public, or how it signs in and with which saved account, and why it needs to; what each endpoint
      needs (the headers and cookies `needs` found, and where they come from); and the pace it runs at.
    - `process`: the steps that found the data, in order, naming each endpoint or page and what it returns.
    - `checks`: what you compared against the portal itself, such as "the portal lists 12 open bids; the crawl
      found 12" or "due dates match the times the page shows".
    - `gaps`: what the scraper can't get yet and why (empty if nothing).
    After this, end your turn: foundry replays every site your branch touches and opens the pull request, and the
    scraper goes into review. A test scrape of the live portal starts at once and comments its results on the pull
    request."""
    async with svcs.Container(ctx.request_context.lifespan_context) as services:
        crawls, proposals = await services.aget(Crawls, Proposals)
        try:
            report = await crawls.replay(WORKSPACE.checkout, site)
        except exceptions.Error as error:
            return str(error)
    if not proposals.passed(report):
        return f"Not submitted: the replay must be healthy with listings.\n{described(report)}"
    submission = handoff.Submission(
        site=site,
        title=title,
        summary=summary,
        method=method,
        access=access,
        process=tuple(process),
        checks=tuple(checks),
        gaps=tuple(gaps),
    )
    WORKSPACE.submissions.save(submission)
    return (
        "Submitted. After your turn foundry replays your branch, crawls the live portal from it and opens or updates "
        "the pull request; the result shows in this run's chat. End your turn now with a short summary."
    )


@server.tool()
async def propose_change(
    title: str, summary: str, blocker: str, testing: list[str], ctx: Context[svcs.Registry]
) -> str:
    """Open a pull request with everything your branch changes, for a change to foundry itself: your tools, prompts,
    the browser or agent code, or the shared core. `summary` says what changes, `blocker` what stopped you without
    it, and `testing` how you checked it (commands you ran and what they showed). A person reviews it. Ask them to
    merge it with `request_help` (include the link), and once it is merged call `restart` to carry on with the new
    code."""
    change = handoff.Change(title=title, summary=summary, blocker=blocker, testing=tuple(testing))
    async with svcs.Container(ctx.request_context.lifespan_context) as services:
        clones, pulls, proposals = await services.aget(Clones, GitHubPulls, Proposals)
        await clones.commit(WORKSPACE.checkout, title)
        branch = await clones.push(WORKSPACE.checkout)
        pull = await pulls.open(branch, title, proposals.describe_change(CONTEXT.run_id, change))
    WORKSPACE.change_pulls.keep(handoff.ChangePull(number=pull.number, url=pull.html_url))
    return f"Opened {pull.html_url}."


@server.tool()
async def restart(ctx: Context[svcs.Registry]) -> str:
    """Restart this run on the latest `main`, so changes to foundry merged since it started take effect. You keep
    this conversation, your checkout (moved onto the new `main`, with uncommitted work kept) and the portal's
    browser profile; the browser itself is a new one. After this, end your turn."""
    async with svcs.Container(ctx.request_context.lifespan_context) as services:
        clones, mirror, pulls, settings = await services.aget(Clones, Mirror, GitHubPulls, Settings)
        change = WORKSPACE.change_pulls.read()
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


@server.tool()
async def credentials(ctx: Context[svcs.Registry]) -> str:
    """The portal's other saved credentials (API keys, tokens, second logins), with their values. Your spider reads
    them as `self.credentials["<name>"]` on live crawls."""
    async with svcs.Container(ctx.request_context.lifespan_context) as services:
        saved = await (await services.aget(Credentials)).values(CONTEXT.source_id)
    if not saved:
        return "This portal has no other saved credentials."
    return "\n".join(f"{name}: {value}" for name, value in saved.items())


@server.tool()
async def save_credential(name: str, kind: CredentialKind, value: str, note: str, ctx: Context[svcs.Registry]) -> str:
    """Save a secret the scraper signs in with beyond the login: an API key, a bearer token, a second account's
    password. `name` is lowercase with underscores, such as `api_key`; saving a name again replaces it. `note` says
    what it is and where it came from. Live crawls hand it to the spider as `self.credentials[name]`; replays don't,
    so the spider must parse fixtures without it. People see it, masked, on the scraper's Access tab."""
    try:
        credential = NewCredential(name=name, kind=kind, value=value, note=note)
    except ValidationError as error:
        return f"Not saved: {error.errors()[0]['msg']}"
    async with svcs.Container(ctx.request_context.lifespan_context) as services:
        await (await services.aget(Credentials)).save(CONTEXT.source_id, credential)
    return f"Saved {name}."


if __name__ == "__main__":
    server.run("stdio")


@server.tool()
async def remember(name: str, about: str, description: str, body: str, ctx: Context[svcs.Registry]) -> str:
    """Save something you learned for every agent after you, as the `learnings` skill says. `name` is a short slug
    (`planetbids-rate-limit`); saving a name again replaces that learning, so update one rather than add a near copy.
    `about` is what it applies to: `platform:<name>`, `buyer:<buyer folder>`, or `general`. `description` is the one
    line the index shows; `body` the learning itself, with why it holds and how to apply it."""
    try:
        learning = NewLearning(name=name, about=about, description=description, body=body)
    except ValidationError as error:
        return f"Not saved: {error}"
    async with svcs.Container(ctx.request_context.lifespan_context) as services:
        await (await services.aget(Learnings)).remember(CONTEXT.run_id, learning)
    return f"Saved `{name}`. Every run from now on starts with it in its index."
