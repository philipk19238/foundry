import datetime as dt
import enum
import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from foundry.app.db.db import Base, UtcDateTime


class Role(enum.StrEnum):
    ADMIN = "admin"
    HELPER = "helper"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[Role] = mapped_column(String(16))
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)


class Login(Base):
    """A signed-in browser. The cookie holds the token; only its hash is stored."""

    __tablename__ = "logins"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)


class ApiToken(Base):
    __tablename__ = "api_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)
