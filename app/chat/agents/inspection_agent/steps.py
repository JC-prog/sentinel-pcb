"""Runs one inspection step by name. The single place a step name turns into the sub-agent call
that does it - used by both the ReAct toolbox (when the model asks for a step) and the
deterministic pipeline (for the steps the model left undone). Ordering is policy.py's concern, not
this module's: callers check `policy.check` first."""

from app.chat.agents.inspection_agent import classifier, policy, verifier
from app.chat.agents.inspection_agent.state import InspectionRun


async def execute(step: str, run: InspectionRun) -> None:
    if step == policy.VERIFY_IMAGE:
        verifier.verify_image(run)
    elif step == policy.VALIDATE_MEASUREMENTS:
        verifier.validate_measurements(run)
    elif step == policy.CLASSIFY_REGION:
        await classifier.classify_region(run)
    elif step == policy.CLASSIFY_DEFECT:
        await classifier.classify_defect(run)
    else:
        raise ValueError(f"unknown inspection step {step!r}")
