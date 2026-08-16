"""Tests for the persisted workflow lifecycle (app.agents.orchestrator.runner) - the production
path app/main.py uses. Contrast with test_orchestrator.py, which tests the pure/offline
run_workflow() reference implementation these functions re-sequence.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.orchestrator.runner import continue_workflow, create_workflow
from app.db.models import AuditEvent, Workflow, WorkflowStatus
from app.settings import settings

MODEL_PATH = Path(settings.adc_model_path)
DEMO_IMAGE = Path(__file__).resolve().parents[3] / "simulation" / "images" / "pcb-demo.png"


async def test_rejected_quality_reaches_terminal_state(db_session: AsyncSession) -> None:
    created = await create_workflow(image=b"not an image", image_filename="garbage.png")
    await continue_workflow(created.id, b"not an image", "garbage.png")

    workflow = await db_session.get(Workflow, created.id)
    assert workflow is not None
    assert workflow.status == WorkflowStatus.REJECTED_QUALITY
    assert workflow.decision == "rejected_quality"
    assert workflow.completed_at is not None

    events = (
        (
            await db_session.execute(
                select(AuditEvent)
                .where(AuditEvent.workflow_id == workflow.id)
                .order_by(AuditEvent.id)
            )
        )
        .scalars()
        .all()
    )
    assert [e.to_status for e in events] == [
        WorkflowStatus.RECEIVED,
        WorkflowStatus.PREPARING,
        WorkflowStatus.QUALITY_CHECK,
        WorkflowStatus.REJECTED_QUALITY,
    ]


@pytest.mark.skipif(
    not MODEL_PATH.exists() or not DEMO_IMAGE.exists(),
    reason="ADC model/demo image not present - see docs/ADC-Design-Doc-Team10-V2.md §8",
)
async def test_full_pipeline_reaches_a_decision(db_session: AsyncSession) -> None:
    image_bytes = DEMO_IMAGE.read_bytes()
    created = await create_workflow(image=image_bytes, image_filename=DEMO_IMAGE.name)
    await continue_workflow(created.id, image_bytes, DEMO_IMAGE.name)

    workflow = await db_session.get(Workflow, created.id)
    assert workflow is not None
    assert workflow.status in (WorkflowStatus.COMPLETED, WorkflowStatus.HUMAN_REVIEW)
    assert workflow.decision in ("auto_accept", "escalate_to_human")
    assert workflow.completed_at is not None
