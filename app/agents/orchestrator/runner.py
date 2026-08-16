"""Persisted workflow lifecycle - the production path used by `app/main.py`.

Wraps the same pure step functions `app.agents.orchestrator.run_workflow()` calls
(`prepare_sample`, `validate_sample`, `get_detector().predict`, `app.policies.validate_action`),
persisting a `Workflow` + `AuditEvent` row between each phase so the state machine is durable and
observable (Tracing view / `GET /workflows/{id}/trace`). `run_workflow()` itself is left
untouched - it stays the synchronous, DB-free reference implementation, still covered by its own
tests.

Re-sequencing the steps here (rather than adding a transition-callback hook to `run_workflow()`)
avoids an awkward sync-callback-into-async-DB-write bridge - see the plan's "Notable choices"
section.
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
from app.agents.multi_modal_inference import get_detector
from app.db.models import AuditEvent, Workflow, WorkflowStatus
from app.db.session import async_session_factory
from app.policies import ActionType, ProposedAction, thresholds, validate_action
from app.settings import settings

_ACTOR = "orchestrator"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _image_storage_path(workflow_id: str, image_filename: str) -> Path:
    storage_dir = Path(settings.image_storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(image_filename).suffix or ".bin"
    return storage_dir / f"{workflow_id}{suffix}"


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
    detail: str | None = None,
) -> None:
    from_status = workflow.status
    workflow.status = to_status
    session.add(
        AuditEvent(
            workflow_id=workflow.id,
            actor=_ACTOR,
            action=action,
            from_status=from_status,
            to_status=to_status,
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
    """Runs the same phases `app.agents.orchestrator.run_workflow()` does, persisting a state
    transition + audit event between each one - intended to run via `BackgroundTasks`.
    """

    async with async_session_factory() as session:
        workflow = await session.get(Workflow, workflow_id)
        if workflow is None:
            return  # shouldn't happen - create_workflow() always inserts before this is scheduled

        await _transition(session, workflow, WorkflowStatus.PREPARING, "SAMPLE_ASSEMBLED")
        sample = prepare_sample(workflow_id, image, image_filename, metadata)

        await _transition(session, workflow, WorkflowStatus.QUALITY_CHECK, "QUALITY_CHECK")
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
                workflow.rationale,
            )
            return

        await _transition(session, workflow, WorkflowStatus.INFERENCE, "INFERENCE")
        pil_image = Image.open(io.BytesIO(sample.image)).convert("RGB")
        inference = await run_in_threadpool(get_detector().predict, pil_image)

        auto_accept = inference.overall_confidence >= thresholds.auto_accept_confidence
        proposed = ProposedAction(
            action=ActionType.AUTO_ACCEPT if auto_accept else ActionType.ESCALATE_TO_HUMAN,
            proposed_by=_ACTOR,
            workflow_id=workflow_id,
            rationale=(
                f"overall_confidence={inference.overall_confidence:.3f} "
                f"{'>=' if auto_accept else '<'} "
                f"auto_accept_confidence={thresholds.auto_accept_confidence}"
            ),
        )
        validate_action(proposed)  # raises PolicyViolation if this ever isn't an allowed action

        workflow.decision = "auto_accept" if auto_accept else "escalate_to_human"
        workflow.overall_confidence = inference.overall_confidence
        workflow.rationale = proposed.rationale
        workflow.detections = [d.model_dump() for d in inference.detections]
        workflow.completed_at = _utcnow()

        terminal_status = WorkflowStatus.COMPLETED if auto_accept else WorkflowStatus.HUMAN_REVIEW
        await _transition(
            session, workflow, terminal_status, "STATE_TRANSITION", proposed.rationale
        )
