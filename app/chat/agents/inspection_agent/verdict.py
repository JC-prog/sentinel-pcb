"""The verdict: ACCEPTED or REVIEW_REQUIRED, from fixed rules over what the sub-agents found. A
pure function - no LLM and no I/O - so the same evidence always gives the same answer, and the
reasons it returns are exactly why."""

from dataclasses import dataclass

from app.chat.agents.inspection_agent.state import InspectionRun
from app.chat.db.models import CaseStatus
from app.shared.config.settings import settings


@dataclass(frozen=True)
class Verdict:
    status: CaseStatus
    reasons: list[str]

    @property
    def review_required(self) -> bool:
        return self.status == CaseStatus.REVIEW_REQUIRED


def decide(run: InspectionRun) -> Verdict:
    reasons: list[str] = []

    if run.region_uncertain and run.region is not None:
        reasons.append(
            f"region confidence {run.region.confidence:.2f} is below the "
            f"{settings.adc_region_confidence_threshold:.2f} threshold, so no defect model was run"
        )
    elif run.defect is not None and run.defect.confidence < settings.adc_defect_confidence_threshold:
        reasons.append(
            f"defect confidence {run.defect.confidence:.2f} is below the "
            f"{settings.adc_defect_confidence_threshold:.2f} threshold"
        )

    validation = run.measurement_validation
    if validation is not None and not validation["valid"]:
        issues = ", ".join(validation["issues"]) or "unspecified issues"
        reasons.append(f"measurement validation failed ({issues})")

    status = CaseStatus.REVIEW_REQUIRED if reasons else CaseStatus.ACCEPTED
    return Verdict(status=status, reasons=reasons)
