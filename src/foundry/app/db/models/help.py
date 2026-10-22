import datetime as dt
import enum
import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from foundry.app.db.db import Base, UtcDateTime


class HelpStatus(enum.StrEnum):
    OPEN = "open"
    CLAIMED = "claimed"
    DONE = "done"
    EXPIRED = "expired"


class HelpRequest(Base):
    """An agent stuck on a page it can't get past. A person claims it, works in the agent's browser, and marks it
    done; the agent waits for that, or gives up when it expires."""

    __tablename__ = "help_requests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    live_view_url: Mapped[str] = mapped_column(String(2048))
    status: Mapped[HelpStatus] = mapped_column(String(16))
    claimed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)
    expires_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
