"""Foundry over MCP, at `/mcp`: what the UI does, for an agent. Send `Authorization: Bearer <token>` with an API token
from the API tokens page; every tool acts as that token's person, with the same roles as the UI. Talking to a
scraper's agent works as in its chat: `send_message` reaches it mid-task, answers a question it waits on, or reopens
an ended job, and `wait_for_run` returns as soon as it writes back. A client on the 2026-07-28 protocol can instead
read a job as a resource and `subscriptions/listen` to it: it hears when the job, its chat or the open questions
change (`server/changes`)."""

import asyncio
import contextvars
import functools
import uuid
from collections.abc import Awaitable, Callable
from typing import Literal, ParamSpec, TypeVar

import svcs
import svcs.fastapi
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

from foundry.app import exceptions
from foundry.app.app import Imports, Reviews
from foundry.app.auth.models import UserView
from foundry.app.bronze.bronze import Bronze
from foundry.app.bronze.models import BronzeLink, RecordingLink
from foundry.app.catalog.catalog import Catalog, Opportunities
from foundry.app.catalog.models import OpportunityRow
from foundry.app.db.models.runs import RunKind
from foundry.app.files.models import Bucket
from foundry.app.help.help import HelpQueue, RunChat
from foundry.app.help.models import HelpView
from foundry.app.progress.models import TrialView
from foundry.app.progress.progress import Tracker
from foundry.app.runs.models import RunView
from foundry.app.runs.runs import Queue, RunHistory, Timeline
from foundry.app.scrapers.scrapers import ScraperHistory
from foundry.app.settings import settings
from foundry.server.changes.changes import ListenerBus
from foundry.server.changes.models import QUESTIONS
from foundry.server.models import RunDetail, RunLines, ScraperSummary, SourceDetail

WAIT_POLL = 0.5
P = ParamSpec("P")
R = TypeVar("R")
CONTAINER: contextvars.ContextVar[svcs.Container] = contextvars.ContextVar("foundry_mcp_container")
Lines = Literal["chat", "everything"]
LISTENERS = ListenerBus()

server = MCPServer(
    "foundry",
    subscriptions=LISTENERS,
    instructions=(
        "Foundry builds scrapers for procurement portals. Each scraper moves through stages: building (an agent "
        "job is writing it; talk to the agent with send_message), needs_you (the agent asked a question), "
        "ready_for_review (test it with test_scrape, read its pull request, then approve or send_message asking for "
        "changes), live (scraped every 6 hours; read its data with list_opportunities), and stopped. Agent jobs "
        "(build, repair, sign in) have an agent you can talk to; scrapes run code only. Follow a job with "
        "wait_for_run and stop once its status is succeeded, failed or skipped. Clients that can listen: read "
        "foundry://runs/{run_id}/chat, then subscriptions/listen to it and to foundry://runs/{run_id}, and read "
        "again on each update; foundry://questions changes when an agent asks or a question is answered. "
        "Everything a portal sent a scrape is kept as bronze: get_bronze links its HAR, get_recordings what an "
        "agent recorded."
    ),
)


def tool(function: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Registers a tool whose refusals reach the caller as foundry words them, the way the UI shows them."""

    @functools.wraps(function)
    async def run(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await function(*args, **kwargs)
        except exceptions.Error as error:
            raise ToolError(str(error)) from error

    server.tool()(run)
    return run


def services() -> svcs.Container:
    return CONTAINER.get()


async def user() -> UserView:
    return await services().aget(UserView)


async def admin() -> UserView:
    person = await user()
    if not person.is_admin:
        raise exceptions.NotAllowed("change scrapers or start jobs")
    return person


@tool
async def list_scrapers() -> list[ScraperSummary]:
    """Every portal with its scraper's stage: building, waiting (needs you), review, live or stopped."""
    catalog, tracker = await services().aget(Catalog, Tracker)
    stages = await tracker.stages()
    return [ScraperSummary(source=source, stage=stages[source.id]) for source in await catalog.sources()]


@tool
async def get_scraper(source_id: uuid.UUID) -> SourceDetail:
    """One portal in full: its stage, the agent job you talk to, its newest version and pull request, every version
    and its recent jobs."""
    progress = await (await services().aget(Tracker)).progress(source_id)
    return SourceDetail(progress=progress, url=f"{settings.base_url}/sources/{source_id}")


@tool
async def build_scraper(url: str) -> SourceDetail:
    """Adds a portal by the URL of its opportunity listing and starts an agent job building its scraper. A portal
    added before is returned as it is."""
    person = await admin()
    catalog, queue = await services().aget(Catalog, Queue)
    source = await catalog.find(url.strip())
    if source is None:
        source = await catalog.add_source(url.strip())
        await queue.request(source.id, RunKind.ONBOARD, person.id)
    return await get_scraper(source.id)


@tool
async def register_pull(number: int) -> list[SourceDetail]:
    """Registers a pull request a person opened (one that adds or changes a buyer.toml and its spider) as a version
    in review on each portal it touches, adding portals that are new. Open pull requests are also picked up every 10
    minutes on their own."""
    await admin()
    sources = await (await services().aget(Imports)).register(number)
    return [await get_scraper(source.id) for source in sources]


@tool
async def test_scrape(source_id: uuid.UUID) -> RunDetail:
    """Scrapes the live portal once with the newest version, merged or in review, and keeps every listing it finds
    on the job without saving any. Wait for it with wait_for_run, then read the listings in get_run's `trial`."""
    person = await admin()
    run = await (await services().aget(Queue)).request(source_id, RunKind.TRIAL, person.id)
    return await get_run(run.id)


@tool
async def approve(source_id: uuid.UUID) -> RunDetail:
    """Approves the version in review: merges its pull request and starts the merge check that makes it live.
    Refused while an agent job is working on the scraper."""
    person = await admin()
    run = await (await services().aget(Reviews)).approve(source_id, person.id)
    return await get_run(run.id)


@tool
async def start_job(
    source_id: uuid.UUID, kind: Literal["refresh", "heal", "relogin", "review", "onboard"]
) -> RunDetail:
    """Starts a job on a portal: refresh (scrape now with the live version and save what is new), heal (an agent
    repairs a broken scraper), relogin (an agent signs in again), review (check the pull request for a merge now),
    or onboard (an agent builds the scraper again from scratch)."""
    person = await admin()
    run = await (await services().aget(Queue)).request(source_id, RunKind(kind), person.id)
    return await get_run(run.id)


@tool
async def list_runs() -> list[RunView]:
    """The most recent jobs across every portal, newest first."""
    return await (await services().aget(RunHistory)).recent()


@tool
async def get_run(run_id: uuid.UUID) -> RunDetail:
    """A job's status and summary, its open questions, the versions its agent built, and, for a test scrape, every
    listing it found."""
    history, help_queue, scrapers = await services().aget(RunHistory, HelpQueue, ScraperHistory)
    run = await history.run(run_id)
    return RunDetail(
        run=run,
        help=await help_queue.of_run(run_id),
        built=await scrapers.built_by(run_id),
        trial=TrialView.of(run),
        url=f"{settings.base_url}/runs/{run_id}",
    )


@tool
async def read_run(run_id: uuid.UUID, after: int = 0, scope: Lines = "chat") -> RunLines:
    """A job's lines after the cursor `after` (0 for all). `chat` is what the agent and people said, and questions;
    `everything` adds every command, edit and tool call. Pass the returned `after` to read on."""
    return await lines(run_id, after, scope)


@tool
async def wait_for_run(run_id: uuid.UUID, after: int, seconds: int = 50, scope: Lines = "chat") -> RunLines:
    """Waits up to `seconds` (at most 120) for something new on a job: a line after `after`, or a change of status.
    It returns as soon as that happens, so call it in a loop to follow a job, passing the returned `after`."""
    history = await services().aget(RunHistory)
    status = (await history.run(run_id)).status
    try:
        async with asyncio.timeout(min(seconds, 120)):
            while True:
                found = await lines(run_id, after, scope)
                if found.lines or found.status != status:
                    return found
                await asyncio.sleep(WAIT_POLL)
    except TimeoutError:
        return await lines(run_id, after, scope)


@tool
async def send_message(run_id: uuid.UUID, text: str) -> RunLines:
    """Writes to an agent job, as its chat does. While the agent works the message reaches it mid-task; while it
    waits on a question the message answers it; once the job has ended the message reopens it, and the agent carries
    on in the same conversation (this is how to ask for changes). Follow up with wait_for_run to hear back."""
    person = await user()
    timeline = await services().aget(Timeline)
    seen = await timeline.since(run_id, 0)
    await (await services().aget(RunChat)).say(run_id, person, text)
    return await lines(run_id, seen[-1].id if seen else 0, "chat")


@tool
async def ask_to_take_over(run_id: uuid.UUID) -> RunDetail:
    """Asks the agent to pause and hand over its browser. Driving it needs a person on the scraper's page."""
    person = await user()
    await (await services().aget(RunChat)).ask_to_take_over(run_id, person)
    return await get_run(run_id)


@tool
async def list_questions() -> list[HelpView]:
    """Questions agents are waiting on right now, across every scraper. Answer one with send_message on its job."""
    return await (await services().aget(HelpQueue)).live()


@tool
async def list_opportunities(
    source_id: uuid.UUID | None = None, words: str = "", page: int = 0
) -> list[OpportunityRow]:
    """The saved opportunities, newest first, each the newest version of one (`snapshots` counts the portal's edits),
    from one portal or all, whose title or buyer holds `words`. 50 a page."""
    return await (await services().aget(Opportunities)).opportunities(source_id, words, max(page, 0))


@tool
async def get_file_link(notice_id: str, file_id: str) -> str:
    """A download link for one of an opportunity's files: a short-lived link to the saved copy, or the portal's own
    URL if the download never worked."""
    opportunities, bucket = await services().aget(Opportunities, Bucket)
    file = await opportunities.file(notice_id, file_id)
    return file.url if file.sha256 is None else await bucket.link(file.sha256, file.name)


@tool
async def get_bronze(run_id: uuid.UUID) -> BronzeLink:
    """Everything the portal sent a scrape (refresh or test scrape), as a short-lived link to a HAR 1.2 file, which
    any HAR viewer opens: every response but the attachments, with what signs in to the portal hidden. A saved
    opportunity's `run_id` names the scrape that stored it."""
    link = await (await services().aget(Bronze)).har(run_id)
    if link is None:
        raise exceptions.NoBronze(f"run {run_id}")
    return link


@tool
async def get_recordings(run_id: uuid.UUID) -> list[RecordingLink]:
    """The browser recordings an agent job made while finding the portal's API (the recorder's JSON lines, or HAR),
    each with a short-lived link, with what signs in to the portal hidden."""
    return await (await services().aget(Bronze)).recordings(run_id)


@tool
async def restart_scrape(run_id: uuid.UUID) -> RunDetail:
    """Restarts a scrape (refresh or test scrape) that went quiet or failed. It carries on where it got to rather
    than starting over."""
    await admin()
    runs, queue = await services().aget(RunHistory, Queue)
    await queue.restart(await runs.run(run_id))
    return await get_run(run_id)


@server.resource("foundry://runs/{run_id}", mime_type="application/json")
async def run_resource(run_id: str) -> RunDetail:
    """A job, as get_run returns it."""
    return await get_run(uuid.UUID(run_id))


@server.resource("foundry://runs/{run_id}/chat", mime_type="application/json")
async def chat_resource(run_id: str) -> RunLines:
    """A job's chat from the start, as read_run returns it."""
    return await lines(uuid.UUID(run_id), 0, "chat")


@server.resource("foundry://sources/{source_id}", mime_type="application/json")
async def source_resource(source_id: str) -> SourceDetail:
    """A scraper, as get_scraper returns it."""
    return await get_scraper(uuid.UUID(source_id))


@server.resource(QUESTIONS, mime_type="application/json")
async def questions_resource() -> list[HelpView]:
    """The questions agents wait on, as list_questions returns them."""
    return await list_questions()


async def lines(run_id: uuid.UUID, after: int, scope: Lines) -> RunLines:
    history, timeline, help_queue = await services().aget(RunHistory, Timeline, HelpQueue)
    run = await history.run(run_id)
    events = await timeline.since(run_id, after)
    shown = events if scope == "everything" else [event for event in events if event.in_chat]
    return RunLines(
        status=run.status,
        summary=run.summary,
        waiting=bool(await help_queue.of_run(run_id)) and run.status == "waiting",
        lines=shown,
        after=events[-1].id if events else after,
    )


app = server.streamable_http_app(
    streamable_http_path="/",
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)
