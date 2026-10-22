"""Learnings, one row per name."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from foundry.app.db.models.learnings import Learning

_ALL = select(Learning).order_by(Learning.about, Learning.name)


async def learnings(session: AsyncSession) -> Sequence[Learning]:
    return (await session.scalars(_ALL)).all()


async def keep(session: AsyncSession, learning: Learning) -> None:
    """Saves a learning, replacing the one with its name."""
    await session.merge(learning)
