from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Conversation, Message


async def get_conversation(session: AsyncSession, conversation_id: str) -> Conversation | None:
    return await session.get(Conversation, conversation_id)


async def add_conversation(session: AsyncSession, conversation: Conversation) -> Conversation:
    session.add(conversation)
    await session.commit()
    return conversation


async def list_conversations_for_user(session: AsyncSession, user_id: str) -> list[Conversation]:
    result = await session.scalars(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
    )
    return list(result)


async def get_conversation_with_messages(
    session: AsyncSession, conversation_id: str
) -> Conversation | None:
    result = await session.scalars(
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(Conversation.id == conversation_id)
    )
    return result.first()


async def get_recent_messages(
    session: AsyncSession, conversation_id: str, *, limit: int
) -> list[Message]:
    """Most recent `limit` messages, oldest first - see app/chat/history.py's windowing."""

    result = await session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    return list(reversed(result.all()))


async def add_message(session: AsyncSession, message: Message) -> Message:
    session.add(message)
    await session.commit()
    return message


async def count_messages_by_role(session: AsyncSession, conversation_id: str, role: str) -> int:
    """Used by app/memory/service.py to throttle extraction to every Nth assistant turn."""

    result = await session.scalar(
        select(func.count())
        .select_from(Message)
        .where(Message.conversation_id == conversation_id, Message.role == role)
    )
    return result or 0


async def touch_conversation(
    session: AsyncSession, conversation: Conversation, *, title: str | None = None
) -> None:
    """Bumps updated_at explicitly - appending a Message doesn't itself dirty the Conversation
    row, so `onupdate` alone would never fire and the sidebar's sort-by-updated_at would go stale."""

    if title is not None:
        conversation.title = title
    conversation.updated_at = datetime.now(UTC)
    await session.commit()


async def delete_conversation(session: AsyncSession, conversation: Conversation) -> None:
    await session.delete(conversation)
    await session.commit()
