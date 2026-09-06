from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import repository
from app.auth.schemas import RegisterRequest
from app.auth.security import (
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from app.core.auth import (
    EmailAlreadyRegistered,
    EmployeeIdAlreadyRegistered,
    InvalidCredentials,
    InvalidRefreshToken,
    UsernameAlreadyRegistered,
)
from app.db.models import RefreshToken, User, UserRole

__all__ = [
    "EmailAlreadyRegistered",
    "EmployeeIdAlreadyRegistered",
    "InvalidCredentials",
    "InvalidRefreshToken",
    "UsernameAlreadyRegistered",
    "authenticate_user",
    "issue_tokens",
    "register_user",
    "revoke_refresh_token",
    "rotate_refresh_token",
]


async def register_user(session: AsyncSession, data: RegisterRequest) -> User:
    if await repository.get_user_by_username(session, data.username) is not None:
        raise UsernameAlreadyRegistered
    if await repository.get_user_by_email(session, data.email) is not None:
        raise EmailAlreadyRegistered
    if await repository.get_user_by_employee_id(session, data.employee_id) is not None:
        raise EmployeeIdAlreadyRegistered

    # Bootstrap: the first user ever created becomes Admin regardless of what they requested -
    # a safety net guaranteeing at least one Admin exists even if the first registrant doesn't
    # think to pick it. Admin is otherwise a normal, selectable choice on the register form.
    user_count = await repository.count_users(session)
    role = UserRole.ADMIN if user_count == 0 else UserRole(data.role)

    user = User(
        username=data.username,
        email=data.email,
        password_hash=hash_password(data.password),
        employee_id=data.employee_id,
        department_shift=data.department_shift,
        role=role,
    )
    return await repository.add_user(session, user)


async def authenticate_user(session: AsyncSession, username: str, password: str) -> User:
    user = await repository.get_user_by_username(session, username)
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        raise InvalidCredentials
    return user


async def issue_tokens(session: AsyncSession, user: User) -> tuple[str, str]:
    """Returns (access_token, raw_refresh_token)."""

    access_token = create_access_token(user.id, user.role)
    raw_refresh_token, token_hash, expires_at = new_refresh_token()
    await repository.add_refresh_token(
        session, RefreshToken(user_id=user.id, token_hash=token_hash, expires_at=expires_at)
    )
    return access_token, raw_refresh_token


async def _find_active_refresh_token(session: AsyncSession, raw_refresh_token: str) -> RefreshToken:
    token_hash = hash_refresh_token(raw_refresh_token)
    token = await repository.get_refresh_token_by_hash(session, token_hash)
    now = datetime.now(UTC)
    if token is None or token.revoked_at is not None or token.expires_at < now:
        raise InvalidRefreshToken
    return token


async def rotate_refresh_token(session: AsyncSession, raw_refresh_token: str) -> tuple[str, str]:
    """Validates and revokes the given refresh token, issuing a fresh access + refresh token
    pair. Rotating (rather than reusing the same refresh token) means a stolen-but-unused token
    can't be replayed after the legitimate client has already refreshed with it."""

    token = await _find_active_refresh_token(session, raw_refresh_token)
    user = await repository.get_user_by_id(session, token.user_id)
    if user is None or not user.is_active:
        raise InvalidRefreshToken

    await repository.revoke_refresh_token(session, token, datetime.now(UTC))
    return await issue_tokens(session, user)


async def revoke_refresh_token(session: AsyncSession, raw_refresh_token: str) -> None:
    """Used by logout. Idempotent - an already-invalid token is simply a no-op, not an error."""

    try:
        token = await _find_active_refresh_token(session, raw_refresh_token)
    except InvalidRefreshToken:
        return
    await repository.revoke_refresh_token(session, token, datetime.now(UTC))
