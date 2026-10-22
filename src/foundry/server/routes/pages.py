import asyncio
import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Form, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BeforeValidator

from foundry.app.auth.auth import Logins
from foundry.app.auth.models import UserView
from foundry.app.catalog.catalog import OPPORTUNITY_PAGE, Catalog, Opportunities
from foundry.app.db.db import now
from foundry.app.db.models.runs import RunKind
from foundry.app.help.help import HelpQueue, RunChat
from foundry.app.progress.progress import Tracker
from foundry.app.runs.runs import RunFeed, RunHistory, Timeline
from foundry.app.scrapers.scrapers import ScraperHistory
from foundry.app.settings import Settings
from foundry.server.routes.auth import SignedIn

router = APIRouter(default_response_class=HTMLResponse)
STREAM_SECONDS = 55
RECONNECT_MS = 1000


@router.get("/")
async def home(request: Request, services: SignedIn) -> Response:
    templates, user, catalog, tracker, help_queue = await services.aget(
        Jinja2Templates, UserView, Catalog, Tracker, HelpQueue
    )
    context = {
        "user": user,
        "sources": await catalog.sources(),
        "stages": await tracker.stages(),
        "help": await help_queue.live(),
    }
    return templates.TemplateResponse(request, "home.html", context)


@router.get("/opportunities")
async def opportunities(
    request: Request,
    services: SignedIn,
    q: str = "",
    source: Annotated[uuid.UUID | None, BeforeValidator(lambda value: value or None)] = None,
    page: Annotated[int, Query(ge=0)] = 0,
) -> Response:
    """Every stored opportunity, newest snapshot of each, searchable by title or buyer and filtered by portal."""
    templates, user, catalog, stored, tracker = await services.aget(
        Jinja2Templates, UserView, Catalog, Opportunities, Tracker
    )
    context = {
        "user": user,
        "rows": await stored.opportunities(source, q, page),
        "sources": await catalog.sources(),
        "stages": await tracker.stages(),
        "q": q,
        "source_id": source,
        "page": page,
        "size": OPPORTUNITY_PAGE,
    }
    return templates.TemplateResponse(request, "opportunities.html", context)


@router.get("/runs/{run_id}")
async def run(request: Request, run_id: uuid.UUID, services: SignedIn) -> Response:
    templates, user, runs, catalog, timeline, desk = await services.aget(
        Jinja2Templates, UserView, RunHistory, Catalog, Timeline, HelpQueue
    )
    found = await runs.run(run_id)
    if not found.has_agent:
        tab = "test" if found.kind == RunKind.TRIAL else "data"
        return RedirectResponse(f"/sources/{found.source_id}/{tab}", status_code=status.HTTP_303_SEE_OTHER)
    latest = await runs.last_agent(found.source_id)
    if latest is not None and latest.id == found.id:
        return RedirectResponse(f"/sources/{found.source_id}/chat", status_code=status.HTTP_303_SEE_OTHER)
    context = {
        "user": user,
        "run": found,
        "source": await catalog.source(found.source_id),
        "events": await timeline.since(run_id, 0),
        "help": await desk.of_run(run_id),
        "built": await (await services.aget(ScraperHistory)).built_by(run_id),
        "repo": (await services.aget(Settings)).repo,
    }
    return templates.TemplateResponse(request, "run.html", context)


@router.get("/runs/{run_id}/events")
async def run_events(request: Request, run_id: uuid.UUID, services: SignedIn, after: int = 0) -> Response:
    """The run's new lines since ``after``, as out-of-band swaps: what sending a message gets back."""
    templates, user, runs, timeline, desk = await services.aget(
        Jinja2Templates, UserView, RunHistory, Timeline, HelpQueue
    )
    context = {
        "user": user,
        "run": await runs.run(run_id),
        "events": await timeline.since(run_id, after),
        "after": after,
        "help": await desk.of_run(run_id),
    }
    return templates.TemplateResponse(request, "events.html", context)


@router.get("/runs/{run_id}/stream")
async def stream(request: Request, run_id: uuid.UUID, services: SignedIn, after: int = 0) -> Response:
    """The run's chat, live: each new line is pushed as it is written (server-sent events), as the same out-of-band
    swaps sending a message gets back. A reconnecting browser sends the last line it saw as Last-Event-ID and picks up
    from there. Each stream ends after STREAM_SECONDS and the browser reconnects at once, so a deploy can retire the
    old server in a minute instead of waiting on every open page."""
    templates, user, feed, desk = await services.aget(Jinja2Templates, UserView, RunFeed, HelpQueue)
    page = templates.get_template("events.html")
    since = int(request.headers.get("last-event-id") or after)
    loop = asyncio.get_running_loop()
    ends = loop.time() + STREAM_SECONDS

    async def stopped() -> bool:
        return loop.time() > ends or await request.is_disconnected()

    async def frames() -> AsyncGenerator[str, None]:
        yield f"retry: {RECONNECT_MS}\n\n"
        async for update in feed.follow(run_id, since, stopped):
            context = {"user": user, "run": update.run, "events": update.events, "after": update.after}
            html = page.render({**context, "help": await desk.of_run(run_id)})
            yield (
                f"id: {update.after}\nevent: lines\n" + "".join(f"data: {line}\n" for line in html.splitlines()) + "\n"
            )

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(frames(), media_type="text/event-stream", headers=headers)


@router.post("/runs/{run_id}/messages")
async def say(
    request: Request, run_id: uuid.UUID, text: Annotated[str, Form()], services: SignedIn,
    after: Annotated[int, Form()] = 0,
) -> Response:  # fmt: skip
    """Sends a message; the page gets back every new line since ``after``, its own message included, so it shows at
    once instead of on the next poll."""
    user, chat = await services.aget(UserView, RunChat)
    await chat.say(run_id, user, text.strip())
    if "HX-Request" not in request.headers:
        return RedirectResponse(f"/runs/{run_id}", status_code=status.HTTP_303_SEE_OTHER)
    return await run_events(request, run_id, services, after)


@router.post("/runs/{run_id}/takeover")
async def take_over(
    request: Request, run_id: uuid.UUID, services: SignedIn, after: Annotated[int, Form()] = 0
) -> Response:
    user, chat = await services.aget(UserView, RunChat)
    await chat.ask_to_take_over(run_id, user)
    if "HX-Request" not in request.headers:
        return RedirectResponse(f"/runs/{run_id}", status_code=status.HTTP_303_SEE_OTHER)
    return await run_events(request, run_id, services, after)


@router.get("/help")
async def help_queue(request: Request, services: SignedIn) -> Response:
    templates, user, desk = await services.aget(Jinja2Templates, UserView, HelpQueue)
    context = {"user": user, "requests": await desk.recent(), "now": now()}
    return templates.TemplateResponse(request, "help_queue.html", context)


@router.get("/help/{help_id}")
async def help_page(request: Request, help_id: uuid.UUID, services: SignedIn) -> Response:
    templates, user, desk, runs, catalog = await services.aget(
        Jinja2Templates, UserView, HelpQueue, RunHistory, Catalog
    )
    found = await desk.request(help_id)
    run = await runs.run(found.run_id)
    context = {
        "user": user,
        "request_": found,
        "run": run,
        "source": await catalog.source(run.source_id),
        "live": found.is_live(now()),
        "mine": found.is_held_by(user),
    }
    return templates.TemplateResponse(request, "help.html", context)


@router.post("/help/{help_id}/claim")
async def claim(help_id: uuid.UUID, services: SignedIn) -> Response:
    user, desk = await services.aget(UserView, HelpQueue)
    request_ = await desk.claim(help_id, user)
    return RedirectResponse(f"/runs/{request_.run_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/help/{help_id}/give-back")
async def give_back(help_id: uuid.UUID, services: SignedIn) -> Response:
    user, desk = await services.aget(UserView, HelpQueue)
    await desk.give_back(help_id, user)
    request_ = await desk.request(help_id)
    return RedirectResponse(f"/runs/{request_.run_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/help/{help_id}/done")
async def done(help_id: uuid.UUID, services: SignedIn, note: Annotated[str, Form()] = "") -> Response:
    user, desk = await services.aget(UserView, HelpQueue)
    await desk.done(help_id, user, note)
    request_ = await desk.request(help_id)
    return RedirectResponse(f"/runs/{request_.run_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/tokens")
async def tokens(request: Request, services: SignedIn) -> Response:
    templates, user, logins = await services.aget(Jinja2Templates, UserView, Logins)
    context = {"user": user, "tokens": await logins.tokens(user), "minted": None}
    return templates.TemplateResponse(request, "tokens.html", context)


@router.post("/tokens")
async def mint(request: Request, name: Annotated[str, Form()], services: SignedIn) -> Response:
    templates, user, logins = await services.aget(Jinja2Templates, UserView, Logins)
    minted = await logins.mint(user, name.strip() or "token")
    context = {"user": user, "tokens": await logins.tokens(user), "minted": minted.value}
    return templates.TemplateResponse(request, "tokens.html", context)


@router.post("/tokens/{token_hash}/revoke")
async def revoke(token_hash: str, services: SignedIn) -> Response:
    user, logins = await services.aget(UserView, Logins)
    await logins.revoke(user, token_hash)
    return RedirectResponse("/tokens", status_code=status.HTTP_303_SEE_OTHER)
