"""Shared helpers for the chat agent tests: a real PNG, a ToolContext built the way the chat turn
builds one, an upload written where the tools read uploads from, and calling a tool."""

import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.toolkit import ChatTool
from app.chat.core.tools import ToolContext
from app.shared.config.settings import settings
from app.shared.db.models import User


def png_bytes(size: tuple[int, int] = (4, 4)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color=(200, 200, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


VALID_IMAGE = png_bytes()


def tool_context(
    session: AsyncSession,
    user: User,
    *,
    conversation_id: str = "c1",
    turn_started_at: datetime | None = None,
    image_ids: tuple[str, ...] = (),
    xml_ids: tuple[str, ...] = (),
) -> ToolContext:
    return ToolContext(
        session=session,
        user=user,
        conversation_id=conversation_id,
        turn_started_at=turn_started_at or datetime.now(UTC),
        image_ids=image_ids,
        xml_ids=xml_ids,
    )


def upload(monkeypatch: pytest.MonkeyPatch, upload_dir: Path, data: bytes, name: str) -> str:
    """Writes `data` as a chat upload (where app.chat.uploads resolves them) and returns its id."""

    monkeypatch.setattr(settings, "chat_upload_dir", str(upload_dir))
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / name).write_bytes(data)
    return name


async def call(chat_tool: ChatTool, ctx: ToolContext, **arguments: Any) -> dict[str, Any]:
    """Runs a chat tool's own function with `ctx` injected as its runtime context - the way
    LangGraph's ToolNode calls it, minus the model. (tests/chat/test_supervisor.py covers the real
    injection end to end.)"""

    run = chat_tool.tool.coroutine  # type: ignore[attr-defined]
    return dict(json.loads(await run(runtime=SimpleNamespace(context=ctx), **arguments)))
