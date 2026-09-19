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
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image

from app.agents import (
    CreateCaseTool,
    CurrentTimeAgentTool,
    FlagCaseForRetrainingTool,
    InvestigateCaseTool,
    ListCasesTool,
    MonitoringAgentTool,
    ReviewCaseTool,
    ToolRegistry,
    WeatherAgentTool,
    call_tool,
)
from app.agents.access import allowed_tool_names
from app.agents.adc_inspection_agent import golden_images
from app.agents.adc_inspection_agent.schemas import GoldenImageOut
from app.agents.explainability_review_agent import (
    ExplainabilityReviewRequest,
    ExplainabilityReviewResponse,
    ExplainabilityReviewTool,
)
from app.agents.orchestrator_agent import run_stream as orchestrator_run_stream
from app.agents.orchestrator_agent import uploads as orchestrator_uploads
from app.agents.orchestrator_agent.schemas import OrchestratorRunRequest, OrchestratorUploadRecord
from app.agents.router_agent import Clarify, route
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
from app.chat import ChatStreamRequest, get_chat_service, history
from app.chat import repository as chat_repository
from app.chat.messages import build_messages
from app.chat.schemas import ConversationDetail, ConversationSummary, LlmProvider, MessageOut
from app.config.logging_config import configure_logging
from app.config.settings import settings
from app.core.chat import ChatMessage, ConversationNotFound, TextDelta, ToolCallRequest
from app.db import Conversation, User, UserRole, init_models
from app.memory import build_memory_preamble, maybe_extract, remember_explicit
from app.uploads import UploadRecord, resolve_upload_path, save_upload

_REMEMBER_PREFIX = "/remember "

_ACCESS_TOKEN_COOKIE = "access_token"
_REFRESH_TOKEN_COOKIE = "refresh_token"
_REFRESH_TOKEN_PATH = "/api/auth"

_access_logger = logging.getLogger("app.access")
logger = logging.getLogger(__name__)

# Configured at import time (like tool_registry below) rather than inside lifespan, so anything
# logged before the app finishes starting up - or by a standalone script that imports app.main -
# still gets the right format. See app/config/logging_config.py.
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
    ordinary quick JSON responses, but draining `/api/chat/stream`'s body_iterator here would
    buffer the *entire* SSE stream before any of it reaches the browser - so that one route is
    explicitly skipped and logs its own request/response content at the source (_chat_sse)."""

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


# Constructing ExplainabilityReviewTool() here doesn't load anything heavy - it's a thin wrapper;
# the actual CLIP model load is deferred to first use of the agent (see
# app/agents/explainability_review_agent/graph.py's get_mcp_client()).
tool_registry = ToolRegistry(
    [
        CreateCaseTool(),
        CurrentTimeAgentTool(),
        ExplainabilityReviewTool(),
        FlagCaseForRetrainingTool(),
        InvestigateCaseTool(),
        ListCasesTool(),
        MonitoringAgentTool(),
        ReviewCaseTool(),
        WeatherAgentTool(),
    ]
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


# Human-friendly names for the `event: tool_call` SSE frame emitted just before each tool actually
# runs (see _chat_sse below) - purely cosmetic, for the UI to show "Calling <label>..." while a
# tool call is in flight. Falls back to a humanized version of the raw tool name for anything not
# listed here, so a future tool never goes unlabeled.
_TOOL_DISPLAY_LABELS: dict[str, str] = {
    "create_case": "ADC Inspection Agent",
    "explainability_review": "Explainability Agent",
    "investigate_case": "Explainability Agent",
    "list_cases": "Case Lookup",
    "review_case": "Case Review",
    "flag_case_for_retraining": "Monitoring Agent",
    "monitoring_status": "Monitoring Agent",
    "current_time": "Current Time",
    "get_weather": "Weather Agent",
}


def _tool_display_label(name: str) -> str:
    return _TOOL_DISPLAY_LABELS.get(name, name.replace("_", " ").title())


def _available_tool_specs(image_ids: list[str], role: UserRole) -> list[dict[str, Any]] | None:
    """None means "send no `tools` field at all" - both the kill switch and the empty-registry
    case fall back to this, so a disabled feature is byte-identical to the pre-tool-calling
    request shape. Every spec is first narrowed to what `role` is allowed to call at all
    (app/agents/access.py) - re-checked again at dispatch time in _run_tool_call, since hiding a
    spec from the LLM isn't itself an access control. explainability_review and create_case are
    only ever offered when an image is actually attached to this message - the model has no way to
    reference a real upload id itself (see _run_tool_call, which overrides whatever it supplies
    anyway). investigate_case and flag_case_for_retraining need no image attached - they resolve
    an existing Case by number instead."""

    if not settings.chat_tool_calling_enabled:
        return None
    specs = [s for s in tool_registry.specs() if s["name"] in allowed_tool_names(role)]

    if not settings.explainability_agent_enabled:
        specs = [s for s in specs if s["name"] not in ("explainability_review", "investigate_case")]
    if not image_ids:
        specs = [s for s in specs if s["name"] != "explainability_review"]

    if not image_ids or not settings.adc_inspection_agent_enabled:
        specs = [s for s in specs if s["name"] != "create_case"]

    if not settings.monitoring_agent_enabled:
        specs = [
            s for s in specs if s["name"] not in ("monitoring_status", "flag_case_for_retraining")
        ]

    return specs or None


async def _run_tool_call(
    call: ToolCallRequest,
    *,
    session: SessionDep,
    image_ids: list[str],
    xml_ids: list[str],
    conversation_id: str,
    user: User,
) -> str:
    """Executes one model-requested tool call. Never raises - any failure becomes a
    {"error": ...} tool result fed back to the model, so one bad call degrades gracefully
    instead of ending the whole SSE stream (mirrors app/agents/explainability_review_agent/
    mcp_client.py's own graceful-degradation pattern).

    Defense in depth: re-checks role access even though _available_tool_specs already filtered
    what the LLM was offered - a tool-call request naming something that wasn't offered should
    never actually dispatch.

    explainability_review, investigate_case, and the case tools need kwargs the model can't supply
    itself - a real image (and, for explainability_review/investigate_case, the server's OpenAI
    key) - injected here the same way POST /api/agents/explainability-review already does it by
    hand."""

    role = UserRole(user.role)
    if call.name not in allowed_tool_names(role):
        return json.dumps({"error": f"tool {call.name!r} is not permitted for role {user.role!r}"})

    arguments = dict(call.arguments)
    if call.name == "explainability_review":
        image_id = image_ids[0]  # only offered when non-empty - see _available_tool_specs
        image_path = resolve_upload_path(image_id)
        if image_path is None:
            return json.dumps({"error": "image not found"})
        if not settings.openai_api_key:
            return json.dumps({"error": "no OpenAI key configured on this server"})
        arguments = {
            "image": Image.open(image_path).convert("RGB"),
            "image_name": image_id,
            "board_id": arguments.get("board_id", ""),
            "component_ref": arguments.get("component_ref", ""),
            "issue_symptom": arguments.get("issue_symptom"),
            "openai_api_key": settings.openai_api_key,
        }
    elif call.name == "investigate_case":
        if not settings.openai_api_key:
            return json.dumps({"error": "no OpenAI key configured on this server"})
        arguments = {**arguments, "session": session, "openai_api_key": settings.openai_api_key}
    elif call.name == "flag_case_for_retraining":
        arguments = {**arguments, "session": session, "user_id": user.id}
    elif call.name == "create_case":
        image_id = image_ids[0]  # only offered when non-empty - see _available_tool_specs
        image_path = resolve_upload_path(image_id)
        if image_path is None:
            return json.dumps({"error": "image not found"})
        inspection_xml_bytes = None
        inspection_xml_id = None
        if xml_ids:
            xml_path = resolve_upload_path(xml_ids[0])
            if xml_path is not None:
                inspection_xml_bytes = xml_path.read_bytes()
                inspection_xml_id = xml_ids[0]
        arguments = {
            "session": session,
            "image_bytes": image_path.read_bytes(),
            "image_name": image_id,
            "inspection_xml_bytes": inspection_xml_bytes,
            "inspection_xml_id": inspection_xml_id,
            "username": user.username,
            "user_id": user.id,
            "conversation_id": conversation_id,
            "board_id": arguments.get("board_id", ""),
            "component_ref": arguments.get("component_ref", ""),
            "package": arguments.get("package"),
            "feature": arguments.get("feature"),
            "issue_symptom": arguments.get("issue_symptom"),
        }
    elif call.name in {"list_cases", "review_case"}:
        arguments = {**arguments, "session": session, "user_id": user.id, "username": user.username}

    try:
        return await call_tool(tool_registry, call.name, arguments)
    except Exception as exc:  # noqa: BLE001 - degrade to a tool-result error, not an SSE error
        return json.dumps({"error": str(exc)})


async def _chat_sse(
    session: SessionDep,
    conversation: Conversation,
    is_new_conversation: bool,
    provider: LlmProvider,
    message: str,
    image_ids: list[str],
    xml_ids: list[str],
    user: User,
) -> AsyncGenerator[str, None]:
    """SSE body for POST /api/chat/stream: `event: delta` per chunk from the chat service,
    `event: tool_call` (`{name, label}`) just before a tool call actually dispatches, `event:
    error` if it raises, always ending in `event: done`. Same framing as the original app's
    `_trace_stream` (GET /workflows/{id}/trace).

    Persists the user's message before streaming starts (durable even if the LLM call fails
    partway) and the assistant's full reply after streaming succeeds - see app/chat/history.py.
    `conversation` is already resolved/ownership-checked by the caller (chat_stream), since a
    StreamingResponse commits its 200 status before this generator's first item is even
    requested - anything that should be able to 404 instead has to happen before this is called.

    The reply itself may take several tool-call round trips (app/agents/registry.py) before the
    model produces a final answer - see the loop below. Only the final round's text is persisted
    as the assistant's message; intermediate tool-call rounds' text (usually empty) is discarded.
    `tool_call` events are purely a UI progress indicator ("Calling Explainability Agent...") -
    they're never persisted to history either.
    """

    # A pure memory-write command (app/memory/service.py) - never reaches the LLM, so it can't
    # be derailed by (or accidentally leak into) the actual conversation. Checked before touching
    # history/persistence since it's a completely different code path from a normal chat turn.
    if message.strip().startswith(_REMEMBER_PREFIX):
        await history.append_message(session, conversation.id, "user", message, image_ids)
        fact = message.strip().removeprefix(_REMEMBER_PREFIX).strip()
        reply = (
            await remember_explicit(conversation.user_id, conversation.id, fact, provider)
            if fact
            else "Nothing to remember - add some text after /remember."
        )
        yield f"event: delta\ndata: {json.dumps({'text': reply})}\n\n"
        await history.append_message(session, conversation.id, "assistant", reply, [])
        await history.maybe_set_title(session, conversation, message)
        yield "event: done\ndata: {}\n\n"
        return

    turns = await history.load_history(
        session, conversation.id, max_turns=settings.chat_history_max_turns
    )
    await history.append_message(session, conversation.id, "user", message, image_ids)
    system_prompt = (
        await build_memory_preamble(conversation.user_id, message, provider)
        if is_new_conversation
        else None
    )

    service = get_chat_service(provider)
    messages = build_messages(system_prompt, turns, message, image_ids=image_ids, xml_ids=xml_ids)
    available_tools = _available_tool_specs(image_ids, UserRole(user.role))
    logger.debug(
        "chat request: provider=%s message=%r image_ids=%s",
        provider,
        message,
        image_ids,
        extra={"provider": provider, "chat_message": message, "image_ids": image_ids},
    )

    routing_outcome = await route(
        message, has_image=bool(image_ids), candidate_tools=available_tools
    )
    if isinstance(routing_outcome, Clarify):
        yield f"event: delta\ndata: {json.dumps({'text': routing_outcome.question})}\n\n"
        await history.append_message(
            session, conversation.id, "assistant", routing_outcome.question, []
        )
        await history.maybe_set_title(session, conversation, message)
        yield "event: done\ndata: {}\n\n"
        return
    available_tools = routing_outcome.available_tools

    final_text = ""
    try:
        for _round in range(settings.chat_tool_max_rounds):
            text_parts: list[str] = []
            pending_calls: list[ToolCallRequest] = []
            async for event in service.stream_with_tools(messages, available_tools):
                if isinstance(event, TextDelta):
                    text_parts.append(event.text)
                    yield f"event: delta\ndata: {json.dumps({'text': event.text})}\n\n"
                else:
                    pending_calls = event.calls
            final_text = "".join(text_parts)
            if not pending_calls:
                break

            messages.append(
                ChatMessage(role="assistant", content=final_text or None, tool_calls=pending_calls)
            )
            for call in pending_calls:
                yield (
                    "event: tool_call\n"
                    f"data: {json.dumps({'name': call.name, 'label': _tool_display_label(call.name)})}"
                    "\n\n"
                )
                result = await _run_tool_call(
                    call,
                    session=session,
                    image_ids=image_ids,
                    xml_ids=xml_ids,
                    conversation_id=conversation.id,
                    user=user,
                )
                messages.append(
                    ChatMessage(role="tool", tool_call_id=call.id, name=call.name, content=result)
                )
            final_text = ""
        else:
            final_text = (
                "I wasn't able to finish that after several tool calls - could you rephrase or "
                "simplify the request?"
            )
            yield f"event: delta\ndata: {json.dumps({'text': final_text})}\n\n"
    except Exception as exc:  # noqa: BLE001 - reported to the client as an SSE error event
        logger.debug("chat response error: %s", exc, extra={"error": str(exc)})
        yield f"event: error\ndata: {json.dumps({'message': str(exc)})}\n\n"
        return

    logger.debug("chat response: %r", final_text, extra={"final_text": final_text})
    await history.append_message(session, conversation.id, "assistant", final_text, [])
    await history.maybe_set_title(session, conversation, message)
    await maybe_extract(session, conversation, provider)
    yield "event: done\ndata: {}\n\n"


@app.post("/api/chat/stream")
async def chat_stream(
    request: ChatStreamRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: SessionDep,
) -> StreamingResponse:
    if not request.message.strip() and not request.image_ids:
        raise HTTPException(status_code=422, detail="message must not be empty")
    if request.provider == "openai" and not settings.openai_api_key:
        raise HTTPException(
            status_code=503, detail="OpenAI provider is not configured on this server"
        )

    try:
        conversation, is_new_conversation = await history.get_or_create_conversation(
            session, user.id, request.conversation_id
        )
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="conversation not found") from exc

    return StreamingResponse(
        _chat_sse(
            session,
            conversation,
            is_new_conversation,
            request.provider,
            request.message,
            request.image_ids,
            request.xml_ids,
            user,
        ),
        media_type="text/event-stream",
    )


@app.get("/api/conversations")
async def list_conversations(
    user: Annotated[User, Depends(get_current_user)],
    session: SessionDep,
) -> list[ConversationSummary]:
    conversations = await chat_repository.list_conversations_for_user(session, user.id)
    return [ConversationSummary.model_validate(c) for c in conversations]


@app.get("/api/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: SessionDep,
) -> ConversationDetail:
    conversation = await chat_repository.get_conversation_with_messages(session, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="conversation not found")
    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[MessageOut.model_validate(m) for m in conversation.messages],
    )


@app.delete("/api/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: SessionDep,
) -> None:
    conversation = await chat_repository.get_conversation(session, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="conversation not found")
    await chat_repository.delete_conversation(session, conversation)


@app.post("/api/agents/explainability-review")
async def explainability_review(
    request: ExplainabilityReviewRequest,
    _user: Annotated[User, Depends(get_current_user)],
) -> ExplainabilityReviewResponse:
    """Direct invocation of ExplainabilityReviewTool through the same ToolRegistry/call_tool()
    an LLM-driven tool-calling loop would use later (see DEVELOPMENT.md) - just called by this
    route instead of by a model deciding to call it."""

    if not settings.explainability_agent_enabled:
        raise HTTPException(status_code=503, detail="explainability review agent is disabled")
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=503, detail="OpenAI provider is not configured on this server"
        )

    image_path = resolve_upload_path(request.image_id)
    if image_path is None:
        raise HTTPException(status_code=404, detail="image not found")
    image = Image.open(image_path).convert("RGB")

    result_json = await call_tool(
        tool_registry,
        "explainability_review",
        {
            "image": image,
            "image_name": request.image_id,
            "board_id": request.board_id,
            "component_ref": request.component_ref,
            "issue_symptom": request.issue_symptom,
            "openai_api_key": settings.openai_api_key,
        },
    )
    return ExplainabilityReviewResponse.model_validate_json(result_json)


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


def _require_qa_or_admin(user: User) -> None:
    if UserRole(user.role) not in (UserRole.QA, UserRole.ADMIN):
        raise HTTPException(status_code=403, detail="QA or admin role required")


@app.post("/api/orchestrator/uploads/dataset")
async def orchestrator_upload_dataset(
    file: Annotated[UploadFile, File()],
    user: Annotated[User, Depends(get_current_user)],
) -> OrchestratorUploadRecord:
    if not settings.orchestrator_agent_enabled:
        raise HTTPException(status_code=503, detail="orchestrator agent is disabled")
    _require_qa_or_admin(user)
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=422, detail="file must be a CSV")
    upload_id = await orchestrator_uploads.save_dataset_csv(file)
    return OrchestratorUploadRecord(id=upload_id)


@app.post("/api/orchestrator/uploads/xml")
async def orchestrator_upload_xml(
    file: Annotated[UploadFile, File()],
    user: Annotated[User, Depends(get_current_user)],
) -> OrchestratorUploadRecord:
    """Separate from POST /api/uploads/xml, which is only for create_case - keeps the two agents'
    upload domains decoupled."""

    if not settings.orchestrator_agent_enabled:
        raise HTTPException(status_code=503, detail="orchestrator agent is disabled")
    _require_qa_or_admin(user)
    if not (file.filename or "").lower().endswith(".xml"):
        raise HTTPException(status_code=422, detail="file must be an XML document")
    upload_id = await orchestrator_uploads.save_inspection_xml(file)
    return OrchestratorUploadRecord(id=upload_id)


@app.post("/api/orchestrator/uploads/image-root")
async def orchestrator_upload_image_root(
    files: Annotated[list[UploadFile], File()],
    relative_paths: Annotated[list[str], Form()],
    user: Annotated[User, Depends(get_current_user)],
) -> OrchestratorUploadRecord:
    """`relative_paths[i]` is `files[i]`'s webkitRelativePath from the Work tab's
    <input webkitdirectory multiple> folder picker - see orchestrator_agent/uploads.py."""

    if not settings.orchestrator_agent_enabled:
        raise HTTPException(status_code=503, detail="orchestrator agent is disabled")
    _require_qa_or_admin(user)
    if len(files) != len(relative_paths):
        raise HTTPException(status_code=422, detail="files and relative_paths must match in length")
    try:
        upload_id = await orchestrator_uploads.save_image_root_files(files, relative_paths)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return OrchestratorUploadRecord(id=upload_id)


async def _orchestrator_sse(request: OrchestratorRunRequest, username: str) -> AsyncGenerator[str, None]:
    dataset_path = orchestrator_uploads.resolve_dataset_path(request.dataset_id)
    xml_path = orchestrator_uploads.resolve_xml_path(request.xml_id)
    image_root_path = (
        orchestrator_uploads.resolve_image_root_path(request.image_root_id)
        if request.image_root_id
        else None
    )

    if dataset_path is None:
        yield f"event: error\ndata: {json.dumps({'message': 'dataset upload not found'})}\n\n"
        yield "event: done\ndata: {}\n\n"
        return
    if xml_path is None:
        yield f"event: error\ndata: {json.dumps({'message': 'inspection XML upload not found'})}\n\n"
        yield "event: done\ndata: {}\n\n"
        return

    try:
        async for event in orchestrator_run_stream(
            mode=request.mode,
            dataset_csv=str(dataset_path),
            inspection_xml=str(xml_path),
            image_root=str(image_root_path) if image_root_path else None,
            username=username,
            feature_threshold=request.feature_threshold,
            defect_threshold=request.defect_threshold,
            use_llm=request.use_llm,
            llm_model=request.llm_model,
            llm_fallback=request.llm_fallback,
        ):
            yield f"event: {event['event']}\ndata: {json.dumps(event['data'])}\n\n"
    except Exception as exc:  # reported to the client as an SSE error event
        logger.exception("orchestrator_agent run failed")
        yield f"event: error\ndata: {json.dumps({'message': str(exc)})}\n\n"
        yield "event: done\ndata: {}\n\n"


@app.post("/api/orchestrator/run/stream")
async def orchestrator_run(
    request: OrchestratorRunRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> StreamingResponse:
    if not settings.orchestrator_agent_enabled:
        raise HTTPException(status_code=503, detail="orchestrator agent is disabled")
    _require_qa_or_admin(user)

    return StreamingResponse(
        _orchestrator_sse(request, user.username),
        media_type="text/event-stream",
    )
