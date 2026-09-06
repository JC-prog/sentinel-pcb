from typing import Any

from pydantic import BaseModel


class ExplainabilityReviewRequest(BaseModel):
    image_id: str
    board_id: str
    component_ref: str
    issue_symptom: str | None = None


class ExplainabilityReviewResponse(BaseModel):
    defect_category: str
    defect_location: dict[str, Any] | None
    explanation: str
    confidence_score: float
    self_check_passed: bool
    errors: list[str]
