import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates

from foundry.app.auth.auth import Logins
from foundry.app.auth.models import UserView
from foundry.app.catalog.catalog import Catalog
from foundry.app.db.db import now
from foundry.app.db.models.catalog import ScraperState
from foundry.app.db.models.runs import RunKind
from foundry.app.help.help import HelpQueue, RunChat
from foundry.app.runs.runs import Queue, RunFeed, RunHistory, Timeline
from foundry.app.scrapers.scrapers import ScraperHistory
from foundry.app.settings import Settings
from foundry.server.models import TrialState, TrialView
from foundry.server.routes.auth import Admin, SignedIn

router = APIRouter(default_response_class=HTMLResponse)


@router.get("/")
async def home(request: Request, services: SignedIn) -> Response:
    templates, user, catalog, runs, desk = await services.aget(
        Jinja2Templates, UserView, Catalog, RunHistory, HelpQueue
    )
    context = {
        "user": user,
        "sources": await catalog.sources(),
        "runs": await runs.recent(),
        "help": await desk.live(),
    }
    return templates.TemplateResponse(request, "home.html", context)


@router.post("/sources")
async def add_source(url: Annotated[str, Form()], services: Admin) -> Response:
    user, catalog, queue = await services.aget(UserView, Catalog, Queue)
    source = await catalog.add_source(url.strip())
    run = await queue.request(source.id, RunKind.ONBOARD, user.id)
    return RedirectResponse(f"/runs/{run.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/sources/{source_id}")
async def source(request: Request, source_id: uuid.UUID, services: SignedIn) -> Response:
    templates, user, catalog, runs, settings = await services.aget(
        Jinja2Templates, UserView, Catalog, RunHistory, Settings
    )
    scrapers = await (await services.aget(ScraperHistory)).of_source(source_id)
    recent = await runs.of_source(source_id)
    context = {
        "user": user,
        "source": await catalog.source(source_id),
        "newest": next((scraper for scraper in scrapers if scraper.state != ScraperState.CLOSED), None),
        "scrapers": scrapers,
        "tried": TrialState.of(await runs.last(source_id, RunKind.TRIAL), recent),
        "repo": settings.repo,
        "runs": recent,
        "notices": await catalog.notices(source_id),
    }
    return templates.TemplateResponse(request, "source.html", context)


@router.post("/sources/{source_id}/runs")
async def start_run(source_id: uuid.UUID, kind: Annotated[RunKind, Form()], services: Admin) -> Response:
    user, queue = await services.aget(UserView, Queue)
    run = await queue.request(source_id, kind, user.id)
    return RedirectResponse(f"/runs/{run.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/runs/{run_id}")
async def run(request: Request, run_id: uuid.UUID, services: SignedIn) -> Response:
    templates, user, runs, catalog, timeline, desk = await services.aget(
        Jinja2Templates, UserView, RunHistory, Catalog, Timeline, HelpQueue
    )
    found = await runs.run(run_id)
    context = {
        "user": user,
        "run": found,
        "source": await catalog.source(found.source_id),
        "events": await timeline.since(run_id, 0),
        "help": await desk.of_run(run_id),
        "built": await (await services.aget(ScraperHistory)).built_by(run_id),
        "trial": TrialView.of(found),
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
    from there."""
    templates, user, feed, desk = await services.aget(Jinja2Templates, UserView, RunFeed, HelpQueue)
    page = templates.get_template("events.html")
    since = int(request.headers.get("last-event-id") or after)

    async def frames() -> AsyncGenerator[str, None]:
        async for update in feed.follow(run_id, since, request.is_disconnected):
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
