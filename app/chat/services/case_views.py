"""Read models for the Cases tab: a Case as a QA/Admin reads it outside the conversation that made
it. Pure reads - nothing here changes a Case; relabelling stays a chat action (two steps, confirmed
in a later turn). Cases are shared by QA and Admin, like Work-tab runs and the Models tab.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.db.models import Case, Conversation
from app.chat.services.cases import parse_case_number
from app.shared.db.models import RetrainingTicket, User

MAX_LIMIT = 100


class CaseTicketOut(BaseModel):
    id: str
    status: str
    job_id: str | None
    model_name: str | None
    observed_label: str | None
    correct_label: str | None


class CaseSummaryOut(BaseModel):
    case_number: str
    status: str
    board_id: str
    component_ref: str
    region: str | None
    defect_model: str | None
    defect_model_version: str | None
    defect_label: str | None
    defect_confidence: float | None
    corrected_label: str | None
    created_by: str | None
    created_at: datetime


class CaseDetailOut(CaseSummaryOut):
    package: str | None
    feature: str | None
    issue_symptom: str | None
    region_confidence: float | None
    region_model_version: str | None
    defect_scores: dict[str, float]
    measurement_validation: dict[str, Any] | None
    observations: list[str]
    image_id: str
    inspection_xml_id: str | None
    correction_reason: str | None
    corrected_by: str | None
    corrected_at: datetime | None
    resolved_by: str | None
    resolved_at: datetime | None
    resolution_note: str | None
    # A relabel proposed in chat and not yet confirmed.
    pending_label: str | None
    pending_reason: str | None
    ticket: CaseTicketOut | None
    # Only set for the user who owns the conversation: it is theirs to open.
    conversation_id: str | None


class CaseListOut(BaseModel):
    total: int
    limit: int
    offset: int
    cases: list[CaseSummaryOut]


def _filters(
    stmt: Select[Any],
    *,
    status: str | None,
    model: str | None,
    q: str | None,
    corrected: bool | None,
    board: str | None = None,
    component: str | None = None,
    created_by_user_id: str | None = None,
) -> Select[Any]:
    if board:
        stmt = stmt.where(Case.board_id.ilike(board.strip()))
    if component:
        stmt = stmt.where(Case.component_ref.ilike(component.strip()))
    if created_by_user_id:
        stmt = stmt.where(Case.created_by_user_id == created_by_user_id)
    if status:
        stmt = stmt.where(Case.status == status)
    if model:
        stmt = stmt.where(Case.defect_model == model)
    if corrected is True:
        stmt = stmt.where(Case.corrected_label.is_not(None))
    elif corrected is False:
        stmt = stmt.where(Case.corrected_label.is_(None))
    needle = (q or "").strip()
    if needle:
        clauses: list[ColumnElement[bool]] = [
            Case.board_id.ilike(f"%{needle}%"),
            Case.component_ref.ilike(f"%{needle}%"),
            Case.defect_label.ilike(f"%{needle}%"),
        ]
        sequence = parse_case_number(needle)
        if sequence is not None:
            clauses.append(Case.sequence_number == sequence)
        stmt = stmt.where(or_(*clauses))
    return stmt


async def _usernames(session: AsyncSession, user_ids: set[str | None]) -> dict[str, str]:
    ids = {u for u in user_ids if u}
    if not ids:
        return {}
    rows = await session.execute(select(User.id, User.username).where(User.id.in_(ids)))
    return {user_id: username for user_id, username in rows.tuples()}


def _summary(case: Case, names: dict[str, str]) -> CaseSummaryOut:
    return CaseSummaryOut(
        case_number=case.case_number,
        status=case.status,
        board_id=case.board_id,
        component_ref=case.component_ref,
        region=case.region,
        defect_model=case.defect_model,
        defect_model_version=case.defect_model_version,
        defect_label=case.defect_label,
        defect_confidence=case.defect_confidence,
        corrected_label=case.corrected_label,
        created_by=names.get(case.created_by_user_id),
        created_at=case.created_at,
    )


async def list_cases(
    session: AsyncSession,
    *,
    status: str | None = None,
    model: str | None = None,
    q: str | None = None,
    corrected: bool | None = None,
    board: str | None = None,
    component: str | None = None,
    created_by_user_id: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> CaseListOut:
    limit = max(1, min(limit, MAX_LIMIT))
    offset = max(0, offset)
    base = _filters(
        select(Case),
        status=status,
        model=model,
        q=q,
        corrected=corrected,
        board=board,
        component=component,
        created_by_user_id=created_by_user_id,
    )
    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    cases = list(
        await session.scalars(
            base.order_by(Case.created_at.desc(), Case.sequence_number.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    names = await _usernames(session, {c.created_by_user_id for c in cases})
    return CaseListOut(
        total=total,
        limit=limit,
        offset=offset,
        cases=[_summary(c, names) for c in cases],
    )


async def get_case_detail(
    session: AsyncSession, case_number: str, *, viewer_id: str
) -> CaseDetailOut | None:
    sequence = parse_case_number(case_number)
    if sequence is None:
        return None
    case = await session.scalar(select(Case).where(Case.sequence_number == sequence))
    if case is None:
        return None

    names = await _usernames(
        session, {case.created_by_user_id, case.corrected_by_user_id, case.resolved_by_user_id}
    )
    ticket = await session.scalar(
        select(RetrainingTicket)
        .where(RetrainingTicket.case_id == case.id)
        .order_by(RetrainingTicket.created_at.desc())
        .limit(1)
    )
    conversation = await session.get(Conversation, case.conversation_id)
    owns_conversation = conversation is not None and conversation.user_id == viewer_id
    return CaseDetailOut(
        **_summary(case, names).model_dump(),
        package=case.package,
        feature=case.feature,
        issue_symptom=case.issue_symptom,
        region_confidence=case.region_confidence,
        region_model_version=case.region_model_version,
        defect_scores=case.defect_scores or {},
        measurement_validation=case.measurement_validation,
        observations=case.observations or [],
        image_id=case.image_id,
        inspection_xml_id=case.inspection_xml_id,
        correction_reason=case.correction_reason,
        corrected_by=names.get(case.corrected_by_user_id or ""),
        corrected_at=case.corrected_at,
        resolved_by=names.get(case.resolved_by_user_id or ""),
        resolved_at=case.resolved_at,
        resolution_note=case.resolution_note,
        pending_label=case.pending_label,
        pending_reason=case.pending_reason,
        ticket=(
            CaseTicketOut(
                id=ticket.id,
                status=ticket.status,
                job_id=ticket.job_id,
                model_name=ticket.model_name,
                observed_label=ticket.observed_label,
                correct_label=ticket.correct_label,
            )
            if ticket
            else None
        ),
        conversation_id=case.conversation_id if owns_conversation else None,
    )
