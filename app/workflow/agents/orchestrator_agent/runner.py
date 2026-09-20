"""Dispatches one Work-tab run (a "prepare", "prepare_verify", or "run_full" mode - mirroring the
source project's three tkinter buttons) to an async event stream that app/main.py's
/api/orchestrator/run/stream route turns into SSE frames.

"prepare" and "prepare_verify" call the dataset services directly (there's no planner/policy loop
for those two - same as ui.py's _prepare/_verify), wrapped in asyncio.to_thread since they're sync
pandas/opencv work. "run_full" builds an OrchestratorAgent and streams its async generator.
"""

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

from app.workflow.agents.orchestrator_agent.orchestrator import OrchestratorAgent
from app.workflow.agents.orchestrator_agent.services.dataset_preparation import DatasetPreparationService
from app.workflow.agents.orchestrator_agent.services.dataset_verification import DatasetVerificationService

_MODES = {"prepare", "prepare_verify", "run_full"}


async def run_stream(
    *,
    mode: str,
    dataset_csv: str,
    inspection_xml: str,
    image_root: str | None,
    username: str,
    feature_threshold: float = 0.70,
    defect_threshold: float = 0.70,
    use_llm: bool = False,
    llm_model: str | None = None,
    llm_fallback: bool = True,
) -> AsyncGenerator[dict[str, Any], None]:
    if mode not in _MODES:
        yield {"event": "error", "data": {"message": f"unknown mode: {mode!r}"}}
        yield {"event": "done", "data": {}}
        return

    try:
        if mode == "prepare":
            async for event in _run_prepare(dataset_csv, inspection_xml, image_root):
                yield event
        elif mode == "prepare_verify":
            async for event in _run_prepare_verify(dataset_csv, inspection_xml, image_root):
                yield event
        else:
            agent = OrchestratorAgent(
                username=username,
                feature_threshold=feature_threshold,
                defect_threshold=defect_threshold,
                use_llm=use_llm,
                planner_model=llm_model,
                allow_llm_fallback=llm_fallback,
            )
            async for event in agent.run(dataset_csv, inspection_xml, image_root):
                yield event
    except Exception as exc:  # noqa: BLE001 - last-resort guard so the stream always terminates with an event
        yield {"event": "error", "data": {"message": f"{type(exc).__name__}: {exc}"}}

    yield {"event": "done", "data": {}}


async def _run_prepare(
    dataset_csv: str, inspection_xml: str, image_root: str | None
) -> AsyncGenerator[dict[str, Any], None]:
    yield {"event": "log", "data": {"text": "=== DATASET PREPARATION ==="}}
    result = await asyncio.to_thread(
        DatasetPreparationService().prepare, dataset_csv, inspection_xml, image_root
    )
    yield {
        "event": "status",
        "data": {
            "status": result.status,
            "input_samples": result.metrics.get("total_samples", 0),
            "preparation_ready": result.metrics.get("ready_samples", 0),
            "verification_passed": 0,
            "inference_attempted": 0,
            "accepted": 0,
            "review_required": 0,
        },
    }
    payload = {
        "status": result.status,
        "message": result.message,
        "metrics": result.metrics,
        "errors": result.errors[:30],
    }
    yield {"event": "log", "data": {"text": json.dumps(payload, indent=2)}}
    yield {"event": "result", "data": payload}


async def _run_prepare_verify(
    dataset_csv: str, inspection_xml: str, image_root: str | None
) -> AsyncGenerator[dict[str, Any], None]:
    yield {"event": "log", "data": {"text": "=== PREPARE + VERIFY ==="}}
    prep = await asyncio.to_thread(
        DatasetPreparationService().prepare, dataset_csv, inspection_xml, image_root
    )
    samples = prep.data.get("samples", [])
    verify = await asyncio.to_thread(DatasetVerificationService().verify, samples)

    yield {
        "event": "status",
        "data": {
            "status": verify.status,
            "input_samples": prep.metrics.get("total_samples", 0),
            "preparation_ready": prep.metrics.get("ready_samples", 0),
            "verification_passed": verify.metrics.get("passed_samples", 0),
            "inference_attempted": 0,
            "accepted": 0,
            "review_required": 0,
        },
    }
    payload = {
        "preparation": {"status": prep.status, "metrics": prep.metrics},
        "verification": {
            "status": verify.status,
            "metrics": verify.metrics,
            "sample_results": verify.data.get("sample_results", []),
        },
    }
    yield {"event": "log", "data": {"text": json.dumps(payload, indent=2)}}
    yield {"event": "result", "data": payload}
