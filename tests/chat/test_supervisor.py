"""The supervisor (app/chat/agents/supervisor.py): one chat turn as a LangGraph agent, streamed back
as plain events. Driven by a scripted model and stand-in tools, so it tests the turn's behaviour -
not any agent, and no database."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Annotated, Any

import pytest
from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from app.chat.agents import supervisor
from app.chat.agents.supervisor import (
    ROUNDS_EXHAUSTED,
    Answer,
    SupervisorEvent,
    TextChunk,
    ToolFinished,
    ToolStarted,
)
from app.chat.agents.toolkit import ChatTool, Runtime, ToolRefused, returns_json
from app.chat.core.tools import ToolContext
from app.shared.config.settings import settings
from tests.chat._llm import ScriptedModel, ai, always, install


@tool("echo_user")
@returns_json
async def echo_user(runtime: Runtime, note: Annotated[str, "anything"] = "") -> dict[str, Any]:
    """Reports who is asking, as the tool sees it."""
    ctx = runtime.context
    return {"note": note, "user": ctx.user.id, "conversation": ctx.conversation_id}


@tool("refuse")
@returns_json
async def refuse(runtime: Runtime) -> dict[str, Any]:
    """Always refuses."""
    raise ToolRefused("not allowed", hint="try later")


ECHO = ChatTool(echo_user, label="Echo", shows_card=True)
REFUSE = ChatTool(refuse, label="Refuse", shows_card=True)


def _ctx() -> ToolContext:
    user: Any = SimpleNamespace(id="u-real", username="jane", role="qa")
    return ToolContext(
        session=None,  # type: ignore[arg-type]  # the stand-in tools never touch the database
        user=user,
        conversation_id="c-real",
        turn_started_at=datetime.now(UTC),
    )


async def _run(
    monkeypatch: pytest.MonkeyPatch, model: ScriptedModel, tools: list[ChatTool]
) -> list[SupervisorEvent]:
    install(monkeypatch, model)
    return [
        event
        async for event in supervisor.run_turn(
            provider="ollama", messages=[HumanMessage("hi")], ctx=_ctx(), tools=tools
        )
    ]


async def test_a_plain_reply_is_text_then_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    events = await _run(monkeypatch, ScriptedModel(replies=[ai("Hello there")]), [ECHO])

    assert events == [TextChunk("Hello there"), Answer("Hello there")]


async def test_a_tool_call_is_announced_run_and_its_result_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ScriptedModel(replies=[ai("", ("echo_user", {"note": "x"})), ai("Done.")])

    events = await _run(monkeypatch, model, [ECHO])

    started, finished, text, answer = events
    assert started == ToolStarted("echo_user", "Echo")
    assert isinstance(finished, ToolFinished) and finished.name == "echo_user"
    assert json.loads(finished.result)["note"] == "x"
    assert (text, answer) == (TextChunk("Done."), Answer("Done."))


async def test_the_context_comes_from_the_request_never_from_the_models_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The model tries to say who it is; the tool still sees the request's user and conversation."""

    forged = {"note": "x", "user": "u-forged", "conversation": "c-forged", "runtime": "forged"}
    model = ScriptedModel(replies=[ai("", ("echo_user", forged)), ai("ok")])

    events = await _run(monkeypatch, model, [ECHO])

    finished = next(e for e in events if isinstance(e, ToolFinished))
    seen = json.loads(finished.result)
    assert (seen["user"], seen["conversation"]) == ("u-real", "c-real")


async def test_a_successful_card_tool_carries_its_result_as_a_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ScriptedModel(replies=[ai("", ("echo_user", {"note": "x"})), ai("ok")])

    events = await _run(monkeypatch, model, [ECHO])

    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert finished.card == {"note": "x", "user": "u-real", "conversation": "c-real"}


async def test_a_refusal_has_no_card_and_reaches_the_model_with_its_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ScriptedModel(replies=[ai("", "refuse"), ai("I can't do that.")])

    events = await _run(monkeypatch, model, [REFUSE])

    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert finished.card is None
    shown = [m for m in model.seen[-1] if isinstance(m, ToolMessage)]
    assert json.loads(str(shown[0].content)) == {"error": "not allowed", "hint": "try later"}


async def test_a_tool_without_a_card_never_produces_one(monkeypatch: pytest.MonkeyPatch) -> None:
    plain = ChatTool(echo_user, label="Echo")
    model = ScriptedModel(replies=[ai("", ("echo_user", {})), ai("ok")])

    events = await _run(monkeypatch, model, [plain])

    assert next(e for e in events if isinstance(e, ToolFinished)).card is None


async def test_the_model_is_given_exactly_the_tools_passed_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ScriptedModel(replies=[ai("hi")])

    await _run(monkeypatch, model, [ECHO])

    assert model.offered == ["echo_user"]


async def test_a_call_for_a_tool_it_was_not_given_is_not_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ScriptedModel(replies=[ai("", "refuse"), ai("ok")])

    events = await _run(monkeypatch, model, [ECHO])  # REFUSE was never offered

    [result] = [m for m in model.seen[-1] if isinstance(m, ToolMessage)]
    assert result.status == "error"
    assert not any(isinstance(e, ToolFinished) and e.card for e in events)


async def test_a_model_that_never_stops_calling_tools_is_cut_off_after_the_round_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forever(messages: list[BaseMessage]) -> Any:
        return ai("", ("echo_user", {}))

    model = always(forever)

    events = await _run(monkeypatch, model, [ECHO])

    assert events[-1] == Answer(ROUNDS_EXHAUSTED, rounds_exhausted=True)
    assert len(model.seen) == settings.chat_tool_max_rounds


async def test_a_model_failure_propagates_for_the_caller_to_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeError, match="upstream down"):
        await _run(monkeypatch, ScriptedModel(replies=[RuntimeError("upstream down")]), [ECHO])
