from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.schemas import RegisterRequest
from app.auth.security import (
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from app.db.models import RefreshToken, User, UserRole


class EmailAlreadyRegistered(Exception):
    pass


class EmployeeIdAlreadyRegistered(Exception):
    pass


class InvalidCredentials(Exception):
    pass


class InvalidRefreshToken(Exception):
    pass


async def register_user(session: AsyncSession, data: RegisterRequest) -> User:
    if await session.scalar(select(User).where(User.email == data.email)) is not None:
        raise EmailAlreadyRegistered
    if await session.scalar(select(User).where(User.employee_id == data.employee_id)) is not None:
        raise EmployeeIdAlreadyRegistered

    # Bootstrap: the first user ever created becomes Admin regardless of what they requested -
    # Admin is deliberately not a choice on the public register form (app/auth/schemas.py). Every
    # user after that gets exactly the role they asked for (QA or Operator only).
    user_count = await session.scalar(select(func.count()).select_from(User))
    role = UserRole.ADMIN if user_count == 0 else UserRole(data.role)

    user = User(
        name=data.name,
        email=data.email,
        password_hash=hash_password(data.password),
        employee_id=data.employee_id,
        department_shift=data.department_shift,
        role=role,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def authenticate_user(session: AsyncSession, email: str, password: str) -> User:
    user = await session.scalar(select(User).where(User.email == email))
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        raise InvalidCredentials
    return user


async def issue_tokens(session: AsyncSession, user: User) -> tuple[str, str]:
    """Returns (access_token, raw_refresh_token)."""

    access_token = create_access_token(user.id, user.role)
    raw_refresh_token, token_hash, expires_at = new_refresh_token()
    session.add(RefreshToken(user_id=user.id, token_hash=token_hash, expires_at=expires_at))
    await session.commit()
    return access_token, raw_refresh_token


async def _find_active_refresh_token(session: AsyncSession, raw_refresh_token: str) -> RefreshToken:
    token_hash = hash_refresh_token(raw_refresh_token)
    token = await session.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    now = datetime.now(UTC)
    if token is None or token.revoked_at is not None or token.expires_at < now:
        raise InvalidRefreshToken
    return token


async def rotate_refresh_token(session: AsyncSession, raw_refresh_token: str) -> tuple[str, str]:
    """Validates and revokes the given refresh token, issuing a fresh access + refresh token
    pair. Rotating (rather than reusing the same refresh token) means a stolen-but-unused token
    can't be replayed after the legitimate client has already refreshed with it."""

    token = await _find_active_refresh_token(session, raw_refresh_token)
    user = await session.get(User, token.user_id)
    if user is None or not user.is_active:
        raise InvalidRefreshToken

    token.revoked_at = datetime.now(UTC)
    await session.commit()
    return await issue_tokens(session, user)


async def revoke_refresh_token(session: AsyncSession, raw_refresh_token: str) -> None:
    """Used by logout. Idempotent - an already-invalid token is simply a no-op, not an error."""

    try:
        token = await _find_active_refresh_token(session, raw_refresh_token)
    except InvalidRefreshToken:
        return
    token.revoked_at = datetime.now(UTC)
    await session.commit()
