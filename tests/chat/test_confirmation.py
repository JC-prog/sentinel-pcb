"""The shared two-step rule (app/chat/services/confirmation.py) - pure, no database."""

from datetime import UTC, datetime, timedelta

from app.chat.services.confirmation import ConfirmationProblem, confirmation_problem

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def _check(**overrides: object) -> ConfirmationProblem | None:
    args: dict[str, object] = {
        "pending_at": NOW - timedelta(minutes=1),  # proposed in an earlier turn
        "pending_by_user_id": "u1",
        "user_id": "u1",
        "turn_started_at": NOW,
    }
    args.update(overrides)
    return confirmation_problem(**args)  # type: ignore[arg-type]


def test_a_proposal_from_an_earlier_turn_by_the_same_user_may_be_committed() -> None:
    assert _check() is None


def test_nothing_pending_cannot_be_committed() -> None:
    assert _check(pending_at=None) == ConfirmationProblem.NOTHING_PENDING


def test_another_users_proposal_cannot_be_committed() -> None:
    assert _check(pending_by_user_id="u2") == ConfirmationProblem.PROPOSED_BY_SOMEONE_ELSE


def test_a_proposal_made_in_the_current_turn_cannot_be_committed() -> None:
    assert _check(pending_at=NOW + timedelta(seconds=1)) == ConfirmationProblem.SAME_TURN


def test_a_proposal_made_at_the_very_instant_the_turn_began_still_counts_as_this_turn() -> None:
    assert _check(pending_at=NOW) == ConfirmationProblem.SAME_TURN
