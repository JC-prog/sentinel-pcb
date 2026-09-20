from app.chat.core.chat import ChatMessage, ChatTurn


def build_messages(
    system_prompt: str | None,
    history: list[ChatTurn],
    message: str,
    *,
    image_ids: list[str] | None = None,
    xml_ids: list[str] | None = None,
) -> list[ChatMessage]:
    """Builds the initial message list for one chat turn's tool-calling loop
    (app/main.py's _chat_sse) - system prompt, then prior history, then the new user message.
    Only the entry point; the loop itself appends assistant/tool ChatMessages as rounds happen."""

    messages = [ChatMessage(role="system", content=system_prompt)] if system_prompt else []
    messages += [ChatMessage(role=turn.role, content=turn.content) for turn in history]
    messages.append(
        ChatMessage(role="user", content=_with_attachment_note(message, image_ids or [], xml_ids or []))
    )
    return messages


def _with_attachment_note(message: str, image_ids: list[str], xml_ids: list[str]) -> str:
    """The model is never shown the actual image/XML bytes in this turn - only a subsequent tool
    call resolves the real upload server-side (app/main.py's _run_tool_call). Without this note
    the model has no textual signal that anything was attached at all - a tool merely being
    *offered* isn't reliably read as "the user attached something", and models were declining to
    call adc_inspection/create_case, telling the user no image was provided even though one was."""

    notes = []
    if image_ids:
        notes.append(f"{len(image_ids)} image{'s' if len(image_ids) != 1 else ''}")
    if xml_ids:
        notes.append(f"{len(xml_ids)} inspection XML file{'s' if len(xml_ids) != 1 else ''}")
    if not notes:
        return message
    attachment_line = f"[The user has attached {' and '.join(notes)} to this message.]"
    return f"{message}\n\n{attachment_line}" if message.strip() else attachment_line
