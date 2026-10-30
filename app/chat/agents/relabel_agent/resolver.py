"""Resolver sub-agent: what is the user correcting, and which model's label. The numbered Case, or -
when they said "it" / "that case" - what they are looking at: an inspection they just ran (a draft,
not yet a Case; `image_number` picks one of several) or the latest Case, whichever is newer
(app/chat/services/current_inspection.py).

A correction is of one model's label: the defect model's (the usual case) or the region model's,
when the region itself was wrong - which is also the only correction possible when the region was
too uncertain for a defect model to run."""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.relabel_agent.errors import RelabelRefused
from app.chat.db.models import Case, CaseDraft
from app.chat.services.cases import REGION_MODEL, resolve_case_ref
from app.chat.services.current_inspection import current_inspection

Stage = Literal["defect", "region"]


@dataclass(frozen=True)
class Target:
    """What a relabel applies to: a saved Case, or an inspection that becomes one on confirmation."""

    case: Case | None = None
    draft: CaseDraft | None = None

    @property
    def case_number(self) -> str | None:
        return self.case.case_number if self.case else None

    @property
    def image_number(self) -> int | None:
        return self.draft.image_number if self.draft else None

    @property
    def what(self) -> str:
        """How the target is named to the user."""

        if self.case_number:
            return self.case_number
        return f"image {self.image_number}" if self.image_number else "this inspection"

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
        return self._number("defect_confidence")

    @property
    def region(self) -> str | None:
        return self.case.region if self.case else self._payload("region")

    @property
    def region_model_version(self) -> str | None:
        return self.case.region_model_version if self.case else self._payload("region_model_version")

    @property
    def region_confidence(self) -> float | None:
        if self.case:
            return self.case.region_confidence
        return self._number("region_confidence")

    @property
    def corrected_label(self) -> str | None:
        return self.case.corrected_label if self.case else None

    @property
    def corrected_model(self) -> str | None:
        if self.case is None or self.case.corrected_label is None:
            return None
        return self.case.corrected_model or self.case.defect_model

    def model_for(self, stage: Stage) -> str | None:
        return REGION_MODEL if stage == "region" else self.defect_model

    def observed(self, stage: Stage) -> tuple[str | None, str | None, float | None]:
        """(label, model version, confidence) that model gave - what a correction corrects."""

        if stage == "region":
            return self.region, self.region_model_version, self.region_confidence
        return self.defect_label, self.defect_model_version, self.defect_confidence

    def _payload(self, key: str) -> str | None:
        value = self.draft.payload.get(key) if self.draft else None
        return str(value) if value is not None else None

    def _number(self, key: str) -> float | None:
        value = self.draft.payload.get(key) if self.draft else None
        return float(value) if isinstance(value, int | float) else None


def stage_of(model: str | None) -> Stage:
    return "region" if model == REGION_MODEL else "defect"


async def resolve_target(
    session: AsyncSession,
    case_number: str | None,
    conversation_id: str,
    user_id: str,
    *,
    image_number: int | None = None,
    pending: bool = False,
) -> Target:
    """`pending`: looking for a proposal to confirm or cancel, so among several images prefer the
    one that has a relabel waiting."""

    if (case_number or "").strip():
        case, error = await resolve_case_ref(session, case_number, conversation_id)
        if case is None:
            raise RelabelRefused(error or "case not found")
        return Target(case=case)

    current = await current_inspection(
        session,
        conversation_id=conversation_id,
        user_id=user_id,
        image_number=image_number,
        pending="relabel" if pending else None,
    )
    if current.is_empty:
        raise RelabelRefused(
            current.empty_reason(
                "there is nothing to relabel in this conversation yet - inspect an image first, "
                "or give a case number."
            )
        )
    return Target(draft=current.draft) if current.uses_draft else Target(case=current.case)


def require_label(target: Target, stage: Stage) -> tuple[str, str]:
    """(model, the label it gave) for a correction of that stage, or a refusal saying why there is
    nothing to correct."""

    model = target.model_for(stage)
    label, _, _ = target.observed(stage)
    if model and label:
        return model, label
    if stage == "region":
        raise RelabelRefused(f"{target.what} has no region classification to correct.")
    raise RelabelRefused(
        f"{target.what} has no defect classification to correct - the region was too uncertain "
        "for a defect model to run. If the region itself is wrong, correct the region instead "
        "(stage='region')."
    )
