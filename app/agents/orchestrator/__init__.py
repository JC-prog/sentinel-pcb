"""Orchestrator: owns the workflow, routes Dataset Preparation -> Dataset Quality ->
Multi-Modal Inference -> policy decision.

Pure, synchronous, DB-free reference implementation - kept in sync with the persisted path
(`app/agents/orchestrator/runner.py`, used by the live API) so the two "parallel
implementations of the same pipeline" never drift on decision logic, only on I/O.

TODO(spec): the Orchestrator's LLM reasoning trigger - bounded to genuinely ambiguous/conflicting
signals - is not implemented yet; every case here resolves deterministically (feature/defect
confidence + disagreement, see `runner.py`). Explainability & Review's report is built in the
persisted path only - this reference implementation only returns the decision, it doesn't hand
off to that agent (it has no database to store an Explanation row in).
"""

from __future__ import annotations

import io
import uuid
from typing import Literal

from PIL import Image
from pydantic import BaseModel

from app.agents.dataset_preparation import prepare_sample
from app.agents.dataset_quality import validate_sample
from app.agents.multi_modal_inference import Detection, get_defect_classifier, get_detector
from app.policies import ActionType, ProposedAction, thresholds, validate_action

Decision = Literal["auto_accept", "escalate_to_human", "rejected_quality"]


class WorkflowResult(BaseModel):
    workflow_id: str
    decision: Decision
    detections: list[Detection]
    overall_confidence: float
    """The defect classifier's confidence - what actually gates auto-accept/escalate."""
    feature_confidence: float
    defect_label: str | None
    rationale: str


def run_workflow(
    image: bytes,
    image_filename: str,
    metadata: dict[str, str] | None = None,
    workflow_id: str | None = None,
) -> WorkflowResult:
    wf_id = workflow_id or str(uuid.uuid4())

    sample = prepare_sample(wf_id, image, image_filename, metadata)

    quality = validate_sample(sample)
    if not quality.passed:
        return WorkflowResult(
            workflow_id=wf_id,
            decision="rejected_quality",
            detections=[],
            overall_confidence=0.0,
            feature_confidence=0.0,
            defect_label=None,
            rationale=f"Dataset Quality failed: {', '.join(quality.failed_checks)}",
        )

    pil_image = Image.open(io.BytesIO(sample.image)).convert("RGB")
    inference = get_detector().predict(pil_image)
    defect = get_defect_classifier().predict(inference)

    low_confidence = defect.confidence < thresholds.auto_accept_confidence
    disagreement = abs(inference.overall_confidence - defect.confidence)
    conflicting = disagreement > thresholds.confidence_disagreement_delta
    auto_accept = not low_confidence and not conflicting

    reasons: list[str] = []
    if low_confidence:
        reasons.append(
            f"defect_confidence={defect.confidence:.3f} < "
            f"auto_accept_confidence={thresholds.auto_accept_confidence}"
        )
    if conflicting:
        reasons.append(
            f"disagreement={disagreement:.3f} > "
            f"confidence_disagreement_delta={thresholds.confidence_disagreement_delta} "
            f"(feature_confidence={inference.overall_confidence:.3f}, "
            f"defect_confidence={defect.confidence:.3f})"
        )
    rationale = (
        "; ".join(reasons)
        if reasons
        else (
            f"defect_confidence={defect.confidence:.3f} >= "
            f"auto_accept_confidence={thresholds.auto_accept_confidence}, no disagreement"
        )
    )

    action = ProposedAction(
        action=ActionType.AUTO_ACCEPT if auto_accept else ActionType.ESCALATE_TO_HUMAN,
        proposed_by="orchestrator",
        workflow_id=wf_id,
        rationale=rationale,
    )
    validate_action(action)  # raises PolicyViolation if this ever isn't an allowed action

    return WorkflowResult(
        workflow_id=wf_id,
        decision="auto_accept" if auto_accept else "escalate_to_human",
        detections=inference.detections,
        overall_confidence=defect.confidence,
        feature_confidence=inference.overall_confidence,
        defect_label=defect.label,
        rationale=action.rationale,
    )


__all__ = ["Decision", "WorkflowResult", "run_workflow"]
