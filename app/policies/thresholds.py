"""Configurable thresholds behind deterministic routing decisions.

Kept separate from `allowlist.py`: thresholds are tunable operational knobs (what counts
as "high confidence", how much drift is tolerable); the allowlist is a security boundary
(who may propose what). Both are versioned in git like prompts, per DEVELOPMENT.md.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Thresholds(BaseModel):
    """Every numeric threshold referenced in the proposal / design doc, in one place -
    so they're reviewed like policy, not hardcoded inline in agent logic.
    """

    auto_accept_confidence: float = Field(
        default=0.95,
        description="Below this, a case cannot be auto-accepted even if otherwise conflict-free.",
    )
    hitl_escalation_confidence: float = Field(
        default=0.80,
        description="Below this, escalate to human review regardless of other signals.",
    )
    max_reprep_retries: int = Field(
        default=1,
        description="Cap on Multi-Modal Inference -> Dataset Preparation re-preparation loops.",
    )
    drift_psi_threshold: float = Field(
        default=0.2,
        description="Population Stability Index above this triggers a retraining proposal.",
    )


# TODO(spec): load overrides from settings/env once per-agent specs confirm which
# thresholds are global vs. per operational slice (see proposal's Fairness consideration).
thresholds = Thresholds()
