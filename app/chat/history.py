"""Business logic for per-conversation memory: turning persisted Message rows into the history a
ChatService needs, and persisting new turns as they happen. Route-facing (app/main.py); DB access
itself lives in app/chat/repository.py, mirroring app/auth/service.py's split from
app/auth/repository.py.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat import repository
from app.core.chat import ChatTurn, ConversationNotFound
from app.db.models import Conversation, Message

_TITLE_MAX_LENGTH = 60


async def get_or_create_conversation(
    session: AsyncSession, user_id: str, conversation_id: str
) -> Conversation:
    """The frontend generates conversation_id client-side and sends it on every request, so the
    first message of a new chat both creates and uses the row in one call - see
    ui/src/app/chat.service.ts. Raises ConversationNotFound if the id is already used by a
    different user, rather than silently reusing (or 403ing on, which would confirm it exists)
    someone else's conversation."""

    conversation = await repository.get_conversation(session, conversation_id)
    if conversation is None:
        conversation = Conversation(id=conversation_id, user_id=user_id)
        return await repository.add_conversation(session, conversation)
    if conversation.user_id != user_id:
        raise ConversationNotFound(conversation_id)
    return conversation


async def load_history(
    session: AsyncSession, conversation_id: str, *, max_turns: int
) -> list[ChatTurn]:
    """The most recent `max_turns` messages, oldest first. All messages stay persisted regardless
    of this window - only what gets sent to the LLM is trimmed."""

    messages = await repository.get_recent_messages(session, conversation_id, limit=max_turns)
    return [ChatTurn(role=m.role, content=m.content) for m in messages]  # type: ignore[arg-type]


async def append_message(
    session: AsyncSession, conversation_id: str, role: str, content: str, image_ids: list[str]
) -> Message:
    message = Message(
        conversation_id=conversation_id, role=role, content=content, image_ids=image_ids
    )
    return await repository.add_message(session, message)


async def maybe_set_title(
    session: AsyncSession, conversation: Conversation, first_user_message: str
) -> None:
    """Sets the conversation's title from its first user message, once, the first time this is
    called with the default title still in place - otherwise just bumps updated_at (see
    repository.touch_conversation) so the sidebar's recency sort reflects this turn."""

    if conversation.title != "New chat":
        await repository.touch_conversation(session, conversation)
        return

    title = first_user_message.strip().splitlines()[0] if first_user_message.strip() else "New chat"
    if len(title) > _TITLE_MAX_LENGTH:
        title = title[:_TITLE_MAX_LENGTH].rstrip() + "…"
    await repository.touch_conversation(session, conversation, title=title)
