"""Adapted from orchestrator-agent/adc_agentic_project's services/multimodal_inference.py.

The source called a local ONNXImageClassifier directly:

    class TwoStageInferenceService:
        def __init__(self, model_lifecycle, feature_confidence_threshold=0.70):
            self.models = model_lifecycle
            self.feature_threshold = feature_confidence_threshold

        def infer_sample(self, sample):
            feature_model_result = self.models.get_model("feature")
            if not feature_model_result.success:
                return feature_model_result

            # Stage 1 uses the defect ROI crop, consistent with supplied model documentation.
            stage1 = feature_model_result.data["model"].predict(sample["defect_image"])
            predicted_feature = stage1["prediction"]
            ...
            stage2 = defect_model_result.data["model"].predict(sample["defect_image"])
            ...

This port keeps the same two-stage feature -> defect routing and threshold logic, but replaces
the local `.predict()` calls with calls to sentinel-pcb's inference microservice
(app/shared/inference/client.py) via the new `_classify` helper below - see model_lifecycle.py's
docstring for why. `infer_sample` and `_classify` are `async` because that client is async
(httpx). Everything else - the routing, the threshold gate, the comparison/agreement fields - is
unchanged from the source.
"""

from pathlib import Path

from app.workflow.agents.orchestrator_agent.routing import route_feature
from app.workflow.agents.orchestrator_agent.services.common import ServiceResult
from app.shared.inference import InferenceError, InferenceNotConfigured, classify


class TwoStageInferenceService:
    """
    Executes the sentinel-pcb inference service's region/body/lead/text models.
    Golden image + measurements are retained as context/validation inputs;
    the classifiers themselves accept one RGB image.
    """

    def __init__(self, model_lifecycle, username, feature_confidence_threshold=0.70):
        self.models = model_lifecycle
        self.username = username
        self.feature_threshold = feature_confidence_threshold

    async def infer_sample(self, sample):
        feature_model_result = self.models.get_model("feature")
        if not feature_model_result.success:
            return feature_model_result

        # Stage 1 uses the defect ROI crop, consistent with supplied model documentation.
        stage1 = await self._classify(feature_model_result.data["service_model"], sample["defect_image"])
        if stage1 is None:
            return ServiceResult(False, "INFERENCE_SERVICE_ERROR",
                                 data={"sample_id": sample["sample_id"]}, recoverable=True)
        predicted_feature = stage1["prediction"]

        if stage1["confidence"] < self.feature_threshold:
            return ServiceResult(
                False, "FEATURE_CLASSIFICATION_UNCERTAIN",
                data={"sample_id": sample["sample_id"], "feature_classification": stage1},
                recoverable=True, next_action="review"
            )

        route = route_feature(predicted_feature)
        if route is None:
            return ServiceResult(False, "UNSUPPORTED_FEATURE",
                                 data={"feature_classification": stage1}, recoverable=True)

        defect_model_result = self.models.get_model(route)
        if not defect_model_result.success:
            return defect_model_result
        stage2 = await self._classify(defect_model_result.data["service_model"], sample["defect_image"])
        if stage2 is None:
            return ServiceResult(False, "INFERENCE_SERVICE_ERROR",
                                 data={"sample_id": sample["sample_id"]}, recoverable=True)

        source_feature = sample.get("source_feature")
        machine_defect = sample.get("machine_defect", "")
        normalized_machine = machine_defect.split("_")[0].replace("Insuffcient", "Insufficient")
        return ServiceResult(
            True, "INFERENCE_COMPLETED",
            data={
                "sample_id": sample["sample_id"],
                "source_feature": source_feature,
                "machine_defect": machine_defect,
                "feature_classification": stage1,
                # "selected_model" is route_feature()'s raw key ("body"/"lead"/"text"), used only
                # for routing; "service_model" is the actual inference-service model name
                # (e.g. "pcb_body_defect", from model_lifecycle.py) that modelops code elsewhere
                # (drift reports, retraining tickets) needs to match against.
                "routing": {
                    "selected_model": route,
                    "service_model": defect_model_result.data["service_model"],
                },
                "defect_classification": stage2,
                "comparison": {
                    "feature_agreement": source_feature.lower() == predicted_feature.lower(),
                    "defect_agreement": normalized_machine.lower() == stage2["prediction"].lower(),
                },
                "failed_inspections": sample.get("failed_inspections", {}),
            }
        )

    async def _classify(self, model_name, image_path):
        """Returns None (never raises) on any failure to read the image or reach the inference
        service - infer_sample turns that into a recoverable ServiceResult, matching the source's
        own graceful-degradation convention."""

        try:
            image_bytes = Path(image_path).read_bytes()
        except OSError:
            return None

        try:
            result = await classify(
                model=model_name,
                username=self.username,
                image=image_bytes,
                filename=Path(image_path).name,
            )
        except (InferenceNotConfigured, InferenceError):
            return None

        return {
            "prediction": result.label,
            "confidence": result.confidence,
            "probabilities": result.scores,
            # "" when the inference service predates versioning - normalized to None so callers
            # get a clean "unknown" rather than an empty-looking version string.
            "model_version": result.model_version or None,
        }
