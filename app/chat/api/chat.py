"""Routes only - see app/chat/services/streaming.py for the tool-calling loop's actual logic and
app/chat/services/repository.py for conversation persistence."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.chat.core.chat import ConversationNotFound
from app.chat.services import ChatStreamRequest, history
from app.chat.services import repository as chat_repository
from app.chat.services.schemas import (
    ConversationDetail,
    ConversationRename,
    ConversationSummary,
    MessageFeedback,
    MessageOut,
)
from app.chat.services.streaming import chat_sse
from app.shared.auth import get_current_user
from app.shared.auth.dependencies import SessionDep
from app.shared.config.settings import settings
from app.shared.db import User

router = APIRouter()
logger = logging.getLogger(__name__)


def _chosen_model(requested: str | None) -> str | None:
    """The requested chat model if the server allows it, else None (the server's default) - a model
    that was removed from CHAT_MODELS since the browser stored it must not break chat."""

    if requested is None:
        return None
    if requested in settings.selectable_chat_models:
        return requested
    logger.warning("chat model %r is not allowed; using the default", requested)
    return None


class ChatModelsOut(BaseModel):
    default: str
    models: list[str]


@router.get("/api/chat/models")
async def chat_models(user: Annotated[User, Depends(get_current_user)]) -> ChatModelsOut:
    """What Settings offers for the chat reply's model."""

    return ChatModelsOut(default=settings.openai_model, models=settings.selectable_chat_models)


@router.post("/api/chat/stream")
async def chat_stream(
    request: ChatStreamRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: SessionDep,
) -> StreamingResponse:
    if not request.message.strip() and not request.image_ids:
        raise HTTPException(status_code=422, detail="message must not be empty")
    if not settings.openai_api_key:
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
            _chosen_model(request.model),
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


@router.patch("/api/conversations/{conversation_id}")
async def rename_conversation(
    conversation_id: str,
    body: ConversationRename,
    user: Annotated[User, Depends(get_current_user)],
    session: SessionDep,
) -> ConversationSummary:
    title = " ".join(body.title.split())
    if not title:
        raise HTTPException(status_code=422, detail="title must not be empty")
    conversation = await chat_repository.get_conversation(session, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="conversation not found")
    await chat_repository.touch_conversation(session, conversation, title=title[:80])
    return ConversationSummary.model_validate(conversation)


@router.put("/api/messages/{message_id}/feedback", status_code=204)
async def rate_message(
    message_id: str,
    body: MessageFeedback,
    user: Annotated[User, Depends(get_current_user)],
    session: SessionDep,
) -> None:
    """Thumbs up or down on an assistant reply (null clears it). Only the owner of the conversation
    can rate, and only the assistant's replies."""

    message = await chat_repository.get_message(session, message_id)
    conversation = (
        await chat_repository.get_conversation(session, message.conversation_id) if message else None
    )
    if message is None or conversation is None or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="message not found")
    if message.role != "assistant":
        raise HTTPException(status_code=422, detail="only a reply can be rated")
    await chat_repository.set_feedback(session, message, body.value)


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
