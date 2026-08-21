"""Explainability & Review: drafts a report for an escalated workflow, before it reaches
HUMAN_REVIEW. Templated for this pass - no live LLM call - per the deliberate sequencing decision
to not turn on agentic/LLM behavior before Continuous Monitoring & Drift (Observability) exists
to watch it. Claims are built directly from evidence already computed by this point (feature
detections, the defect classification, the Orchestrator's own escalation rationale), so
`_grounding_check` is a real function call that's always a pass-through today - kept as its own
step so swapping in a real LLM + a real grounding check later is "replace this function's body,"
not "add a missing step."

Does not go through `app.policies.validate_action`/`ProposedAction` - it drafts a report, it
doesn't execute or decide anything. The Orchestrator already decided to escalate before this runs.
"""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.explainability_review.case_context import retrieve_context
from app.agents.multi_modal_inference import DefectPrediction, InferenceResult
from app.db.models import Workflow

PROMPT_VERSION = "template-v1"


class Claim(BaseModel):
    text: str
    supported: bool
    evidence_ref: str


class ExplanationDraft(BaseModel):
    """Trimmed from `planning/docs/DATA-MODEL.md`'s `Explanation` entity - no `explanationId`
    (the DB row's own PK covers it), no `subCaseId` (this build has no `ComponentSubCase`)."""

    claims: list[Claim]
    stripped_claims: list[Claim] = []
    grounded_flag: bool
    recommendation: str
    retrieved_precedents: list[dict[str, object]] = []
    retrieved_guidance: list[dict[str, object]] = []
    prompt_version: str = PROMPT_VERSION


def _grounding_check(claims: list[Claim]) -> tuple[list[Claim], list[Claim]]:
    """Every claim reaching this point is template-constructed directly from evidence, so there
    is nothing to strip today - see the module docstring for why this is still a real function.
    """

    return claims, []


async def draft_explanation(
    session: AsyncSession,
    workflow: Workflow,
    inference: InferenceResult,
    defect: DefectPrediction,
    escalation_rationale: str,
) -> ExplanationDraft:
    feature_labels = sorted({d.label for d in inference.detections})
    claims = [
        Claim(
            text=(
                f"Feature detection found {len(inference.detections)} structural feature(s) "
                f"({', '.join(feature_labels) or 'none'}) with a minimum confidence of "
                f"{inference.overall_confidence:.2f}."
            ),
            supported=True,
            evidence_ref="multi_modal_inference.feature_detection",
        ),
        Claim(
            text=(
                f"Defect classification predicted '{defect.label}' with confidence "
                f"{defect.confidence:.2f} ({defect.basis})."
            ),
            supported=True,
            evidence_ref="multi_modal_inference.defect_classification",
        ),
        Claim(
            text=escalation_rationale,
            supported=True,
            evidence_ref="orchestrator.policy_decision",
        ),
    ]
    claims, stripped = _grounding_check(claims)

    context = await retrieve_context(
        session,
        query_text=f"{defect.label}: {escalation_rationale}",
        current_workflow_id=workflow.id,
    )

    recommendation = (
        f"Escalate for human review: '{defect.label}' at {defect.confidence:.0%} confidence."
    )
    if context.guidance:
        recommendation += f' See retrieved guidance: "{context.guidance[0]["title"]}".'

    return ExplanationDraft(
        claims=claims,
        stripped_claims=stripped,
        grounded_flag=len(stripped) == 0,
        recommendation=recommendation,
        retrieved_precedents=context.precedents,
        retrieved_guidance=context.guidance,
    )
