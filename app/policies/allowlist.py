"""Hard allowlist gating every LLM-proposed action before an agent may act on it.

This is the "Policy & Rules Validation Tool" from the proposal's Table 2, and the
guardrail referenced throughout the proposal/design doc: an LLM may *recommend* an
action, but never execute it directly. Only an action that (a) is a recognized type and
(b) is permitted for the agent that proposed it passes `validate_action`.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ActionType(StrEnum):
    """Actions an LLM proposal is allowed to request.

    Adding a value here is a policy change - review it like a security control, not a
    routine code change.
    """

    AUTO_ACCEPT = "auto_accept"
    ESCALATE_TO_HUMAN = "escalate_to_human"
    REQUEST_REPREPARATION = "request_reprep"
    PROPOSE_RETRAINING = "propose_retraining"


# Which agent may propose which action. This is the actual hard allowlist; ActionType
# only defines the structurally valid values.
#
# TODO(spec): the proposer names below are provisional - reconcile against whichever
# agent set the team finalizes (see the still-open app/agents/ vs app/services/
# restructuring question) and the per-agent specs once they exist.
ALLOWED_PROPOSERS: dict[ActionType, frozenset[str]] = {
    ActionType.AUTO_ACCEPT: frozenset({"orchestrator"}),
    ActionType.ESCALATE_TO_HUMAN: frozenset({"orchestrator"}),
    ActionType.REQUEST_REPREPARATION: frozenset({"multi_modal_inference"}),
    ActionType.PROPOSE_RETRAINING: frozenset({"continuous_monitoring_drift"}),
}


class ProposedAction(BaseModel):
    """An action an agent's LLM step wants to take, pending policy validation."""

    action: ActionType
    proposed_by: str = Field(min_length=1, description="Name of the proposing agent.")
    workflow_id: str = Field(min_length=1)
    rationale: str = Field(min_length=1, description="Why the agent proposed this action.")
    payload: dict[str, object] = Field(default_factory=dict)


class PolicyViolation(Exception):
    """Raised when a proposed action fails allowlist or payload validation."""

    def __init__(self, action: ProposedAction, reason: str) -> None:
        self.action = action
        self.reason = reason
        super().__init__(
            f"policy violation for {action.action!r} by {action.proposed_by!r}: {reason}"
        )


def validate_action(action: ProposedAction) -> ProposedAction:
    """Validate a proposed action against the hard allowlist.

    Returns the action unchanged if it passes. Raises `PolicyViolation` otherwise.
    Callers must never execute a `ProposedAction` that hasn't been through this check.
    """

    allowed_proposers = ALLOWED_PROPOSERS.get(action.action, frozenset())
    if action.proposed_by not in allowed_proposers:
        raise PolicyViolation(
            action, f"{action.proposed_by!r} is not allowed to propose {action.action!r}"
        )

    # TODO(spec): per-action payload rules (required fields, value ranges) land here
    # once the per-agent specs define what each action's payload must contain.

    return action
