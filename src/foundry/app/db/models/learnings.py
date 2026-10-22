import datetime as dt
import uuid

from sqlalchemy import String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from foundry.app.db.db import Base, UtcDateTime


class Learning(Base):
    """Something an agent learned that the next agent should know: one fact per row, named, shared by every run.
    `about` says what it applies to (a platform, a buyer, or everything); saving a name again replaces it."""

    __tablename__ = "learnings"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    about: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    updated_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)
