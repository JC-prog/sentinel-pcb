import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Case, CaseStatus

_CASE_NUMBER_RE = re.compile(r"^(?:CASE-)?0*(\d+)$", re.IGNORECASE)


def parse_case_number(case_number: str) -> int | None:
    """"CASE-000123", "case-123", or a bare "123" -> 123. None if unparseable."""

    match = _CASE_NUMBER_RE.match(case_number.strip())
    return int(match.group(1)) if match else None


async def create_case(
    session: AsyncSession,
    *,
    created_by_user_id: str,
    conversation_id: str,
    board_id: str,
    component_ref: str,
    package: str | None,
    feature: str | None,
    issue_symptom: str | None,
    image_id: str,
    inspection_xml_id: str | None,
    golden_image_id: str | None,
    region: str | None,
    region_confidence: float | None,
    defect_model: str | None,
    defect_label: str | None,
    defect_confidence: float | None,
    defect_scores: dict[str, float],
    measurement_validation: dict[str, Any] | None,
    explainability_result: dict[str, Any] | None,
    observations: list[str],
    status: str,
) -> Case:
    case = Case(
        created_by_user_id=created_by_user_id,
        conversation_id=conversation_id,
        board_id=board_id,
        component_ref=component_ref,
        package=package,
        feature=feature,
        issue_symptom=issue_symptom,
        image_id=image_id,
        inspection_xml_id=inspection_xml_id,
        golden_image_id=golden_image_id,
        region=region,
        region_confidence=region_confidence,
        defect_model=defect_model,
        defect_label=defect_label,
        defect_confidence=defect_confidence,
        defect_scores=defect_scores,
        measurement_validation=measurement_validation,
        explainability_result=explainability_result,
        observations=observations,
        status=status,
    )
    session.add(case)
    await session.commit()
    await session.refresh(case)
    return case


async def get_case_by_sequence_number(session: AsyncSession, sequence_number: int) -> Case | None:
    result = await session.scalars(select(Case).where(Case.sequence_number == sequence_number))
    return result.first()


async def list_cases(
    session: AsyncSession, *, status: CaseStatus | None, limit: int
) -> list[Case]:
    """Not scoped to the creating user - case review is inherently cross-operator, unlike
    Conversation's per-user scoping (app/chat/repository.py)."""

    stmt = select(Case).order_by(Case.created_at.desc()).limit(limit)
    if status is not None:
        stmt = stmt.where(Case.status == status)
    result = await session.scalars(stmt)
    return list(result)


async def resolve_case(
    session: AsyncSession,
    case: Case,
    *,
    status: CaseStatus,
    resolved_by_user_id: str,
    note: str | None,
) -> Case:
    case.status = status
    case.resolved_by_user_id = resolved_by_user_id
    case.resolved_at = datetime.now(UTC)
    case.resolution_note = note
    await session.commit()
    await session.refresh(case)
    return case
