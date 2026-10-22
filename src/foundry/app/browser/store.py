"""A portal's sign-in state: the account the agent made and the session read from its browser, on the source row,
and its other credentials, one row per name."""

import uuid
from collections.abc import Sequence

from pydantic import JsonValue
from sqlalchemy import and_, bindparam, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from foundry.app.core.models import Lease
from foundry.app.db.models.access import Credential
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

_CREDENTIALS = select(Credential).where(Credential.source_id == bindparam("source")).order_by(Credential.name)
_FORGET = delete(Credential).where(Credential.source_id == bindparam("source"), Credential.name == bindparam("label"))


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


async def credentials(session: AsyncSession, source_id: uuid.UUID) -> Sequence[Credential]:
    return (await session.scalars(_CREDENTIALS, {"source": source_id})).all()


async def put_credential(session: AsyncSession, credential: Credential) -> None:
    """Saves a credential, replacing the one with its name."""
    await session.execute(_FORGET, {"source": credential.source_id, "label": credential.name})
    session.add(credential)


async def forget_credential(session: AsyncSession, source_id: uuid.UUID, name: str) -> None:
    await session.execute(_FORGET, {"source": source_id, "label": name})
