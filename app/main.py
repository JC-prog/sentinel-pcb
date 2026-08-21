import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.continuous_monitoring_drift import ObservabilityReport, compute_metrics
from app.agents.orchestrator.runner import continue_workflow, create_workflow
from app.db import AuditEvent, Explanation, Workflow, WorkflowStatus, get_session, init_models
from app.db.session import async_session_factory
from app.settings import settings

_TERMINAL_STATUSES = [s.value for s in WorkflowStatus if s.is_terminal]
_ACTIVE_STATUSES = [s.value for s in WorkflowStatus if not s.is_terminal]

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await init_models()
    yield


app = FastAPI(title="SentinelPCB", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


class WorkflowSummary(BaseModel):
    workflow_id: str
    status: str
    board_id: str | None
    component_id: str | None
    recipe_id: str | None
    image_filename: str
    decision: str | None
    overall_confidence: float | None
    """The defect classifier's confidence - what actually gates auto-accept/escalate."""
    feature_confidence: float | None
    defect_label: str | None
    created_at: datetime
    completed_at: datetime | None


class ExplanationOut(BaseModel):
    claims: list[dict[str, object]]
    stripped_claims: list[dict[str, object]]
    grounded_flag: bool
    recommendation: str
    retrieved_precedents: list[dict[str, object]]
    retrieved_guidance: list[dict[str, object]]
    prompt_version: str


class WorkflowDetail(WorkflowSummary):
    metadata: dict[str, str]
    detections: list[dict[str, object]] | None
    rationale: str | None
    explanation: ExplanationOut | None


def _to_summary(workflow: Workflow) -> WorkflowSummary:
    return WorkflowSummary(
        workflow_id=workflow.id,
        status=workflow.status,
        board_id=workflow.board_id,
        component_id=workflow.component_id,
        recipe_id=workflow.recipe_id,
        image_filename=workflow.image_filename,
        decision=workflow.decision,
        overall_confidence=workflow.overall_confidence,
        feature_confidence=workflow.feature_confidence,
        defect_label=workflow.defect_label,
        created_at=workflow.created_at,
        completed_at=workflow.completed_at,
    )


def _to_explanation(explanation: Explanation) -> ExplanationOut:
    return ExplanationOut(
        claims=explanation.claims,
        stripped_claims=explanation.stripped_claims,
        grounded_flag=explanation.grounded_flag,
        recommendation=explanation.recommendation,
        retrieved_precedents=explanation.retrieved_precedents,
        retrieved_guidance=explanation.retrieved_guidance,
        prompt_version=explanation.prompt_version,
    )


def _to_detail(workflow: Workflow) -> WorkflowDetail:
    return WorkflowDetail(
        **_to_summary(workflow).model_dump(),
        metadata=workflow.metadata_json,
        detections=workflow.detections,
        rationale=workflow.rationale,
        explanation=_to_explanation(workflow.explanation) if workflow.explanation else None,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/workflows", status_code=202)
async def submit_workflow(
    background_tasks: BackgroundTasks,
    image: Annotated[UploadFile, File()],
    board_id: Annotated[str | None, Form()] = None,
    component_id: Annotated[str | None, Form()] = None,
    recipe_id: Annotated[str | None, Form()] = None,
    metadata: Annotated[str, Form()] = "{}",
) -> WorkflowSummary:
    """Queue entry point: the "AOI Camera + PLC" edge posts here (see
    `planning/docs/ADC-Design-Doc-Team10-V2.md` §5). Persists a RECEIVED workflow immediately and
    schedules the rest of the pipeline (Dataset Preparation -> Dataset Quality -> Multi-Modal
    Inference -> Orchestrator policy decision) as a background task, so the response doesn't
    block on processing - see `app/agents/orchestrator/runner.py`.
    """

    image_bytes = await image.read()
    parsed_metadata: dict[str, str] = json.loads(metadata) if metadata else {}

    workflow = await create_workflow(
        image=image_bytes,
        image_filename=image.filename or "unknown",
        board_id=board_id,
        component_id=component_id,
        recipe_id=recipe_id,
        metadata=parsed_metadata,
    )
    background_tasks.add_task(
        continue_workflow, workflow.id, image_bytes, workflow.image_filename, parsed_metadata
    )
    return _to_summary(workflow)


@app.get("/workflows")
async def list_workflows(
    session: SessionDep,
    terminal: bool | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[WorkflowSummary]:
    """Queue view: `terminal=false`. History view: `terminal=true`. A specific `status` (e.g.
    `LEARNING_QUEUE`) takes precedence over `terminal`. Omit both for everything.
    """

    stmt = select(Workflow).order_by(Workflow.created_at.desc()).limit(limit)
    if status is not None:
        stmt = stmt.where(Workflow.status == status)
    elif terminal is True:
        stmt = stmt.where(Workflow.status.in_(_TERMINAL_STATUSES))
    elif terminal is False:
        stmt = stmt.where(Workflow.status.in_(_ACTIVE_STATUSES))

    result = await session.execute(stmt)
    return [_to_summary(w) for w in result.scalars().all()]


@app.get("/workflows/{workflow_id}")
async def get_workflow(workflow_id: str, session: SessionDep) -> WorkflowDetail:
    """Board Information view + History drill-in."""

    # explanation is only ever accessed here (via _to_detail) - eager-load it explicitly, since
    # a plain lazy relationship access after session.get() isn't supported under async SQLAlchemy.
    workflow = await session.get(
        Workflow, workflow_id, options=[selectinload(Workflow.explanation)]
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    return _to_detail(workflow)


@app.get("/workflows/{workflow_id}/image")
async def get_workflow_image(workflow_id: str, session: SessionDep) -> FileResponse:
    workflow = await session.get(Workflow, workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    return FileResponse(workflow.image_path)


async def _trace_stream(workflow_id: str) -> AsyncGenerator[str, None]:
    """Polls for new AuditEvent rows every ~0.5s and yields them as SSE `transition` events,
    ending with a `done` event once the workflow reaches a terminal status. A fresh connection
    always replays full history first (last_id starts at 0), so a client connecting mid-workflow
    still sees everything that already happened - see the plan's M4 rationale for why this polls
    Postgres directly instead of using an in-process pub/sub (no Redis in this stack, single app
    instance, 500ms latency is imperceptible in a demo).
    """

    last_id = 0
    while True:
        async with async_session_factory() as session:
            result = await session.execute(
                select(AuditEvent)
                .where(AuditEvent.workflow_id == workflow_id, AuditEvent.id > last_id)
                .order_by(AuditEvent.id)
            )
            events = result.scalars().all()
            workflow = await session.get(Workflow, workflow_id)

        for event in events:
            last_id = event.id
            payload = {
                "id": event.id,
                "actor": event.actor,
                "action": event.action,
                "from_status": event.from_status,
                "to_status": event.to_status,
                "detail": event.detail,
                "created_at": event.created_at.isoformat(),
            }
            yield f"event: transition\ndata: {json.dumps(payload)}\n\n"

        if workflow is None or WorkflowStatus(workflow.status).is_terminal:
            yield "event: done\ndata: {}\n\n"
            return

        await asyncio.sleep(0.5)


@app.get("/workflows/{workflow_id}/trace")
async def trace_workflow(workflow_id: str, session: SessionDep) -> StreamingResponse:
    """Tracing view: a Server-Sent Events stream of this workflow's state transitions."""

    workflow = await session.get(Workflow, workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    return StreamingResponse(_trace_stream(workflow_id), media_type="text/event-stream")


@app.get("/observability/metrics")
async def observability_metrics(session: SessionDep) -> ObservabilityReport:
    """Observability view: decision distribution, confidence averages, feature/defect
    disagreement rate, and dashboard-only threshold-breach alerts - see
    app.agents.continuous_monitoring_drift.
    """

    return await compute_metrics(session)
