"""The worker's composition root: the dispatcher this process carries out runs with, and the boot check."""

import asyncio
import os
import sys
import uuid
from pathlib import Path

import modal
import svcs
from pydantic import SecretStr

from foundry.app import app as foundry
from foundry.app import exceptions
from foundry.app.db.models.runs import RunStatus
from foundry.app.repo.repo import Mirror
from foundry.app.runs.models import Dispatcher, EventKind
from foundry.app.runs.runs import Runs, Timeline
from foundry.app.scrapers.models import CrawlState
from foundry.app.settings import Settings, settings

APP = "foundry"
SHUTDOWN_SECONDS = 30
DEPLOY_SECONDS = 1500
OUTPUT_TAIL = 2000
FUNCTIONS = "src/foundry/worker/functions.py"
KEYS = "foundry"
DEPLOY_KEYS = "foundry-deploy"
VOLUME = "foundry-workspaces"
MODAL_TOKEN = ("MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET")
SECRETS = (
    "DATABASE_URL", "OPENAI_API_KEY", "KERNEL_API_KEY", "KNOCK_SECRET_KEY", "SERVICE_TOKEN", "CREDENTIALS_KEY",
    "SEED_EMAIL", "SEED_PASSWORD", "GITHUB_TOKEN", "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
)  # fmt: skip


class LocalDispatcher:
    """Carries out runs as tasks in this process: for local development, one machine."""

    def __init__(self, registry: svcs.Registry) -> None:
        self._registry = registry
        self._tasks: set[asyncio.Task[None]] = set()

    async def dispatch(self, run_id: uuid.UUID) -> None:
        task = asyncio.create_task(self._carry_out(run_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def redeploy(self, run_id: uuid.UUID) -> None:
        """This process runs the code on disk, so there is nothing to deploy: pull `main` and restart the server to
        run the new code; the run carries on here."""
        await self.dispatch(run_id)

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=SHUTDOWN_SECONDS)

    async def _carry_out(self, run_id: uuid.UUID) -> None:
        async with svcs.Container(self._registry) as services:
            jobs = await services.aget(foundry.Jobs)
            await jobs.carry_out(run_id)


class VolumeCrawls:
    """Live crawls' state on the workspaces volume, committed as they go, so a crawl whose container died carries on
    in another."""

    def __init__(self, root: Path, volume: modal.Volume) -> None:
        self._root = root
        self._volume = volume

    def folder(self, run_id: uuid.UUID) -> Path:
        return self._root / str(run_id)

    async def keep(self) -> None:
        await self._volume.commit.aio()


class ModalDispatcher:
    """Carries out each run on a Modal container of its own, in Modal's dev environment."""

    def __init__(self, carry_out: modal.Function[..., None, None], redeploy: modal.Function[..., None, None]) -> None:
        self._carry_out = carry_out
        self._redeploy = redeploy

    async def dispatch(self, run_id: uuid.UUID) -> None:
        await self._carry_out.spawn.aio(str(run_id))

    async def redeploy(self, run_id: uuid.UUID) -> None:
        await self._redeploy.spawn.aio(str(run_id))


class Deployer:
    """Deploys the latest `main` from inside Modal and carries a restarting run on there. Only merged code is ever
    deployed: what a run wants changed reaches `main` through a pull request a person merged."""

    def __init__(
        self, mirror: Mirror, runs: Runs, timeline: Timeline, dispatcher: Dispatcher, settings: Settings
    ) -> None:
        self._mirror = mirror
        self._runs = runs
        self._timeline = timeline
        self._dispatcher = dispatcher
        self._environment = settings.modal_environment

    async def carry_on(self, run_id: uuid.UUID) -> None:
        try:
            commit = await self.deploy()
        except exceptions.Error as error:
            await self._runs.finish(run_id, RunStatus.FAILED, str(error))
            return
        await self._timeline.log(run_id, EventKind.STATUS, f"Deployed main at {commit[:12]}; carrying on there.")
        await self._dispatcher.dispatch(run_id)

    async def deploy(self) -> str:
        commit = await self._mirror.main()
        async with self._mirror.tree(commit) as tree:
            outside = {name: value for name, value in os.environ.items() if not name.startswith("MODAL_")}
            keys = {name: os.environ[name] for name in MODAL_TOKEN if name in os.environ}
            environment = {**outside, **keys, "DEPLOYED_COMMIT": commit, "PYTHONPATH": str(tree / "src")}
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "modal", "deploy", "--env", self._environment, FUNCTIONS,
                cwd=tree, env=environment, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )  # fmt: skip
            try:
                async with asyncio.timeout(DEPLOY_SECONDS):
                    output, _ = await process.communicate()
            except TimeoutError:
                process.kill()
                raise exceptions.DeployFailed(commit, "timed out") from None
        if process.returncode != 0:
            raise exceptions.DeployFailed(commit, output.decode(errors="replace")[-OUTPUT_TAIL:])
        return commit


async def publish_secrets(settings: Settings) -> None:
    """Replaces the two named Modal secrets with this machine's settings: `foundry`, every key the app reads, and
    `foundry-deploy`, the Modal token, which only the redeploy function gets."""
    if settings.database_url.startswith("sqlite"):
        raise exceptions.MissingSetting(
            "DATABASE_URL to a Postgres database: Modal containers can't share a SQLite file"
        )
    values = {name: getattr(settings, name.lower()) for name in (*SECRETS, *MODAL_TOKEN)}
    plain = {
        name: value.get_secret_value() if isinstance(value, SecretStr) else str(value)
        for name, value in values.items()
        if value is not None
    }
    missing = [name for name in MODAL_TOKEN if name not in plain]
    if missing:
        raise exceptions.MissingSetting(f"{' and '.join(missing)} so a run can redeploy foundry")
    environment = settings.modal_environment
    for name, contents in (
        (KEYS, {key: plain[key] for key in SECRETS if key in plain}),
        (DEPLOY_KEYS, {key: plain[key] for key in MODAL_TOKEN}),
    ):
        await modal.Secret.objects.delete.aio(name, environment_name=environment, allow_missing=True)
        await modal.Secret.objects.create.aio(name, contents, environment_name=environment)


def register(registry: svcs.Registry) -> None:
    """The dispatcher this process uses, then the queue and jobs that use it; on Modal, live crawls keep their state on
    the workspaces volume."""
    match settings.dispatcher:
        case "local":
            local = LocalDispatcher(registry)
            registry.register_value(Dispatcher, local, on_registry_close=local.close)
        case "modal":
            carry_out = modal.Function.from_name(APP, "carry_out", environment_name=settings.modal_environment)
            redeploy = modal.Function.from_name(APP, "redeploy", environment_name=settings.modal_environment)
            registry.register_value(Dispatcher, ModalDispatcher(carry_out, redeploy))
            volume = modal.Volume.from_name(VOLUME, environment_name=settings.modal_environment)
            registry.register_value(CrawlState, VolumeCrawls(settings.workspaces_dir / "crawls", volume))
    foundry.register_queue(registry)
    registry.register_factory(Deployer, svcs.aautowire(Deployer))


async def booted(registry: svcs.Registry) -> None:
    """Builds every registered service once, so a missing setting stops the process before it takes work."""
    async with svcs.Container(registry) as services:
        for registered in registry:
            await services.aget(registered.svc_type)
