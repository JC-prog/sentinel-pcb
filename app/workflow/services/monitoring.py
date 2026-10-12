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

from app.shared import inference
from app.shared.db import User
from app.shared.db.models import DriftReportStatus, RetrainingTicketStatus
from app.shared.modelops import drift as drift_repo
from app.shared.modelops import jobs as job_repo
from app.shared.modelops import run_drift
from app.shared.modelops import tickets as ticket_repo
from app.shared.modelops import versions as version_repo
from app.workflow.services import run_store
from app.workflow.services.schemas import (
    WorkflowCorrectionOut,
    WorkflowDecisionStatsOut,
    WorkflowDriftReportOut,
    WorkflowDriftReportRequest,
    WorkflowModelDriftOut,
    WorkflowQueueCorrectionsOut,
    WorkflowQueueCorrectionsRequest,
    WorkflowRetrainingPlanOut,
    WorkflowRetrainingPlanRequest,
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
        super().__init__(
            "nothing to add for sample(s) "
            f"{', '.join(sample_ids)}: not reviewed yet, or the model already predicted the final "
            "decision"
        )
        self.sample_ids = sample_ids


class PlanRefused(Exception):
    """A retraining plan that cannot be drafted, with the reason the operator should hear."""


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


async def _open_tickets_by_model(session: AsyncSession, run_id: str | None = None) -> dict[str, int]:
    counts = await ticket_repo.count_tickets(
        session, status=RetrainingTicketStatus.OPEN, run_id=run_id
    )
    return {model: n for model, n in counts.items() if model}


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
            totals=run_drift.summarize_run([], [])["totals"],
            models=[],
            corrections=[],
            decisions=WorkflowDecisionStatsOut(**run_drift.summarize_run([], [])["decisions"]),
            open_tickets=await _open_tickets_by_model(session),
            run_open_tickets=await _open_tickets_by_model(session, run_id),
        )
    summary = run_drift.summarize_run(points, reviews)
    tickets = await ticket_repo.sample_refs_for_run(session, run_id)
    return WorkflowRunDriftOut(
        run_id=run_id,
        available=True,
        totals=summary["totals"],
        drift_rate=summary["drift_rate"],
        models=[WorkflowModelDriftOut(**m) for m in summary["models"]],
        corrections=[
            WorkflowCorrectionOut(
                **c,
                queued=c["sample_id"] in tickets,
                ticket_status=tickets.get(c["sample_id"]),
            )
            for c in summary["corrections"]
        ],
        decisions=WorkflowDecisionStatsOut(**summary["decisions"]),
        open_tickets=await _open_tickets_by_model(session),
        run_open_tickets=await _open_tickets_by_model(session, run_id),
    )


async def draft_retraining_plan(
    session: AsyncSession, *, request: WorkflowRetrainingPlanRequest, user: User
) -> WorkflowRetrainingPlanOut:
    """Turns every open retraining ticket for a model - queued corrections and chat relabels alike -
    into a retraining job awaiting an Admin's approval (the same job chat's draft_retraining_plan
    makes). Nothing is retrained or promoted here: approving is an Admin action in the Models tab."""

    model_name = request.model_name.strip()
    open_reports = await drift_repo.list_drift_reports(
        session, model_name=model_name, status=DriftReportStatus.OPEN
    )
    rationale = (request.rationale or "").strip() or (
        f"Retrain {model_name} on samples the operator corrected in the Review Console."
        + (f" {len(open_reports)} open drift report(s) point at it." if open_reports else "")
    )
    async def draft() -> Any:
        return await job_repo.draft_job(
            session,
            model_name=model_name,
            created_by_user_id=user.id,
            rationale=rationale,
            drift_report_ids=[r.id for r in open_reports],
        )

    try:
        try:
            job = await draft()
        except job_repo.BaseVersionUnknown:
            # No live version recorded yet (nobody has opened the Models tab on this database) and
            # the tickets name none - e.g. samples routed by their region guess. Ask the inference
            # service what it is serving, the same sync the Models tab does, and try once more.
            if not await _sync_model_versions(session):
                raise
            job = await draft()
    except job_repo.NothingToRetrain as exc:
        raise PlanRefused(
            f"no open retraining tickets for {model_name} - add some samples to the training data "
            "first"
        ) from exc
    except job_repo.BaseVersionUnknown as exc:
        raise PlanRefused(
            f"cannot tell which version of {model_name} to retrain from - the inference service "
            "could not be reached to ask; start it and try again"
        ) from exc
    return WorkflowRetrainingPlanOut(
        job_id=job.id,
        model_name=job.model_name,
        status=job.status,
        base_version=job.base_version,
        sample_count=len(job.samples),
        drift_reports_linked=len(open_reports),
    )


async def _sync_model_versions(session: AsyncSession) -> bool:
    """Records the versions the inference service is serving (what the Models tab does when it
    opens). False when the service can't be reached."""

    try:
        infos = await inference.list_models()
    except (inference.InferenceNotConfigured, inference.InferenceError) as exc:
        logger.warning("could not sync model versions for a retraining plan: %s", exc)
        return False
    await version_repo.sync_versions(session, infos)
    return True


def _correction_reason(run_id: str, correction: dict[str, Any]) -> str:
    guess = correction.get("region_guess") or {}
    if correction.get("agent1_label") is None:
        confidence = guess.get("confidence")
        said = (
            f"the model couldn't decide (region guess {guess.get('label')}"
            + (f", {round(confidence * 100)}%" if isinstance(confidence, int | float) else "")
            + ")"
        )
    else:
        said = f"the model said {correction['agent1_label']}"
    reason = (
        f"Labelled by the operator in run {run_id[:8]}: {said}; the correct label is "
        f"{correction['final_result']} ({correction['selected_source']})"
    )
    region = correction.get("region_result")
    if region:
        reason += f"; the operator set the region to {region}"
    notes = correction.get("operator_notes")
    return f"{reason}. Notes: {notes}" if notes else reason


async def queue_corrections(
    session: AsyncSession, *, request: WorkflowQueueCorrectionsRequest, user: User
) -> WorkflowQueueCorrectionsOut:
    """Adds each selected sample to the training data - one retraining ticket with the operator's
    label, for the defect model it belongs to (the one that classified it, or the one its region
    guess routes to) - built from the stored sample and decision, never from the browser.
    All-or-nothing on what is selected: a sample the model got right (or not reviewed), or one with no
    model to route to, rejects the whole request. A sample already added for this run is skipped, so
    asking twice adds nothing twice."""

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

    already_flagged: dict[str, str] = {}
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
