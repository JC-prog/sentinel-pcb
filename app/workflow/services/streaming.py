"""Business logic for the Work tab's streaming run: resolves uploaded dataset/XML/image-root
paths, bridges into the teammate's app/workflow/src/agent1_orchestrator/ business logic (and its
sibling top-level planner/state/verification packages - see _bridge_sys_path below), and turns its
results into SSE frame text. Kept separate from app/workflow/api/orchestrator.py (the thin route
layer) the same way app/chat/services/streaming.py is kept separate from app/chat/api/chat.py.

"prepare" and "prepare_verify" call the dataset services directly (there's no planner/policy loop
for those two - same as ui.py's own _prepare_async/_verify_async), wrapped in asyncio.to_thread
since they're sync pandas/opencv work. "run_full" builds an OrchestratorAgent and runs its (also
synchronous) run() in a worker thread, live-streaming progress via the on_step callback
(agents/orchestrator.py) rather than waiting for the whole run to finish.
"""

import asyncio
import json
import logging
import os
import sys
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from app.shared.config.settings import settings
from app.workflow.services import uploads as orchestrator_uploads
from app.workflow.services.schemas import OrchestratorRunRequest

logger = logging.getLogger(__name__)

_MODES = {"prepare", "prepare_verify", "run_full"}

# The two sys.path roots the teammate's own code expects for its bare (agents.*, services.*,
# planner.*, state.*, verification.*, policy.*, inference.*) imports - exactly what his ui.py
# inserts for a direct tkinter launch. Only ever affects those bare, unqualified imports; this
# repo's own code always imports through the fully-qualified app.<module>... form, so there's no
# collision with e.g. the separate top-level inference/ microservice package.
_WORKFLOW_ROOT = Path(__file__).resolve().parents[1]
_AGENT1_ROOT = _WORKFLOW_ROOT / "src" / "agent1_orchestrator"


def _bridge_sys_path() -> None:
    for path in (_WORKFLOW_ROOT, _AGENT1_ROOT):
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)


def _frame(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def orchestrator_sse(
    request: OrchestratorRunRequest, username: str
) -> AsyncGenerator[str, None]:
    if request.mode not in _MODES:
        yield _frame("error", {"message": f"unknown mode: {request.mode!r}"})
        yield _frame("done", {})
        return

    dataset_path = orchestrator_uploads.resolve_dataset_path(request.dataset_id)
    xml_path = orchestrator_uploads.resolve_xml_path(request.xml_id)
    image_root_path = (
        orchestrator_uploads.resolve_image_root_path(request.image_root_id)
        if request.image_root_id
        else None
    )

    if dataset_path is None:
        yield _frame("error", {"message": "dataset upload not found"})
        yield _frame("done", {})
        return
    if xml_path is None:
        yield _frame("error", {"message": "inspection XML upload not found"})
        yield _frame("done", {})
        return

    image_root = str(image_root_path) if image_root_path is not None else None
    _bridge_sys_path()

    try:
        if request.mode == "prepare":
            async for frame in _run_prepare(str(dataset_path), str(xml_path), image_root):
                yield frame
        elif request.mode == "prepare_verify":
            async for frame in _run_prepare_verify(str(dataset_path), str(xml_path), image_root):
                yield frame
        else:
            async for frame in _run_full(
                request, str(dataset_path), str(xml_path), image_root, username
            ):
                yield frame
    except Exception as exc:  # last-resort guard so the stream always terminates with an event
        logger.exception("orchestrator run failed")
        yield _frame("error", {"message": f"{type(exc).__name__}: {exc}"})

    yield _frame("done", {})


async def _run_prepare(
    dataset_csv: str, inspection_xml: str, image_root: str | None
) -> AsyncGenerator[str, None]:
    # Bare imports only resolvable via _bridge_sys_path() above - app/workflow/src is excluded
    # from mypy (pyproject.toml) the same way it's excluded from ruff, so it can't be resolved
    # statically either.
    from services.dataset_preparation import DatasetPreparationService  # type: ignore

    yield _frame("log", {"text": "=== DATASET PREPARATION ==="})
    result = await asyncio.to_thread(
        DatasetPreparationService().prepare, dataset_csv, inspection_xml, image_root
    )
    yield _frame("status", {
        "status": result.status,
        "input_samples": result.metrics.get("total_samples", 0),
        "preparation_ready": result.metrics.get("ready_samples", 0),
        "verification_passed": 0,
        "inference_attempted": 0,
        "accepted": 0,
        "review_required": 0,
    })
    payload = {
        "status": result.status,
        "message": result.message,
        "metrics": result.metrics,
        "errors": result.errors[:30],
    }
    yield _frame("log", {"text": json.dumps(payload, indent=2, default=str)})
    yield _frame("result", payload)


async def _run_prepare_verify(
    dataset_csv: str, inspection_xml: str, image_root: str | None
) -> AsyncGenerator[str, None]:
    # See _run_prepare's comment above on these bare, statically-unresolvable imports.
    from services.dataset_preparation import DatasetPreparationService
    from services.dataset_verification import DatasetVerificationService  # type: ignore

    yield _frame("log", {"text": "=== PREPARE + VERIFY ==="})
    prep = await asyncio.to_thread(
        DatasetPreparationService().prepare, dataset_csv, inspection_xml, image_root
    )
    samples = prep.data.get("samples", [])
    verify = await asyncio.to_thread(DatasetVerificationService().verify, samples)

    yield _frame("status", {
        "status": verify.status,
        "input_samples": prep.metrics.get("total_samples", 0),
        "preparation_ready": prep.metrics.get("ready_samples", 0),
        "verification_passed": verify.metrics.get("passed_samples", 0),
        "inference_attempted": 0,
        "accepted": 0,
        "review_required": 0,
    })
    payload = {
        "preparation": {"status": prep.status, "metrics": prep.metrics},
        "verification": {
            "status": verify.status,
            "metrics": verify.metrics,
            "sample_results": verify.data.get("sample_results", []),
        },
    }
    yield _frame("log", {"text": json.dumps(payload, indent=2, default=str)})
    yield _frame("result", payload)


async def _run_full(
    request: OrchestratorRunRequest,
    dataset_csv: str,
    inspection_xml: str,
    image_root: str | None,
    username: str,
) -> AsyncGenerator[str, None]:
    # See _run_prepare's comment above on these bare, statically-unresolvable imports.
    from agents.orchestrator import OrchestratorAgent  # type: ignore[import-not-found]
    from state.workflow_state import WorkflowState  # type: ignore[import-not-found]

    if request.use_llm and settings.orchestrator_openai_api_key:
        # agent1_orchestrator's planner (planner/llm_planner.py) reads OPENAI_API_KEY straight
        # from the environment - its own ported code, not settings. Mirror settings' key into it,
        # the same carve-out CLAUDE.md documents for the sibling explainability agent; never
        # overwrite a key already present in the process environment.
        os.environ.setdefault("OPENAI_API_KEY", settings.orchestrator_openai_api_key)

    agent = OrchestratorAgent(
        str(_WORKFLOW_ROOT),
        feature_threshold=request.feature_threshold,
        defect_threshold=request.defect_threshold,
        use_llm=request.use_llm,
        planner_model=request.llm_model or settings.orchestrator_openai_model,
        allow_llm_fallback=request.llm_fallback,
        # Agent 2 escalation and local vector-db indexing are out of scope for this pass - see
        # inference/MODELS.md-adjacent notes and the plan's "explicitly kept, not wired" section.
        # Matches ui.py's own current default for both flags.
        enable_a2a=False,
        populate_vector_db=False,
    )

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    def on_step(state: WorkflowState) -> None:
        loop.call_soon_threadsafe(
            queue.put_nowait,
            {
                "status": {
                    "status": state.status,
                    "input_samples": state.input_samples,
                    "preparation_ready": state.preparation_ready,
                    "verification_passed": state.verification_passed,
                    "inference_attempted": state.inference_attempted,
                    "accepted": state.accepted,
                    "review_required": state.review_required,
                },
                "plan_step": state.plan_history[-1] if state.plan_history else None,
                "observation": state.observations[-1] if state.observations else None,
            },
        )

    run_task = asyncio.ensure_future(
        asyncio.to_thread(agent.run, dataset_csv, inspection_xml, image_root, on_step=on_step)
    )
    run_task.add_done_callback(lambda _task: queue.put_nowait(None))

    while True:
        item = await queue.get()
        if item is None:
            break
        if item["observation"]:
            yield _frame("log", {"text": item["observation"]})
        if item["plan_step"] is not None:
            yield _frame("plan_step", item["plan_step"])
        yield _frame("status", item["status"])

    state = await run_task  # re-raises if the worker thread raised

    result_payload = {
        "workflow_status": state.status,
        "termination_reason": state.termination_reason,
        "results": state.inference_results,
        "input_samples": state.input_samples,
        "preparation_ready": state.preparation_ready,
        "preparation_failed": state.preparation_failed,
        "verification_passed": state.verification_passed,
        "verification_failed": state.verification_failed,
        "inference_attempted": state.inference_attempted,
        "inference_completed": state.inference_completed,
        "accepted": state.accepted,
        "review_required": state.review_required,
        "inference_aborted": state.inference_aborted,
        "errors": state.errors,
    }
    yield _frame("log", {"text": json.dumps(result_payload, indent=2, default=str)})
    yield _frame("result", result_payload)
