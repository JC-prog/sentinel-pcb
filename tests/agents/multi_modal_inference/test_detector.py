from pathlib import Path

import pytest
from PIL import Image

from app.agents.multi_modal_inference import get_detector
from app.settings import settings

MODEL_PATH = Path(settings.adc_model_path)
DEMO_IMAGE = Path(__file__).resolve().parents[3] / "simulation" / "images" / "pcb-demo.png"

pytestmark = pytest.mark.skipif(
    not MODEL_PATH.exists() or not DEMO_IMAGE.exists(),
    reason="ADC model/demo image not present - see docs/ADC-Design-Doc-Team10-V2.md §8",
)


def test_predict_returns_well_formed_result() -> None:
    detector = get_detector()
    image = Image.open(DEMO_IMAGE)

    result = detector.predict(image)

    assert result.model_version == MODEL_PATH.name
    assert 0.0 <= result.overall_confidence <= 1.0
    for detection in result.detections:
        assert 0.0 <= detection.confidence <= 1.0
        assert detection.label in detector.labels.values()
        x1, y1, x2, y2 = detection.bbox
        assert x1 < x2
        assert y1 < y2


def test_overall_confidence_is_min_of_detections() -> None:
    detector = get_detector()
    image = Image.open(DEMO_IMAGE)

    result = detector.predict(image)

    if result.detections:
        assert result.overall_confidence == min(d.confidence for d in result.detections)
    else:
        assert result.overall_confidence == 0.0
