"""Filing a drift report or retraining tickets for a finished bulk run, straight into the same
app/shared/modelops/ tables chat's monitoring agent and the Models tab already use. Called by
app/workflow/api/orchestrator.py's two monitoring routes - kept out of that file the same way
uploads.py/streaming.py are, per its own "Routes only" docstring.

There is no run history on the server (orchestrator runs are streamed once and never persisted -
see streaming.py), so every sample dict here is exactly what the frontend already received in the
run's SSE `result` event and is sending back verbatim. That makes this data only as trustworthy as
the requesting QA/Admin session, not a server-verified record - acceptable for an internal,
role-gated action, but worth stating plainly rather than leaving implicit.

A sample dict is one element of orchestrator.py's `state.inference_results`, in one of two shapes:
  - success (result.success=True): top-level "feature_classification"/"defect_classification"/
    "routing" keys (routing.service_model is the real inference-service model name; see
    multimodal_inference.py - NOT routing.selected_model, which is just the internal routing key).
  - failure (result.success=False, e.g. FEATURE_CLASSIFICATION_UNCERTAIN): those keys are nested
    one level down, under "details" - and only ever "feature_classification" (stage 2 was never
    reached), so there is no resolvable model_name for a failure-shaped sample.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db import User
from app.shared.modelops import drift as drift_repo
from app.shared.modelops import tickets as ticket_repo
from app.workflow.services.schemas import (
    WorkflowDriftReportOut,
    WorkflowDriftReportRequest,
    WorkflowRetrainingTicketOut,
    WorkflowRetrainingTicketsRequest,
)


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


async def file_drift_report(
    session: AsyncSession, *, request: WorkflowDriftReportRequest, user: User
) -> WorkflowDriftReportOut:
    model_version = request.model_version
    if model_version is None:
        model_version = next(
            (v for s in request.samples if (v := _model_version(s)) is not None), None
        )
    sample_ids = [s.get("sample_id") for s in request.samples if s.get("sample_id")]

    report = await drift_repo.create_drift_report(
        session,
        model_name=request.model_name,
        reported_by_user_id=user.id,
        description=request.description,
        model_version=model_version,
        stats={"source": "workflow", "sample_ids": sample_ids},
    )
    return WorkflowDriftReportOut(
        id=report.id,
        model_name=report.model_name,
        model_version=report.model_version,
        status=report.status,
    )


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

    results = []
    for item in request.tickets:
        ticket = await ticket_repo.create_ticket(
            session,
            sample_ref=item.sample.get("sample_id"),
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
