import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Cookie, Depends, FastAPI, File, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import SecretStr

from app.auth import LoginRequest, RegisterRequest, UserOut, get_current_user
from app.auth.dependencies import SessionDep
from app.auth.security import decode_access_token
from app.auth.service import (
    EmailAlreadyRegistered,
    EmployeeIdAlreadyRegistered,
    InvalidCredentials,
    InvalidRefreshToken,
    authenticate_user,
    issue_tokens,
    register_user,
    revoke_refresh_token,
    rotate_refresh_token,
)
from app.chat import ChatStreamRequest, get_chat_service
from app.chat.schemas import LlmProvider
from app.db import User, init_models
from app.settings import settings
from app.uploads import UploadRecord, resolve_upload_path, save_upload

_ACCESS_TOKEN_COOKIE = "access_token"
_REFRESH_TOKEN_COOKIE = "refresh_token"
_REFRESH_TOKEN_PATH = "/api/auth"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await init_models()
    yield


app = FastAPI(title="SentinelChat", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    response.set_cookie(
        _ACCESS_TOKEN_COOKIE,
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
    response.delete_cookie(_ACCESS_TOKEN_COOKIE, path="/")
    response.delete_cookie(_REFRESH_TOKEN_COOKIE, path=_REFRESH_TOKEN_PATH)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/auth/register", status_code=201)
async def register(request: RegisterRequest, response: Response, session: SessionDep) -> UserOut:
    try:
        user = await register_user(session, request)
    except EmailAlreadyRegistered as exc:
        raise HTTPException(status_code=409, detail="email already registered") from exc
    except EmployeeIdAlreadyRegistered as exc:
        raise HTTPException(status_code=409, detail="employee ID already registered") from exc

    access_token, refresh_token = await issue_tokens(session, user)
    _set_auth_cookies(response, access_token, refresh_token)
    return UserOut.model_validate(user)


@app.post("/api/auth/login")
async def login(request: LoginRequest, response: Response, session: SessionDep) -> UserOut:
    try:
        user = await authenticate_user(session, request.email, request.password)
    except InvalidCredentials as exc:
        raise HTTPException(status_code=401, detail="incorrect email or password") from exc

    access_token, refresh_token = await issue_tokens(session, user)
    _set_auth_cookies(response, access_token, refresh_token)
    return UserOut.model_validate(user)


@app.post("/api/auth/logout", status_code=204)
async def logout(
    response: Response,
    session: SessionDep,
    refresh_token: Annotated[str | None, Cookie()] = None,
) -> None:
    if refresh_token is not None:
        await revoke_refresh_token(session, refresh_token)
    _clear_auth_cookies(response)


@app.post("/api/auth/refresh")
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


@app.get("/api/auth/me")
async def me(user: Annotated[User, Depends(get_current_user)]) -> UserOut:
    return UserOut.model_validate(user)


@app.post("/api/uploads")
async def upload_image(
    file: Annotated[UploadFile, File()],
    _user: Annotated[User, Depends(get_current_user)],
) -> UploadRecord:
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=422, detail="file must be an image")
    return await save_upload(file)


@app.get("/api/uploads/{filename}")
async def get_upload(
    filename: str,
    _user: Annotated[User, Depends(get_current_user)],
) -> FileResponse:
    path = resolve_upload_path(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="upload not found")
    return FileResponse(path)


async def _chat_sse(
    provider: LlmProvider, openai_api_key: SecretStr | None, message: str, image_ids: list[str]
) -> AsyncGenerator[str, None]:
    """SSE body for POST /api/chat/stream: `event: delta` per chunk from the chat service,
    `event: error` if it raises, always ending in `event: done`. Same framing as the original
    app's `_trace_stream` (GET /workflows/{id}/trace)."""

    service = get_chat_service(provider, openai_api_key)
    try:
        async for chunk in service.stream_reply(message, image_ids):
            yield f"event: delta\ndata: {json.dumps({'text': chunk})}\n\n"
    except Exception as exc:  # noqa: BLE001 - reported to the client as an SSE error event
        yield f"event: error\ndata: {json.dumps({'message': str(exc)})}\n\n"
        return
    yield "event: done\ndata: {}\n\n"


@app.post("/api/chat/stream")
async def chat_stream(
    request: ChatStreamRequest,
    _user: Annotated[User, Depends(get_current_user)],
) -> StreamingResponse:
    if not request.message.strip() and not request.image_ids:
        raise HTTPException(status_code=422, detail="message must not be empty")
    if request.provider == "openai" and request.openai_api_key is None:
        raise HTTPException(status_code=422, detail="openai_api_key is required for provider=openai")
    return StreamingResponse(
        _chat_sse(request.provider, request.openai_api_key, request.message, request.image_ids),
        media_type="text/event-stream",
    )
