"""The chat models' request/response debug logging (app/shared/config/llm.py's LlmDebugLogger),
gated behind the DEBUG log level - see app/shared/config/logging_config.py."""

import logging

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from app.shared.config import llm
from app.shared.config.llm import LlmDebugLogger, build_chat_model
from app.shared.config.settings import settings
from tests.chat._llm import ScriptedModel, ai


def _model() -> ScriptedModel:
    return ScriptedModel(replies=[ai("Hello from the model")], callbacks=[LlmDebugLogger()])


async def test_the_request_and_response_are_logged_at_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=llm.logger.name)

    await _model().ainvoke([SystemMessage("be brief"), HumanMessage("hi")])

    records = [r for r in caplog.records if r.name == llm.logger.name]
    request = next(r for r in records if hasattr(r, "payload"))
    assert request.payload["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "human", "content": "hi"},
    ]
    response = next(r for r in records if hasattr(r, "content"))
    assert response.content == "Hello from the model"


async def test_nothing_is_logged_at_the_default_info_level(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=llm.logger.name)

    await _model().ainvoke([HumanMessage("hi")])

    assert [r for r in caplog.records if r.name == llm.logger.name] == []


def test_every_model_the_factory_builds_carries_the_debug_logger() -> None:
    model = build_chat_model("ollama")

    callbacks = model.callbacks
    assert isinstance(callbacks, list)
    assert any(isinstance(cb, LlmDebugLogger) for cb in callbacks)


def test_the_factory_builds_the_model_the_conversation_provider_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ollama_model", "gemma4:12b")
    monkeypatch.setattr(settings, "ollama_base_url", "http://ollama.test:11434")
    monkeypatch.setattr(settings, "openai_model", "gpt-4o-mini")
    monkeypatch.setattr(settings, "openai_base_url", "http://litellm.test:4000/v1")
    monkeypatch.setattr(settings, "openai_api_key", "sk-proxy")

    local = build_chat_model("ollama")
    hosted = build_chat_model("openai")

    assert (local.model, local.base_url) == ("gemma4:12b", "http://ollama.test:11434")  # type: ignore[attr-defined]
    # OpenAI always goes through the gateway, never api.openai.com directly
    assert hosted.model_name == "gpt-4o-mini"  # type: ignore[attr-defined]
    assert hosted.openai_api_base == "http://litellm.test:4000/v1"  # type: ignore[attr-defined]


def test_the_agents_own_model_follows_agent_llm_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agent_llm_provider", "ollama")
    monkeypatch.setattr(settings, "agent_llm_model", "gemma4:26b")

    model = build_chat_model()

    assert model.model == "gemma4:26b"  # type: ignore[attr-defined]
