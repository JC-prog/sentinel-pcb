"""Ported verbatim from orchestrator-agent/adc_agentic_project's services/result_comparison.py."""

def final_decision(inference_result, defect_confidence_threshold=0.70):
    data = inference_result.data
    defect = data["defect_classification"]
    comparison = data["comparison"]
    if defect["confidence"] < defect_confidence_threshold:
        return "REVIEW_REQUIRED"
    if not comparison["feature_agreement"]:
        return "REVIEW_REQUIRED"
    if not comparison["defect_agreement"]:
        return "REVIEW_REQUIRED"
    return "ACCEPTED"
