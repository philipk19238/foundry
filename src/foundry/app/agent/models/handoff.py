import enum
import uuid
from pathlib import Path

from pydantic import BaseModel


class Start(enum.StrEnum):
    """How a run's turn begins: on its task, back after a restart onto new code, or reopened by a message."""

    FRESH = "fresh"
    RESTARTED = "restarted"
    REOPENED = "reopened"


class ToolsContext(BaseModel, frozen=True):
    """What the foundry MCP server, started by Codex for one run, needs to act for that run."""

    run_id: uuid.UUID
    source_id: uuid.UUID
    fence: int
    live_view_url: str
    workspace: Path


class Method(enum.StrEnum):
    """How a scraper reads its portal, from the most reliable to the least: the portal's own API, the HTML its server
    sends, or a real browser."""

    API = "api"
    HTML = "html"
    BROWSER = "browser"


class Submission(BaseModel, frozen=True):
    """What the agent hands in with `submit`, which fills the pull request's template: the site, what the scraper
    does, how it reads the portal and signs in, the steps that found the data, what the agent checked against the
    portal itself, and what the scraper can't get yet. A run's submission is saved in its workspace and read back when
    it reopens, so the fields added with the template have defaults for submissions saved before it."""

    site: str
    title: str
    summary: str
    method: Method | None = None
    access: str = ""
    process: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()


class Change(BaseModel, frozen=True):
    """What the agent hands in with `propose_change`, which fills the template of a pull request to foundry itself."""

    title: str
    summary: str
    blocker: str
    testing: tuple[str, ...]


class ChangePull(BaseModel, frozen=True):
    """The pull request a run last opened for a change to foundry itself, which a restart waits on."""

    number: int
    url: str
