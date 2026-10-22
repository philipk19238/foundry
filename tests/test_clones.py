"""Agents own their checkout's git, so a hook or a config entry they plant there must never run in foundry's process,
where the GitHub token is: foundry takes the run's commits into its mirror and pushes from there, to the run's own
`agents/` branch only, and only what was committed."""

import asyncio
import subprocess
from pathlib import Path

import pytest

from foundry.app import exceptions
from foundry.app.repo.repo import Clones, Git, Mirror
from foundry.app.settings import settings

BRANCH = "agents/run-1"


def git(folder: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=folder, check=True,
                          capture_output=True, text=True).stdout.strip()  # fmt: skip


def setup(tmp_path: Path) -> tuple[Clones, Path, Path]:
    """GitHub (a bare `origin` with `main`), foundry's mirror of it, and a run's checkout one commit ahead."""
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "--quiet", "-b", "main")
    (seed / "kit.py").write_text("PACE = 1\n")
    git(seed, "add", "-A")
    git(seed, "commit", "--quiet", "-m", "kit")
    origin = tmp_path / "origin.git"
    git(tmp_path, "clone", "--quiet", "--bare", str(seed), str(origin))
    mirror = Mirror(Git(settings), settings)
    mirror.folder, mirror.url = tmp_path / "mirror.git", str(origin)
    clones = Clones(Git(settings), mirror, settings)
    checkout = asyncio.run(clones.clone(tmp_path, BRANCH))
    (checkout / "spider.py").write_text("agent's spider\n")
    git(checkout, "add", "spider.py")
    subprocess.run(["git", "commit", "--quiet", "-m", "spider"], cwd=checkout, check=True)  # as the agent does
    return clones, checkout, origin


def plant(checkout: Path, marker: Path) -> None:
    """Everything a checkout's git could run: every hook, and an fsmonitor command."""
    hooks = checkout / ".git" / "hooks"
    for hook in ("pre-push", "post-checkout", "pre-commit", "reference-transaction", "pre-auto-gc", "post-merge"):
        (hooks / hook).write_text(f"#!/bin/sh\necho {hook} >> {marker}\n")
        (hooks / hook).chmod(0o755)
    git(checkout, "config", "core.fsmonitor", f"echo fsmonitor >> {marker}; true")


def test_the_runs_commits_reach_only_its_own_branch_and_nothing_it_planted_runs(tmp_path: Path) -> None:
    clones, checkout, origin = setup(tmp_path)
    marker = tmp_path / "ran"
    plant(checkout, marker)
    (checkout / "draft.py").write_text("not committed\n")

    async def hand_in() -> tuple[str, list[str], bool]:
        commit = await clones.take(checkout, BRANCH)
        changed, ahead = await clones.changed(commit), await clones.ahead(commit)
        await clones.push(commit, BRANCH)
        return commit, changed, ahead

    commit, changed, ahead = asyncio.run(hand_in())
    assert not marker.exists()
    assert (changed, ahead) == (["spider.py"], True)
    assert git(origin, "rev-parse", f"refs/heads/{BRANCH}") == commit
    assert git(origin, "show", f"{BRANCH}:spider.py") == "agent's spider"
    assert "draft.py" not in git(origin, "ls-tree", "--name-only", BRANCH).split()
    assert git(origin, "log", "--format=%s", "main") == "kit"
    assert git(origin, "log", "-1", "--format=%an <%ae>", BRANCH) == settings.git_author


def test_foundry_never_pushes_outside_a_run_branch(tmp_path: Path) -> None:
    clones, checkout, origin = setup(tmp_path)
    commit = asyncio.run(clones.take(checkout, BRANCH))
    with pytest.raises(exceptions.GitFailed):
        asyncio.run(clones.push(commit, "main"))
    assert git(origin, "log", "--format=%s", "main") == "kit"
