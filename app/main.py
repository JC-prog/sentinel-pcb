import json
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from app.chat import ChatStreamRequest, get_chat_service
from app.settings import settings
from app.uploads import UploadRecord, resolve_upload_path, save_upload

app = FastAPI(title="SentinelChat")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/uploads")
async def upload_image(file: Annotated[UploadFile, File()]) -> UploadRecord:
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=422, detail="file must be an image")
    return await save_upload(file)


@app.get("/api/uploads/{filename}")
async def get_upload(filename: str) -> FileResponse:
    path = resolve_upload_path(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="upload not found")
    return FileResponse(path)


async def _chat_sse(message: str, image_ids: list[str]) -> AsyncGenerator[str, None]:
    """SSE body for POST /api/chat/stream: `event: delta` per chunk from the chat service,
    `event: error` if it raises, always ending in `event: done`. Same framing as the original
    app's `_trace_stream` (GET /workflows/{id}/trace)."""

    service = get_chat_service()
    try:
        async for chunk in service.stream_reply(message, image_ids):
            yield f"event: delta\ndata: {json.dumps({'text': chunk})}\n\n"
    except Exception as exc:  # noqa: BLE001 - reported to the client as an SSE error event
        yield f"event: error\ndata: {json.dumps({'message': str(exc)})}\n\n"
        return
    yield "event: done\ndata: {}\n\n"


@app.post("/api/chat/stream")
async def chat_stream(request: ChatStreamRequest) -> StreamingResponse:
    if not request.message.strip() and not request.image_ids:
        raise HTTPException(status_code=422, detail="message must not be empty")
    return StreamingResponse(
        _chat_sse(request.message, request.image_ids), media_type="text/event-stream"
    )
