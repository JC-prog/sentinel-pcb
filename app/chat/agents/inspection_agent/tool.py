"""`inspect_image` - the inspect agent's chat tool. The boundary between the chat and the
inspection: it reads the user's attached upload (from the injected context, never an id the model
names), turns the model's optional arguments into an InspectionRequest, runs the pipeline and shapes
the outcome into the result the model reports and the UI shows as a card.
"""

from typing import Annotated, Any

from langchain_core.tools import tool

from app.chat.agents.inspection_agent.pipeline import InspectionOutcome, run_inspection
from app.chat.agents.inspection_agent.state import InspectionRequest, Stage
from app.chat.agents.toolkit import ChatTool, Runtime, ToolRefused, returns_json, text
from app.chat.core.tools import ToolContext
from app.chat.uploads import resolve_upload_path
from app.shared.config.settings import settings

# What a Case records for a board/component the user didn't identify - a user asking "what defect
# is this?" shouldn't be blocked for lack of a reference designator.
UNKNOWN = "unknown"

_TOP_SCORES = 3


@tool("inspect_image")
@returns_json
async def inspect_image(
    runtime: Runtime,
    question: Annotated[str | None, "What the user wants to know about the image, in their words."] = None,
    board_id: Annotated[str | None, "The AOI board identifier, if the user gave one."] = None,
    component_ref: Annotated[str | None, "The component reference designator (e.g. U7), if known."] = None,
    package: Annotated[str | None, "The component's package type, if known."] = None,
    feature: Annotated[str | None, "The specific feature/pad being flagged, if known."] = None,
    issue_symptom: Annotated[str | None, "What looked wrong, in the user's own words."] = None,
) -> dict[str, Any]:
    """Inspects the PCB component image the user attached: identifies which region it shows and
    what defect it has (two-stage classification through the inference service), optionally
    validates an attached inspection XML's measurements, and saves the result as a reviewable Case
    with a case number. Use it whenever the user attaches an image and asks what is wrong with it.
    Report the region, the defect, the confidence and the verdict it returns."""

    ctx = runtime.context
    request = _request(
        ctx,
        board_id=text(board_id) or UNKNOWN,
        component_ref=text(component_ref) or UNKNOWN,
        package=text(package) or None,
        feature=text(feature) or None,
        issue_symptom=text(issue_symptom) or None,
    )
    outcome = await run_inspection(ctx.session, request, question=text(question) or None)
    return _result(outcome)


INSPECT_IMAGE = ChatTool(
    inspect_image,
    label="Image inspection",
    enabled=lambda: settings.adc_inspection_agent_enabled,
    requires_image=True,
    shows_card=True,
)


def _request(ctx: ToolContext, **identity: Any) -> InspectionRequest:
    """The first attached image (and inspection XML, if any) - read from the user's own uploads."""

    if not ctx.image_ids:
        raise ToolRefused("no image is attached to this message")
    image_id = ctx.image_ids[0]
    image_path = resolve_upload_path(image_id)
    if image_path is None:
        raise ToolRefused("the attached image could not be found - ask the user to re-attach it")

    xml_id = ctx.xml_ids[0] if ctx.xml_ids else None
    xml_path = resolve_upload_path(xml_id) if xml_id else None

    return InspectionRequest(
        image_bytes=image_path.read_bytes(),
        image_name=image_id,
        username=ctx.user.username,
        user_id=ctx.user.id,
        conversation_id=ctx.conversation_id,
        inspection_xml_bytes=xml_path.read_bytes() if xml_path else None,
        inspection_xml_id=xml_id if xml_path else None,
        **identity,
    )


def _result(outcome: InspectionOutcome) -> dict[str, Any]:
    run = outcome.run
    if outcome.case is None or outcome.verdict is None:
        raise ToolRefused(run.error or "inspection failed", observations=run.observations)
    return {
        "case_number": outcome.case.case_number,
        "verdict": outcome.verdict.status.value,
        "review_required": outcome.verdict.review_required,
        "review_reasons": outcome.verdict.reasons,
        "inspection_summary": outcome.summary,
        "region": _stage(run.region),
        "defect": _stage(run.defect),
        "measurement_validation": run.measurement_validation,
        "image_quality": run.image_quality,
        "observations": run.observations,
    }


def _stage(stage: Stage | None) -> dict[str, Any] | None:
    if stage is None:
        return None
    return {
        "model": stage.model,
        "model_version": stage.model_version,
        "label": stage.label,
        "confidence": round(stage.confidence, 4),
        "top_scores": stage.ranked_scores(_TOP_SCORES),
    }
