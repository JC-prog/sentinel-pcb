"""Continuous Monitoring & Drift, extended to also cover agent/policy health - not a new,
separate agent (that would be a scope change against the accepted proposal); this is the same
named component from the taxonomy, doing the observability job it was always meant to do plus
the agent-health metrics the original design never specified.

On-demand, computed live from `Workflow`/`AuditEvent` - no scheduler, no new metrics table. This
stack has no cron/Redis, and there's no ground-truth-verified drift signal yet (HUMAN_REVIEW has
no reviewer-action endpoint), so a background job would have nothing new to compute between runs
that a live query doesn't already answer just as well.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEvent, Workflow
from app.policies import thresholds


class Alert(BaseModel):
    metric: str
    value: float
    threshold: float
    severity: Literal["warning", "critical"]
    message: str


class ObservabilityReport(BaseModel):
    generated_at: datetime
    total_workflows: int
    decision_counts: dict[str, int]
    escalation_rate: float
    avg_feature_confidence: float | None
    avg_defect_confidence: float | None
    disagreement_rate: float
    policy_violation_count: int
    alerts: list[Alert]


async def compute_metrics(session: AsyncSession) -> ObservabilityReport:
    total = (await session.execute(select(func.count(Workflow.id)))).scalar_one()

    decision_rows = (
        await session.execute(
            select(Workflow.decision, func.count(Workflow.id))
            .where(Workflow.decision.is_not(None))
            .group_by(Workflow.decision)
        )
    ).all()
    decision_counts = {decision: count for decision, count in decision_rows}

    escalated = decision_counts.get("escalate_to_human", 0)
    decided_total = sum(decision_counts.values())
    escalation_rate = escalated / decided_total if decided_total else 0.0

    avg_feature_confidence = (
        await session.execute(select(func.avg(Workflow.feature_confidence)))
    ).scalar_one()
    avg_defect_confidence = (
        await session.execute(select(func.avg(Workflow.overall_confidence)))
    ).scalar_one()

    disagreement_count = (
        await session.execute(
            select(func.count(Workflow.id)).where(
                Workflow.feature_confidence.is_not(None),
                Workflow.overall_confidence.is_not(None),
                func.abs(Workflow.feature_confidence - Workflow.overall_confidence)
                > thresholds.confidence_disagreement_delta,
            )
        )
    ).scalar_one()
    disagreement_rate = disagreement_count / decided_total if decided_total else 0.0

    # Near-always zero today - the only current proposer ("orchestrator") is allowlisted for
    # everything it proposes. Real once a second proposer (e.g. multi_modal_inference proposing
    # REQUEST_REPREPARATION) is exercised. See docs/AGENT-DESIGN.md.
    policy_violation_count = (
        await session.execute(
            select(func.count(AuditEvent.id)).where(AuditEvent.action == "POLICY_VIOLATION")
        )
    ).scalar_one()

    alerts: list[Alert] = []
    if escalation_rate > thresholds.max_escalation_rate:
        alerts.append(
            Alert(
                metric="escalation_rate",
                value=escalation_rate,
                threshold=thresholds.max_escalation_rate,
                severity="warning",
                message=f"Escalation rate {escalation_rate:.0%} exceeds "
                f"{thresholds.max_escalation_rate:.0%}.",
            )
        )
    if disagreement_rate > thresholds.max_disagreement_rate:
        alerts.append(
            Alert(
                metric="disagreement_rate",
                value=disagreement_rate,
                threshold=thresholds.max_disagreement_rate,
                severity="warning",
                message=f"Feature/defect disagreement rate {disagreement_rate:.0%} exceeds "
                f"{thresholds.max_disagreement_rate:.0%}.",
            )
        )
    if policy_violation_count > 0:
        alerts.append(
            Alert(
                metric="policy_violation_count",
                value=float(policy_violation_count),
                threshold=0.0,
                severity="critical",
                message=f"{policy_violation_count} policy violation(s) recorded - an agent "
                "proposed an action it isn't allowed to propose. Should never happen.",
            )
        )

    return ObservabilityReport(
        generated_at=datetime.now(UTC),
        total_workflows=total,
        decision_counts=decision_counts,
        escalation_rate=escalation_rate,
        avg_feature_confidence=avg_feature_confidence,
        avg_defect_confidence=avg_defect_confidence,
        disagreement_rate=disagreement_rate,
        policy_violation_count=policy_violation_count,
        alerts=alerts,
    )
