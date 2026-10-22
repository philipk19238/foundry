"""The server's composition root: what the registry holds, the seeded user, the error translator, and the routes."""

import time
from collections.abc import AsyncGenerator
from pathlib import Path

import svcs
import svcs.fastapi
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from foundry.app import app as foundry
from foundry.app import exceptions as errors
from foundry.app.auth.auth import Logins, Users
from foundry.app.auth.models import Token, UserView
from foundry.app.db.models.auth import Role
from foundry.app.runs.runs import Runs
from foundry.app.settings import settings
from foundry.server import exceptions
from foundry.server.routes import api, auth, files, mcp, pages, scrapers
from foundry.worker import worker

HERE = Path(__file__).parent


VERSION = settings.deployed_commit or str(int(time.time()))


def assets(request: Request) -> dict[str, str]:
    """The deploy's version on every page, so a new deploy's stylesheet replaces the browser's cached one."""
    return {"version": VERSION}


@svcs.fastapi.lifespan
async def lifespan(app: FastAPI, registry: svcs.Registry) -> AsyncGenerator[None, None]:
    foundry.register(registry)
    worker.register(registry)
    registry.register_value(Jinja2Templates, Jinja2Templates(directory=HERE / "templates", context_processors=[assets]))
    await worker.booted(registry)
    async with svcs.Container(registry) as services:
        runs = await services.aget(Runs)
        await runs.reap()
    if settings.seed_email is not None and settings.seed_password is not None:
        async with svcs.Container(registry) as services:
            users = await services.aget(Users)
            await users.ensure(settings.seed_email, settings.seed_password.get_secret_value(), Role.ADMIN)
    async with mcp.server.session_manager.run():
        yield


class TokenGate:
    """Lets a request through to the MCP app only with a valid API token, and gives its tools a container that knows
    who is asking."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope)
        bearer = request.headers.get("authorization", "")
        async with svcs.Container(svcs.fastapi.get_registry(request.app)) as container:
            try:
                if not bearer.lower().startswith("bearer "):
                    raise errors.NotSignedIn()
                logins = await container.aget(Logins)
                person = await logins.by_token(Token(value=bearer.split(" ", 1)[1].strip()))
            except errors.Error as error:
                await JSONResponse({"detail": str(error)}, status_code=401)(scope, receive, send)
                return
            container.register_local_value(UserView, person)
            reset = mcp.CONTAINER.set(container)
            try:
                await self._app(scope, receive, send)
            finally:
                mcp.CONTAINER.reset(reset)


app = FastAPI(title="foundry", lifespan=lifespan)
exceptions.install(app)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
app.mount("/mcp", TokenGate(mcp.app))
app.include_router(auth.router)
app.include_router(pages.router)
app.include_router(scrapers.router)
app.include_router(api.router)
app.include_router(files.router)
