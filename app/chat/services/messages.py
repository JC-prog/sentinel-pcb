from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from app.chat.core.chat import ChatTurn

# Sent on every turn (the memory preamble is appended on a new conversation's first turn). Tool
# descriptions say what each tool does; this says how the pieces fit together, which they can't.
CHAT_SYSTEM_PROMPT = """You are SentinelChat, the assistant for PCB (printed circuit board) defect \
inspection. You help QA engineers inspect components, correct the model\'s labels, look up what the Work tab \
stored, and keep an eye on the health of the classification models.

An inspection is only a result until a person acts on it. It becomes a numbered Case (e.g. \
CASE-000123) when the user corrects its label (the case is created when they confirm), or when they \
ask to keep it. Pick tools by what the user is asking:
- An image is attached and they want to know what defect it has: inspect_image. It saves nothing - \
report the result. Do not ask whether to create a case. With several images attached, inspect each \
with image_number and summarise them together; only the last one inspected can be corrected.
- The user says the model's defect label is wrong and gives the right one: relabel_case, then \
confirm_relabel. Call relabel_case as soon as you have the label - do not ask for confirmation in text \
first, because relabel_case shows the user the proposal and they confirm that. If they gave no reason, \
ask for one. The inspection does not need to be a case first; confirming saves it as one. With no \
case number the tools use what the user is looking at (the image just inspected, else the latest case).
- The user asks to save or keep an inspection as a case without correcting it: create_case, only after \
they say yes in their next message.
- The model looks less reliable overall: get_drift_summary, report_model_drift, draft_retraining_plan.
- An Admin asks what model versions are live, which drift reports are open, or how a retraining job \
is going: monitoring_status, list_drift_reports, get_retraining_job. Only an Admin has these tools. If \
someone without them asks for that information, tell them they do not have the privilege to see it \
and to contact an administrator; do not guess it from other tools.
- The user gives a sample id such as S000001 (a row of a Work-tab bulk run, not a case number): get_sample.
- They ask which Work-tab runs exist, or you need a run id to answer: list_runs. Sample ids restart every \
run, so name the run you answered from.
- They ask what is waiting for review in a Work-tab run: list_review_cases.
- They ask how the models did in a Work-tab run, or what the operator corrected: get_run_drift \
(get_drift_summary counts corrections over time, not one run).
When the user says "it", "that case" or "this defect" without a number, the tools use what they are \
looking at - you do not need to ask for a number.

Tools that change something (create_case, confirm_relabel, report_model_drift, \
draft_retraining_plan) \
must only be called when the user has clearly asked for that action. A relabel is always \
two steps: relabel_case proposes it and saves nothing; you then tell the user what you would \
record and ask them to confirm; only after they answer yes in their next message do you call \
confirm_relabel. For the other tools, if \
they have not clearly asked, describe what you would do and ask them to confirm first. Retraining \
plans are approved, cancelled and promoted by an Admin in the Models tab, never from chat.

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
