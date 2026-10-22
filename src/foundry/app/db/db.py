"""The database: one engine per process, and the column types every table shares."""

import datetime as dt
from typing import NewType

import svcs
from sqlalchemy import JSON, DateTime, Dialect, MetaData, TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from foundry.app.settings import settings

Sessionmaker = NewType("Sessionmaker", async_sessionmaker[AsyncSession])

Json = JSON().with_variant(JSONB(), "postgresql")


class UtcDateTime(TypeDecorator[dt.datetime]):
    """A UTC instant: stored without a zone (SQLite keeps none), always read back as an aware UTC datetime."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: dt.datetime | None, dialect: Dialect) -> dt.datetime | None:
        return value.astimezone(dt.UTC).replace(tzinfo=None) if value is not None else None

    def process_result_value(self, value: dt.datetime | None, dialect: Dialect) -> dt.datetime | None:
        return value.replace(tzinfo=dt.UTC) if value is not None else None


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def register(registry: svcs.Registry) -> None:
    engine = create_async_engine(settings.database_url)
    registry.register_value(AsyncEngine, engine, on_registry_close=engine.dispose)
    registry.register_value(Sessionmaker, Sessionmaker(async_sessionmaker(engine, expire_on_commit=False)))
