import datetime as dt
import uuid
from collections.abc import Sequence

from sqlalchemy import bindparam, delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from foundry.app.db.models.auth import ApiToken, Login, Role, User

_BY_EMAIL = select(User).where(User.email == bindparam("address"))
_BY_LOGIN = (
    select(User)
    .join(Login, Login.user_id == User.id)
    .where(Login.token_hash == bindparam("token"), Login.expires_at > bindparam("now"))
)
_BY_TOKEN = select(User).join(ApiToken, ApiToken.user_id == User.id).where(ApiToken.token_hash == bindparam("token"))
_USERS = select(User).order_by(User.created_at)
_TOKENS = select(ApiToken).where(ApiToken.user_id == bindparam("user")).order_by(ApiToken.created_at.desc())
_SIGN_OUT = delete(Login).where(Login.token_hash == bindparam("token"))
_ADD_LOGIN = insert(Login).values(
    token_hash=bindparam("token"), user_id=bindparam("user"), expires_at=bindparam("until")
)
_ADD_TOKEN = insert(ApiToken).values(
    token_hash=bindparam("token"), user_id=bindparam("user"), name=bindparam("label"), created_at=bindparam("at")
)
_REVOKE = delete(ApiToken).where(ApiToken.token_hash == bindparam("token"), ApiToken.user_id == bindparam("user"))


async def add_user(session: AsyncSession, email: str, password_hash: str, role: Role, created_at: dt.datetime) -> User:
    user = User(id=uuid.uuid4(), email=email, password_hash=password_hash, role=role, created_at=created_at)
    session.add(user)
    await session.flush()
    return user


async def by_email(session: AsyncSession, email: str) -> User | None:
    return await session.scalar(_BY_EMAIL, {"address": email})


async def users(session: AsyncSession) -> Sequence[User]:
    return (await session.scalars(_USERS)).all()


async def add_login(session: AsyncSession, token_hash: str, user_id: uuid.UUID, expires_at: dt.datetime) -> None:
    await session.execute(_ADD_LOGIN, {"token": token_hash, "user": user_id, "until": expires_at})


async def by_login(session: AsyncSession, token_hash: str, at: dt.datetime) -> User | None:
    return await session.scalar(_BY_LOGIN, {"token": token_hash, "now": at})


async def sign_out(session: AsyncSession, token_hash: str) -> None:
    await session.execute(_SIGN_OUT, {"token": token_hash})


async def add_token(
    session: AsyncSession, token_hash: str, user_id: uuid.UUID, name: str, created_at: dt.datetime
) -> None:
    await session.execute(_ADD_TOKEN, {"token": token_hash, "user": user_id, "label": name, "at": created_at})


async def by_token(session: AsyncSession, token_hash: str) -> User | None:
    return await session.scalar(_BY_TOKEN, {"token": token_hash})


async def tokens(session: AsyncSession, user_id: uuid.UUID) -> Sequence[ApiToken]:
    return (await session.scalars(_TOKENS, {"user": user_id})).all()


async def revoke(session: AsyncSession, token_hash: str, user_id: uuid.UUID) -> None:
    await session.execute(_REVOKE, {"token": token_hash, "user": user_id})
