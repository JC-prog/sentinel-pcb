import asyncio
from pathlib import Path
from typing import Any

from services.common import ServiceResult
from inference.router import route_feature

from app.shared.inference import classify


class TwoStageInferenceService:
    """
    Executes the sentinel-pcb inference/ microservice's region/body/lead/text models over HTTP
    (app.shared.inference.classify()) - the same four models inference/models.toml deploys.
    Golden image + measurements are retained as context/validation inputs; the classifiers
    themselves accept one RGB image.

    infer_sample() stays a plain synchronous method (OrchestratorAgent calls it with no `await`)
    even though the underlying call is async: it's only ever run inside a worker thread (via
    asyncio.to_thread(agent.run, ...)) with no event loop of its own, so asyncio.run() per
    classify call is the correct, minimal way to bridge in.
    """

    def __init__(self, model_lifecycle, feature_confidence_threshold=0.70, username="workflow"):
        self.models = model_lifecycle
        self.feature_threshold = feature_confidence_threshold
        self.username = username

    def infer_sample(self, sample):
        feature_model_result = self.models.get_model("feature")
        if not feature_model_result.success:
            return feature_model_result

        # Stage 1 uses the defect ROI crop, consistent with the model cards (inference/MODELS.md).
        stage1 = self._classify(feature_model_result.data["service_model"], sample["defect_image"])
        if stage1 is None:
            return ServiceResult(
                False, "INFERENCE_SERVICE_ERROR",
                data={"sample_id": sample["sample_id"]}, recoverable=True,
            )
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
        stage2 = self._classify(defect_model_result.data["service_model"], sample["defect_image"])
        if stage2 is None:
            return ServiceResult(
                False, "INFERENCE_SERVICE_ERROR",
                data={"sample_id": sample["sample_id"]}, recoverable=True,
            )

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

    def _classify(self, model_name: str, image_path: str) -> dict[str, Any] | None:
        try:
            image_bytes = Path(image_path).read_bytes()
        except OSError:
            return None
        try:
            result = asyncio.run(classify(
                model=model_name,
                username=self.username,
                image=image_bytes,
                filename=Path(image_path).name,
            ))
        except Exception:
            return None
        return {
            "prediction": result.label,
            "confidence": result.confidence,
            "probabilities": result.scores,
            "model_version": result.model_version or None,
        }
