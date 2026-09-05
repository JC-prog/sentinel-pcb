import asyncio
from collections.abc import AsyncGenerator
from typing import Protocol

from app.settings import settings


class ChatService(Protocol):
    def stream_reply(self, message: str, image_ids: list[str]) -> AsyncGenerator[str, None]: ...


class PlaceholderChatService:
    """Echoes the message back, word by word. Swap point for a real LLM call - see
    get_chat_service()."""

    async def stream_reply(self, message: str, image_ids: list[str]) -> AsyncGenerator[str, None]:
        image_note = f" (with {len(image_ids)} image(s) attached)" if image_ids else ""
        reply = f"This is a placeholder response{image_note}. You said: {message}"
        words = reply.split(" ")
        for i, word in enumerate(words):
            yield word + (" " if i < len(words) - 1 else "")
            await asyncio.sleep(settings.chat_stream_delay_seconds)


def get_chat_service() -> ChatService:
    return PlaceholderChatService()
