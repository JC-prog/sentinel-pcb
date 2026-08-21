"""Persisted workflow lifecycle - the production path used by `app/main.py`.

Wraps the same pure step functions `app.agents.orchestrator.run_workflow()` calls
(`prepare_sample`, `validate_sample`, the detector/classifier, `app.policies.validate_action`),
persisting a `Workflow` + `AuditEvent` row between each phase so the state machine is durable and
observable (Tracing view / `GET /workflows/{id}/trace`). `run_workflow()` itself is left as the
synchronous, DB-free reference implementation, kept in sync with the same decision logic.

Re-sequencing the steps here (rather than adding a transition-callback hook to `run_workflow()`)
avoids an awkward sync-callback-into-async-DB-write bridge - see the plan's "Notable choices"
section.

Phase sequence (see docs/AGENT-DESIGN.md for the full diagram):
    RECEIVED -> PREPARING -> QUALITY_CHECK -+-> REJECTED_QUALITY [terminal]
                                             +-> INFERENCE -> POLICY_DECISION -+-> ACCEPTED -> LEARNING_QUEUE [terminal]
                                                                                +-> EXPLANATION -> HUMAN_REVIEW [terminal]
"""

from __future__ import annotations

import asyncio
import io
import uuid
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.agents.dataset_preparation import prepare_sample
from app.agents.dataset_quality import validate_sample
from app.agents.explainability_review import draft_explanation
from app.agents.multi_modal_inference import get_defect_classifier, get_detector
from app.db.models import AuditEvent, Explanation, Workflow, WorkflowStatus
from app.db.session import async_session_factory
from app.policies import ActionType, PolicyViolation, ProposedAction, thresholds, validate_action
from app.services.embeddings import get_embedding_provider
from app.settings import settings

_ACTOR = "orchestrator"  # used only for the phases the Orchestrator itself actually owns


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _image_storage_path(workflow_id: str, image_filename: str) -> Path:
    storage_dir = Path(settings.image_storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(image_filename).suffix or ".bin"
    return storage_dir / f"{workflow_id}{suffix}"


def _embed_text(text: str) -> list[float]:
    return get_embedding_provider().embed(text)


async def create_workflow(
    image: bytes,
    image_filename: str,
    board_id: str | None = None,
    component_id: str | None = None,
    recipe_id: str | None = None,
    metadata: dict[str, str] | None = None,
) -> Workflow:
    """Fast path: persists a RECEIVED workflow and returns immediately. Actual processing
    continues in `continue_workflow`, invoked via FastAPI `BackgroundTasks` so this doesn't block
    the HTTP response - see the plan doc for why that matters to the Queue/Tracing views.
    """

    workflow_id = str(uuid.uuid4())
    image_path = _image_storage_path(workflow_id, image_filename)
    image_path.write_bytes(image)

    async with async_session_factory() as session:
        workflow = Workflow(
            id=workflow_id,
            status=WorkflowStatus.RECEIVED,
            board_id=board_id,
            component_id=component_id,
            recipe_id=recipe_id,
            image_filename=image_filename,
            image_path=str(image_path),
            metadata_json=metadata or {},
        )
        session.add(workflow)
        session.add(
            AuditEvent(
                workflow_id=workflow_id,
                actor=_ACTOR,
                action="WORKFLOW_OPENED",
                from_status=None,
                to_status=WorkflowStatus.RECEIVED,
            )
        )
        await session.commit()
        await session.refresh(workflow)
        return workflow


async def _transition(
    session: AsyncSession,
    workflow: Workflow,
    to_status: WorkflowStatus,
    action: str,
    actor: str,
    detail: str | None = None,
) -> None:
    from_status = workflow.status
    workflow.status = to_status
    session.add(
        AuditEvent(
            workflow_id=workflow.id,
            actor=actor,
            action=action,
            from_status=from_status,
            to_status=to_status,
            detail=detail,
        )
    )
    await session.commit()
    if settings.demo_phase_delay_seconds > 0:
        await asyncio.sleep(settings.demo_phase_delay_seconds)


async def _log_event(
    session: AsyncSession, workflow: Workflow, action: str, actor: str, detail: str | None = None
) -> None:
    """For intra-phase events that don't change `Workflow.status` - e.g. the two model calls
    inside INFERENCE, or Explainability's narrative draft inside EXPLANATION.
    """

    session.add(
        AuditEvent(
            workflow_id=workflow.id,
            actor=actor,
            action=action,
            from_status=workflow.status,
            to_status=workflow.status,
            detail=detail,
        )
    )
    await session.commit()
    if settings.demo_phase_delay_seconds > 0:
        await asyncio.sleep(settings.demo_phase_delay_seconds)


async def continue_workflow(
    workflow_id: str,
    image: bytes,
    image_filename: str,
    metadata: dict[str, str] | None = None,
) -> None:
    """Runs the full pipeline, persisting a state transition + audit event between each phase -
    intended to run via `BackgroundTasks`.
    """

    async with async_session_factory() as session:
        workflow = await session.get(Workflow, workflow_id)
        if workflow is None:
            return  # shouldn't happen - create_workflow() always inserts before this is scheduled

        await _transition(
            session, workflow, WorkflowStatus.PREPARING, "SAMPLE_ASSEMBLED", "dataset_preparation"
        )
        sample = prepare_sample(workflow_id, image, image_filename, metadata)

        await _transition(
            session, workflow, WorkflowStatus.QUALITY_CHECK, "QUALITY_CHECK", "dataset_quality"
        )
        quality = validate_sample(sample)
        if not quality.passed:
            workflow.decision = "rejected_quality"
            workflow.rationale = f"Dataset Quality failed: {', '.join(quality.failed_checks)}"
            workflow.detections = []
            workflow.overall_confidence = 0.0
            workflow.completed_at = _utcnow()
            await _transition(
                session,
                workflow,
                WorkflowStatus.REJECTED_QUALITY,
                "STATE_TRANSITION",
                "dataset_quality",
                workflow.rationale,
            )
            return

        await _transition(
            session, workflow, WorkflowStatus.INFERENCE, "INFERENCE", "multi_modal_inference"
        )
        pil_image = Image.open(io.BytesIO(sample.image)).convert("RGB")
        inference = await run_in_threadpool(get_detector().predict, pil_image)
        await _log_event(
            session,
            workflow,
            "FEATURE_DETECTED",
            "multi_modal_inference",
            f"overall_confidence={inference.overall_confidence:.3f}",
        )

        defect = get_defect_classifier().predict(inference)
        await _log_event(
            session,
            workflow,
            "DEFECT_CLASSIFIED",
            "multi_modal_inference",
            f"label={defect.label} confidence={defect.confidence:.3f}",
        )

        await _transition(
            session, workflow, WorkflowStatus.POLICY_DECISION, "POLICY_DECISION_STARTED", _ACTOR
        )

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

        proposed = ProposedAction(
            action=ActionType.AUTO_ACCEPT if auto_accept else ActionType.ESCALATE_TO_HUMAN,
            proposed_by=_ACTOR,
            workflow_id=workflow_id,
            rationale=rationale,
        )
        try:
            validate_action(proposed)
        except PolicyViolation as exc:
            # Still fails loudly (re-raised) - this audit row exists so a violation isn't lost,
            # not to make it a normal workflow outcome.
            await _log_event(session, workflow, "POLICY_VIOLATION", "policy", str(exc))
            raise

        workflow.decision = "auto_accept" if auto_accept else "escalate_to_human"
        workflow.feature_confidence = inference.overall_confidence
        workflow.overall_confidence = defect.confidence
        workflow.defect_label = defect.label
        workflow.rationale = rationale
        workflow.detections = [d.model_dump() for d in inference.detections]

        if auto_accept:
            workflow.completed_at = _utcnow()
            await _transition(
                session, workflow, WorkflowStatus.ACCEPTED, "STATE_TRANSITION", _ACTOR, rationale
            )

            summary = (
                f"board={workflow.board_id} component={workflow.component_id} "
                f"defect={defect.label} confidence={defect.confidence:.2f} decision=auto_accept"
            )
            workflow.embedding = await run_in_threadpool(_embed_text, summary)
            await _transition(
                session,
                workflow,
                WorkflowStatus.LEARNING_QUEUE,
                "STATE_TRANSITION",
                _ACTOR,
                "auto-accepted outcome queued as a labeled training example",
            )
            return

        await _transition(
            session, workflow, WorkflowStatus.EXPLANATION, "STATE_TRANSITION", _ACTOR, rationale
        )
        draft = await draft_explanation(session, workflow, inference, defect, rationale)
        session.add(
            Explanation(
                workflow_id=workflow.id,
                claims=[c.model_dump() for c in draft.claims],
                stripped_claims=[c.model_dump() for c in draft.stripped_claims],
                grounded_flag=draft.grounded_flag,
                recommendation=draft.recommendation,
                retrieved_precedents=draft.retrieved_precedents,
                retrieved_guidance=draft.retrieved_guidance,
                prompt_version=draft.prompt_version,
            )
        )
        await session.commit()
        await _log_event(
            session, workflow, "NARRATIVE_DRAFTED", "explainability_review", draft.recommendation
        )

        workflow.completed_at = _utcnow()
        await _transition(
            session,
            workflow,
            WorkflowStatus.HUMAN_REVIEW,
            "STATE_TRANSITION",
            _ACTOR,
            "handed off to human reviewer",
        )
