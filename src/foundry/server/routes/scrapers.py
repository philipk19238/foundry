"""A scraper's page, one route per tab: its conversation with the agent, its test crawl, its code, its data and its
sign-in. `/sources/{id}` opens the tab its stage needs."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from foundry.app import exceptions
from foundry.app.app import Imports, Reviews
from foundry.app.auth.models import UserView
from foundry.app.browser.browser import Accounts, Credentials
from foundry.app.browser.models import NewCredential
from foundry.app.catalog.catalog import Catalog, Opportunities
from foundry.app.db.models.access import CredentialKind
from foundry.app.db.models.runs import RunKind
from foundry.app.help.help import RunChat
from foundry.app.progress.progress import Tracker
from foundry.app.repo.client import GitHubPulls
from foundry.app.runs.runs import Queue
from foundry.server.models import ScraperPage
from foundry.server.routes.auth import Admin, SignedIn

router = APIRouter(prefix="/sources", default_response_class=HTMLResponse)


@router.post("")
async def add_source(url: Annotated[str, Form()], services: Admin) -> Response:
    """Starts an agent building a scraper for the portal; a portal added before opens its page instead."""
    user, catalog, queue = await services.aget(UserView, Catalog, Queue)
    known = await catalog.find(url.strip())
    if known is not None:
        return RedirectResponse(f"/sources/{known.id}", status_code=status.HTTP_303_SEE_OTHER)
    source = await catalog.add_source(url.strip())
    await queue.request(source.id, RunKind.ONBOARD, user.id)
    return RedirectResponse(f"/sources/{source.id}/chat", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/import")
async def import_pull(number: Annotated[int, Form(gt=0)], services: Admin) -> Response:
    """Registers a pull request a person opened, so its scraper is reviewed here like an agent's."""
    sources = await (await services.aget(Imports)).register(number)
    return RedirectResponse(f"/sources/{sources[0].id}/test", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{source_id}")
async def opening(source_id: uuid.UUID, services: SignedIn) -> Response:
    progress = await (await services.aget(Tracker)).progress(source_id)
    return RedirectResponse(f"/sources/{source_id}/{progress.stage.opens_on}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{source_id}/stage")
async def stage(source_id: uuid.UUID, services: SignedIn) -> Response:
    """What the page polls to know it is stale: the stage, and whether a merge or a crawl is under way."""
    progress = await (await services.aget(Tracker)).progress(source_id)
    return PlainTextResponse(f"{progress.stage}:{progress.merging}:{progress.crawling}")


@router.get("/{source_id}/chat")
async def chat(request: Request, source_id: uuid.UUID, services: SignedIn) -> Response:
    """The work log beside the conversation."""
    templates = await services.aget(Jinja2Templates)
    page = await ScraperPage.from_container(services, source_id, "chat")
    return templates.TemplateResponse(request, "source.html", dict(page))


@router.get("/{source_id}/browser")
async def browser(request: Request, source_id: uuid.UUID, services: SignedIn) -> Response:
    """The agent's browser, to watch while it works; driving it goes through taking over."""
    templates = await services.aget(Jinja2Templates)
    page = await ScraperPage.from_container(services, source_id, "browser")
    return templates.TemplateResponse(request, "source.html", dict(page))


@router.get("/{source_id}/test")
async def test(request: Request, source_id: uuid.UUID, services: SignedIn) -> Response:
    templates, tracker = await services.aget(Jinja2Templates, Tracker)
    page = await ScraperPage.from_container(services, source_id, "test")
    if page.progress.newest is None:
        return RedirectResponse(f"/sources/{source_id}/chat", status_code=status.HTTP_303_SEE_OTHER)
    tried = await tracker.trials(page.progress)
    return templates.TemplateResponse(request, "source.html", dict(page) | {"tried": tried})


@router.get("/{source_id}/test/results")
async def test_results(request: Request, source_id: uuid.UUID, services: SignedIn) -> Response:
    """The Test tab's results alone, which the tab polls while a test crawl runs."""
    templates, tracker = await services.aget(Jinja2Templates, Tracker)
    page = await ScraperPage.from_container(services, source_id, "test")
    tried = await tracker.trials(page.progress)
    return templates.TemplateResponse(request, "_test.html", dict(page) | {"tried": tried})


@router.post("/{source_id}/test")
async def run_test(request: Request, source_id: uuid.UUID, services: Admin) -> Response:
    user, queue = await services.aget(UserView, Queue)
    await queue.request(source_id, RunKind.TRIAL, user.id)
    if "HX-Request" not in request.headers:
        return RedirectResponse(f"/sources/{source_id}/test", status_code=status.HTTP_303_SEE_OTHER)
    return await test_results(request, source_id, services)


@router.post("/{source_id}/test/changes")
async def request_changes(source_id: uuid.UUID, text: Annotated[str, Form()], services: Admin) -> Response:
    """Sends the agent a person's request about the latest test, with what the test found, and opens the chat."""
    user, tracker, chat = await services.aget(UserView, Tracker, RunChat)
    progress = await tracker.progress(source_id)
    if progress.agent is None:
        raise exceptions.NotAllowed("ask for changes to a scraper no agent wrote")
    tried = await tracker.trials(progress)
    about = f"\n\n{tried.trial.described}" if tried.trial is not None else ""
    await chat.say(progress.agent.id, user, text.strip() + about)
    return RedirectResponse(f"/sources/{source_id}/chat", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{source_id}/code")
async def code(request: Request, source_id: uuid.UUID, services: SignedIn) -> Response:
    """The newest version's pull request, file by file; GitHub refusing leaves the link to it."""
    templates, pulls = await services.aget(Jinja2Templates, GitHubPulls)
    page = await ScraperPage.from_container(services, source_id, "code")
    if page.progress.newest is None:
        return RedirectResponse(f"/sources/{source_id}/chat", status_code=status.HTTP_303_SEE_OTHER)
    context = dict(page) | {"files": [], "unread": None}
    try:
        context["files"] = await pulls.files(page.progress.newest.pull)
    except exceptions.Error as error:
        context["unread"] = str(error)
    return templates.TemplateResponse(request, "source.html", context)


@router.get("/{source_id}/data")
async def data(request: Request, source_id: uuid.UUID, services: SignedIn) -> Response:
    templates, opportunities = await services.aget(Jinja2Templates, Opportunities)
    page = await ScraperPage.from_container(services, source_id, "data")
    rows = await opportunities.opportunities(source_id, "", 0)
    return templates.TemplateResponse(request, "source.html", dict(page) | {"rows": rows})


@router.get("/{source_id}/access")
async def access(request: Request, source_id: uuid.UUID, services: SignedIn) -> Response:
    """How the scraper signs in: the login, the session read from the agent's browser, and other credentials, none
    of their secrets shown."""
    templates, accounts, credentials = await services.aget(Jinja2Templates, Accounts, Credentials)
    page = await ScraperPage.from_container(services, source_id, "access")
    context = dict(page) | {
        "login": await accounts.username(source_id),
        "session": await accounts.session(source_id),
        "credentials": await credentials.shown(source_id),
        "kinds": list(CredentialKind),
    }
    return templates.TemplateResponse(request, "source.html", context)


@router.post("/{source_id}/credentials")
async def save_credential(
    source_id: uuid.UUID, credential: Annotated[NewCredential, Form()], services: Admin
) -> Response:
    await (await services.aget(Credentials)).save(source_id, credential)
    return RedirectResponse(f"/sources/{source_id}/access", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{source_id}/credentials/{name}/forget")
async def forget_credential(source_id: uuid.UUID, name: str, services: Admin) -> Response:
    await (await services.aget(Credentials)).forget(source_id, name)
    return RedirectResponse(f"/sources/{source_id}/access", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{source_id}/merge")
async def approve(source_id: uuid.UUID, services: Admin) -> Response:
    """Approves the version in review: merges its pull request and registers it at once."""
    user, reviews = await services.aget(UserView, Reviews)
    await reviews.approve(source_id, user.id)
    return RedirectResponse(f"/sources/{source_id}/data", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{source_id}/runs")
async def start_run(source_id: uuid.UUID, kind: Annotated[RunKind, Form()], services: Admin) -> Response:
    user, queue = await services.aget(UserView, Queue)
    await queue.request(source_id, kind, user.id)
    to = "chat" if kind.is_agent else "data"
    return RedirectResponse(f"/sources/{source_id}/{to}", status_code=status.HTTP_303_SEE_OTHER)
