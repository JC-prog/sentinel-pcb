"""The monitoring agent's tools: the model-health side of the chat. A reviewer flags a Case the
model got wrong (flag_case_for_retraining), reports that a model looks like it has drifted
(report_model_drift, get_drift_summary), and asks for a retraining plan (draft_retraining_plan);
monitoring_status is the Admin's read-only overview.

Nothing here retrains anything or changes which model is live. The end of the road for a request
is a drafted RetrainingJob in PENDING_APPROVAL - an Admin approves it (and later promotes the
result) in the Models tab, which is the only place those actions exist. Tickets, drift reports,
jobs and the version registry are shared tables (app/shared/modelops/); the numbers behind "has it
drifted" are computed from Cases in app/chat/services/drift.py. None of these tools needs LLM
reasoning of its own - they are structured reads and writes the chat model orchestrates.
"""

import json
from typing import Any

from app.chat.services.cases import get_case_by_sequence_number, parse_case_number
from app.chat.services.drift import drift_summary
from app.shared.db.models import (
    DriftReportStatus,
    ModelVersionStatus,
    RetrainingJobStatus,
    RetrainingTicketStatus,
)
from app.shared.modelops import drift as drift_repo
from app.shared.modelops import jobs as job_repo
from app.shared.modelops import tickets as ticket_repo
from app.shared.modelops import versions as version_repo

_DEFAULT_WINDOW_DAYS = 7
_MAX_WINDOW_DAYS = 90

_MODEL_PARAM = {
    "type": "string",
    "description": (
        "The model's name, e.g. pcb_region, pcb_body_defect, pcb_lead_defect or pcb_text_defect."
    ),
}
_WINDOW_PARAM = {
    "type": "integer",
    "description": (
        "Days in the recent window; it is compared against the same number of days before it."
    ),
    "default": _DEFAULT_WINDOW_DAYS,
}


def _window_days(kwargs: dict[str, Any]) -> int:
    return max(1, min(int(kwargs.get("window_days") or _DEFAULT_WINDOW_DAYS), _MAX_WINDOW_DAYS))


class FlagCaseForRetrainingTool:
    name = "flag_case_for_retraining"
    description = (
        "Flags a Case as a bad model call, queuing a retraining ticket for engineering. Requires "
        "an explanation of why the model's verdict looks wrong - retraining itself happens "
        "separately, this only queues the request."
    )

    def __init__(self) -> None:
        self.parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                "case_number": {
                    "type": "string",
                    "description": 'The case number, e.g. "CASE-000123".',
                },
                "reason": {
                    "type": "string",
                    "description": "Why this case's model output looks wrong. Required.",
                },
                "correct_label": {
                    "type": "string",
                    "description": "What the defect label should have been, if the reviewer knows.",
                },
            },
            "required": ["case_number", "reason"],
        }

    async def run(self, **kwargs: Any) -> str:
        session = kwargs["session"]
        reason = (kwargs.get("reason") or "").strip()
        if not reason:
            return json.dumps({"error": "reason is required"})

        case_number = kwargs.get("case_number") or ""
        sequence_number = parse_case_number(case_number)
        if sequence_number is None:
            return json.dumps({"error": f"invalid case number: {case_number!r}"})

        case = await get_case_by_sequence_number(session, sequence_number)
        if case is None:
            return json.dumps({"error": "case not found"})

        # The defect model's call is what a reviewer disputes; record which weights made it and
        # what they said, since the Case alone can't tell us once that model has been retrained.
        ticket = await ticket_repo.create_ticket(
            session,
            case_id=case.id,
            case_number=case.case_number,
            flagged_by_user_id=kwargs["user_id"],
            reason=reason,
            model_name=case.defect_model,
            model_version=case.defect_model_version,
            observed_label=case.defect_label,
            correct_label=(kwargs.get("correct_label") or "").strip() or None,
        )
        return json.dumps(
            {
                "ticket_id": ticket.id,
                "case_number": case.case_number,
                "status": ticket.status,
                "model": ticket.model_name,
                "model_version": ticket.model_version,
            }
        )


class GetDriftSummaryTool:
    name = "get_drift_summary"
    description = (
        "Shows whether a model looks like it is drifting: its recent review rate, the share of "
        "its calls reviewers overrode as false positives, and its classifier confidence, "
        "compared with the period before and broken down by model version - with plain-language "
        "signals for whatever moved. Read-only; use it before reporting drift or planning a "
        "retrain."
    )

    def __init__(self) -> None:
        self.parameters: dict[str, Any] = {
            "type": "object",
            "properties": {"model": _MODEL_PARAM, "window_days": _WINDOW_PARAM},
            "required": ["model"],
        }

    async def run(self, **kwargs: Any) -> str:
        session = kwargs["session"]
        model = (kwargs.get("model") or "").strip()
        if not model:
            return json.dumps({"error": "model is required"})

        summary = await drift_summary(session, model, days=_window_days(kwargs))
        live = await version_repo.get_live_version(session, model)
        open_reports = await drift_repo.count_reports_by_model(
            session, status=DriftReportStatus.OPEN
        )
        open_tickets = await ticket_repo.count_tickets(
            session, status=RetrainingTicketStatus.OPEN
        )
        return json.dumps(
            {
                **summary,
                "live_version": live.version if live else None,
                "open_drift_reports": open_reports.get(model, 0),
                "open_retraining_tickets": open_tickets.get(model, 0),
            }
        )


class ReportModelDriftTool:
    name = "report_model_drift"
    description = (
        "Files a drift report: the user believes a model has drifted (its verdicts have become "
        "less reliable). Records their description, the cases they point to as evidence, and a "
        "snapshot of the model's current drift numbers, so the Models tab and engineers can see "
        "it. This only records the report - it does not retrain anything."
    )

    def __init__(self) -> None:
        self.parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                "model": _MODEL_PARAM,
                "description": {
                    "type": "string",
                    "description": "What the user is seeing that suggests the model drifted.",
                },
                "case_numbers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Case numbers that show the problem, if the user named any.",
                },
                "window_days": _WINDOW_PARAM,
            },
            "required": ["model", "description"],
        }

    async def run(self, **kwargs: Any) -> str:
        session = kwargs["session"]
        model = (kwargs.get("model") or "").strip()
        description = (kwargs.get("description") or "").strip()
        if not model or not description:
            return json.dumps({"error": "both model and description are required"})

        case_ids: list[str] = []
        case_numbers: list[str] = []
        unknown: list[str] = []
        for number in kwargs.get("case_numbers") or []:
            sequence_number = parse_case_number(str(number))
            case = (
                await get_case_by_sequence_number(session, sequence_number)
                if sequence_number is not None
                else None
            )
            if case is None:
                unknown.append(str(number))
            else:
                case_ids.append(case.id)
                case_numbers.append(case.case_number)

        summary = await drift_summary(session, model, days=_window_days(kwargs))
        live = await version_repo.get_live_version(session, model)
        report = await drift_repo.create_drift_report(
            session,
            model_name=model,
            reported_by_user_id=kwargs["user_id"],
            description=description,
            model_version=live.version if live else None,
            case_ids=case_ids,
            case_numbers=case_numbers,
            stats=summary,
        )
        open_reports = await drift_repo.count_reports_by_model(
            session, status=DriftReportStatus.OPEN
        )
        return json.dumps(
            {
                "report_id": report.id,
                "model": model,
                "model_version": report.model_version,
                "evidence_cases": len(case_ids),
                "unknown_case_numbers": unknown,
                "signals": summary["signals"],
                "enough_data": summary["enough_data"],
                "open_drift_reports_for_model": open_reports.get(model, 0),
                "next_step": (
                    "To retrain, flag the cases the model got wrong (flag_case_for_retraining) "
                    "and ask for a retraining plan."
                ),
            }
        )


class DraftRetrainingPlanTool:
    name = "draft_retraining_plan"
    description = (
        "Drafts a retraining plan for a model from every open retraining ticket flagged against "
        "it, linking any open drift reports. The plan is queued as pending approval - an Admin "
        "must approve it in the Models tab before anything is sent for retraining, and "
        "promoting a retrained model is a separate Admin step. Fails if no cases have been "
        "flagged for that model yet."
    )

    def __init__(self) -> None:
        self.parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                "model": _MODEL_PARAM,
                "rationale": {
                    "type": "string",
                    "description": (
                        "Why retraining is warranted, in the user's words. Optional - a summary "
                        "of the flagged cases and drift signals is written if omitted."
                    ),
                },
            },
            "required": ["model"],
        }

    async def run(self, **kwargs: Any) -> str:
        session = kwargs["session"]
        model = (kwargs.get("model") or "").strip()
        if not model:
            return json.dumps({"error": "model is required"})

        open_reports = await drift_repo.list_drift_reports(
            session, model_name=model, status=DriftReportStatus.OPEN
        )
        rationale = (kwargs.get("rationale") or "").strip()
        if not rationale:
            summary = await drift_summary(session, model, days=_DEFAULT_WINDOW_DAYS)
            rationale = _default_rationale(model, len(open_reports), summary["signals"])

        try:
            job = await job_repo.draft_job(
                session,
                model_name=model,
                created_by_user_id=kwargs["user_id"],
                rationale=rationale,
                drift_report_ids=[r.id for r in open_reports],
            )
        except job_repo.NothingToRetrain:
            return json.dumps(
                {
                    "error": (
                        f"no open retraining tickets for {model} - flag the cases it got wrong "
                        "first (flag_case_for_retraining)"
                    )
                }
            )
        except job_repo.BaseVersionUnknown:
            return json.dumps(
                {
                    "error": (
                        f"cannot tell which version of {model} to retrain from - no live version "
                        "is recorded yet. Open the Models tab so it syncs with the inference "
                        "service, then try again."
                    )
                }
            )

        return json.dumps(
            {
                "job_id": job.id,
                "model": model,
                "status": job.status,
                "base_version": job.base_version,
                "flagged_cases": len(job.samples),
                "drift_reports_linked": len(open_reports),
                "next_step": (
                    "An Admin must approve this plan in the Models tab before it is sent for "
                    "retraining."
                ),
            }
        )


def _default_rationale(model: str, drift_reports: int, signals: list[str]) -> str:
    parts = [f"Retrain {model} on cases reviewers flagged as misclassified."]
    if drift_reports:
        parts.append(f"{drift_reports} open drift report(s) point at it.")
    if signals:
        parts.append("Recent indicators: " + "; ".join(signals) + ".")
    return " ".join(parts)


class MonitoringAgentTool:
    name = "monitoring_status"
    description = (
        "Admin overview of model health: which version of each model is live, open drift "
        "reports, open retraining tickets, and the retraining queue. Read-only."
    )

    def __init__(self) -> None:
        self.parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs: Any) -> str:
        session = kwargs["session"]
        live = [
            v
            for v in await version_repo.list_versions(session)
            if v.status == ModelVersionStatus.LIVE
        ]
        queue = await job_repo.list_jobs(
            session,
            statuses=[
                RetrainingJobStatus.PENDING_APPROVAL,
                RetrainingJobStatus.APPROVED,
                RetrainingJobStatus.QUEUED,
                RetrainingJobStatus.RUNNING,
            ],
        )
        payload: dict[str, Any] = {
            "live_models": {v.model_name: v.version for v in live},
            "open_drift_reports": await drift_repo.count_reports_by_model(
                session, status=DriftReportStatus.OPEN
            ),
            "open_retraining_tickets": await ticket_repo.count_tickets(
                session, status=RetrainingTicketStatus.OPEN
            ),
            "retraining_queue": [
                {
                    "job_id": j.id,
                    "model": j.model_name,
                    "status": j.status,
                    "samples": len(j.samples),
                    "progress": j.progress,
                }
                for j in queue
            ],
        }
        if not live:
            payload["note"] = (
                "No model versions are recorded yet - the Models tab syncs them from the "
                "inference service."
            )
        return json.dumps(payload)
