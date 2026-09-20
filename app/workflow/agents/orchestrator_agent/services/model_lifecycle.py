"""Adapted from orchestrator-agent/adc_agentic_project's services/model_lifecycle.py.

The source resolved and loaded four local ONNX model files:

    class ModelLifecycleService:
        def __init__(self, project_root, config_path="config/models.yaml"):
            self.project_root = Path(project_root)
            with open(self.project_root/config_path, "r", encoding="utf-8") as f:
                self.config = yaml.safe_load(f)["models"]
            self._cache = {}

        def get_model(self, key):
            key = key.lower()
            if key not in self.config:
                return ServiceResult(False, "MODEL_NOT_CONFIGURED", errors=[key])
            if key not in self._cache:
                cfg = self.config[key]
                model = self.project_root/cfg["path"]
                labels = self.project_root/cfg["labels"]
                if not model.exists() or not labels.exists():
                    return ServiceResult(False, "MODEL_FILES_MISSING",
                                         errors=[str(model), str(labels)], recoverable=True)
                self._cache[key] = ONNXImageClassifier(model, labels, cfg["input_size"])
            return ServiceResult(True, "MODEL_AVAILABLE", data={"model": self._cache[key], "model_key": key})

sentinel-pcb already runs the exact same PCB region/body/lead/text pipeline as a deployed ONNX
inference microservice (inference/, app/shared/inference/client.py) - same labels, same input sizes as
the four local .onnx files above. So this port resolves each key to that service's model name
instead of loading a local file, and drops the ONNXImageClassifier/yaml-config machinery
entirely - see services/multimodal_inference.py for the classify() calls this enables.
"""

from app.workflow.agents.orchestrator_agent.services.common import ServiceResult
from app.shared.config.settings import settings

# feature/body/lead/text keys (matches config/models.yaml's keys) -> the inference service's
# registered model names (inference/models.toml).
_MODEL_KEY_TO_SERVICE_NAME = {
    "feature": "pcb_region",
    "body": "pcb_body_defect",
    "lead": "pcb_lead_defect",
    "text": "pcb_text_defect",
}


class ModelLifecycleService:
    """Resolve the four approved model keys used by the two-stage pipeline to inference-service
    model names. No local caching/loading needed - the service holds the models."""

    def get_model(self, key):
        key = key.lower()
        service_name = _MODEL_KEY_TO_SERVICE_NAME.get(key)
        if service_name is None:
            return ServiceResult(False, "MODEL_NOT_CONFIGURED", errors=[key])
        if not settings.inference_base_url:
            return ServiceResult(False, "MODEL_FILES_MISSING",
                                 errors=["settings.inference_base_url is not set"], recoverable=True)
        return ServiceResult(True, "MODEL_AVAILABLE", data={"model_key": key, "service_model": service_name})
