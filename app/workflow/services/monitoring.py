"""Filing a drift report or retraining tickets for a finished bulk run, straight into the same
app/shared/modelops/ tables chat's monitoring agent and the Models tab already use. Called by
app/workflow/api/orchestrator.py's two monitoring routes - kept out of that file the same way
uploads.py/streaming.py are, per its own "Routes only" docstring.

Two paths live here. The manual forms (file_drift_report's `samples`, flag_samples_for_retraining)
take sample dicts the frontend received in the run's SSE `result` event and sends back verbatim, so
that data is only as trustworthy as the requesting QA/Admin session - acceptable for an internal,
role-gated action, but worth stating plainly. The Drift & Retraining tab uses run_drift_summary and
queue_corrections instead, which read the run the server stored in Qdrant (run_store.py) - the
samples and the operator's decisions - and take only ids from the browser.

A sample dict is one element of the teammate's WorkflowState.inference_results
(app/workflow/src/agent1_orchestrator/agents/orchestrator.py, state/workflow_state.py), in one of
two shapes:
  - success (result.success=True): top-level "feature_classification"/"defect_classification"/
    "routing" keys (routing.service_model is the real inference-service model name; see
    multimodal_inference.py - NOT routing.selected_model, which is just the internal routing key).
  - failure (result.success=False, e.g. FEATURE_CLASSIFICATION_UNCERTAIN): those keys are nested
    one level down, under "details" - and only ever "feature_classification" (stage 2 was never
    reached), so there is no resolvable model_name for a failure-shaped sample.
"""

import logging
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db import User
from app.shared.modelops import drift as drift_repo
from app.shared.modelops import run_drift
from app.shared.modelops import tickets as ticket_repo
from app.workflow.services import run_store
from app.workflow.services.schemas import (
    WorkflowCorrectionOut,
    WorkflowDriftReportOut,
    WorkflowDriftReportRequest,
    WorkflowModelDriftOut,
    WorkflowQueueCorrectionsOut,
    WorkflowQueueCorrectionsRequest,
    WorkflowRetrainingTicketOut,
    WorkflowRetrainingTicketsRequest,
    WorkflowRunDriftOut,
)

logger = logging.getLogger(__name__)


class UnknownRun(Exception):
    """The run is not stored (never saved, or the id is wrong)."""


class StoreUnavailable(Exception):
    """The stored run could not be read, so nothing server-side can be said about it."""


class NotACorrection(Exception):
    """Selected samples the operator did not correct (no decision, or they agreed with Agent 1) -
    carries their sample ids."""

    def __init__(self, sample_ids: list[str]) -> None:
        super().__init__(f"no operator correction recorded for sample(s): {', '.join(sample_ids)}")
        self.sample_ids = sample_ids


class UnresolvableSample(Exception):
    """One or more selected samples are missing a sample_id, or never reached stage-2 routing (so
    there's no real model name to file a ticket against) - carries the offending sample_id(s) (or
    "?" when even that is missing) for the route to report."""

    def __init__(self, sample_ids: list[str]) -> None:
        super().__init__(f"no resolvable model for sample(s): {', '.join(sample_ids)}")
        self.sample_ids = sample_ids


def _is_resolvable(sample: dict[str, Any]) -> bool:
    return bool(sample.get("sample_id")) and _model_name(sample) is not None


def _feature_classification(sample: dict[str, Any]) -> dict[str, Any] | None:
    direct = sample.get("feature_classification")
    if isinstance(direct, dict):
        return direct
    details = sample.get("details")
    nested = details.get("feature_classification") if isinstance(details, dict) else None
    return nested if isinstance(nested, dict) else None


def _model_name(sample: dict[str, Any]) -> str | None:
    """The real inference-service model name (e.g. "pcb_body_defect") - only known once a sample
    reached stage-2 routing. None for a sample that stopped at stage 1."""

    routing = sample.get("routing")
    name = routing.get("service_model") if isinstance(routing, dict) else None
    return name if isinstance(name, str) and name else None


def _model_version(sample: dict[str, Any]) -> str | None:
    """Prefers the defect (stage-2) model's version; falls back to the feature (stage-1) model's
    when stage 2 was never reached."""

    defect = sample.get("defect_classification")
    if isinstance(defect, dict) and isinstance(defect.get("model_version"), str):
        return defect["model_version"] or None
    feature = _feature_classification(sample)
    version = feature.get("model_version") if feature else None
    return version if isinstance(version, str) and version else None


def _observed_label(sample: dict[str, Any]) -> str | None:
    defect = sample.get("defect_classification")
    prediction = defect.get("prediction") if isinstance(defect, dict) else None
    return str(prediction) if prediction else None


async def _stored_run(run_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The run's sample points and review points from Qdrant."""

    try:
        return await run_store.sample_points(run_id), await run_store.reviews_for_run(run_id)
    except KeyError as exc:
        raise UnknownRun(run_id) from exc
    except run_store.StoreUnavailable as exc:
        raise StoreUnavailable(str(exc)) from exc


async def run_drift_summary(session: AsyncSession, *, run_id: str) -> WorkflowRunDriftOut:
    """The Drift & Retraining tab's numbers for a run, computed from what Qdrant stored (the
    samples and the operator's decisions), not from anything the browser sends. Fail-open: with
    the store unreachable the tab gets an empty summary and a message, not an error."""

    try:
        points, reviews = await _stored_run(run_id)
    except StoreUnavailable:
        return WorkflowRunDriftOut(
            run_id=run_id,
            available=False,
            message="The stored run could not be read right now - try again shortly.",
            totals={"samples": 0, "review_required": 0, "decided": 0, "corrected": 0},
            models=[],
            corrections=[],
        )
    summary = run_drift.summarize_run(points, reviews)
    queued = await ticket_repo.sample_refs_for_run(session, run_id)
    return WorkflowRunDriftOut(
        run_id=run_id,
        available=True,
        totals=summary["totals"],
        models=[WorkflowModelDriftOut(**m) for m in summary["models"]],
        corrections=[
            WorkflowCorrectionOut(**c, queued=c["sample_id"] in queued)
            for c in summary["corrections"]
        ],
    )


def _correction_reason(run_id: str, correction: dict[str, Any]) -> str:
    reason = (
        f"Operator corrected Agent 1 in run {run_id[:8]}: {correction['agent1_label']} -> "
        f"{correction['final_result']} (decision: {correction['selected_source']})"
    )
    notes = correction.get("operator_notes")
    return f"{reason}. Notes: {notes}" if notes else reason


async def queue_corrections(
    session: AsyncSession, *, request: WorkflowQueueCorrectionsRequest, user: User
) -> WorkflowQueueCorrectionsOut:
    """Files a retraining ticket for each selected sample the operator corrected, built from the
    stored sample and decision (never from the browser). All-or-nothing on what is selected: a
    sample with no correction, or no model recorded, rejects the whole request. A sample already
    queued for this run is skipped, so asking twice files nothing twice."""

    points, reviews = await _stored_run(request.run_id)
    point_by_id = {str(p.get("sample_id")): p for p in points}
    review_by_id = {str(r.get("sample_id")): r for r in reviews}

    corrections: dict[str, dict[str, Any]] = {}
    not_corrections: list[str] = []
    for sample_id in dict.fromkeys(request.sample_ids):
        point = point_by_id.get(sample_id)
        correction = run_drift.correction_of(point, review_by_id.get(sample_id)) if point else None
        if correction is None:
            not_corrections.append(sample_id)
        else:
            corrections[sample_id] = correction
    if not_corrections:
        raise NotACorrection(not_corrections)
    unresolvable = [sid for sid, c in corrections.items() if not c["queueable"]]
    if unresolvable:
        raise UnresolvableSample(unresolvable)

    queued = await ticket_repo.sample_refs_for_run(session, request.run_id)
    created: list[WorkflowRetrainingTicketOut] = []
    already = [sid for sid in corrections if sid in queued]
    for sample_id, correction in corrections.items():
        if sample_id in queued:
            continue
        try:
            ticket = await ticket_repo.create_ticket(
                session,
                sample_ref=sample_id,
                run_id=request.run_id,
                flagged_by_user_id=user.id,
                reason=_correction_reason(request.run_id, correction),
                model_name=correction["model_name"],
                model_version=correction["model_version"],
                observed_label=correction["agent1_label"],
                correct_label=correction["final_result"],
            )
        except IntegrityError:
            # A concurrent request queued the same sample between the check and the insert.
            await session.rollback()
            already.append(sample_id)
            continue
        created.append(
            WorkflowRetrainingTicketOut(
                id=ticket.id,
                sample_ref=ticket.sample_ref,
                model_name=ticket.model_name,
                status=ticket.status,
            )
        )
    return WorkflowQueueCorrectionsOut(created=created, already_queued=already)


async def file_drift_report(
    session: AsyncSession, *, request: WorkflowDriftReportRequest, user: User
) -> WorkflowDriftReportOut:
    model_version = request.model_version
    if model_version is None:
        model_version = next(
            (v for s in request.samples if (v := _model_version(s)) is not None), None
        )
    sample_ids = [s.get("sample_id") for s in request.samples if s.get("sample_id")]

    stats: dict[str, Any] = {"source": "workflow", "sample_ids": sample_ids}
    if request.run_id:
        stats["run_id"] = request.run_id
        stats.update(await _run_snapshot(request.run_id, request.model_name))

    report = await drift_repo.create_drift_report(
        session,
        model_name=request.model_name,
        reported_by_user_id=user.id,
        description=request.description,
        model_version=model_version,
        stats=stats,
    )
    return WorkflowDriftReportOut(
        id=report.id,
        model_name=report.model_name,
        model_version=report.model_version,
        status=report.status,
    )


async def _run_snapshot(run_id: str, model_name: str) -> dict[str, Any]:
    """The server's own numbers for `model_name` in the run, frozen into the report (a snapshot, like
    chat's, so the report still says what was true when it was made). Empty when the run can't be
    read - the report is still filed, with the browser's evidence only."""

    try:
        points, reviews = await _stored_run(run_id)
    except (UnknownRun, StoreUnavailable) as exc:
        logger.warning("drift report for run %s filed without a snapshot: %s", run_id, exc)
        return {}
    summary = run_drift.summarize_run(points, reviews)
    model = next((m for m in summary["models"] if m["model_name"] == model_name), None)
    return {"run_totals": summary["totals"], "model": model}


async def flag_samples_for_retraining(
    session: AsyncSession, *, request: WorkflowRetrainingTicketsRequest, user: User
) -> list[WorkflowRetrainingTicketOut]:
    """All-or-nothing: if any selected sample has no resolvable model name, nothing is written -
    raises UnresolvableSample rather than filing some tickets and silently dropping the rest."""

    unresolvable = [
        item.sample.get("sample_id", "?")
        for item in request.tickets
        if not _is_resolvable(item.sample)
    ]
    if unresolvable:
        raise UnresolvableSample(unresolvable)

    already_flagged: set[str] = set()
    if request.run_id:
        already_flagged = await ticket_repo.sample_refs_for_run(session, request.run_id)

    results = []
    for item in request.tickets:
        if request.run_id and item.sample.get("sample_id") in already_flagged:
            continue  # this run's sample already has a ticket - never flag it twice
        ticket = await ticket_repo.create_ticket(
            session,
            sample_ref=item.sample.get("sample_id"),
            run_id=request.run_id,
            flagged_by_user_id=user.id,
            reason=item.reason,
            model_name=_model_name(item.sample),
            model_version=_model_version(item.sample),
            observed_label=_observed_label(item.sample),
            correct_label=item.correct_label,
        )
        results.append(
            WorkflowRetrainingTicketOut(
                id=ticket.id,
                sample_ref=ticket.sample_ref,
                model_name=ticket.model_name,
                status=ticket.status,
            )
        )
    return results
