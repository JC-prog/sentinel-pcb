import pytest

from app.agents import router_agent
from app.agents.router_agent import Clarify, Proceed, RouterDecision, classify_intent, route
from app.agents.router_agent import graph as router_graph
from app.config.settings import settings

_TOOLS = [
    {"name": "get_weather", "description": "Gets the weather for a location."},
    {"name": "current_time", "description": "Gets the current time."},
]


@pytest.fixture(autouse=True)
def _router_enabled_with_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """tests/conftest.py disables the router by default (see its own docstring) - this file is
    the one place that re-enables it, matching tests/test_memory.py's pattern for memory_enabled."""

    monkeypatch.setattr(settings, "intent_router_enabled", True)
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")


async def test_classify_intent_picks_a_confident_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        router_graph,
        "_query_router_llm",
        lambda prompt: {"target_tool": "get_weather", "confidence": 0.95, "clarifying_question": None},
    )

    decision = await classify_intent("what's the weather in Tokyo?", has_image=False, candidate_tools=_TOOLS)

    assert decision is not None
    assert decision.target_tool == "get_weather"
    assert decision.confidence == 0.95
    assert decision.clarifying_question is None


async def test_classify_intent_asks_for_clarification_when_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        router_graph,
        "_query_router_llm",
        lambda prompt: {
            "target_tool": None,
            "confidence": 0.2,
            "clarifying_question": "Do you want the weather or the current time?",
        },
    )

    decision = await classify_intent("tell me about Tokyo", has_image=False, candidate_tools=_TOOLS)

    assert decision is not None
    assert decision.confidence < settings.intent_router_confidence_threshold
    assert decision.clarifying_question == "Do you want the weather or the current time?"


async def test_classify_intent_rejects_a_hallucinated_tool_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        router_graph,
        "_query_router_llm",
        lambda prompt: {"target_tool": "not_a_real_tool", "confidence": 0.9, "clarifying_question": None},
    )

    decision = await classify_intent("do something", has_image=False, candidate_tools=_TOOLS)

    assert decision is not None
    assert decision.target_tool is None


async def test_classify_intent_falls_back_to_not_routed_on_llm_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(prompt: str) -> dict[str, object]:
        raise RuntimeError("boom")

    monkeypatch.setattr(router_graph, "_query_router_llm", _raise)

    decision = await classify_intent("anything", has_image=False, candidate_tools=_TOOLS)

    assert decision is None


async def test_classify_intent_skips_the_llm_when_no_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        router_graph,
        "_query_router_llm",
        lambda prompt: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    decision = await classify_intent("anything", has_image=False, candidate_tools=[])

    assert decision is None


async def test_classify_intent_skips_the_llm_when_no_key_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(
        router_graph,
        "_query_router_llm",
        lambda prompt: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    decision = await classify_intent("anything", has_image=False, candidate_tools=_TOOLS)

    assert decision is None


# route() is the policy layer built on top of classify_intent() - these tests mock
# classify_intent() directly (via the router_agent module object, since route() looks it up as a
# module global) rather than going through the LLM, so they only exercise the threshold/narrowing
# policy itself, independent of app/main.py or the HTTP layer.


def _mock_decision(monkeypatch: pytest.MonkeyPatch, decision: RouterDecision | None) -> None:
    async def _fake_classify_intent(
        message: str, *, has_image: bool, candidate_tools: object
    ) -> RouterDecision | None:
        return decision

    monkeypatch.setattr(router_agent, "classify_intent", _fake_classify_intent)


async def test_route_proceeds_unrestricted_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "intent_router_enabled", False)
    monkeypatch.setattr(
        router_agent, "classify_intent", lambda *a, **k: (_ for _ in ()).throw(AssertionError())
    )

    outcome = await route("anything", has_image=False, candidate_tools=_TOOLS)

    assert outcome == Proceed(_TOOLS)


async def test_route_proceeds_unrestricted_when_no_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        router_agent, "classify_intent", lambda *a, **k: (_ for _ in ()).throw(AssertionError())
    )

    outcome = await route("anything", has_image=False, candidate_tools=[])

    assert outcome == Proceed([])


async def test_route_proceeds_unrestricted_when_classify_intent_could_not_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression this guards: classify_intent() returning None (no key, no candidates, or
    an upstream failure) used to be indistinguishable from a genuine high-confidence "no tool
    needed" RouterDecision, so route() cleared every tool instead of falling back to offering
    them all - see classify_intent()'s docstring."""

    _mock_decision(monkeypatch, None)

    outcome = await route("classify this board", has_image=True, candidate_tools=_TOOLS)

    assert outcome == Proceed(_TOOLS)


async def test_route_clarifies_below_the_confidence_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_decision(
        monkeypatch,
        RouterDecision(target_tool=None, confidence=0.2, clarifying_question="Weather or time?"),
    )

    outcome = await route("tell me about Tokyo", has_image=False, candidate_tools=_TOOLS)

    assert outcome == Clarify("Weather or time?")


async def test_route_falls_back_to_a_default_clarifying_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_decision(
        monkeypatch, RouterDecision(target_tool=None, confidence=0.0, clarifying_question=None)
    )

    outcome = await route("tell me about Tokyo", has_image=False, candidate_tools=_TOOLS)

    assert isinstance(outcome, Clarify)
    assert outcome.question


async def test_route_narrows_to_the_confident_pick(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_decision(
        monkeypatch,
        RouterDecision(target_tool="get_weather", confidence=0.95, clarifying_question=None),
    )

    outcome = await route("what's the weather in Tokyo?", has_image=False, candidate_tools=_TOOLS)

    assert outcome == Proceed([_TOOLS[0]])


async def test_route_clears_tools_when_none_are_needed(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_decision(
        monkeypatch, RouterDecision(target_tool=None, confidence=0.99, clarifying_question=None)
    )

    outcome = await route("hi there", has_image=False, candidate_tools=_TOOLS)

    assert outcome == Proceed(None)
