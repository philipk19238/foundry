import asyncio

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from foundry.app.db.db import Base
from foundry.app.db.models import auth, catalog, help, runs  # noqa: F401
from foundry.app.settings import settings


def migrate(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


async def online() -> None:
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as connection:
        await connection.run_sync(migrate)
    await engine.dispose()


asyncio.run(online())
