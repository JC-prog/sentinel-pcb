"""Routes only - see app/chat/agents/inspection_agent/golden_images.py for storage logic."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.chat.agents.inspection_agent import golden_images
from app.chat.agents.inspection_agent.schemas import GoldenImageOut
from app.shared.auth import get_current_user
from app.shared.auth.dependencies import SessionDep
from app.shared.db import User, UserRole

router = APIRouter()


@router.post("/api/admin/golden-images", status_code=201)
async def register_golden_image(
    board_id: Annotated[str, Form()],
    component_ref: Annotated[str, Form()],
    package: Annotated[str, Form()],
    feature: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    user: Annotated[User, Depends(get_current_user)],
    session: SessionDep,
    notes: Annotated[str | None, Form()] = None,
) -> GoldenImageOut:
    """Admin-only: registers one golden reference image, looked up later by
    app/chat/agents/inspection_agent/golden_images.py's find_golden_image() when a case is flagged
    for the same board_id/component_ref/package/feature. Minimal by design - a single-image
    registration endpoint, not a bulk importer or management UI."""

    if UserRole(user.role) != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="admin role required")
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=422, detail="file must be an image")

    golden = await golden_images.save_golden_image(
        session,
        file_bytes=await file.read(),
        filename=file.filename or "golden.png",
        board_id=board_id,
        component_ref=component_ref,
        package=package,
        feature=feature,
        notes=notes,
        registered_by_user_id=user.id,
    )
    return GoldenImageOut.model_validate(golden)
