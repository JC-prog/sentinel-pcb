"""Work-tab routes: uploads (dataset CSV / inspection XML / image-root) and the streaming run
endpoint. Split out of app/main.py so orchestrator_agent, like every other agent package, owns
its own routes rather than main.py accumulating every domain's endpoints in one file. Gated to
QA/Admin and settings.orchestrator_agent_enabled - never registered as a chat Tool, so the chat
LLM's function-calling loop can never reach any of this (see the package's own docstring).
"""

import json
import logging
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.agents.orchestrator_agent import uploads as orchestrator_uploads
from app.agents.orchestrator_agent.runner import run_stream as orchestrator_run_stream
from app.agents.orchestrator_agent.schemas import OrchestratorRunRequest, OrchestratorUploadRecord
from app.auth import get_current_user
from app.config.settings import settings
from app.db import User, UserRole

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_qa_or_admin(user: User) -> None:
    if UserRole(user.role) not in (UserRole.QA, UserRole.ADMIN):
        raise HTTPException(status_code=403, detail="QA or admin role required")


@router.post("/api/orchestrator/uploads/dataset")
async def orchestrator_upload_dataset(
    file: Annotated[UploadFile, File()],
    user: Annotated[User, Depends(get_current_user)],
) -> OrchestratorUploadRecord:
    if not settings.orchestrator_agent_enabled:
        raise HTTPException(status_code=503, detail="orchestrator agent is disabled")
    _require_qa_or_admin(user)
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=422, detail="file must be a CSV")
    upload_id = await orchestrator_uploads.save_dataset_csv(file)
    return OrchestratorUploadRecord(id=upload_id)


@router.post("/api/orchestrator/uploads/xml")
async def orchestrator_upload_xml(
    file: Annotated[UploadFile, File()],
    user: Annotated[User, Depends(get_current_user)],
) -> OrchestratorUploadRecord:
    """Separate from POST /api/uploads/xml, which is only for create_case - keeps the two agents'
    upload domains decoupled."""

    if not settings.orchestrator_agent_enabled:
        raise HTTPException(status_code=503, detail="orchestrator agent is disabled")
    _require_qa_or_admin(user)
    if not (file.filename or "").lower().endswith(".xml"):
        raise HTTPException(status_code=422, detail="file must be an XML document")
    upload_id = await orchestrator_uploads.save_inspection_xml(file)
    return OrchestratorUploadRecord(id=upload_id)


@router.post("/api/orchestrator/uploads/image-root")
async def orchestrator_upload_image_root(
    files: Annotated[list[UploadFile], File()],
    relative_paths: Annotated[list[str], Form()],
    user: Annotated[User, Depends(get_current_user)],
) -> OrchestratorUploadRecord:
    """`relative_paths[i]` is `files[i]`'s webkitRelativePath from the Work tab's
    <input webkitdirectory multiple> folder picker - see orchestrator_agent/uploads.py."""

    if not settings.orchestrator_agent_enabled:
        raise HTTPException(status_code=503, detail="orchestrator agent is disabled")
    _require_qa_or_admin(user)
    if len(files) != len(relative_paths):
        raise HTTPException(status_code=422, detail="files and relative_paths must match in length")
    try:
        upload_id = await orchestrator_uploads.save_image_root_files(files, relative_paths)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return OrchestratorUploadRecord(id=upload_id)


async def _orchestrator_sse(request: OrchestratorRunRequest, username: str) -> AsyncGenerator[str, None]:
    dataset_path = orchestrator_uploads.resolve_dataset_path(request.dataset_id)
    xml_path = orchestrator_uploads.resolve_xml_path(request.xml_id)
    image_root_path = (
        orchestrator_uploads.resolve_image_root_path(request.image_root_id)
        if request.image_root_id
        else None
    )

    if dataset_path is None:
        yield f"event: error\ndata: {json.dumps({'message': 'dataset upload not found'})}\n\n"
        yield "event: done\ndata: {}\n\n"
        return
    if xml_path is None:
        yield f"event: error\ndata: {json.dumps({'message': 'inspection XML upload not found'})}\n\n"
        yield "event: done\ndata: {}\n\n"
        return

    try:
        async for event in orchestrator_run_stream(
            mode=request.mode,
            dataset_csv=str(dataset_path),
            inspection_xml=str(xml_path),
            image_root=str(image_root_path) if image_root_path else None,
            username=username,
            feature_threshold=request.feature_threshold,
            defect_threshold=request.defect_threshold,
            use_llm=request.use_llm,
            llm_model=request.llm_model,
            llm_fallback=request.llm_fallback,
        ):
            yield f"event: {event['event']}\ndata: {json.dumps(event['data'])}\n\n"
    except Exception as exc:  # reported to the client as an SSE error event
        logger.exception("orchestrator_agent run failed")
        yield f"event: error\ndata: {json.dumps({'message': str(exc)})}\n\n"
        yield "event: done\ndata: {}\n\n"


@router.post("/api/orchestrator/run/stream")
async def orchestrator_run(
    request: OrchestratorRunRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> StreamingResponse:
    if not settings.orchestrator_agent_enabled:
        raise HTTPException(status_code=503, detail="orchestrator agent is disabled")
    _require_qa_or_admin(user)

    return StreamingResponse(
        _orchestrator_sse(request, user.username),
        media_type="text/event-stream",
    )
