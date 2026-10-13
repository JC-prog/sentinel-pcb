"""The Admin briefing on chat's opening screen: what is waiting for them in the Models tab. Read
only and Admin only. It counts what the shared model-operations tables hold; acting on any of it
(approve, cancel, promote) is the Models tab's, never chat's."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.shared.auth import get_current_user
from app.shared.auth.dependencies import SessionDep
from app.shared.config.settings import settings
from app.shared.db import User, UserRole
from app.shared.db.models import DriftReportStatus, RetrainingJobStatus, RetrainingTicketStatus
from app.shared.modelops import drift as drift_repo
from app.shared.modelops import jobs as job_repo
from app.shared.modelops import tickets as ticket_repo

router = APIRouter()


class BriefingOut(BaseModel):
    open_drift_reports: int
    open_auto_reports: int
    plans_awaiting_approval: int
    corrections_waiting: int


@router.get("/api/chat/briefing")
async def briefing(
    user: Annotated[User, Depends(get_current_user)], session: SessionDep
) -> BriefingOut:
    if UserRole(user.role) != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="admin role required")
    if not settings.modelops_enabled:
        raise HTTPException(status_code=503, detail="model operations are disabled")

    reports = await drift_repo.list_drift_reports(
        session, status=DriftReportStatus.OPEN, limit=200
    )
    pending = await job_repo.list_jobs(
        session, statuses=[RetrainingJobStatus.PENDING_APPROVAL], limit=200
    )
    waiting = await ticket_repo.count_tickets(session, status=RetrainingTicketStatus.OPEN)
    return BriefingOut(
        open_drift_reports=len(reports),
        open_auto_reports=sum(1 for r in reports if (r.stats or {}).get("auto")),
        plans_awaiting_approval=len(pending),
        corrections_waiting=sum(waiting.values()),
    )
