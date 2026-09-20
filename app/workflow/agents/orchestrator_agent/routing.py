"""Ported verbatim from orchestrator-agent/adc_agentic_project's inference/router.py - a plain
label mapping (stage-1 feature classification -> stage-2 defect model key), not ONNX-specific,
unlike the rest of that module and inference/onnx_classifier.py, which orchestrator_agent does not
port (see services/model_lifecycle.py's docstring for why).
"""

ROUTES = {
    "Body": "body",
    "Lead": "lead",
    "Text": "text",
}


def route_feature(predicted_feature: str) -> str | None:
    return ROUTES.get(predicted_feature)
