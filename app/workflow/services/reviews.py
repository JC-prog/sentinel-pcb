"""Agent 2 (the explainability review) and human-in-the-loop conflict resolution for the Work tab,
the web replacement for the source project's tkinter HumanReviewDialog/ReviewConsole (ui.py).

Flow: a finished run is persisted (run_store.py - the source project's three Qdrant collections)
and registers every REVIEW_REQUIRED sample's Agent 2 input here (register_run), keyed by
(run_id, sample_id). The Work tab then asks for a review of one sample (run_review), which runs the
drop-in's LangGraph pipeline (src/agent2_explainability/pipeline/review_graph.py), stores its output
as the sample's review point and compares its verdict with Agent 1's. If they agree there is nothing
to resolve; if they differ the operator picks Agent 1, Agent 2 or a manual IPC class, and
save_decision adds that to the same review point (Qdrant, like the source project's Data API).

The Agent 2 input - image paths, board, component, AOI measurements - is kept server-side rather
than sent back by the browser the way the monitoring routes' sample dicts are: those paths are read
from disk and the image is base64-sent to a VLM, so a browser-supplied path would be a
file-disclosure hole. The in-memory registry (bounded) is a cache over Qdrant: after a backend
restart ensure_run_loaded rebuilds it from the stored sample points, so the Review Console still
lists the run. With Qdrant unreachable the registry is all there is (a restart then makes run_review
raise UnknownReviewCase - rerun the dataset), and a decision falls back to the Postgres
`workflow_review_decisions` table, which also still holds decisions saved before runs were persisted.
"""

import asyncio
import importlib
import logging
import os
import re
import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.config.settings import settings
from app.shared.db import User
from app.shared.db.models import WorkflowReviewDecision
from app.workflow.services import run_store
from app.workflow.services.schemas import (
    WorkflowReviewCaseOut,
    WorkflowReviewDecisionOut,
    WorkflowReviewDecisionRequest,
    WorkflowReviewOut,
)

logger = logging.getLogger(__name__)

SELECTED_SOURCES = frozenset({"MACHINE", "AI", "MANUAL"})

_MAX_REGISTERED_CASES = 2000
_cases: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
_reviews: dict[tuple[str, str], WorkflowReviewOut] = {}
_cases_lock = threading.Lock()

_CANONICAL_DEFECTS = (
    "missing part",
    "shifted",
    "foreign material",
    "tombstone",
    "solder insufficient",
    "wrong part",
    "no defect",
)


class UnknownReviewCase(Exception):
    """No registered Agent 2 input for this (run_id, sample_id) - never registered, evicted, or
    lost to a backend restart."""


def normalize_defect(label: str | None) -> str:
    """'MissingPart' / 'WrongPart_13' / 'Shift' / 'Golden' -> the canonical lower-case IPC class,
    the same comparison ui.py's normalize_defect does before deciding whether the agents agree."""

    if not label:
        return "no defect"
    cleaned = label.strip().split("_")[0]
    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", cleaned).lower().replace("-", " ")
    spaced = " ".join(spaced.split())
    if spaced == "golden":
        return "no defect"
    squashed = spaced.replace(" ", "")
    for canonical in _CANONICAL_DEFECTS:
        if canonical.replace(" ", "") == squashed:
            return canonical
    # The body model's "Shift" is a prefix of the canonical "shifted".
    for canonical in _CANONICAL_DEFECTS:
        if len(squashed) >= 4 and canonical.replace(" ", "").startswith(squashed):
            return canonical
    return spaced


def _agent1_label(result: dict[str, Any]) -> str | None:
    defect = result.get("defect_classification")
    prediction = defect.get("prediction") if isinstance(defect, dict) else None
    return str(prediction) if prediction else None


def _feature_label(result: dict[str, Any]) -> str | None:
    """The stage-1 prediction - top level for a completed sample, nested under "details" for one
    that stopped at feature classification (see monitoring.py's module docstring)."""

    feature = result.get("feature_classification")
    if not isinstance(feature, dict):
        details = result.get("details")
        feature = details.get("feature_classification") if isinstance(details, dict) else None
    prediction = feature.get("prediction") if isinstance(feature, dict) else None
    return str(prediction) if prediction else None


def build_agent2_input(sample: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Mirrors adc_shared/agent2_api.py's build_input for one verified sample + its Agent 1
    result. A sample that stopped at feature classification (low-confidence stage 1) has no defect
    prediction, so - like the source - Agent 1's baseline falls back to the dataset's own machine
    defect label; Agent 2 then audits that AOI call."""

    defect = result.get("defect_classification")
    return {
        "board_id": sample.get("board", "UNKNOWN"),
        "component_ref": sample.get("component", "UNKNOWN"),
        "defect_image_path": sample.get("defect_image"),
        "golden_image_path": sample.get("golden_image"),
        "feature_type": _feature_label(result) or sample.get("source_feature") or "Body",
        "preliminary_defect": _agent1_label(result) or sample.get("machine_defect") or "",
        "confidence": defect.get("confidence", 0.0) if isinstance(defect, dict) else 0.0,
        "aoi_measurements": sample.get("failed_inspections", {}),
    }


def register_run(
    run_id: str, verified_samples: list[dict[str, Any]], results: list[dict[str, Any]]
) -> int:
    """Remembers the Agent 2 input for every REVIEW_REQUIRED result - including samples that
    stopped at feature classification, which the source also sends to Agent 2. Returns how many
    were registered."""

    by_id = {str(s.get("sample_id")): s for s in verified_samples}
    registered = 0
    with _cases_lock:
        for result in results:
            sample_id = str(result.get("sample_id"))
            sample = by_id.get(sample_id)
            if result.get("final_decision") != "REVIEW_REQUIRED" or sample is None:
                continue
            _cases[(run_id, sample_id)] = build_agent2_input(sample, result)
            registered += 1
        _evict_locked()
    return registered


def _evict_locked() -> None:
    while len(_cases) > _MAX_REGISTERED_CASES:
        evicted, _ = _cases.popitem(last=False)
        _reviews.pop(evicted, None)


async def ensure_run_loaded(run_id: str) -> None:
    """Rebuilds the registry for `run_id` from Qdrant when it isn't in memory (backend restarted,
    or the run was evicted): the REVIEW_REQUIRED sample points give the Agent 2 inputs, the stored
    review points give the reviews already generated. A no-op when the run is already registered or
    Qdrant has nothing / can't be reached."""

    with _cases_lock:
        if any(rid == run_id for rid, _ in _cases):
            return
    try:
        points = await run_store.review_cases(run_id)
        stored = await run_store.reviews_for_run(run_id)
    except (run_store.StoreUnavailable, KeyError, ValueError):
        return
    with _cases_lock:
        for point in points:
            key = (run_id, str(point.get("sample_id")))
            if key not in _cases:
                _cases[key] = build_agent2_input(
                    point.get("sample") or {}, point.get("inference") or {}
                )
        for record in stored:
            key = (run_id, str(record.get("sample_id")))
            output = (record.get("result") or {}).get("output")
            if output is not None and key in _cases and key not in _reviews:
                _reviews[key] = _build_review(run_id, key[1], _cases[key], output)
        _evict_locked()


def _execute_pipeline() -> Callable[[dict[str, Any]], dict[str, Any]]:
    # src/ has no importable package root of its own (it is excluded from mypy/ruff, see
    # pyproject.toml), so resolve it by full dotted path, lazily - it imports langchain, yaml and
    # python-dotenv, none of which a run that never reviews anything should pay for.
    module = importlib.import_module("app.workflow.src.agent2_explainability.pipeline.review_graph")
    execute: Callable[[dict[str, Any]], dict[str, Any]] = module.execute_explainability_review
    return execute


def _build_review(
    run_id: str, sample_id: str, case: dict[str, Any], output: dict[str, Any]
) -> WorkflowReviewOut:
    agent1 = normalize_defect(case["preliminary_defect"])
    agent2 = normalize_defect(output.get("predicted_defect") or case["preliminary_defect"])
    self_check_passed = bool(output.get("self_check_passed", False))
    return WorkflowReviewOut(
        run_id=run_id,
        sample_id=sample_id,
        agent1_verdict=agent1,
        agent2_verdict=agent2,
        conflict=agent1 != agent2 or not self_check_passed,
        diagnosis=str(output.get("diagnosis") or ""),
        confidence=float(output.get("final_confidence") or 0.0),
        self_check_passed=self_check_passed,
        contradiction_detected=bool(output.get("contradiction_detected", False)),
        ipc_citations=[str(c) for c in output.get("ipc_citations") or []],
        visual_evidence=str(output.get("visual_evidence") or ""),
        errors=[str(e) for e in output.get("errors") or []],
    )


async def run_review(run_id: str, sample_id: str) -> WorkflowReviewOut:
    await ensure_run_loaded(run_id)
    with _cases_lock:
        case = _cases.get((run_id, sample_id))
        existing = _reviews.get((run_id, sample_id))
    if case is None:
        raise UnknownReviewCase(sample_id)
    if existing is not None:
        # The first Agent 2 review of a sample is immutable (the source project's rule), so asking
        # again returns it instead of spending another VLM call on a result that couldn't be saved.
        return existing

    if settings.orchestrator_openai_api_key:
        # review_graph.py reads OPENAI_API_KEY straight from the environment (its own drop-in
        # code, not settings) - same carve-out as the planner in streaming.py; never overwrites a
        # key already in the process environment. Without one it uses its heuristic self-check.
        os.environ.setdefault("OPENAI_API_KEY", settings.orchestrator_openai_api_key)

    output = await asyncio.to_thread(_execute_pipeline(), dict(case))

    review = _build_review(run_id, sample_id, case, output)
    with _cases_lock:
        _reviews[(run_id, sample_id)] = review
    try:
        await run_store.save_review(
            run_id,
            sample_id,
            {
                "review_status": "GENERATED_UNVALIDATED",
                "source": "agent2_web_pipeline",
                "output": output,
                "warning": "Validate evidence before promoting to approved verdict.",
            },
        )
    except (run_store.StoreUnavailable, KeyError, ValueError) as exc:
        logger.warning("agent 2 review for %s/%s not persisted: %s", run_id, sample_id, exc)
    return review


def sample_ids_for_run(run_id: str) -> list[str]:
    """The REVIEW_REQUIRED samples registered for `run_id`, in run order."""

    with _cases_lock:
        return [sample_id for (rid, sample_id) in _cases if rid == run_id]


def image_path(run_id: str, sample_id: str, kind: str) -> Path | None:
    """The golden/defect image of a registered case, only if it exists on disk. The path comes from
    the server-side registry, never from the request, so this can't be pointed at another file."""

    with _cases_lock:
        case = _cases.get((run_id, sample_id))
    raw = case.get("golden_image_path" if kind == "golden" else "defect_image_path") if case else None
    if kind not in {"golden", "defect"} or not raw:
        return None
    path = Path(str(raw))
    return path if path.is_file() else None


async def list_cases(session: AsyncSession, *, run_id: str) -> list[WorkflowReviewCaseOut]:
    """Every registered review case of a run with its Agent 2 review (once one ran) and operator
    decision (once one was saved) - what the Work tab's Review Console renders."""

    await ensure_run_loaded(run_id)
    decisions = {d.sample_id: d for d in await list_decisions(session, run_id=run_id)}
    cases: list[WorkflowReviewCaseOut] = []
    for sample_id in sample_ids_for_run(run_id):
        with _cases_lock:
            case = _cases.get((run_id, sample_id))
            review = _reviews.get((run_id, sample_id))
        if case is None:
            continue
        cases.append(
            WorkflowReviewCaseOut(
                run_id=run_id,
                sample_id=sample_id,
                board_id=str(case.get("board_id") or ""),
                component_ref=str(case.get("component_ref") or ""),
                feature_type=str(case.get("feature_type") or ""),
                agent1_verdict=normalize_defect(case.get("preliminary_defect")),
                agent1_confidence=float(case.get("confidence") or 0.0),
                has_golden_image=image_path(run_id, sample_id, "golden") is not None,
                has_defect_image=image_path(run_id, sample_id, "defect") is not None,
                review=review,
                decision=decisions.get(sample_id),
            )
        )
    return cases


def _to_out(row: WorkflowReviewDecision) -> WorkflowReviewDecisionOut:
    return WorkflowReviewDecisionOut(
        run_id=row.run_id,
        sample_id=row.sample_id,
        selected_source=row.selected_source,
        final_result=row.final_result,
        machine_result=row.machine_result,
        ai_result=row.ai_result,
        operator_notes=row.operator_notes,
        decided_by_user_id=row.decided_by_user_id,
    )


def _stored_decision_out(
    run_id: str, sample_id: str, decision: dict[str, Any]
) -> WorkflowReviewDecisionOut:
    return WorkflowReviewDecisionOut(
        run_id=run_id,
        sample_id=sample_id,
        selected_source=str(decision.get("selected_source") or ""),
        final_result=str(decision.get("final_result") or ""),
        machine_result=decision.get("machine_result"),
        ai_result=decision.get("ai_result"),
        operator_notes=decision.get("operator_notes"),
        decided_by_user_id=str(decision.get("decided_by_user_id") or ""),
    )


async def save_decision(
    session: AsyncSession, *, request: WorkflowReviewDecisionRequest, user: User
) -> WorkflowReviewDecisionOut:
    """Records the operator's decision for (run_id, sample_id), replacing an earlier one. It goes
    onto the sample's Qdrant review point; when the run isn't stored there (Qdrant down, or a run
    from before runs were persisted) it is written to Postgres instead so it is never lost."""

    decision = {
        "selected_source": request.selected_source,
        "final_result": request.final_result.strip(),
        "operator_notes": (request.operator_notes or "").strip() or None,
        "machine_result": request.machine_result,
        "ai_result": request.ai_result,
        "ai_diagnosis": request.ai_diagnosis,
        "decided_by_user_id": user.id,
    }
    try:
        record = await run_store.save_decision(request.run_id, request.sample_id, decision)
    except (run_store.StoreUnavailable, KeyError, ValueError) as exc:
        logger.warning(
            "decision for %s/%s not stored in qdrant (%s); using postgres",
            request.run_id,
            request.sample_id,
            exc,
        )
        return await _save_decision_postgres(session, request=request, user=user)
    return _stored_decision_out(request.run_id, request.sample_id, record["human_decision"])


async def _save_decision_postgres(
    session: AsyncSession, *, request: WorkflowReviewDecisionRequest, user: User
) -> WorkflowReviewDecisionOut:
    row = await session.scalar(
        select(WorkflowReviewDecision).where(
            WorkflowReviewDecision.run_id == request.run_id,
            WorkflowReviewDecision.sample_id == request.sample_id,
        )
    )
    if row is None:
        row = WorkflowReviewDecision(run_id=request.run_id, sample_id=request.sample_id)
        session.add(row)
    row.selected_source = request.selected_source
    row.final_result = request.final_result.strip()
    row.machine_result = request.machine_result
    row.ai_result = request.ai_result
    row.ai_diagnosis = request.ai_diagnosis
    row.operator_notes = (request.operator_notes or "").strip() or None
    row.decided_by_user_id = user.id
    await session.commit()
    await session.refresh(row)
    return _to_out(row)


async def list_decisions(session: AsyncSession, *, run_id: str) -> list[WorkflowReviewDecisionOut]:
    """The run's decisions: the Qdrant ones, plus any Postgres ones for samples Qdrant has none for
    (the fallback path above, and decisions saved before runs were persisted)."""

    rows = await session.scalars(
        select(WorkflowReviewDecision)
        .where(WorkflowReviewDecision.run_id == run_id)
        .order_by(WorkflowReviewDecision.decided_at)
    )
    decisions = {row.sample_id: _to_out(row) for row in rows}
    try:
        stored = await run_store.reviews_for_run(run_id)
    except (run_store.StoreUnavailable, KeyError, ValueError):
        stored = []
    for record in sorted(stored, key=lambda r: str(r.get("reviewed_at_utc") or "")):
        if record.get("human_decision"):
            sample_id = str(record["sample_id"])
            decisions[sample_id] = _stored_decision_out(run_id, sample_id, record["human_decision"])
    return list(decisions.values())
