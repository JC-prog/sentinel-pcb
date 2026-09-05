"""Pure interface for a chat completion backend - no framework/IO imports. Concrete
implementations live in app/chat/providers/; app/chat/service.py's get_chat_service() factory is
the only place that constructs one.
"""

from collections.abc import AsyncGenerator
from typing import Protocol


class ChatService(Protocol):
    def stream_reply(self, message: str, image_ids: list[str]) -> AsyncGenerator[str, None]: ...
