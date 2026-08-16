from pathlib import Path

import pytest

from app.agents.orchestrator import run_workflow
from app.settings import settings

MODEL_PATH = Path(settings.adc_model_path)
DEMO_IMAGE = Path(__file__).resolve().parents[3] / "simulation" / "images" / "pcb-demo.png"


def test_rejected_quality_short_circuits_before_inference() -> None:
    result = run_workflow(image=b"not an image", image_filename="garbage.png")

    assert result.decision == "rejected_quality"
    assert result.detections == []
    assert result.overall_confidence == 0.0
    assert "image_undecodable" in result.rationale


@pytest.mark.skipif(
    not MODEL_PATH.exists() or not DEMO_IMAGE.exists(),
    reason="ADC model/demo image not present - see docs/ADC-Design-Doc-Team10-V2.md §8",
)
def test_full_pipeline_reaches_a_decision() -> None:
    result = run_workflow(image=DEMO_IMAGE.read_bytes(), image_filename=DEMO_IMAGE.name)

    assert result.decision in ("auto_accept", "escalate_to_human")
    assert result.workflow_id
    assert result.rationale
