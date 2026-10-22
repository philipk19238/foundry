import datetime as dt
import enum
import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from foundry.app.db.db import Base, UtcDateTime


class CredentialKind(enum.StrEnum):
    PASSWORD = "password"
    TOKEN = "token"
    API_KEY = "api_key"
    COOKIE = "cookie"
    OTHER = "other"


class Credential(Base):
    """A secret a portal's scraper signs in with, beyond its login: an API key, a bearer token, a second account.
    One per name per source; saving a name again replaces it. The value is sealed with the credentials key."""

    __tablename__ = "credentials"

    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[CredentialKind] = mapped_column(String(16))
    sealed: Mapped[str] = mapped_column(Text)
    note: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)
