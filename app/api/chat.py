"""Routes only - see app/chat/streaming.py for the tool-calling loop's actual logic and
app/chat/repository.py for conversation persistence."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from PIL import Image

from app.agents import call_tool
from app.agents.explainability_review_agent import (
    ExplainabilityReviewRequest,
    ExplainabilityReviewResponse,
)
from app.auth import get_current_user
from app.auth.dependencies import SessionDep
from app.chat import ChatStreamRequest, history
from app.chat import repository as chat_repository
from app.chat.schemas import ConversationDetail, ConversationSummary, MessageOut
from app.chat.streaming import chat_sse, tool_registry
from app.config.settings import settings
from app.core.chat import ConversationNotFound
from app.db import User
from app.uploads import resolve_upload_path

router = APIRouter()


@router.post("/api/chat/stream")
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
        chat_sse(
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


@router.get("/api/conversations")
async def list_conversations(
    user: Annotated[User, Depends(get_current_user)],
    session: SessionDep,
) -> list[ConversationSummary]:
    conversations = await chat_repository.list_conversations_for_user(session, user.id)
    return [ConversationSummary.model_validate(c) for c in conversations]


@router.get("/api/conversations/{conversation_id}")
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


@router.delete("/api/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: SessionDep,
) -> None:
    conversation = await chat_repository.get_conversation(session, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="conversation not found")
    await chat_repository.delete_conversation(session, conversation)


@router.post("/api/agents/explainability-review")
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
