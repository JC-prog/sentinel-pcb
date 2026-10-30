"""The explain agent's chat tool: an advisory explanation of the image the user just inspected,
from Agent 2 (asked over MCP, app/chat/services/explain_client.py).

Its only argument picks one of the images inspected in the latest turn: the image, board, component
and the model's own call come from the inspection the user is looking at (a draft, or the latest
case) - never from anything the model supplies, so it cannot be pointed at another file. It decides nothing and saves nothing; the
explanation is shown next to the model's label and the person decides.
"""

from typing import Annotated, Any

from langchain_core.tools import tool

from app.chat.agents.toolkit import ChatTool, Runtime, ToolRefused, returns_json
from app.chat.services import explain_client
from app.chat.services.current_inspection import current_inspection
from app.chat.services.explain_client import ExplainRequest, ExplainUnavailable
from app.shared.config.settings import settings


@tool("explain_inspection")
@returns_json
async def explain_inspection(
    runtime: Runtime,
    image_number: Annotated[
        int | None,
        "When several images were inspected in the latest turn, which one to explain (the number "
        "on its result). Omit for the last one inspected.",
    ] = None,
) -> dict[str, Any]:
    """Explains the image just inspected: what is visible, which IPC criteria apply, and whether
    that supports the model's label - Agent 2's grounded view. ADVISORY: it does not change the
    model's label, the verdict or any record, and it can disagree with the model. Use it when the
    user asks why the model said that, whether it is right, or to explain the result. It uses the
    most recent inspection in this conversation (or, of several images, the one with that
    image_number); it cannot be pointed at any other file. Report what it returns;
    never present it as the final decision - the person decides."""

    ctx = runtime.context
    current = await current_inspection(
        ctx.session,
        conversation_id=ctx.conversation_id,
        user_id=ctx.user.id,
        image_number=image_number,
    )

    if current.uses_draft and current.draft is not None:
        fields: dict[str, Any] = dict(current.draft.payload)
    elif current.case is not None:
        case = current.case
        fields = {
            "image_id": case.image_id,
            "inspection_xml_id": case.inspection_xml_id,
            "board_id": case.board_id,
            "component_ref": case.component_ref,
            "issue_symptom": case.issue_symptom,
            "defect_label": case.defect_label,
            "defect_confidence": case.defect_confidence,
        }
    else:
        raise ToolRefused(
            current.empty_reason(
                "there is no inspection in this conversation to explain - inspect an image first."
            )
        )

    def known(value: Any) -> str:
        label = str(value or "")
        return "" if label.lower() == "unknown" else label

    request = ExplainRequest(
        image_name=str(fields.get("image_id") or ""),
        board_id=known(fields.get("board_id")),
        component_ref=known(fields.get("component_ref")),
        issue_symptom=str(fields.get("issue_symptom") or ""),
        model_label=str(fields.get("defect_label") or ""),
        model_confidence=fields.get("defect_confidence"),
        xml_name=str(fields.get("inspection_xml_id") or ""),
    )
    try:
        explanation = await explain_client.explain(request)
    except ExplainUnavailable as unavailable:
        raise ToolRefused(
            f"{unavailable.message}. The inspection result above still stands."
        ) from unavailable

    return {
        **explanation,
        "instruction": (
            "Tell the user this is Agent 2's advisory explanation, not a decision. State whether "
            "it agrees with the model's label (agrees_with_model), in a sentence or two, and "
            "mention if telemetry was not recorded or the guardrail flagged it. If it disagrees, "
            "say the person decides, and offer relabel_case only if they say the label is wrong."
        ),
    }


def _enabled() -> bool:
    return settings.explain_available


EXPLAIN_INSPECTION = ChatTool(
    explain_inspection, label="Explanation", enabled=_enabled, shows_card=True
)
