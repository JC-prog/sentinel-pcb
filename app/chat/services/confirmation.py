"""The rule behind every two-step action in chat (a relabel, a case review): the model may *propose*
an action in one turn, but only a LATER turn - after the user has had a chance to answer - may commit
it, only for the user who proposed it, and only while the proposal is fresh.

It is enforced in code, not left to the model's good manners: `turn_started_at` is when the current
chat turn began, and a proposal is stamped inside the turn that made it, so a commit that arrives in
the same turn is refused whatever the model was told or tricked into. A proposal older than
`expires_after` is refused too, so a "yes" meant for something else, much later, cannot commit a
proposal the user had walked away from. Each agent words the refusal for its own action; this module
only decides whether there is one.
"""

from datetime import datetime, timedelta
from enum import StrEnum

from app.shared.config.settings import settings


class ConfirmationProblem(StrEnum):
    NOTHING_PENDING = "nothing_pending"
    PROPOSED_BY_SOMEONE_ELSE = "proposed_by_someone_else"
    SAME_TURN = "same_turn"
    EXPIRED = "expired"


def proposal_ttl() -> timedelta:
    return timedelta(minutes=settings.chat_proposal_ttl_minutes)


def confirmation_problem(
    *,
    pending_at: datetime | None,
    pending_by_user_id: str | None,
    user_id: str,
    turn_started_at: datetime,
    expires_after: timedelta | None = None,
) -> ConfirmationProblem | None:
    """Why a pending proposal may not be committed right now, or None if it may. Without
    `expires_after` a proposal never goes stale (an inspection waiting to be saved as a case)."""

    if pending_at is None:
        return ConfirmationProblem.NOTHING_PENDING
    if pending_by_user_id != user_id:
        return ConfirmationProblem.PROPOSED_BY_SOMEONE_ELSE
    if pending_at >= turn_started_at:
        return ConfirmationProblem.SAME_TURN
    if expires_after is not None and turn_started_at - pending_at > expires_after:
        return ConfirmationProblem.EXPIRED
    return None
