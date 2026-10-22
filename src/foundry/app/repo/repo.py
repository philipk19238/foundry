"""The foundry repo on GitHub, as runs use it: a bare mirror on this machine that serves clones and the trees of old
commits, and per-run clones whose branches are pushed to GitHub. Nothing here pushes to `main`; a person merges."""

import asyncio
import contextlib
import email.utils
import shutil
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path

from pydantic import SecretStr

from foundry.app import exceptions
from foundry.app.repo.models import BRANCHES
from foundry.app.settings import Settings

GIT_TIMEOUT = 300
OUTPUT_TAIL = 2000
CHECKOUT = "foundry"
CREDENTIALS = '!f() { echo username=x-access-token; echo "password=$FOUNDRY_GIT_TOKEN"; }; f'


class Git:
    """Runs git as the configured author; a failed command is an Error carrying git's own message."""

    def __init__(self, settings: Settings) -> None:
        name, address = email.utils.parseaddr(settings.git_author)
        self._author = ("-c", f"user.name={name}", "-c", f"user.email={address}")

    async def run(self, folder: Path, *args: str) -> str:
        return await self._exec(folder, args, {})

    async def authorized(self, folder: Path, token: SecretStr, *args: str) -> str:
        """Runs git with the token handed over by a one-command credential helper that reads it from this process's
        environment, so it never lands in a config file or the process list. (Works on any git; the container's
        is older than `GIT_CONFIG_COUNT`.)"""
        helper = ("-c", "credential.helper=", "-c", f"credential.helper={CREDENTIALS}")
        return await self._exec(folder, (*helper, *args), {"FOUNDRY_GIT_TOKEN": token.get_secret_value()})

    async def succeeds(self, folder: Path, *args: str) -> bool:
        try:
            await self.run(folder, *args)
        except exceptions.GitFailed:
            return False
        return True

    async def _exec(self, folder: Path, args: tuple[str, ...], environment: dict[str, str]) -> str:
        process = await asyncio.create_subprocess_exec(
            "git", *self._author, *args, cwd=folder, env={"PATH": "/usr/bin:/bin:/usr/local/bin", **environment},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )  # fmt: skip
        try:
            async with asyncio.timeout(GIT_TIMEOUT):
                out, err = await process.communicate()
        except TimeoutError:
            process.kill()
            raise exceptions.GitFailed(" ".join(args[:2]), "timed out") from None
        if process.returncode != 0:
            raise exceptions.GitFailed(" ".join(args[:2]), err.decode(errors="replace").strip()[-OUTPUT_TAIL:])
        return out.decode().strip()


class Mirror:
    """A bare mirror of the repo on this machine: what `main` is on GitHub, and the files at any commit."""

    def __init__(self, git: Git, settings: Settings) -> None:
        self._git = git
        self.url = f"https://github.com/{settings.repo}.git"
        self.folder = settings.repo_cache.resolve()
        self._fetching = asyncio.Lock()

    async def fetch(self) -> None:
        """Brings the mirror up to date with GitHub, making it the first time."""
        async with self._fetching:
            if not self.folder.exists():
                self.folder.parent.mkdir(parents=True, exist_ok=True)
                await self._git.run(self.folder.parent, "clone", "--quiet", "--bare", self.url, self.folder.name)
                return
            await self._git.run(self.folder, "fetch", "--quiet", "--prune", "origin", "+refs/heads/*:refs/heads/*")

    async def main(self) -> str:
        """The commit `main` is at on GitHub now."""
        await self.fetch()
        return await self._git.run(self.folder, "rev-parse", "refs/heads/main")

    @contextlib.asynccontextmanager
    async def tree(self, commit: str) -> AsyncGenerator[Path, None]:
        """The repo's files at ``commit``, so a spider runs with the core it was checked against."""
        known = self.folder.exists() and await self._git.succeeds(self.folder, "cat-file", "-e", f"{commit}^{{commit}}")
        if not known:
            await self.fetch()
        with tempfile.TemporaryDirectory(prefix="foundry-tree-") as folder:
            tree = Path(folder)
            archive = tree / "tree.tar"
            await self._git.run(self.folder, "archive", "--output", str(archive), commit)
            await asyncio.to_thread(shutil.unpack_archive, archive, tree)
            archive.unlink()
            yield tree


class Clones:
    """Runs' clones of the repo. A run works on its own branch under `agents/` and pushes only that branch."""

    def __init__(self, git: Git, mirror: Mirror, settings: Settings) -> None:
        self._git = git
        self._mirror = mirror
        self._token = settings.github_token

    async def clone(self, folder: Path, branch: str) -> Path:
        """The run's checkout on its own branch, cloned from `main` the first time and reused when the run resumes."""
        checkout = folder / CHECKOUT
        if checkout.exists():
            return checkout
        await self._mirror.fetch()
        await self._git.run(folder, "clone", "--quiet", str(self._mirror.folder), CHECKOUT)
        await self._git.run(checkout, "remote", "set-url", "origin", self._mirror.url)
        await self._git.run(checkout, "checkout", "--quiet", "-b", branch)
        return checkout

    async def commit(self, checkout: Path, message: str) -> None:
        await self._git.run(checkout, "add", "-A")
        if not await self._git.succeeds(checkout, "diff", "--cached", "--quiet"):
            await self._git.run(checkout, "commit", "--quiet", "-m", message)

    async def changed(self, checkout: Path) -> list[str]:
        """The paths the branch changes against the latest `main`."""
        await self._git.run(checkout, "fetch", "--quiet", "origin", "main")
        return (await self._git.run(checkout, "diff", "--name-only", "origin/main...HEAD")).split()

    async def ahead(self, checkout: Path) -> bool:
        """Whether the branch has commits the latest `main` doesn't."""
        await self._git.run(checkout, "fetch", "--quiet", "origin", "main")
        return await self._git.run(checkout, "rev-list", "--count", "origin/main..HEAD") != "0"

    async def landed(self, checkout: Path) -> bool:
        """Whether everything the branch changes is already on the latest `main`, as once its pull request was
        squash-merged: a new change must then start from `main`, or its pull request conflicts with the merge."""
        changed = await self.changed(checkout)
        if not changed:
            return False
        return await self._git.succeeds(checkout, "diff", "--quiet", "origin/main", "HEAD", "--", *changed)

    async def push(self, checkout: Path) -> str:
        """Pushes the run's branch to GitHub and returns its name. A run owns its branch, so it may rewrite it."""
        branch = await self._git.run(checkout, "branch", "--show-current")
        if not branch.startswith(BRANCHES):
            raise exceptions.GitFailed("push", f"{branch} is not a run branch; runs push only {BRANCHES}* branches")
        if self._token is None:
            raise exceptions.NoGitHubToken()
        await self._git.authorized(checkout, self._token, "push", "--quiet", "--force", "origin", f"HEAD:{branch}")
        return branch

    async def onto_main(self, checkout: Path) -> None:
        """Moves the run's branch onto the latest `main`, keeping the work it has not committed. Used after a pull
        request from this branch was merged, so its commits are already on `main`."""
        branch = await self._git.run(checkout, "branch", "--show-current")
        await self._git.run(checkout, "fetch", "--quiet", "origin", "main")
        await self._git.run(checkout, "stash", "push", "--quiet", "--include-untracked")
        await self._git.run(checkout, "checkout", "--quiet", "-B", branch, "origin/main")
        if await self._git.run(checkout, "stash", "list"):
            await self._git.run(checkout, "stash", "pop", "--quiet")
