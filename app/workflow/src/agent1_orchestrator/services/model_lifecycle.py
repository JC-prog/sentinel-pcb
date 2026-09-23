from app.shared.config.settings import settings
from services.common import ServiceResult

# The four approved model keys, mapped to the names inference/models.toml deploys them under.
_MODEL_KEY_TO_SERVICE_NAME = {
    "feature": "pcb_region",
    "body": "pcb_body_defect",
    "lead": "pcb_lead_defect",
    "text": "pcb_text_defect",
}


class ModelLifecycleService:
    """Resolve the four approved model keys to the sentinel-pcb inference/ microservice's model
    names - see app/shared/inference/client.py's classify(). No local ONNX loading; the
    inference/ microservice holds the models and their weights.

    `project_root`/`config_path` are accepted (and ignored) only so OrchestratorAgent's existing
    `ModelLifecycleService(project_root)` call site needs no change.
    """

    def __init__(self, project_root: object = None, config_path: object = None) -> None:
        del project_root, config_path

    def get_model(self, key: str) -> ServiceResult:
        key = key.lower()
        service_name = _MODEL_KEY_TO_SERVICE_NAME.get(key)
        if service_name is None:
            return ServiceResult(False, "MODEL_NOT_CONFIGURED", errors=[key])
        if not settings.inference_base_url:
            return ServiceResult(
                False, "MODEL_FILES_MISSING",
                errors=["settings.inference_base_url is not set"], recoverable=True,
            )
        return ServiceResult(
            True, "MODEL_AVAILABLE", data={"model_key": key, "service_model": service_name}
        )
