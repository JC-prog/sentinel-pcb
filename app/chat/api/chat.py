"""Routes only - see app/chat/services/streaming.py for the tool-calling loop's actual logic and
app/chat/services/repository.py for conversation persistence."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.chat.core.chat import ConversationNotFound
from app.chat.services import ChatStreamRequest, history
from app.chat.services import repository as chat_repository
from app.chat.services.schemas import ConversationDetail, ConversationSummary, MessageOut
from app.chat.services.streaming import chat_sse
from app.shared.auth import get_current_user
from app.shared.auth.dependencies import SessionDep
from app.shared.config.settings import settings
from app.shared.db import User

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
