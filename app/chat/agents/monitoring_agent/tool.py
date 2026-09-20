"""monitoring_status is still a placeholder - Admin-only, no real logic yet. Future work: model/
dataset performance, drift detection, and retrain/deploy recommendations (see app/chat/agents/access.py
for the role gating). No graph.py for it deliberately - every other agent's graph.py represents a
real (if small) pipeline, and faking a one-node LangGraph with nothing in it would be less honest
than this bare Tool. A future session adds graph.py once there's real multi-step logic to justify
it.

flag_case_for_retraining is real, if intentionally small: a single DB write queuing a
RetrainingTicket (retraining_tickets.py) for a Case a QA/Admin reviewer believes the model got
wrong. It doesn't get its own graph.py either - one DB write behind a required-reason check
doesn't need a pipeline any more than monitoring_status's absence of one does. Actually retraining
happens on the separate inference server, never here - this only queues the request.
"""

import json
from typing import Any

from app.chat.agents.monitoring_agent.retraining_tickets import create_ticket


class MonitoringAgentTool:
    name = "monitoring_status"
    description = (
        "Reports on deployed model performance, drift, and dataset/retraining recommendations "
        "for engineers. Not yet implemented in this release."
    )

    def __init__(self) -> None:
        self.parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs: Any) -> str:
        return json.dumps(
            {
                "status": "not_implemented",
                "message": (
                    "Monitoring agent is a placeholder in this release - model performance "
                    "metrics, drift detection, and retraining/deployment recommendations aren't "
                    "wired up yet."
                ),
            }
        )


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
            },
            "required": ["case_number", "reason"],
        }

    async def run(self, **kwargs: Any) -> str:
        # Deferred import: monitoring_agent shouldn't have to load adc_inspection_agent's (larger)
        # dependency chain just to exist - only flag_case_for_retraining actually needs case lookup.
        from app.chat.agents.adc_inspection_agent.repository import (
            get_case_by_sequence_number,
            parse_case_number,
        )

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

        ticket = await create_ticket(
            session, case_id=case.id, flagged_by_user_id=kwargs["user_id"], reason=reason
        )
        return json.dumps(
            {"ticket_id": ticket.id, "case_number": case.case_number, "status": ticket.status}
        )
