"""Request/response schemas for the /api/orchestrator/* routes in app/workflow/api/orchestrator.py."""

from pydantic import BaseModel


class OrchestratorUploadRecord(BaseModel):
    id: str


class OrchestratorRunRequest(BaseModel):
    """Per-run controls, deliberately not settings.py fields - mirrors the source project's
    tkinter UI, where thresholds and LLM planner options are per-run controls, not server config."""

    mode: str  # "prepare" | "prepare_verify" | "run_full"
    dataset_id: str
    xml_id: str
    image_root_id: str | None = None
    feature_threshold: float = 0.70
    defect_threshold: float = 0.70
    use_llm: bool = False
    llm_model: str | None = None
    llm_fallback: bool = True
