"""Chat routes: conversations, the SSE chat stream, chat image/XML uploads and the read-only Cases list. Route declarations only - logic lives in app/chat/services/, app/chat/agents/,
app/chat/uploads/.
"""

from fastapi import APIRouter

from app.chat.api import cases, chat, uploads

router = APIRouter()
router.include_router(uploads.router)
router.include_router(chat.router)
router.include_router(cases.router)

# Never buffered by the request-logging middleware (app/main.py) - it would delay the live stream.
# app/chat/services/streaming.py logs the request/response content itself instead.
STREAMING_PATHS: frozenset[str] = frozenset({"/api/chat/stream"})
