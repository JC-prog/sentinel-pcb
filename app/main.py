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
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image

from app.agents import CurrentTimeTool, ToolRegistry, WeatherTool, call_tool
from app.agents.explainability_review_agent import (
    ExplainabilityReviewRequest,
    ExplainabilityReviewResponse,
    ExplainabilityReviewTool,
)
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
from app.db import Conversation, User, init_models
from app.memory import build_memory_preamble, maybe_extract, remember_explicit
from app.uploads import UploadRecord, resolve_upload_path, save_upload

_REMEMBER_PREFIX = "/remember "

_ACCESS_TOKEN_COOKIE = "access_token"
_REFRESH_TOKEN_COOKIE = "refresh_token"
_REFRESH_TOKEN_PATH = "/api/auth"

_access_logger = logging.getLogger("app.access")

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


@app.middleware("http")
async def _log_requests(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """One structured log line per request (method, path, status, duration, and the caller's
    user id when authenticated) - Uvicorn's own access log already prints a plain-text line per
    request, but doesn't attach these as separate, queryable fields the way app/config/logging_config.py's
    JSON formatter can. Decodes the access_token cookie directly (JWT only, no DB round trip) just
    to attribute the log line - any failure (missing/expired/invalid token) just means an
    unauthenticated-looking log line, not a 401; auth itself is still enforced by
    get_current_user on whichever routes require it."""

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
    return response


# Constructing ExplainabilityReviewTool() here doesn't load anything heavy - it's a thin wrapper;
# the actual CLIP model load is deferred to first use of the agent (see
# app/agents/explainability_review_agent/graph.py's get_mcp_client()).
tool_registry = ToolRegistry([CurrentTimeTool(), ExplainabilityReviewTool(), WeatherTool()])


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


def _available_tool_specs(image_ids: list[str]) -> list[dict[str, Any]] | None:
    """None means "send no `tools` field at all" - both the kill switch and the empty-registry
    case fall back to this, so a disabled feature is byte-identical to the pre-tool-calling
    request shape. explainability_review is only ever offered when an image is actually attached
    to this message - the model has no way to reference a real upload id itself (see
    _run_tool_call, which overrides whatever it supplies anyway)."""

    if not settings.chat_tool_calling_enabled:
        return None
    specs = tool_registry.specs()
    if not image_ids or not settings.explainability_agent_enabled:
        specs = [s for s in specs if s["name"] != "explainability_review"]
    return specs or None


async def _run_tool_call(call: ToolCallRequest, *, image_ids: list[str]) -> str:
    """Executes one model-requested tool call. Never raises - any failure becomes a
    {"error": ...} tool result fed back to the model, so one bad call degrades gracefully
    instead of ending the whole SSE stream (mirrors app/agents/explainability_review_agent/
    mcp_client.py's own graceful-degradation pattern).

    explainability_review needs kwargs the model can't supply itself - a real PIL.Image and the
    server's OpenAI key - injected here the same way POST /api/agents/explainability-review
    already does it by hand."""

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
) -> AsyncGenerator[str, None]:
    """SSE body for POST /api/chat/stream: `event: delta` per chunk from the chat service,
    `event: error` if it raises, always ending in `event: done`. Same framing as the original
    app's `_trace_stream` (GET /workflows/{id}/trace).

    Persists the user's message before streaming starts (durable even if the LLM call fails
    partway) and the assistant's full reply after streaming succeeds - see app/chat/history.py.
    `conversation` is already resolved/ownership-checked by the caller (chat_stream), since a
    StreamingResponse commits its 200 status before this generator's first item is even
    requested - anything that should be able to 404 instead has to happen before this is called.

    The reply itself may take several tool-call round trips (app/agents/registry.py) before the
    model produces a final answer - see the loop below. Only the final round's text is persisted
    as the assistant's message; intermediate tool-call rounds' text (usually empty) is discarded.
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
    messages = build_messages(system_prompt, turns, message)
    available_tools = _available_tool_specs(image_ids)

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
                result = await _run_tool_call(call, image_ids=image_ids)
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
        yield f"event: error\ndata: {json.dumps({'message': str(exc)})}\n\n"
        return

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
