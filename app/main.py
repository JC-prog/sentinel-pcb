import json
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any

import jwt
from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.agents.adc_inspection_agent import golden_images
from app.agents.adc_inspection_agent.schemas import GoldenImageOut
from app.agents.orchestrator_agent.router import router as orchestrator_router
from app.auth import LoginRequest, RegisterRequest, UserOut, get_current_user
from app.auth.dependencies import SessionDep
from app.auth.security import decode_access_token
from app.auth.service import (
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
from app.chat.router import router as chat_router
from app.config.logging_config import configure_logging
from app.config.settings import settings
from app.db import User, UserRole, init_models
from app.uploads import UploadRecord, resolve_upload_path, save_upload

_ACCESS_TOKEN_COOKIE = "access_token"
_REFRESH_TOKEN_COOKIE = "refresh_token"
_REFRESH_TOKEN_PATH = "/api/auth"

_access_logger = logging.getLogger("app.access")
logger = logging.getLogger(__name__)

# Configured at import time (like each router's own module-level setup) rather than inside
# lifespan, so anything logged before the app finishes starting up - or by a standalone script
# that imports app.main - still gets the right format. See app/config/logging_config.py.
configure_logging()


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

# Each domain owns its own routes - see app/chat/router.py (chat streaming, conversations, the
# Explainability & Review Agent's direct-invocation endpoint) and
# app/agents/orchestrator_agent/router.py (the Work tab's uploads and streaming run endpoint).
# What stays here is app-wide: setup/middleware/lifespan, auth, generic uploads, and the one
# small admin endpoint below.
app.include_router(chat_router)
app.include_router(orchestrator_router)


def _redact_and_parse_json_body(raw: bytes, content_type: str) -> Any | None:
    """Best-effort JSON parse for debug logging - returns None for non-JSON bodies (e.g. the
    multipart image upload) rather than trying to describe arbitrary binary content. Redacts the
    top-level "password" field (register/login) so it never ends up in a log line."""

    if "application/json" not in content_type or not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if isinstance(data, dict) and "password" in data:
        data = {**data, "password": "***"}
    return data


_STREAMING_RESPONSE_PATHS = frozenset({"/api/chat/stream", "/api/orchestrator/run/stream"})


@app.middleware("http")
async def _log_requests(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """One structured INFO line per request (method, path, status, duration, and the caller's
    user id when authenticated) - Uvicorn's own access log already prints a plain-text line per
    request, but doesn't attach these as separate, queryable fields the way app/config/logging_config.py's
    JSON formatter can. Decodes the access_token cookie directly (JWT only, no DB round trip) just
    to attribute the log line - any failure (missing/expired/invalid token) just means an
    unauthenticated-looking log line, not a 401; auth itself is still enforced by
    get_current_user on whichever routes require it.

    Separately, at DEBUG (settings.log_level), also logs the request/response JSON bodies - gated
    behind an isEnabledFor() check done *before* touching either body, so there's zero extra
    buffering when the feature is off (this matters for e.g. large image uploads).

    `call_next()`'s return value is always Starlette's internal `_StreamingResponse` wrapper
    (confirmed against BaseHTTPMiddleware's source - it wraps every response this way, not just
    ones that were "really" streaming), so there's no in-memory `.body` to read here for any
    route - the body has to be drained from `response.body_iterator` and a fresh Response
    reconstructed from the collected bytes so the client still receives it. That's fine for
    ordinary quick JSON responses, but draining a streaming route's body_iterator here would
    buffer the *entire* SSE stream before any of it reaches the browser - so those routes are
    explicitly skipped (_STREAMING_RESPONSE_PATHS) and log their own request/response content at
    the source instead (chat's _chat_sse, orchestrator_agent's _orchestrator_sse)."""

    debug_enabled = _access_logger.isEnabledFor(logging.DEBUG)
    request_body_bytes = await request.body() if debug_enabled else b""

    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000

    user_id: str | None = None
    access_token = request.cookies.get(_ACCESS_TOKEN_COOKIE)
    if access_token:
        try:
            user_id = decode_access_token(access_token).user_id
        except jwt.PyJWTError:
            pass

    _access_logger.info(
        "%s %s -> %s (%.1fms)",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": round(duration_ms, 1),
            "user_id": user_id,
        },
    )

    if debug_enabled:
        request_body = _redact_and_parse_json_body(
            request_body_bytes, request.headers.get("content-type", "")
        )
        response_body = None
        if request.url.path not in _STREAMING_RESPONSE_PATHS:
            # body_iterator exists at runtime (call_next() always returns Starlette's internal
            # _StreamingResponse - see the docstring above) but isn't part of Response's public
            # type, hence the ignore.
            body_iterator = response.body_iterator  # type: ignore[attr-defined]
            response_body_bytes = b"".join([chunk async for chunk in body_iterator])
            response_body = _redact_and_parse_json_body(
                response_body_bytes, response.headers.get("content-type", "")
            )
            # body_iterator is now exhausted - rebuild a Response over the same bytes so the
            # client still receives it (this becomes what's actually returned, below).
            # headers=dict(response.headers) would silently collapse repeated header names (e.g.
            # register/login/refresh's two Set-Cookie headers) down to just the first one - dict()
            # on Starlette's Headers keeps only one value per key. Copy raw_headers instead so
            # every original header, duplicates included, survives the rebuild.
            original_headers = response.raw_headers
            response = Response(content=response_body_bytes, status_code=response.status_code)
            response.raw_headers = original_headers
        _access_logger.debug(
            "%s %s request body: %s response body: %s",
            request.method,
            request.url.path,
            request_body,
            response_body,
            extra={"request_body": request_body, "response_body": response_body},
        )
    return response


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
    except UsernameAlreadyRegistered as exc:
        raise HTTPException(status_code=409, detail="username already registered") from exc
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
        user = await authenticate_user(session, request.username, request.password)
    except InvalidCredentials as exc:
        raise HTTPException(status_code=401, detail="incorrect username or password") from exc

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


@app.post("/api/uploads/xml")
async def upload_inspection_xml(
    file: Annotated[UploadFile, File()],
    _user: Annotated[User, Depends(get_current_user)],
) -> UploadRecord:
    """Same storage (app.uploads.service) as image uploads - content-type-agnostic already, so no
    new storage dir/setting is needed for this. Only consumed by create_case
    (app/agents/adc_inspection_agent/), and only optionally there."""

    if not (file.filename or "").lower().endswith(".xml"):
        raise HTTPException(status_code=422, detail="file must be an XML document")
    return await save_upload(file)


@app.post("/api/admin/golden-images", status_code=201)
async def register_golden_image(
    board_id: Annotated[str, Form()],
    component_ref: Annotated[str, Form()],
    package: Annotated[str, Form()],
    feature: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    user: Annotated[User, Depends(get_current_user)],
    session: SessionDep,
    notes: Annotated[str | None, Form()] = None,
) -> GoldenImageOut:
    """Admin-only: registers one golden reference image, looked up later by
    app/agents/adc_inspection_agent/golden_images.py's find_golden_image() when a case is flagged
    for the same board_id/component_ref/package/feature. Minimal by design - a single-image
    registration endpoint, not a bulk importer or management UI."""

    if UserRole(user.role) != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="admin role required")
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=422, detail="file must be an image")

    golden = await golden_images.save_golden_image(
        session,
        file_bytes=await file.read(),
        filename=file.filename or "golden.png",
        board_id=board_id,
        component_ref=component_ref,
        package=package,
        feature=feature,
        notes=notes,
        registered_by_user_id=user.id,
    )
    return GoldenImageOut.model_validate(golden)
