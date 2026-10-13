"""The step-order rules of an inspection: what may run now, given what has already run. Both the
LLM-driven pass (react.py) and the deterministic pipeline (pipeline.py) go through `check`, so a
model that asks for a step out of order - classifying before the image is verified, running a
defect model on an uncertain region - is refused with the reason instead of executing, and the
deterministic path can never do what the model path would not be allowed to."""

from app.chat.agents.inspection_agent.state import InspectionRun

VERIFY_IMAGE = "verify_image"
VALIDATE_MEASUREMENTS = "validate_measurements"
CLASSIFY_REGION = "classify_region"
CLASSIFY_DEFECT = "classify_defect"

# The order a complete inspection runs its steps in.
STEPS = (VERIFY_IMAGE, VALIDATE_MEASUREMENTS, CLASSIFY_REGION, CLASSIFY_DEFECT)


def is_done(step: str, run: InspectionRun) -> bool:
    if step == VERIFY_IMAGE:
        return bool(run.image_quality)
    if step == VALIDATE_MEASUREMENTS:
        return run.measurement_validation is not None
    if step == CLASSIFY_REGION:
        return run.region is not None
    if step == CLASSIFY_DEFECT:
        return run.defect is not None
    raise ValueError(step)


def check(step: str, run: InspectionRun) -> tuple[bool, str]:
    """(allowed, reason). The reason is what a refused model is told."""

    if run.error:
        return False, f"the inspection already failed: {run.error}"

    if step == VERIFY_IMAGE:
        if is_done(step, run):
            return False, "the image was already verified."
        return True, "ok"

    if not is_done(VERIFY_IMAGE, run):
        return False, "verify_image must run first."

    if step == VALIDATE_MEASUREMENTS:
        if run.request.inspection_xml_bytes is None:
            return False, "no inspection XML is attached."
        if is_done(step, run):
            return False, "the measurements were already validated."
        return True, "ok"

    if step == CLASSIFY_REGION:
        if is_done(step, run):
            return False, "the region was already classified."
        return True, "ok"

    if step == CLASSIFY_DEFECT:
        if run.region is None:
            return False, "classify_region must run first."
        if run.region_uncertain:
            return False, "the region result was not confident enough to run a defect model."
        if is_done(step, run):
            return False, "the defect was already classified."
        return True, "ok"

    return False, f"unknown step {step!r}."
