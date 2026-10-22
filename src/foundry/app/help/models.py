import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from foundry.app.auth.models import UserView
from foundry.app.db.models.help import HelpStatus


class HelpView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    reason: str
    live_view_url: str
    status: HelpStatus
    claimed_by: uuid.UUID | None
    note: str | None
    created_at: dt.datetime
    expires_at: dt.datetime
    resolved_at: dt.datetime | None

    def is_live(self, at: dt.datetime) -> bool:
        return self.status in (HelpStatus.OPEN, HelpStatus.CLAIMED) and self.expires_at > at

    def is_held_by(self, user: UserView) -> bool:
        return self.status == HelpStatus.CLAIMED and self.claimed_by == user.id


class HumanHelp(BaseModel, frozen=True):
    """A person finished what the agent asked for; the note is what they want the agent to know."""

    outcome: Literal["helped"] = "helped"
    note: str


class NoAnswer(BaseModel, frozen=True):
    """Nobody finished the request before it expired."""

    outcome: Literal["unanswered"] = "unanswered"


Answer = Annotated[HumanHelp | NoAnswer, Field(discriminator="outcome")]


class Notifier(Protocol):
    """Tells people what an agent asks or says, with a link to the run's chat; everything else stays behind sign-in
    there. A failed send raises NotifyFailed."""

    async def help_requested(
        self, help_id: uuid.UUID, recipients: Sequence[UserView], reason: str, url: str
    ) -> None: ...

    async def agent_said(self, run_id: uuid.UUID, recipients: Sequence[UserView], text: str, url: str) -> None:
        """One message from the agent; Knock batches a burst of them into one email."""
        ...
