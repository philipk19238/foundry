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


class Submission(BaseModel, frozen=True):
    """What the agent hands in with `submit`: the site its work is for, and the pull request's title and summary."""

    site: str
    title: str
    summary: str


class ChangePull(BaseModel, frozen=True):
    """The pull request a run last opened for a change to foundry itself, which a restart waits on."""

    number: int
    url: str
