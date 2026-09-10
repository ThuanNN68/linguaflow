"""Server-side direct-chat blocking rules."""

from __future__ import annotations

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import BlockedUser


class DirectMessagingBlockedError(Exception):
    """A direct-chat action is disallowed by either participant's block."""


async def is_blocked_between(db: AsyncSession, first_id: str, second_id: str) -> bool:
    """Check the two permitted directional rows in one targeted query."""
    if first_id == second_id:
        return False
    match = await db.scalar(
        select(BlockedUser.blocker_id).where(
            or_(
                (BlockedUser.blocker_id == first_id) & (BlockedUser.blocked_id == second_id),
                (BlockedUser.blocker_id == second_id) & (BlockedUser.blocked_id == first_id),
            )
        ).limit(1)
    )
    return match is not None


async def block_user(db: AsyncSession, blocker_id: str, blocked_id: str) -> None:
    if blocker_id == blocked_id:
        raise ValueError("You cannot block yourself")
    relation = BlockedUser(blocker_id=blocker_id, blocked_id=blocked_id)
    db.add(relation)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()


async def unblock_user(db: AsyncSession, blocker_id: str, blocked_id: str) -> None:
    await db.execute(
        delete(BlockedUser).where(
            BlockedUser.blocker_id == blocker_id,
            BlockedUser.blocked_id == blocked_id,
        )
    )
    await db.commit()


async def list_blocked_user_ids(db: AsyncSession, blocker_id: str) -> list[str]:
    rows = await db.scalars(
        select(BlockedUser.blocked_id)
        .where(BlockedUser.blocker_id == blocker_id)
        .order_by(BlockedUser.created_at.desc(), BlockedUser.blocked_id.desc())
    )
    return list(rows)
