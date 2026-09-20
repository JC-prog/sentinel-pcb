"""The inspection agent's LLM-driven pass (react.py) and how inspect_image combines it with the
deterministic pipeline. The chat model is a scripted fake; the inference service is a mocked
httpx transport, as in test_inspect_image_tool.py.

What these pin down: the LLM can drive the steps and its summary is returned; out-of-order calls are
refused by the policy engine; and whatever the LLM does or fails to do, the verdict and the
persisted Case come from the deterministic rules.
"""

import json
from collections.abc import Sequence
from typing import Any

import httpx
import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.tools import BaseTool
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.inspection_agent import InspectImageTool
from app.shared.config.settings import settings
from tests.chat.agents.test_inspect_image_tool import (
    _VALID_IMAGE,
    _confident_handler,
    _fetch_case,
    _make_conversation,
    _make_user,
    _mock_async_client,
    _run_create_case,
    _uncertain_region_handler,
)

BUILD_MODEL = "app.chat.agents.inspection_agent.react.build_chat_model"


class ScriptedChatModel(FakeMessagesListChatModel):
    """Replays canned AI messages in order (cycling), and accepts bind_tools() like a real model
    would - the fake base class doesn't, and create_agent binds the tools it is given."""

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ScriptedChatModel":
        return self


class BrokenChatModel(FakeMessagesListChatModel):
    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "BrokenChatModel":
        return self

    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
        raise RuntimeError("llm is down")


def _calls(*names: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": {}, "id": f"call-{i}-{name}"} for i, name in enumerate(names)
        ],
    )


def _say(text: str) -> AIMessage:
    return AIMessage(content=text)


def _script(*messages: BaseMessage) -> ScriptedChatModel:
    return ScriptedChatModel(responses=list(messages))


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "inference_base_url", "http://inference.test:8001")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "inspection_agent_llm_enabled", True)


def _record_inference_calls(
    monkeypatch: pytest.MonkeyPatch, handler: Any = _confident_handler
) -> list[str]:
    """Which model each /classify request named, in order."""

    models: list[str] = []

    def recording(request: httpx.Request) -> httpx.Response:
        body = request.content.decode(errors="ignore")
        models.append("pcb_region" if "pcb_region" in body else "defect")
        return handler(request)  # type: ignore[no-any-return]

    _mock_async_client(monkeypatch, recording)
    return models


async def _inspect(session: AsyncSession, **overrides: Any) -> dict[str, Any]:
    user = await _make_user(session)
    conversation = await _make_conversation(session, user)
    return await _run_create_case(session, user, conversation, **overrides)


async def test_the_llm_drives_the_steps_and_its_summary_is_returned(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = _script(
        _calls("verify_image"),
        _calls("lookup_golden_image"),
        _calls("classify_region"),
        _calls("classify_defect"),
        _say("Body region; MissingPart at 80% confidence."),
    )
    monkeypatch.setattr(BUILD_MODEL, lambda: model)
    inference_calls = _record_inference_calls(monkeypatch)

    result = await _inspect(db_async_session)

    assert result["inspection_summary"] == "Body region; MissingPart at 80% confidence."
    assert inference_calls == ["pcb_region", "defect"]  # each classifier ran exactly once
    assert result["final_decision"] == "ACCEPTED"
    assert result["defect_label"] == "MissingPart"
    # the steps the LLM ran are not repeated by the deterministic pass
    assert sum("Image verification" in o for o in result["observations"]) == 1
    case = await _fetch_case(db_async_session, result["case_number"])
    assert case.status == "accepted"


async def test_out_of_order_calls_are_refused_and_the_pipeline_completes_the_run(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The LLM jumps straight to classify_defect: the policy engine refuses it (no execution, so no
    inference request), the LLM gives up, and the deterministic pass runs everything in order."""

    model = _script(_calls("classify_defect"), _say("I could not classify it."))
    monkeypatch.setattr(BUILD_MODEL, lambda: model)
    inference_calls = _record_inference_calls(monkeypatch)

    result = await _inspect(db_async_session)

    assert inference_calls == ["pcb_region", "defect"]  # in order, and only from the pipeline
    assert result["final_decision"] == "ACCEPTED"
    assert result["inspection_summary"] == "I could not classify it."


async def test_the_verdict_comes_from_the_rules_not_from_what_the_llm_says(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Region confidence is below the threshold, so the deterministic finalize step must call
    this REVIEW_REQUIRED - however cheerfully the LLM describes it."""

    model = _script(
        _calls("verify_image"),
        _calls("lookup_golden_image"),
        _calls("classify_region"),
        _say("Looks like an ordinary, perfectly fine Body component."),
    )
    monkeypatch.setattr(BUILD_MODEL, lambda: model)
    _record_inference_calls(monkeypatch, _uncertain_region_handler)

    result = await _inspect(db_async_session)

    assert result["final_decision"] == "REVIEW_REQUIRED"
    assert result["review_required"] is True
    assert "perfectly fine" in result["inspection_summary"]
    case = await _fetch_case(db_async_session, result["case_number"])
    assert case.status == "review_required"


async def test_a_failing_llm_falls_back_to_the_deterministic_pipeline(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(BUILD_MODEL, lambda: BrokenChatModel(responses=[_say("unused")]))
    inference_calls = _record_inference_calls(monkeypatch)

    result = await _inspect(db_async_session)

    assert result["inspection_summary"] is None
    assert result["final_decision"] == "ACCEPTED"
    assert inference_calls == ["pcb_region", "defect"]
    assert any("LLM-driven inspection pass failed" in o for o in result["observations"])


async def test_a_model_that_never_stops_calling_tools_is_cut_off(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One response, replayed forever: verify_image over and over. The tool-call budget ends it,
    and the pipeline still finishes the inspection."""

    monkeypatch.setattr(BUILD_MODEL, lambda: _script(_calls("verify_image")))
    inference_calls = _record_inference_calls(monkeypatch)

    result = await _inspect(db_async_session)

    assert result["inspection_summary"] is None
    assert result["final_decision"] == "ACCEPTED"
    assert inference_calls == ["pcb_region", "defect"]


async def test_the_llm_pass_is_skipped_when_disabled(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    def must_not_be_called() -> BaseChatModel:
        raise AssertionError("the LLM pass should not have run")

    monkeypatch.setattr(BUILD_MODEL, must_not_be_called)
    monkeypatch.setattr(settings, "inspection_agent_llm_enabled", False)
    _record_inference_calls(monkeypatch)

    result = await _inspect(db_async_session)

    assert result["inspection_summary"] is None
    assert result["final_decision"] == "ACCEPTED"


async def test_the_llm_pass_is_skipped_without_an_openai_key(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    def must_not_be_called() -> BaseChatModel:
        raise AssertionError("the LLM pass should not have run")

    monkeypatch.setattr(BUILD_MODEL, must_not_be_called)
    monkeypatch.setattr(settings, "openai_api_key", "")
    _record_inference_calls(monkeypatch)

    result = await _inspect(db_async_session)

    assert result["inspection_summary"] is None
    assert result["final_decision"] == "ACCEPTED"


async def test_the_tools_offered_to_the_llm_are_the_inspection_steps(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The LLM gets exactly the inspection steps (plus list_models) - nothing that persists a
    Case, decides a verdict, or reaches another agent."""

    from app.chat.agents.inspection_agent import react

    seen: list[BaseTool] = []
    real_build_tools = react.build_tools

    def spying_build_tools(*args: Any, **kwargs: Any) -> list[BaseTool]:
        seen.extend(real_build_tools(*args, **kwargs))
        return seen

    monkeypatch.setattr(react, "build_tools", spying_build_tools)
    monkeypatch.setattr(BUILD_MODEL, lambda: _script(_say("nothing to do")))
    _record_inference_calls(monkeypatch)

    await _inspect(db_async_session)

    assert {t.name for t in seen} == {
        "verify_image",
        "lookup_golden_image",
        "validate_measurements",
        "align_and_check_quality",
        "classify_region",
        "classify_defect",
        "list_models",
    }


async def test_board_and_component_are_optional(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "What defect is this?" with nothing else known still inspects and saves a Case."""

    monkeypatch.setattr(settings, "openai_api_key", "")
    _record_inference_calls(monkeypatch)
    user = await _make_user(db_async_session)
    conversation = await _make_conversation(db_async_session, user)

    result = json.loads(
        await InspectImageTool().run(
            session=db_async_session,
            image_bytes=_VALID_IMAGE,
            image_name="board.png",
            username=user.username,
            user_id=user.id,
            conversation_id=conversation.id,
            question="what defect does this have?",
        )
    )

    assert result["final_decision"] == "ACCEPTED"
    case = await _fetch_case(db_async_session, result["case_number"])
    assert (case.board_id, case.component_ref) == ("unknown", "unknown")
