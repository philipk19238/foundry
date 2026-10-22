"""Help requests change state only by conditional UPDATEs: of a claim, a done and an expiry that race, exactly one
lands, and the agent reads which (formal/help `OneOutcome`)."""

import datetime as dt
import uuid
from collections.abc import Sequence

from sqlalchemy import and_, bindparam, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from foundry.app.db.models.help import HelpRequest, HelpStatus

_LIVE = (HelpStatus.OPEN, HelpStatus.CLAIMED)

_ONE = select(HelpRequest).where(HelpRequest.id == bindparam("help"))
_OPEN = (
    select(HelpRequest)
    .where(HelpRequest.status.in_(_LIVE), HelpRequest.expires_at > bindparam("now"))
    .order_by(HelpRequest.created_at)
)
_RECENT = select(HelpRequest).order_by(HelpRequest.created_at.desc()).limit(bindparam("limit"))
_OF_RUN = select(HelpRequest).where(HelpRequest.run_id == bindparam("run")).order_by(HelpRequest.created_at)

_CLAIM = (
    update(HelpRequest)
    .where(
        HelpRequest.id == bindparam("help"),
        HelpRequest.status == HelpStatus.OPEN,
        HelpRequest.expires_at > bindparam("now"),
    )
    .values(status=HelpStatus.CLAIMED, claimed_by=bindparam("user"))
    .returning(HelpRequest.id)
)
_GIVE_BACK = (
    update(HelpRequest)
    .where(
        HelpRequest.id == bindparam("help"),
        HelpRequest.status == HelpStatus.CLAIMED,
        HelpRequest.claimed_by == bindparam("user"),
    )
    .values(status=HelpStatus.OPEN, claimed_by=None)
    .returning(HelpRequest.id)
)
_DONE = (
    update(HelpRequest)
    .where(
        HelpRequest.id == bindparam("help"),
        HelpRequest.status == HelpStatus.CLAIMED,
        HelpRequest.claimed_by == bindparam("user"),
        HelpRequest.expires_at > bindparam("now"),
    )
    .values(status=HelpStatus.DONE, note=bindparam("text"), resolved_at=bindparam("now"))
    .returning(HelpRequest.id)
)
_ANSWER = (
    update(HelpRequest)
    .where(
        HelpRequest.run_id == bindparam("run"),
        HelpRequest.expires_at > bindparam("now"),
        or_(
            HelpRequest.status == HelpStatus.OPEN,
            and_(HelpRequest.status == HelpStatus.CLAIMED, HelpRequest.claimed_by == bindparam("user")),
        ),
    )
    .values(status=HelpStatus.DONE, claimed_by=bindparam("user"), note=bindparam("text"), resolved_at=bindparam("now"))
    .returning(HelpRequest.id)
)
_EXPIRE = (
    update(HelpRequest)
    .where(HelpRequest.id == bindparam("help"), HelpRequest.status.in_(_LIVE))
    .values(status=HelpStatus.EXPIRED, resolved_at=bindparam("now"))
)


async def add(
    session: AsyncSession, run_id: uuid.UUID, reason: str, live_view_url: str, at: dt.datetime, expires_at: dt.datetime
) -> HelpRequest:
    request = HelpRequest(
        id=uuid.uuid4(),
        run_id=run_id,
        reason=reason,
        live_view_url=live_view_url,
        status=HelpStatus.OPEN,
        created_at=at,
        expires_at=expires_at,
    )
    session.add(request)
    await session.flush()
    return request


async def one(session: AsyncSession, help_id: uuid.UUID) -> HelpRequest | None:
    return await session.scalar(_ONE, {"help": help_id}, execution_options={"populate_existing": True})


async def open_requests(session: AsyncSession, at: dt.datetime) -> Sequence[HelpRequest]:
    return (await session.scalars(_OPEN, {"now": at})).all()


async def recent(session: AsyncSession, limit: int) -> Sequence[HelpRequest]:
    return (await session.scalars(_RECENT, {"limit": limit})).all()


async def of_run(session: AsyncSession, run_id: uuid.UUID) -> Sequence[HelpRequest]:
    return (await session.scalars(_OF_RUN, {"run": run_id})).all()


async def claim(session: AsyncSession, help_id: uuid.UUID, user_id: uuid.UUID, at: dt.datetime) -> bool:
    return await session.scalar(_CLAIM, {"help": help_id, "user": user_id, "now": at}) is not None


async def give_back(session: AsyncSession, help_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    return await session.scalar(_GIVE_BACK, {"help": help_id, "user": user_id}) is not None


async def done(session: AsyncSession, help_id: uuid.UUID, user_id: uuid.UUID, note: str, at: dt.datetime) -> bool:
    return await session.scalar(_DONE, {"help": help_id, "user": user_id, "text": note, "now": at}) is not None


async def answer(session: AsyncSession, run_id: uuid.UUID, user_id: uuid.UUID, text: str, at: dt.datetime) -> bool:
    """Answers the run's live request with ``text``, if it is open or this person holds it; a request someone else
    holds stays theirs, since they are driving the browser (formal/runs `OneDriver`)."""
    binds = {"run": run_id, "user": user_id, "text": text, "now": at}
    return await session.scalar(_ANSWER, binds) is not None


async def expire(session: AsyncSession, help_id: uuid.UUID, at: dt.datetime) -> None:
    await session.execute(_EXPIRE, {"help": help_id, "now": at})
