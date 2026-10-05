"""Offline check of the eval harness itself (scripted fake model, no LLM; runs in the normal suite)."""

from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from tests.evals.harness import Case, Trace, load_cases, run_case, score


class _ScriptedModel(BaseChatModel):
    replies: list[AIMessage]

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self.replies.pop(0))])

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:  # tool schemas are irrelevant here
        return self


def _script(monkeypatch: pytest.MonkeyPatch, *replies: AIMessage) -> None:
    def build(*args: Any, **kwargs: Any) -> BaseChatModel:
        return _ScriptedModel(replies=list(replies))

    monkeypatch.setattr("app.chat.agents.supervisor.build_chat_model", build)


def _call(name: str, args: dict[str, Any]) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": "call-1"}])


def test_cases_file_is_valid() -> None:
    cases = load_cases()
    assert cases
    assert len({c.id for c in cases}) == len(cases)


async def test_run_case_records_calls_and_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    _script(
        monkeypatch,
        _call("relabel_case", {"case_number": "CASE-000012", "correct_label": "short", "reason": "QA says so"}),
        AIMessage(content="Confirm?"),
    )
    case = Case(
        id="t",
        message="CASE-000012 is a short",
        expect_tools=frozenset({"relabel_case"}),
        forbid_tools=frozenset({"confirm_relabel"}),
        expect_args={"relabel_case": {"case_number": "case-000012"}},
    )

    trace = await run_case(case, provider="openai")

    assert trace.called == ["relabel_case"]
    assert trace.answer == "Confirm?"
    assert score(case, trace).passed


def test_score_flags_each_kind_of_problem() -> None:
    case = Case(
        id="t",
        message="m",
        expect_tools=frozenset({"inspect_image"}),
        forbid_tools=frozenset({"confirm_relabel"}),
    )
    trace = Trace(called=["confirm_relabel", "get_drift_summary"])

    verdict = score(case, trace)

    assert not verdict.passed
    assert len(verdict.problems) == 3
