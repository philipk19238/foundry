"""A portal's sign-in state on its source row: the account the agent made, and the session read from its browser."""

import uuid

from pydantic import JsonValue
from sqlalchemy import and_, bindparam, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from foundry.app.core.models import Lease
from foundry.app.db.models.catalog import Source

_HELD = and_(
    Source.id == bindparam("source"),
    Source.lease_run_id == bindparam("run"),
    Source.fence == bindparam("token"),
)

_ACCOUNT = select(Source.username, Source.secret).where(Source.id == bindparam("source"))
_SESSION = select(Source.session).where(Source.id == bindparam("source"))
_SET_SESSION = update(Source).where(_HELD).values(session=bindparam("cookies")).returning(Source.id)
_SET_ACCOUNT = (
    update(Source).where(_HELD).values(username=bindparam("login"), secret=bindparam("sealed")).returning(Source.id)
)


def _held(lease: Lease) -> dict[str, uuid.UUID | int]:
    return {"source": lease.source_id, "run": lease.run_id, "token": lease.fence}


async def account(session: AsyncSession, source_id: uuid.UUID) -> tuple[str, str] | None:
    row = (await session.execute(_ACCOUNT, {"source": source_id})).one_or_none()
    if row is None or row.username is None or row.secret is None:
        return None
    return row.username, row.secret


async def portal_session(session: AsyncSession, source_id: uuid.UUID) -> dict[str, JsonValue] | None:
    return await session.scalar(_SESSION, {"source": source_id})


async def set_session(session: AsyncSession, lease: Lease, cookies: dict[str, JsonValue]) -> bool:
    return await session.scalar(_SET_SESSION, {**_held(lease), "cookies": cookies}) is not None


async def set_account(session: AsyncSession, lease: Lease, username: str, sealed: str) -> bool:
    return await session.scalar(_SET_ACCOUNT, {**_held(lease), "login": username, "sealed": sealed}) is not None
