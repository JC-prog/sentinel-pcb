import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.db.models import Case

_CASE_NUMBER_RE = re.compile(r"^(?:CASE-)?0*(\d+)$", re.IGNORECASE)

# The stage-1 classifier every inspection runs first; the defect models are named by region.
REGION_MODEL = "pcb_region"


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
    region: str | None,
    region_confidence: float | None,
    defect_model: str | None,
    defect_label: str | None,
    defect_confidence: float | None,
    defect_scores: dict[str, float],
    measurement_validation: dict[str, Any] | None,
    observations: list[str],
    status: str,
    region_model_version: str | None = None,
    defect_model_version: str | None = None,
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
        region=region,
        region_confidence=region_confidence,
        defect_model=defect_model,
        region_model_version=region_model_version,
        defect_model_version=defect_model_version,
        defect_label=defect_label,
        defect_confidence=defect_confidence,
        defect_scores=defect_scores,
        measurement_validation=measurement_validation,
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


async def resolve_case_ref(
    session: AsyncSession, case_number: str | None, conversation_id: str
) -> tuple[Case | None, str | None]:
    """The (case, error) a chat tool needs from an optional case number: the numbered case, or -
    when the user said "it" / "that case" and gave none - the latest case in this conversation.
    Exactly one of the two is None; the error is the string the tool hands back to the model."""

    case_number = (case_number or "").strip()
    if not case_number:
        latest = await latest_case_in_conversation(session, conversation_id)
        if latest is None:
            return None, (
                "no case in this conversation yet - inspect an image first, or give a case number"
            )
        return latest, None

    sequence_number = parse_case_number(case_number)
    if sequence_number is None:
        return None, f"invalid case number: {case_number!r}"
    case = await get_case_by_sequence_number(session, sequence_number)
    if case is None:
        return None, "case not found"
    return case, None


async def latest_case_in_conversation(session: AsyncSession, conversation_id: str) -> Case | None:
    """The most recent Case created from this conversation - what "the current defect" means
    when a user asks a follow-up without naming a case number."""

    result = await session.scalars(
        select(Case)
        .where(Case.conversation_id == conversation_id)
        .order_by(Case.created_at.desc())
        .limit(1)
    )
    return result.first()
