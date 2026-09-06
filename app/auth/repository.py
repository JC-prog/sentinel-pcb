from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RefreshToken, User


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    user = await session.scalar(select(User).where(User.username == username))
    return user


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    user = await session.scalar(select(User).where(User.email == email))
    return user


async def get_user_by_employee_id(session: AsyncSession, employee_id: str) -> User | None:
    user = await session.scalar(select(User).where(User.employee_id == employee_id))
    return user


async def get_user_by_id(session: AsyncSession, user_id: str) -> User | None:
    return await session.get(User, user_id)


async def count_users(session: AsyncSession) -> int:
    result = await session.scalar(select(func.count()).select_from(User))
    return result or 0


async def save_user(session: AsyncSession, user: User) -> User:
    """Commits pending changes to `user` (new or already-mutated) and returns the refreshed row."""

    await session.commit()
    await session.refresh(user)
    return user


async def add_user(session: AsyncSession, user: User) -> User:
    session.add(user)
    return await save_user(session, user)


async def add_refresh_token(session: AsyncSession, token: RefreshToken) -> RefreshToken:
    session.add(token)
    await session.commit()
    return token


async def get_refresh_token_by_hash(session: AsyncSession, token_hash: str) -> RefreshToken | None:
    token = await session.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    return token


async def revoke_refresh_token(
    session: AsyncSession, token: RefreshToken, revoked_at: datetime
) -> None:
    token.revoked_at = revoked_at
    await session.commit()
