"""The sample agent's lookups: what the Work tab stored about a dataset sample, shaped for the model.

A sample is one row of a bulk run's dataset (`S000001`); `sample_id` is only unique within a run, so
a lookup without a run id answers from the newest run that has it and says which others do. Local
image paths are left out - they are server paths the user cannot use and the model should not repeat.
Everything is read from Qdrant through app/chat/services/run_samples.py; nothing is written.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.sample_agent.errors import SampleRefused
from app.chat.services import run_samples
from app.chat.services.run_samples import RunStoreUnavailable, StoredRun
from app.shared.config.langfuse import traced
from app.shared.modelops import run_drift
from app.shared.modelops import tickets as ticket_repo

MAX_CASES = 50

_SAMPLE_FIELDS = (
    "board",
    "package",
    "component",
    "source_feature",
    "machine_defect",
    "timestamp",
    "xml_feature_status",
    "preparation_status",
)


def _confidence(value: Any) -> float | None:
    return round(float(value), 4) if isinstance(value, int | float) else None


def _stage(result: dict[str, Any], key: str) -> dict[str, Any] | None:
    """A classifier's answer - top level for a completed sample, nested under "details" for one
    that stopped at the first stage."""

    stage = result.get(key)
    if not isinstance(stage, dict):
        details = result.get("details")
        stage = details.get(key) if isinstance(details, dict) else None
    if not isinstance(stage, dict) or not stage.get("prediction"):
        return None
    return {"label": stage["prediction"], "confidence": _confidence(stage.get("confidence"))}


def _agent1(inference: dict[str, Any] | None, final_decision: str | None) -> dict[str, Any]:
    result = inference or {}
    return {
        "final_decision": final_decision,
        "status": result.get("status"),
        "feature": _stage(result, "feature_classification"),
        "defect": _stage(result, "defect_classification"),
        "errors": [str(e) for e in result.get("errors") or []],
    }


def _failed_inspections(sample: dict[str, Any]) -> list[dict[str, Any]]:
    inspections = sample.get("failed_inspections")
    if not isinstance(inspections, dict):
        return []
    summary: list[dict[str, Any]] = []
    for name, detail in inspections.items():
        if not isinstance(detail, dict):
            continue
        measurements = detail.get("measurements")
        if not isinstance(measurements, dict):
            measurements = {}
        failed = [str(c) for c in detail.get("failed_criteria") or []]
        summary.append(
            {
                "inspection": name,
                "status": detail.get("status"),
                "failed_criteria": failed,
                "measurements": {
                    criterion: {
                        "value": m.get("Value"),
                        "minimum": m.get("Minimum"),
                        "maximum": m.get("Maximum"),
                        "target": m.get("Target"),
                    }
                    for criterion, m in measurements.items()
                    if criterion in failed and isinstance(m, dict)
                },
            }
        )
    return summary


def _agent2(review: dict[str, Any] | None) -> dict[str, Any] | None:
    result = review.get("result") if review else None
    if not isinstance(result, dict) or not isinstance(result.get("output"), dict):
        return None
    output = result["output"]
    return {
        "review_status": result.get("review_status"),
        "predicted_defect": output.get("predicted_defect"),
        "confidence": _confidence(output.get("final_confidence")),
        "diagnosis": output.get("diagnosis"),
        "contradiction_detected": output.get("contradiction_detected"),
        "self_check_passed": output.get("self_check_passed"),
        "ipc_citations": [str(c) for c in output.get("ipc_citations") or []],
        "errors": [str(e) for e in output.get("errors") or []],
    }


def _human_decision(review: dict[str, Any] | None) -> dict[str, Any] | None:
    decision = review.get("human_decision") if review else None
    if not review or not isinstance(decision, dict):
        return None
    return {
        "selected_source": decision.get("selected_source"),
        "final_result": decision.get("final_result"),
        "operator_notes": decision.get("operator_notes"),
        "decided_at": review.get("reviewed_at_utc"),
    }


def _review_state(final_decision: str | None, agent2: Any, decision: Any) -> str:
    if final_decision != run_samples.REVIEW_REQUIRED:
        return "not_flagged"
    if decision is not None:
        return "decided"
    return "awaiting_operator" if agent2 is not None else "awaiting_agent2"


@traced("sample-lookup")
async def describe_sample(sample_id: str, run_id: str | None) -> dict[str, Any]:
    try:
        found = await run_samples.find_samples(sample_id, run_id)
        if not found:
            raise SampleRefused(await _not_found(sample_id, run_id))
        chosen = found[0]
        review = await run_samples.get_review(chosen.run.run_id, sample_id)
    except RunStoreUnavailable as exc:
        raise SampleRefused(exc.message) from exc

    sample = chosen.point.get("sample") or {}
    final_decision = chosen.point.get("final_decision")
    agent2, decision = _agent2(review), _human_decision(review)
    described: dict[str, Any] = {
        "sample_id": sample_id,
        "run_id": chosen.run.run_id,
        "run_saved_at": chosen.run.saved_at_utc,
        **{field: sample.get(field) for field in _SAMPLE_FIELDS},
        "failed_inspections": _failed_inspections(sample),
        "agent1": _agent1(chosen.point.get("inference"), final_decision),
        "agent2": agent2,
        "human_decision": decision,
        "review_state": _review_state(final_decision, agent2, decision),
    }
    others = [{"run_id": s.run.run_id, "saved_at_utc": s.run.saved_at_utc} for s in found[1:]]
    if others:
        described["other_runs"] = others
    return described


async def _not_found(sample_id: str, run_id: str | None) -> str:
    latest = await run_samples.latest_run()
    if latest is None:
        return "no Work-tab runs are stored yet - run a dataset in the Work tab first."
    where = f" in run {run_id}" if run_id else ""
    return (
        f"no stored sample {sample_id!r}{where}. Sample ids look like S000001 (the dataset's "
        f"SampleID); the latest stored run is {latest.run_id}."
    )


@traced("sample-review-cases")
async def review_cases(run_id: str | None, limit: int) -> dict[str, Any]:
    limit = max(1, min(limit, MAX_CASES))
    try:
        run = await _resolve_run(run_id)
        points = await run_samples.review_case_points(run.run_id)
        reviews = {str(r["sample_id"]): r for r in await run_samples.reviews_for_run(run.run_id)}
    except RunStoreUnavailable as exc:
        raise SampleRefused(exc.message) from exc

    cases = []
    for point in sorted(points, key=lambda p: str(p.get("sample_id"))):
        sample_id = str(point.get("sample_id"))
        sample = point.get("sample") or {}
        agent1 = _agent1(point.get("inference"), point.get("final_decision"))
        agent2, decision = _agent2(reviews.get(sample_id)), _human_decision(reviews.get(sample_id))
        defect = agent1["defect"] or agent1["feature"]
        cases.append(
            {
                "sample_id": sample_id,
                "board": sample.get("board"),
                "component": sample.get("component"),
                "agent1": defect["label"] if defect else sample.get("machine_defect"),
                "agent2": agent2["predicted_defect"] if agent2 else None,
                "review_state": _review_state(point.get("final_decision"), agent2, decision),
                "final_result": decision["final_result"] if decision else None,
            }
        )
    return {
        "run_id": run.run_id,
        "run_saved_at": run.saved_at_utc,
        "total": len(cases),
        "shown": min(len(cases), limit),
        "cases": cases[:limit],
    }


@traced("sample-run-drift")
async def run_drift_overview(session: AsyncSession, run_id: str | None) -> dict[str, Any]:
    """Drift numbers and the operator's corrections for a Work-tab run, from the run stored in
    Qdrant (app/shared/modelops/run_drift.py - the same numbers the Review Console's Drift &
    Retraining tab shows), with which corrections already have a retraining ticket."""

    try:
        run = await _resolve_run(run_id)
        points = await run_samples.sample_points(run.run_id)
        reviews = await run_samples.reviews_for_run(run.run_id)
    except RunStoreUnavailable as exc:
        raise SampleRefused(exc.message) from exc

    summary = run_drift.summarize_run(points, reviews)
    queued = await ticket_repo.sample_refs_for_run(session, run.run_id)
    corrections = [
        {**c, "queued": c["sample_id"] in queued} for c in summary["corrections"]
    ]
    waiting = [c for c in corrections if not c["queued"] and c["queueable"]]
    models_with_tickets = sorted({c["model_name"] for c in corrections if c["queued"]})
    return {
        "run_id": run.run_id,
        "run_saved_at": run.saved_at_utc,
        "totals": summary["totals"],
        "models": summary["models"],
        "corrections": corrections[:MAX_CASES],
        "corrections_shown": min(len(corrections), MAX_CASES),
        "corrections_total": len(corrections),
        "corrections_not_queued": len(waiting),
        "instruction": _drift_instruction(len(waiting), models_with_tickets),
    }


def _drift_instruction(waiting: int, models_with_tickets: list[str | None]) -> str:
    parts = ["Report the numbers as given; they cover only this run's samples."]
    if waiting:
        parts.append(
            f"{waiting} operator correction(s) are not queued for retraining yet - only the "
            "operator can queue them, in the Review Console's Drift & Retraining tab; you cannot."
        )
    if models_with_tickets:
        names = ", ".join(str(m) for m in models_with_tickets)
        parts.append(
            f"Retraining tickets exist for {names}: if the user wants a plan, offer "
            "draft_retraining_plan for that model - an Admin still approves it in the Models tab."
        )
    return " ".join(parts)


async def _resolve_run(run_id: str | None) -> StoredRun:
    runs = await run_samples.ready_runs()
    if not runs:
        raise SampleRefused("no Work-tab runs are stored yet - run a dataset in the Work tab first.")
    if run_id is None:
        return runs[0]
    for run in runs:
        if run.run_id == run_id:
            return run
    raise SampleRefused(f"no stored run {run_id!r}; the latest stored run is {runs[0].run_id}.")
