import uuid
from typing import Annotated

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from foundry.app.auth.auth import Logins
from foundry.app.auth.models import UserView
from foundry.app.catalog.catalog import Catalog
from foundry.app.db.db import now
from foundry.app.db.models.runs import RunKind
from foundry.app.help.help import HelpQueue
from foundry.app.runs.runs import Queue, RunHistory, Timeline
from foundry.app.scrapers.scrapers import Versions
from foundry.app.settings import Settings
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
    templates, user, catalog, runs, versions, settings = await services.aget(
        Jinja2Templates, UserView, Catalog, RunHistory, Versions, Settings
    )
    context = {
        "user": user,
        "source": await catalog.source(source_id),
        "proposal": await versions.proposed(source_id),
        "repo": settings.repo,
        "runs": await runs.of_source(source_id),
        "notices": await catalog.notices(source_id),
        "kinds": list(RunKind),
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
    }
    return templates.TemplateResponse(request, "run.html", context)


@router.get("/runs/{run_id}/events")
async def run_events(request: Request, run_id: uuid.UUID, services: SignedIn, after: int = 0) -> Response:
    """The run's new timeline lines and its status, polled by the run page."""
    templates, runs, timeline, desk = await services.aget(Jinja2Templates, RunHistory, Timeline, HelpQueue)
    context = {
        "run": await runs.run(run_id),
        "events": await timeline.since(run_id, after),
        "after": after,
        "help": await desk.of_run(run_id),
    }
    return templates.TemplateResponse(request, "events.html", context)


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
    await desk.claim(help_id, user)
    return RedirectResponse(f"/help/{help_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/help/{help_id}/give-back")
async def give_back(help_id: uuid.UUID, services: SignedIn) -> Response:
    user, desk = await services.aget(UserView, HelpQueue)
    await desk.give_back(help_id, user)
    return RedirectResponse(f"/help/{help_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/help/{help_id}/done")
async def done(help_id: uuid.UUID, services: SignedIn, note: Annotated[str, Form()] = "") -> Response:
    user, desk = await services.aget(UserView, HelpQueue)
    await desk.done(help_id, user, note)
    return RedirectResponse(f"/help/{help_id}", status_code=status.HTTP_303_SEE_OTHER)


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
