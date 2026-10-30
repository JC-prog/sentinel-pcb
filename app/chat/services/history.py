"""Business logic for per-conversation memory: turning persisted Message rows into the history a
the supervisor needs, and persisting new turns as they happen. Route-facing (app/chat/api/chat.py); DB access
itself lives in app/chat/services/repository.py, mirroring app/shared/auth/service.py's split from
app/shared/auth/repository.py.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.core.chat import ChatTurn, ConversationNotFound
from app.chat.db.models import Conversation, Message
from app.chat.services import repository

_TITLE_MAX_LENGTH = 60


async def get_or_create_conversation(
    session: AsyncSession, user_id: str, conversation_id: str
) -> tuple[Conversation, bool]:
    """The frontend generates conversation_id client-side and sends it on every request, so the
    first message of a new chat both creates and uses the row in one call - see
    ui/src/app/chat/services/service/ts. Raises ConversationNotFound if the id is already used by a
    different user, rather than silently reusing (or 403ing on, which would confirm it exists)
    someone else's conversation.

    The returned bool is True only when this call created the row - app/chat/memory/service.py uses
    it to decide whether a brand-new conversation should get a long-term-memory preamble."""

    conversation = await repository.get_conversation(session, conversation_id)
    if conversation is None:
        conversation = Conversation(id=conversation_id, user_id=user_id)
        return await repository.add_conversation(session, conversation), True
    if conversation.user_id != user_id:
        raise ConversationNotFound(conversation_id)
    return conversation, False


async def load_history(
    session: AsyncSession, conversation_id: str, *, max_turns: int
) -> list[ChatTurn]:
    """The most recent `max_turns` messages, oldest first. All messages stay persisted regardless
    of this window - only what gets sent to the LLM is trimmed."""

    messages = await repository.get_recent_messages(session, conversation_id, limit=max_turns)
    return [
        ChatTurn(role=m.role, content=_with_tool_note(m))  # type: ignore[arg-type]
        for m in messages
    ]


# The facts of a card worth carrying into a later turn, in the order they read best.
_CARD_FACTS = (
    "case_number",
    "status",
    "verdict",
    "model_label",
    "proposed_label",
    "corrected_label",
    "decision",
    "case_status",
)


def _with_tool_note(message: Message) -> str:
    """An assistant turn plus a one-line note of the cards its tools produced. History replays only
    text, so without this the model forgets the case number or proposal a tool returned unless it
    happened to repeat it in prose - and a follow-up ("who reviewed it?") has nothing to anchor on."""

    if message.role != "assistant" or not message.tool_results:
        return message.content
    notes = []
    for card in message.tool_results:
        result = card.get("result")
        if not isinstance(result, dict):
            continue
        facts = [f"{key}={result[key]}" for key in _CARD_FACTS if result.get(key) not in (None, "")]
        if facts:
            notes.append(f"{card.get('name', 'tool')}({', '.join(facts)})")
    if not notes:
        return message.content
    return f"{message.content}\n\n[Earlier tool results this turn: {'; '.join(notes)}]"


async def append_message(
    session: AsyncSession,
    conversation_id: str,
    role: str,
    content: str,
    image_ids: list[str],
    *,
    tool_results: list[dict[str, Any]] | None = None,
    interrupted: bool = False,
) -> Message:
    message = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        image_ids=image_ids,
        tool_results=tool_results or [],
        interrupted=interrupted,
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
