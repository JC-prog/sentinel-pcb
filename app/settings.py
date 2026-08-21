from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    database_url: str = ""
    redis_url: str = ""
    langsmith_api_key: str = ""
    langsmith_project: str = "sentinel-pcb"
    ollama_base_url: str = "http://localhost:11434"

    # Multi-Modal Inference: trusted ADC model (see docs/ADC-Design-Doc-Team10-V2.md §7).
    # Exported from https://huggingface.co/JcProg/PCBInspect-AI (YOLOv12-Medium, ONNX, NMS baked
    # in). Labels file is derived by replacing the ".onnx" suffix with ".labels.json".
    adc_model_path: str = "models/pcb_feature_detector.onnx"
    adc_input_size: int = 640
    adc_detection_confidence: float = 0.25
    """Per-box confidence floor for a detection to be reported at all - separate from the
    policy-level auto_accept/hitl_escalation thresholds in app.policies.thresholds, which decide
    what to *do* with the detections once we have them."""

    # ui/ (Angular + Vite) dev server origin. Add the deployed origin here once one exists.
    cors_allow_origins: list[str] = ["http://localhost:4200"]

    # Artificial pause between persisted workflow phases (app.agents.orchestrator.runner), so the
    # Tracing view's SSE stream is visibly paced instead of flashing through states in <100ms.
    # Off by default - set via .env for a demo.
    demo_phase_delay_seconds: float = 0.0

    # Where uploaded inspection images are stored on disk (app.agents.orchestrator.runner).
    image_storage_dir: str = "data/images"

    # Case Context RAG embeddings (app.services.embeddings). "local" is the only implemented
    # provider today - abstracted so a hosted provider (and eventually an admin panel to pick
    # one) is a config change later, not a rewrite.
    embedding_provider: Literal["local"] = "local"
    embedding_model_name: str = "all-MiniLM-L6-v2"


settings = Settings()
