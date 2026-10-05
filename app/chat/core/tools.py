"""What a chat tool is given when it runs, besides the model's own arguments.

`ToolContext` is supplied by the chat loop (app/chat/services/streaming.py) and injected into each
tool by LangGraph as `runtime.context` - the model never sees it and cannot supply any of it, so the
user, the session and the attached uploads always come from the request, never from model output.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db import User


@dataclass(frozen=True)
class ToolContext:
    # Real imports, not TYPE_CHECKING-only: LangChain builds each tool's schema with pydantic, which
    # must be able to resolve the context type the injected `runtime` parameter carries.
    session: AsyncSession
    user: User
    conversation_id: str
    # When this chat turn began. A relabel proposed in one turn can only be confirmed in a later
    # one, and this is what tells the turns apart.
    turn_started_at: datetime
    # Upload ids attached to this message - only ever the user's own uploads, never model input.
    image_ids: tuple[str, ...] = ()
    xml_ids: tuple[str, ...] = ()
