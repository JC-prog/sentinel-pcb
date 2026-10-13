"""Resolver sub-agent: what is the user correcting. The numbered Case, or - when they said "it" /
"that case" - the latest thing in this conversation: the inspection they just ran (a draft, not yet
a Case) or the latest Case, whichever is newer. Only something the defect model actually classified,
since a correction is of that model's label."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.relabel_agent.errors import RelabelRefused
from app.chat.db.models import Case, CaseDraft
from app.chat.services import drafts
from app.chat.services.cases import latest_case_in_conversation, resolve_case_ref


@dataclass(frozen=True)
class Target:
    """What a relabel applies to: a saved Case, or an inspection that becomes one on confirmation."""

    case: Case | None = None
    draft: CaseDraft | None = None

    @property
    def case_number(self) -> str | None:
        return self.case.case_number if self.case else None

    @property
    def defect_model(self) -> str | None:
        return self.case.defect_model if self.case else self._payload("defect_model")

    @property
    def defect_label(self) -> str | None:
        return self.case.defect_label if self.case else self._payload("defect_label")

    @property
    def defect_model_version(self) -> str | None:
        return self.case.defect_model_version if self.case else self._payload("defect_model_version")

    @property
    def defect_confidence(self) -> float | None:
        if self.case:
            return self.case.defect_confidence
        value = self._payload("defect_confidence")
        return float(value) if isinstance(value, int | float) else None

    @property
    def corrected_label(self) -> str | None:
        return self.case.corrected_label if self.case else None

    def _payload(self, key: str) -> str | None:
        value = self.draft.payload.get(key) if self.draft else None
        return str(value) if value is not None else None


async def resolve_target(
    session: AsyncSession, case_number: str | None, conversation_id: str, user_id: str
) -> Target:
    if (case_number or "").strip():
        case, error = await resolve_case_ref(session, case_number, conversation_id)
        if case is None:
            raise RelabelRefused(error or "case not found")
        target = Target(case=case)
    else:
        draft = await drafts.get_draft(session, conversation_id=conversation_id, user_id=user_id)
        latest = await latest_case_in_conversation(session, conversation_id)
        if draft is None and latest is None:
            raise RelabelRefused(
                "there is nothing to relabel in this conversation yet - inspect an image first, "
                "or give a case number."
            )
        # The newer of the two is what "it" means: a Case is made from an earlier inspection, so a
        # draft that is newer than the latest Case is the image the user is looking at now.
        use_draft = draft is not None and (latest is None or draft.created_at >= latest.created_at)
        target = Target(draft=draft) if use_draft else Target(case=latest)

    if not target.defect_model or not target.defect_label:
        what = target.case_number or "this inspection"
        raise RelabelRefused(
            f"{what} has no defect classification to correct - the region was too uncertain for "
            "a defect model to run."
        )
    return target
