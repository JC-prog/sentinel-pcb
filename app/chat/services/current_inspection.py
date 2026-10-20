"""What "it" means in a conversation: the inspection the user is looking at. A Case is made from an
earlier inspection, so a draft newer than the conversation's latest Case is the image they are
looking at now; otherwise it is the latest Case. With several images inspected in one turn,
`image_number` picks one of them (a number the user and the model both saw on the results).

Lives in services/ because the relabel, review, explain and inspection agents all need the same
answer and agents do not import each other - copies of this rule would let "explain it" and
"relabel it" pick different targets."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.db.models import Case, CaseDraft
from app.chat.services import drafts
from app.chat.services.cases import latest_case_in_conversation
from app.chat.services.drafts import Pending


@dataclass(frozen=True)
class CurrentInspection:
    draft: CaseDraft | None
    case: Case | None
    # Set when an image_number was asked for and no inspection of that image is waiting.
    missing_image: int | None = None

    @property
    def is_empty(self) -> bool:
        return self.draft is None and self.case is None

    @property
    def uses_draft(self) -> bool:
        if self.draft is None:
            return False
        return self.case is None or self.draft.created_at >= self.case.created_at

    def empty_reason(self, nothing: str) -> str:
        """Why there is nothing to act on: `nothing` is the agent's own wording for an empty
        conversation."""

        if self.missing_image is not None:
            return (
                f"no inspection of image {self.missing_image} is waiting - only the images "
                "inspected in the latest turn that inspected any can be picked by number."
            )
        return nothing


async def current_inspection(
    session: AsyncSession,
    *,
    conversation_id: str,
    user_id: str,
    image_number: int | None = None,
    pending: Pending | None = None,
) -> CurrentInspection:
    draft = await drafts.get_draft(
        session,
        conversation_id=conversation_id,
        user_id=user_id,
        image_number=image_number,
        pending=pending,
    )
    if image_number is not None:
        # A numbered image is one of the latest inspections, never an older Case.
        return CurrentInspection(
            draft=draft, case=None, missing_image=image_number if draft is None else None
        )
    case = await latest_case_in_conversation(session, conversation_id)
    return CurrentInspection(draft=draft, case=case)
