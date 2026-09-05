from typing import Any

from pydantic import BaseModel, SecretStr


class ExplainabilityReviewRequest(BaseModel):
    image_id: str
    board_id: str
    component_ref: str
    issue_symptom: str | None = None
    # Bring-your-own-key, same treatment as ChatStreamRequest.openai_api_key - never persisted
    # server-side, only used for this request. Optional here because this agent also supports a
    # server-side fallback key (settings.explainability_agent_openai_api_key) - see app/main.py.
    openai_api_key: SecretStr | None = None


class ExplainabilityReviewResponse(BaseModel):
    defect_category: str
    defect_location: dict[str, Any] | None
    explanation: str
    confidence_score: float
    self_check_passed: bool
    errors: list[str]
