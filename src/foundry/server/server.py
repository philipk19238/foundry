"""The server's composition root: what the registry holds, the seeded user, the error translator, and the routes."""

from collections.abc import AsyncGenerator
from pathlib import Path

import svcs
import svcs.fastapi
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from foundry.app import app as foundry
from foundry.app.auth.auth import Users
from foundry.app.db.models.auth import Role
from foundry.app.runs.runs import Runs
from foundry.app.settings import settings
from foundry.server import exceptions
from foundry.server.routes import api, auth, files, pages
from foundry.worker import worker

HERE = Path(__file__).parent


@svcs.fastapi.lifespan
async def lifespan(app: FastAPI, registry: svcs.Registry) -> AsyncGenerator[None, None]:
    foundry.register(registry)
    worker.register(registry)
    registry.register_value(Jinja2Templates, Jinja2Templates(directory=HERE / "templates"))
    await worker.booted(registry)
    async with svcs.Container(registry) as services:
        runs = await services.aget(Runs)
        await runs.reap()
    if settings.seed_email is not None and settings.seed_password is not None:
        async with svcs.Container(registry) as services:
            users = await services.aget(Users)
            await users.ensure(settings.seed_email, settings.seed_password.get_secret_value(), Role.ADMIN)
    yield


app = FastAPI(title="foundry", lifespan=lifespan)
exceptions.install(app)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
app.include_router(auth.router)
app.include_router(pages.router)
app.include_router(api.router)
app.include_router(files.router)
