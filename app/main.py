"""Composition root: creates the app, wires up middleware/lifespan, and mounts every route
module under app/api/. Carries no route declarations of its own - see app/api/'s own docstring
for how routes are organized and where to add a new one.
"""

import json
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import jwt
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, auth, chat, health, orchestrator, uploads
from app.api.auth import ACCESS_TOKEN_COOKIE
from app.auth.security import decode_access_token
from app.config.logging_config import configure_logging
from app.config.settings import settings
from app.db import init_models

_access_logger = logging.getLogger("app.access")
logger = logging.getLogger(__name__)

# Configured at import time (like each app/api/ module's own module-level setup) rather than
# inside lifespan, so anything logged before the app finishes starting up - or by a standalone
# script that imports app.main - still gets the right format. See app/config/logging_config.py.
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

# Every API surface the app exposes, one module per domain - see app/api/'s docstring. Adding a
# new endpoint means adding (or extending) a module under app/api/, not this file.
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(uploads.router)
app.include_router(admin.router)
app.include_router(chat.router)
app.include_router(orchestrator.router)


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
    the source instead (app/chat/streaming.py's chat_sse, app/agents/orchestrator_agent/streaming.py's
    orchestrator_sse)."""

    debug_enabled = _access_logger.isEnabledFor(logging.DEBUG)
    request_body_bytes = await request.body() if debug_enabled else b""

    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000

    user_id: str | None = None
    access_token = request.cookies.get(ACCESS_TOKEN_COOKIE)
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
