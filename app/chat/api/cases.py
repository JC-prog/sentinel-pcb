"""The Cases tab's routes: read-only. A Case is created and corrected from chat (two-step, confirmed
in a later turn); this only lets QA and Admin find and read them. Route declarations only - the
read models are in app/chat/services/case_views.py.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.chat.services import case_views
from app.shared.auth import get_current_user
from app.shared.auth.dependencies import SessionDep
from app.shared.db import User, UserRole

router = APIRouter(prefix="/api/cases", tags=["cases"])


async def _qa_or_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if UserRole(user.role) not in (UserRole.QA, UserRole.ADMIN):
        raise HTTPException(status_code=403, detail="QA or admin role required")
    return user


QaOrAdmin = Annotated[User, Depends(_qa_or_admin)]


@router.get("")
async def list_cases(
    _user: QaOrAdmin,
    session: SessionDep,
    status: str | None = None,
    model: str | None = None,
    q: str | None = None,
    corrected: bool | None = None,
    limit: int = 25,
    offset: int = 0,
) -> case_views.CaseListOut:
    """Newest first. `q` matches a case number, board, component or the model's label."""

    return await case_views.list_cases(
        session, status=status, model=model, q=q, corrected=corrected, limit=limit, offset=offset
    )


@router.get("/{case_number}")
async def get_case(
    case_number: str, user: QaOrAdmin, session: SessionDep
) -> case_views.CaseDetailOut:
    detail = await case_views.get_case_detail(session, case_number, viewer_id=user.id)
    if detail is None:
        raise HTTPException(status_code=404, detail="case not found")
    return detail
