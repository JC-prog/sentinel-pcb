import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.db.models import Case, CaseStatus

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
    golden_image_id: str | None,
    region: str | None,
    region_confidence: float | None,
    defect_model: str | None,
    defect_label: str | None,
    defect_confidence: float | None,
    defect_scores: dict[str, float],
    measurement_validation: dict[str, Any] | None,
    observations: list[str],
    status: str,
    explainability_result: dict[str, Any] | None = None,
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
        golden_image_id=golden_image_id,
        region=region,
        region_confidence=region_confidence,
        defect_model=defect_model,
        region_model_version=region_model_version,
        defect_model_version=defect_model_version,
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
    Conversation's per-user scoping (app/chat/services/repository.py)."""

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


def case_summary(case: Case) -> dict[str, Any]:
    """The JSON-friendly view of a Case that chat tools hand back to the model."""

    return {
        "case_number": case.case_number,
        "status": case.status,
        "board_id": case.board_id,
        "component_ref": case.component_ref,
        "package": case.package,
        "feature": case.feature,
        "region": case.region,
        "defect_label": case.defect_label,
        "defect_confidence": case.defect_confidence,
        "region_model_version": case.region_model_version,
        "defect_model_version": case.defect_model_version,
        "created_at": case.created_at.isoformat(),
    }


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


@dataclass(frozen=True)
class SimilarCase:
    case: Case
    score: float
    reasons: list[str] = field(default_factory=list)


# How many recent candidates are scored in Python. Similarity here is structured metadata, not
# vectors, so this bounds the work per request; older history is out of reach by design.
_SIMILARITY_CANDIDATE_WINDOW = 500


def _similarity(reference: Case, other: Case) -> tuple[float, list[str]]:
    """Score in [0, 1] and the reasons behind it. Same defect label dominates (that is what makes
    two cases "the same kind of defect"); where and on what it happened refine the order;
    closeness of the classifier's confidence and, in the caller, recency break ties."""

    score = 0.0
    reasons: list[str] = []
    if reference.defect_label and other.defect_label == reference.defect_label:
        score += 0.5
        reasons.append(f"same defect: {other.defect_label}")
    if reference.region and other.region == reference.region:
        score += 0.15
        reasons.append(f"same region: {other.region}")
    if other.component_ref and other.component_ref == reference.component_ref:
        score += 0.15
        reasons.append(f"same component: {other.component_ref}")
    if other.package and other.package == reference.package:
        score += 0.05
        reasons.append(f"same package: {other.package}")
    if other.feature and other.feature == reference.feature:
        score += 0.05
        reasons.append(f"same feature: {other.feature}")
    if other.board_id and other.board_id != "unknown" and other.board_id == reference.board_id:
        score += 0.05
        reasons.append(f"same board: {other.board_id}")
    if reference.defect_confidence is not None and other.defect_confidence is not None:
        score += 0.05 * (1.0 - abs(reference.defect_confidence - other.defect_confidence))
    return score, reasons


async def find_similar_cases(
    session: AsyncSession, reference: Case, *, limit: int = 5
) -> list[SimilarCase]:
    """Other Cases that look like `reference`, best first. Only cases sharing the reference's
    defect label (or, when it has none - e.g. an uncertain region - its region) are considered:
    a case that shares only a component is a different problem, not a similar one."""

    if reference.defect_label:
        matches = Case.defect_label == reference.defect_label
    elif reference.region:
        matches = Case.region == reference.region
    else:
        return []

    candidates = await session.scalars(
        select(Case)
        .where(matches, Case.id != reference.id)
        .order_by(Case.created_at.desc())
        .limit(_SIMILARITY_CANDIDATE_WINDOW)
    )
    scored = []
    for other in candidates:
        score, reasons = _similarity(reference, other)
        scored.append(SimilarCase(case=other, score=round(score, 3), reasons=reasons))
    # candidates arrive newest first and sort is stable, so equal scores stay newest first
    scored.sort(key=lambda item: item.score, reverse=True)
    return scored[:limit]
