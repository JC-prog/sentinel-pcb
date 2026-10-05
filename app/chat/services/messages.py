from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from app.chat.core.chat import ChatTurn

# Sent on every turn (the memory preamble is appended on a new conversation's first turn). Tool
# descriptions say what each tool does; this says how the pieces fit together, which they can't.
CHAT_SYSTEM_PROMPT = """You are SentinelChat, the assistant for PCB (printed circuit board) defect \
inspection. You help QA engineers inspect components, review the resulting cases, and keep an eye on \
the health of the classification models.

Every image inspection is saved as a numbered Case (e.g. CASE-000123). Pick tools by what the user is asking:
- An image is attached and they want to know what defect it has: inspect_image.
- The user says the model's defect label on a case is wrong: relabel_case, then confirm_relabel.
- The user has reviewed a case flagged for review and wants to approve it (the defect is real) or\
 override it (a false positive): review_case, then confirm_review.
- The model looks less reliable overall: get_drift_summary, report_model_drift, draft_retraining_plan.
When the user says "it", "that case" or "this defect" without a number, the case tools use the latest \
case in this conversation - you do not need to ask for the number.

Tools that change something (confirm_relabel, confirm_review, report_model_drift, draft_retraining_plan) \
must only be called when the user has clearly asked for that action. A relabel or a case review is always \
two steps: relabel_case / review_case proposes it and saves nothing; you then tell the user what you would \
record and ask them to confirm; only after they answer yes in their next message do you call \
confirm_relabel / confirm_review. For the other tools, if \
they have not clearly asked, describe what you would do and ask them to confirm first. Retraining \
plans are approved by an Admin in the Models tab, never from chat.

Report only what the tools return; never invent case numbers, labels or confidence values. Stay on \
PCB inspection and this app."""


def compose_system_prompt(memory_preamble: str | None) -> str:
    return f"{CHAT_SYSTEM_PROMPT}\n\n{memory_preamble}" if memory_preamble else CHAT_SYSTEM_PROMPT


def build_messages(
    system_prompt: str | None,
    history: list[ChatTurn],
    message: str,
    *,
    image_ids: list[str] | None = None,
    xml_ids: list[str] | None = None,
) -> list[BaseMessage]:
    """The prompt for one chat turn (app/chat/agents/supervisor.py): system prompt, then prior
    history, then the new user message. The agent appends its own tool-call rounds from there."""

    messages: list[BaseMessage] = [SystemMessage(system_prompt)] if system_prompt else []
    messages += [
        HumanMessage(turn.content) if turn.role == "user" else AIMessage(turn.content)
        for turn in history
    ]
    messages.append(HumanMessage(_with_attachment_note(message, image_ids or [], xml_ids or [])))
    return messages


def _with_attachment_note(message: str, image_ids: list[str], xml_ids: list[str]) -> str:
    """The model is never shown the actual image/XML bytes in this turn - only a subsequent tool
    call resolves the real upload server-side (from the tool's ToolContext, app/chat/core/tools.py). Without this note
    the model has no textual signal that anything was attached at all - a tool merely being
    *offered* isn't reliably read as "the user attached something", and models were declining to
    call inspect_image, telling the user no image was provided even though one was."""

    notes = []
    if image_ids:
        notes.append(f"{len(image_ids)} image{'s' if len(image_ids) != 1 else ''}")
    if xml_ids:
        notes.append(f"{len(xml_ids)} inspection XML file{'s' if len(xml_ids) != 1 else ''}")
    if not notes:
        return message
    attachment_line = f"[The user has attached {' and '.join(notes)} to this message.]"
    return f"{message}\n\n{attachment_line}" if message.strip() else attachment_line
