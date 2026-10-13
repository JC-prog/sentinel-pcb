"""Classifier sub-agent: the two-stage classification through the inference service - region first,
then the defect model for that region. Two steps, each callable on its own. Deterministic routing;
the models do the classifying. Never raises: an unreachable or unconfigured service becomes
`run.error`."""

from app.chat.agents.inspection_agent.state import InspectionRun, Stage
from app.chat.services.cases import REGION_MODEL
from app.shared.config.settings import settings
from app.shared.inference import InferenceError, InferenceNotConfigured, classify

# Region label (from pcb_region's own class order) -> the defect model that classifies it.
DEFECT_MODEL_BY_REGION = {
    "Body": "pcb_body_defect",
    "Lead": "pcb_lead_defect",
    "Text": "pcb_text_defect",
}


async def classify_region(run: InspectionRun) -> None:
    region = await _classify(run, REGION_MODEL, "Region")
    if region is None:
        return
    run.region = region
    run.note(f"Region classification: {region.label} (confidence={region.confidence:.2f}).")

    # Confidence first: a low-confidence guess is a case for review whatever the label is, so it
    # must not be reported as a routing error.
    threshold = settings.adc_region_confidence_threshold
    if region.confidence < threshold:
        run.region_uncertain = True
        run.note(
            f"Region confidence below threshold ({region.confidence:.2f} < {threshold:.2f}); "
            "skipping defect classification."
        )
        return

    if region.label not in DEFECT_MODEL_BY_REGION:
        run.fail(f"No defect classifier routed for region {region.label!r}.")


async def classify_defect(run: InspectionRun) -> None:
    assert run.region is not None  # policy.py only allows this after a confident region
    defect = await _classify(run, DEFECT_MODEL_BY_REGION[run.region.label], "Defect")
    if defect is None:
        return
    run.defect = defect
    run.note(f"Defect classification: {defect.label} (confidence={defect.confidence:.2f}).")


async def _classify(run: InspectionRun, model: str, stage_name: str) -> Stage | None:
    request = run.request
    try:
        result = await classify(
            model=model,
            username=request.username,
            image=request.image_bytes,
            filename=request.image_name,
        )
    except InferenceNotConfigured as exc:
        run.fail(f"Inference service not configured: {exc}")
        return None
    except InferenceError as exc:
        run.fail(f"{stage_name} classification failed: {exc}")
        return None
    return Stage(
        model=model,
        model_version=result.model_version or None,
        label=result.label,
        confidence=result.confidence,
        scores=result.scores,
    )
