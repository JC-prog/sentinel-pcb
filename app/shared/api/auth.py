"""Routes only - see app/shared/auth/service.py for credential/token business logic. Cookie
get/set/clear lives here rather than in the service layer since it's purely an HTTP response
concern (app/shared/auth/service.py issues tokens without knowing about Response objects or cookies at
all)."""

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response

from app.shared.auth import LoginRequest, RegisterRequest, UserOut, get_current_user
from app.shared.auth.dependencies import SessionDep
from app.shared.auth.security import decode_access_token
from app.shared.auth.service import (
    EmailAlreadyRegistered,
    EmployeeIdAlreadyRegistered,
    InvalidCredentials,
    InvalidRefreshToken,
    UsernameAlreadyRegistered,
    authenticate_user,
    issue_tokens,
    register_user,
    revoke_refresh_token,
    rotate_refresh_token,
)
from app.shared.config.settings import settings
from app.shared.db import User

# Public (not module-private) since app/main.py's request-logging middleware also needs the
# cookie name, to attribute a log line to a user without a DB round trip.
ACCESS_TOKEN_COOKIE = "access_token"
_REFRESH_TOKEN_COOKIE = "refresh_token"
_REFRESH_TOKEN_PATH = "/api/auth"

router = APIRouter()


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    response.set_cookie(
        ACCESS_TOKEN_COOKIE,
        access_token,
        max_age=settings.jwt_access_token_expires_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    # Scoped to /api/auth only - the refresh token doesn't need to (and shouldn't) go out on
    # every chat/upload request, only to the endpoints that actually use it.
    response.set_cookie(
        _REFRESH_TOKEN_COOKIE,
        refresh_token,
        max_age=settings.jwt_refresh_token_expires_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path=_REFRESH_TOKEN_PATH,
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_TOKEN_COOKIE, path="/")
    response.delete_cookie(_REFRESH_TOKEN_COOKIE, path=_REFRESH_TOKEN_PATH)


@router.post("/api/auth/register", status_code=201)
async def register(request: RegisterRequest, response: Response, session: SessionDep) -> UserOut:
    try:
        user = await register_user(session, request)
    except UsernameAlreadyRegistered as exc:
        raise HTTPException(status_code=409, detail="username already registered") from exc
    except EmailAlreadyRegistered as exc:
        raise HTTPException(status_code=409, detail="email already registered") from exc
    except EmployeeIdAlreadyRegistered as exc:
        raise HTTPException(status_code=409, detail="employee ID already registered") from exc

    access_token, refresh_token = await issue_tokens(session, user)
    _set_auth_cookies(response, access_token, refresh_token)
    return UserOut.model_validate(user)


@router.post("/api/auth/login")
async def login(request: LoginRequest, response: Response, session: SessionDep) -> UserOut:
    try:
        user = await authenticate_user(session, request.username, request.password)
    except InvalidCredentials as exc:
        raise HTTPException(status_code=401, detail="incorrect username or password") from exc

    access_token, refresh_token = await issue_tokens(session, user)
    _set_auth_cookies(response, access_token, refresh_token)
    return UserOut.model_validate(user)


@router.post("/api/auth/logout", status_code=204)
async def logout(
    response: Response,
    session: SessionDep,
    refresh_token: Annotated[str | None, Cookie()] = None,
) -> None:
    if refresh_token is not None:
        await revoke_refresh_token(session, refresh_token)
    _clear_auth_cookies(response)


@router.post("/api/auth/refresh")
async def refresh(
    response: Response,
    session: SessionDep,
    refresh_token: Annotated[str | None, Cookie()] = None,
) -> UserOut:
    if refresh_token is None:
        raise HTTPException(status_code=401, detail="no refresh token")
    try:
        access_token, new_refresh_token = await rotate_refresh_token(session, refresh_token)
    except InvalidRefreshToken as exc:
        _clear_auth_cookies(response)
        raise HTTPException(status_code=401, detail="invalid or expired refresh token") from exc

    payload = decode_access_token(access_token)
    user = await session.get(User, payload.user_id)
    assert user is not None  # rotate_refresh_token already checked this user exists and is active
    _set_auth_cookies(response, access_token, new_refresh_token)
    return UserOut.model_validate(user)


@router.get("/api/auth/me")
async def me(user: Annotated[User, Depends(get_current_user)]) -> UserOut:
    return UserOut.model_validate(user)
