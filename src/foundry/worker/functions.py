"""The Modal app, in Modal's dev environment: one container per run, so agents and crawls scale out with the queue,
attachments in R2, schedules that refresh every healthy source and pick up merged pull requests, the web server, and
the redeploy a run asks for to carry on with newly merged code. Publish the keys with `just secrets`, then deploy
with `just deploy`."""

import contextlib
import subprocess
import uuid
from collections.abc import AsyncGenerator

import modal
import svcs
from fastapi import FastAPI

from foundry.app import app as foundry
from foundry.app import exceptions
from foundry.app.settings import ROOT, settings
from foundry.worker import worker

RUN_HOURS = 6
REFRESH_EVERY_HOURS = 6
REVIEW_EVERY_MINUTES = 10
MAX_CONTAINERS = 20
WORKSPACES = "/workspaces"


def deployed_commit() -> str:
    """The commit being deployed: set by the redeploy, or this checkout's HEAD when deploying from a laptop."""
    if settings.deployed_commit is not None:
        return settings.deployed_commit
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True)
    return head.stdout.strip()


def deployment_config() -> modal.Secret:
    """What differs on Modal and holds no key; the keys are the named secret `just secrets` publishes."""
    if settings.files_bucket is None:
        raise exceptions.MissingSetting("FILES_BUCKET: Modal keeps attachments in R2")
    return modal.Secret.from_dict(
        {
            "BASE_URL": settings.base_url,
            "DISPATCHER": "modal",
            "WORKSPACES_DIR": WORKSPACES,
            "FILES_STORAGE": "r2",
            "FILES_BUCKET": settings.files_bucket,
            "DEPLOYED_COMMIT": deployed_commit(),
        }
    )


image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git")
    .uv_sync(str(ROOT))
    .add_local_python_source("foundry", ignore=["**/__pycache__/**"])
)
keys = modal.Secret.from_name(worker.KEYS, environment_name=settings.modal_environment)
app = modal.App(worker.APP, image=image, secrets=[keys, deployment_config()] if modal.is_local() else [keys])
workspaces = modal.Volume.from_name("foundry-workspaces", create_if_missing=True)


@contextlib.asynccontextmanager
async def services() -> AsyncGenerator[svcs.Container, None]:
    """One container's services, as the worker registers them, each built once first so a missing setting stops the
    function before it does anything."""
    async with svcs.Registry() as registry:
        foundry.register(registry)
        worker.register(registry)
        await worker.booted(registry)
        async with svcs.Container(registry) as container:
            yield container


@app.function(timeout=RUN_HOURS * 3600, max_containers=MAX_CONTAINERS, volumes={WORKSPACES: workspaces})
async def carry_out(run_id: str) -> None:
    async with services() as container:
        jobs = await container.aget(foundry.Jobs)
        try:
            await jobs.carry_out(uuid.UUID(run_id))
        finally:
            await workspaces.commit.aio()


@app.function(
    timeout=1800, secrets=[modal.Secret.from_name(worker.DEPLOY_KEYS, environment_name=settings.modal_environment)]
)
async def redeploy(run_id: str) -> None:
    async with services() as container:
        deployer = await container.aget(worker.Deployer)
        await deployer.carry_on(uuid.UUID(run_id))


@app.function(schedule=modal.Period(hours=REFRESH_EVERY_HOURS))
async def refresh_ready() -> None:
    async with services() as container:
        jobs = await container.aget(foundry.Jobs)
        await jobs.refresh_ready()


@app.function(schedule=modal.Period(minutes=REVIEW_EVERY_MINUTES))
async def review_pending() -> None:
    async with services() as container:
        jobs = await container.aget(foundry.Jobs)
        await jobs.review_pending()


@app.function(max_containers=1)
@modal.concurrent(max_inputs=100)
@modal.asgi_app()
def web() -> FastAPI:
    from foundry.server.server import app as server_app

    return server_app
