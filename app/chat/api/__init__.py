"""Chat routes: conversations, the SSE chat stream, chat image/XML uploads, admin golden-image
registration, and the direct case-review-agent endpoint. Route declarations only - logic lives in
app/chat/services/, app/chat/agents/, app/chat/uploads/.
"""

from fastapi import APIRouter

from app.chat.api import admin, chat, uploads

router = APIRouter()
router.include_router(uploads.router)
router.include_router(admin.router)
router.include_router(chat.router)

# Never buffered by the request-logging middleware (app/main.py) - it would delay the live stream.
# app/chat/services/streaming.py logs the request/response content itself instead.
STREAMING_PATHS: frozenset[str] = frozenset({"/api/chat/stream"})
