"""The inspect agent's chat tools - the boundary between the chat and the inspection.

`inspect_image` reads the user's attached upload (from the injected context, never an id the model
names), turns the model's optional arguments into an InspectionRequest, runs the pipeline and shapes
the outcome into the result the model reports and the UI shows as a card. It saves nothing: the
user is asked whether they want a Case, and `create_case` makes one only after they say yes - it takes
no data, only commits the inspection `inspect_image` just parked, and case_creation.py refuses it
unless the user has had a turn to answer.
"""

from typing import Annotated, Any

from langchain_core.tools import tool

from app.chat.agents.inspection_agent import case_creation
from app.chat.agents.inspection_agent.errors import CaseRefused
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
    validates an attached inspection XML's measurements. It saves NOTHING - it reports what it found.
    Use it whenever the user attaches an image and asks what is wrong with it. Report the region,
    the defect, the confidence and the verdict it returns, then ask whether they want a case created
    (a case is what they relabel or review) and only call create_case after they say yes."""

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


@tool("create_case")
@returns_json
async def create_case(runtime: Runtime) -> dict[str, Any]:
    """CREATES A RECORD: saves the image inspection just reported by inspect_image as a numbered
    Case, so it can be relabelled or reviewed. Call it ONLY after you asked the user whether they
    want a case and they replied yes - it is refused otherwise. It takes no arguments: it saves
    exactly what inspect_image found. If the user wants a case for an image you have not inspected
    yet, call inspect_image first."""

    ctx = runtime.context
    try:
        case = await case_creation.create_from_draft(
            ctx.session,
            conversation_id=ctx.conversation_id,
            user_id=ctx.user.id,
            turn_started_at=ctx.turn_started_at,
        )
    except CaseRefused as refusal:
        raise ToolRefused(refusal.message) from refusal

    return {
        "status": "created",
        "case_number": case.case_number,
        "verdict": case.status,
        "region": case.region,
        "defect": case.defect_label,
        "defect_confidence": case.defect_confidence,
        "instruction": (
            f"Tell the user {case.case_number} was created. They can now ask to relabel it "
            "(if the label is wrong) or, when it is flagged for review, to approve or override it."
        ),
    }


def _enabled() -> bool:
    return settings.adc_inspection_agent_enabled


INSPECT_IMAGE = ChatTool(
    inspect_image,
    label="Image inspection",
    enabled=_enabled,
    requires_image=True,
    shows_card=True,
)
CREATE_CASE = ChatTool(create_case, label="Case creation", enabled=_enabled)


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
    if outcome.draft is None or outcome.verdict is None:
        raise ToolRefused(run.error or "inspection failed", observations=run.observations)
    return {
        "case_created": False,
        "instruction": (
            "Nothing is saved as a case yet. Report the result, then ask the user whether they want "
            "a case created for it (they need one to relabel or review it). Only after they reply "
            "yes, call create_case."
        ),
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
