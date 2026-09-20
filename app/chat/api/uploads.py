"""Routes only - see app/chat/uploads/service.py for storage/path-resolution logic. Generic uploads
(chat-attached images and inspection XML) - distinct from app/workflow/api/orchestrator.py's own uploads,
which are stored separately (see app/workflow/services/uploads.py)."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.chat.uploads import UploadRecord, resolve_upload_path, save_upload
from app.shared.auth import get_current_user
from app.shared.db import User

router = APIRouter()


@router.post("/api/uploads")
async def upload_image(
    file: Annotated[UploadFile, File()],
    _user: Annotated[User, Depends(get_current_user)],
) -> UploadRecord:
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=422, detail="file must be an image")
    return await save_upload(file)


@router.get("/api/uploads/{filename}")
async def get_upload(
    filename: str,
    _user: Annotated[User, Depends(get_current_user)],
) -> FileResponse:
    path = resolve_upload_path(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="upload not found")
    return FileResponse(path)


@router.post("/api/uploads/xml")
async def upload_inspection_xml(
    file: Annotated[UploadFile, File()],
    _user: Annotated[User, Depends(get_current_user)],
) -> UploadRecord:
    """Same storage (app.chat.uploads.service) as image uploads - content-type-agnostic already, so no
    new storage dir/setting is needed for this. Only consumed by create_case
    (app/chat/agents/adc_inspection_agent/), and only optionally there."""

    if not (file.filename or "").lower().endswith(".xml"):
        raise HTTPException(status_code=422, detail="file must be an XML document")
    return await save_upload(file)
