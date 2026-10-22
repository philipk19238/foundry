import datetime as dt
import enum
import uuid

from pydantic import JsonValue
from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from foundry.app.db.db import Base, Json, UtcDateTime


class SourceStatus(enum.StrEnum):
    NEW = "new"
    ONBOARDING = "onboarding"
    READY = "ready"
    RELOGIN = "relogin"
    HEALING = "healing"
    IN_REVIEW = "in_review"
    NEEDS_HUMAN = "needs_human"


class ScraperState(enum.StrEnum):
    PROPOSED = "proposed"
    MERGED = "merged"
    CLOSED = "closed"


class Source(Base):
    """A portal we scrape. Its lease columns say which run holds it; `fence` grows with every new holder."""

    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    url: Mapped[str] = mapped_column(String(2048), unique=True)
    name: Mapped[str] = mapped_column(String(256))
    status: Mapped[SourceStatus] = mapped_column(String(32), default=SourceStatus.NEW)
    heals: Mapped[int] = mapped_column(Integer, default=0)
    previous_valid: Mapped[int] = mapped_column(Integer, default=0)
    site: Mapped[str | None] = mapped_column(String(512))
    scraper_version: Mapped[int | None]
    session: Mapped[dict[str, JsonValue] | None] = mapped_column(Json)
    username: Mapped[str | None] = mapped_column(String(320))
    secret: Mapped[str | None] = mapped_column(Text)
    lease_run_id: Mapped[uuid.UUID | None]
    lease_expires_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    fence: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)


class Buyer(Base):
    __tablename__ = "buyers"

    buyer_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(512))


class Notice(Base):
    """One version of an opportunity. `opportunity_id` hashes what never changes (portal and portal id); `notice_id`
    hashes everything, so an edit on the portal is a new row and a re-scrape of the same content is no row. `run_id`
    is the crawl that first stored it, whose bronze holds what the portal sent for it."""

    __tablename__ = "notices"
    __table_args__ = (
        Index("ix_notices_opportunity_id_first_seen_at", "opportunity_id", "first_seen_at", "notice_id"),
        Index("ix_notices_source_id_first_seen_at", "source_id", "first_seen_at"),
    )

    notice_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    opportunity_id: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"))
    buyer_id: Mapped[str | None] = mapped_column(ForeignKey("buyers.buyer_id"))
    external_id: Mapped[str | None] = mapped_column(String(512))
    url: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(Text)
    due_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    description: Mapped[str] = mapped_column(Text)
    metadata_: Mapped[dict[str, JsonValue]] = mapped_column("metadata", Json)
    first_seen_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)
    run_id: Mapped[uuid.UUID | None]


class File(Base):
    __tablename__ = "files"

    notice_id: Mapped[str] = mapped_column(ForeignKey("notices.notice_id"), primary_key=True)
    file_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sha256: Mapped[str | None] = mapped_column(String(64))
    etag: Mapped[str | None] = mapped_column(String(256))
    name: Mapped[str] = mapped_column(String(1024))
    url: Mapped[str] = mapped_column(String(2048))
    content_type: Mapped[str | None] = mapped_column(String(256))
    size: Mapped[int] = mapped_column(Integer)


class Scraper(Base):
    """One version of a source's spider: proposed as a pull request from an agent's branch, then merged (and run from
    the merge commit) or closed."""

    __tablename__ = "scrapers"
    __table_args__ = (UniqueConstraint("source_id", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"))
    version: Mapped[int] = mapped_column(Integer)
    site: Mapped[str] = mapped_column(String(512))
    state: Mapped[ScraperState] = mapped_column(String(16))
    branch: Mapped[str] = mapped_column(String(256))
    pull: Mapped[int] = mapped_column(Integer)
    commit: Mapped[str | None] = mapped_column(String(40))
    run_id: Mapped[uuid.UUID]
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime)
