"""Orchestrator: owns the workflow, routes Dataset Preparation -> Dataset Quality ->
Multi-Modal Inference -> auto-accept/escalate decision.

TODO(spec): this is the deterministic backbone only. The Orchestrator's LLM reasoning trigger -
bounded to genuinely ambiguous/conflicting signals, per docs/ADC-Design-Doc-Team10-V2.md §6.1 -
is not implemented yet; every case here resolves by a single threshold comparison against
`app.policies.thresholds.auto_accept_confidence`. Explainability & Review (building an actual
evidence-backed explanation for the human reviewer) is also not implemented yet - escalation
here just returns the decision, it doesn't yet hand off to that agent.
"""

from __future__ import annotations

import io
import uuid
from typing import Literal

from PIL import Image
from pydantic import BaseModel

from app.agents.dataset_preparation import prepare_sample
from app.agents.dataset_quality import validate_sample
from app.agents.multi_modal_inference import Detection, get_detector
from app.policies import ActionType, ProposedAction, thresholds, validate_action

Decision = Literal["auto_accept", "escalate_to_human", "rejected_quality"]


class WorkflowResult(BaseModel):
    workflow_id: str
    decision: Decision
    detections: list[Detection]
    overall_confidence: float
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
            rationale=f"Dataset Quality failed: {', '.join(quality.failed_checks)}",
        )

    pil_image = Image.open(io.BytesIO(sample.image)).convert("RGB")
    inference = get_detector().predict(pil_image)

    auto_accept = inference.overall_confidence >= thresholds.auto_accept_confidence
    action = ProposedAction(
        action=ActionType.AUTO_ACCEPT if auto_accept else ActionType.ESCALATE_TO_HUMAN,
        proposed_by="orchestrator",
        workflow_id=wf_id,
        rationale=(
            f"overall_confidence={inference.overall_confidence:.3f} "
            f"{'>=' if auto_accept else '<'} "
            f"auto_accept_confidence={thresholds.auto_accept_confidence}"
        ),
    )
    validate_action(action)  # raises PolicyViolation if this ever isn't an allowed action

    return WorkflowResult(
        workflow_id=wf_id,
        decision="auto_accept" if auto_accept else "escalate_to_human",
        detections=inference.detections,
        overall_confidence=inference.overall_confidence,
        rationale=action.rationale,
    )


__all__ = ["Decision", "WorkflowResult", "run_workflow"]
