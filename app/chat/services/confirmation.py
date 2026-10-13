"""The rule behind every two-step action in chat (a relabel, a case review): the model may *propose*
an action in one turn, but only a LATER turn - after the user has had a chance to answer - may commit
it, and only for the user who proposed it.

It is enforced in code, not left to the model's good manners: `turn_started_at` is when the current
chat turn began, and a proposal is stamped inside the turn that made it, so a commit that arrives in
the same turn is refused whatever the model was told or tricked into. Each agent words the refusal
for its own action; this module only decides whether there is one.
"""

from datetime import datetime
from enum import StrEnum


class ConfirmationProblem(StrEnum):
    NOTHING_PENDING = "nothing_pending"
    PROPOSED_BY_SOMEONE_ELSE = "proposed_by_someone_else"
    SAME_TURN = "same_turn"


def confirmation_problem(
    *,
    pending_at: datetime | None,
    pending_by_user_id: str | None,
    user_id: str,
    turn_started_at: datetime,
) -> ConfirmationProblem | None:
    """Why a pending proposal may not be committed right now, or None if it may."""

    if pending_at is None:
        return ConfirmationProblem.NOTHING_PENDING
    if pending_by_user_id != user_id:
        return ConfirmationProblem.PROPOSED_BY_SOMEONE_ELSE
    if pending_at >= turn_started_at:
        return ConfirmationProblem.SAME_TURN
    return None
