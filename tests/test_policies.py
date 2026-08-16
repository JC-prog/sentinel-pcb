import pytest

from app.policies import ActionType, PolicyViolation, ProposedAction, thresholds, validate_action


def test_allowed_proposer_passes() -> None:
    action = ProposedAction(
        action=ActionType.ESCALATE_TO_HUMAN,
        proposed_by="orchestrator",
        workflow_id="wf-1",
        rationale="confidence below threshold",
    )
    assert validate_action(action) is action


def test_disallowed_proposer_is_rejected() -> None:
    action = ProposedAction(
        action=ActionType.PROPOSE_RETRAINING,
        proposed_by="dataset_preparation",
        workflow_id="wf-1",
        rationale="attempting an action outside its allowlist",
    )
    with pytest.raises(PolicyViolation):
        validate_action(action)


def test_default_thresholds_are_sane() -> None:
    assert 0 < thresholds.hitl_escalation_confidence < thresholds.auto_accept_confidence <= 1
    assert thresholds.max_reprep_retries >= 0
