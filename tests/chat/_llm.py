"""A scripted stand-in for the chat model, for every chat test that needs an LLM.

`ScriptedModel` plays back replies - plain text, or tool calls - and records everything it was shown
and offered. `install` puts it where the app builds its chat models (the supervisor's and memory's
fact extraction), and returns the list of providers the app asked for, so a test can assert which
conversation provider was used. Nothing touches the network.
"""

from collections.abc import Callable
from typing import Any

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

_call_counter = 0


def ai(content: str = "", *tools: str | tuple[str, dict[str, Any]]) -> AIMessage:
    """A model reply: text, or tool calls (several in one turn if several are given). A tool is a
    name, or (name, arguments)."""

    global _call_counter
    calls = []
    for spec in tools:
        name, args = (spec, {}) if isinstance(spec, str) else spec
        _call_counter += 1
        calls.append({"name": name, "args": args, "id": f"call-{_call_counter}", "type": "tool_call"})
    return AIMessage(content=content, tool_calls=calls)


class ScriptedModel(BaseChatModel):
    """Replies from `replies` in order (an Exception in the list is raised instead), or from
    `reply_fn(messages)` when given. Records every prompt in `seen` and the tools it was bound to
    in `offered`."""

    replies: list[Any] = Field(default_factory=list)
    reply_fn: Any = None  # Callable[[list[BaseMessage]], AIMessage | Exception] | None
    seen: list[list[BaseMessage]] = Field(default_factory=list)
    offered: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **_: Any) -> "ScriptedModel":
        self.offered = [getattr(t, "name", str(t)) for t in tools]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **_: Any,
    ) -> ChatResult:
        self.seen = [*self.seen, list(messages)]
        reply = self.reply_fn(messages) if self.reply_fn else self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return ChatResult(generations=[ChatGeneration(message=reply)])


def says(text: str) -> ScriptedModel:
    """A model that answers every prompt with `text`."""

    return ScriptedModel(reply_fn=lambda messages: ai(text))


def always(reply: Callable[[list[BaseMessage]], AIMessage | Exception]) -> ScriptedModel:
    return ScriptedModel(reply_fn=reply)


def install(monkeypatch: pytest.MonkeyPatch, model: BaseChatModel) -> list[str]:
    """Makes the app use `model` wherever it builds a chat model. Returns the providers requested,
    in order (the agents' own model - the inspect ReAct pass - asks for none)."""

    providers: list[str] = []

    def build(provider: str | None = None, **_: Any) -> BaseChatModel:
        providers.append(provider or "")
        return model

    monkeypatch.setattr("app.chat.agents.supervisor.build_chat_model", build)
    monkeypatch.setattr("app.chat.memory.service.build_chat_model", build)
    return providers


def conversation(prompt: list[BaseMessage]) -> list[tuple[str, str]]:
    """The prompt as (kind, text) pairs, minus the always-present system message."""

    return [(m.type, str(m.content)) for m in prompt if m.type != "system"]
