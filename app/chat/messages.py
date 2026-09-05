from app.core.chat import ChatMessage, ChatTurn


def build_messages(
    system_prompt: str | None, history: list[ChatTurn], message: str
) -> list[ChatMessage]:
    """Builds the initial message list for one chat turn's tool-calling loop
    (app/main.py's _chat_sse) - system prompt, then prior history, then the new user message.
    Only the entry point; the loop itself appends assistant/tool ChatMessages as rounds happen."""

    messages = [ChatMessage(role="system", content=system_prompt)] if system_prompt else []
    messages += [ChatMessage(role=turn.role, content=turn.content) for turn in history]
    messages.append(ChatMessage(role="user", content=message))
    return messages
