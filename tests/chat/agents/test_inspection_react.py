"""The inspect agent's LLM-driven pass (react.py) and the step-order policy it is held to. The model
is a scripted stand-in for the chat model - no network, no real LLM - and the classifiers are faked
at the inference-client boundary, so only the agent's own behaviour is under test."""

import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.inspection_agent import classifier, pipeline, policy, react
from app.chat.agents.inspection_agent.state import (
    InspectionRequest,
    InspectionRun,
    Stage,
)
from app.chat.db.models import Case, CaseDraft, Conversation
from app.shared.config.settings import settings
from app.shared.db.models import User, UserRole
from app.shared.inference import Classification, InferenceError
from tests.chat._llm import ScriptedModel, ai
from tests.chat.agents._helpers import VALID_IMAGE


def _request(image: bytes = VALID_IMAGE, **fields: Any) -> InspectionRequest:
    return InspectionRequest(
        image_bytes=image,
        image_name="board.png",
        username="jane",
        user_id="u1",
        conversation_id="c1",
        board_id="BOARD-1",
        component_ref="U7",
        **fields,
    )


def _classification(model: str, label: str, confidence: float) -> Classification:
    return Classification(
        model=model,
        model_version=f"{model}@v1",
        username="jane",
        label=label,
        index=0,
        confidence=confidence,
        scores={label: confidence, "Other": round(1 - confidence, 4)},
        request_id="r",
    )


@pytest.fixture
def confident_inference(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fakes the inference client: a confident Body region and a confident MissingPart defect.
    Returns the list of models that were asked, in order."""

    asked: list[str] = []

    async def fake_classify(*, model: str, **_: Any) -> Classification:
        asked.append(model)
        if model == "pcb_region":
            return _classification(model, "Body", 0.95)
        return _classification(model, "MissingPart", 0.9)

    monkeypatch.setattr(classifier, "classify", fake_classify)
    return asked


# --- scripted model -----------------------------------------------------------------------------


def _script(monkeypatch: pytest.MonkeyPatch, *replies: Any) -> ScriptedModel:
    model = ScriptedModel(replies=list(replies), seen=[], offered=[])
    monkeypatch.setattr(react, "build_chat_model", lambda: model)
    return model


def _tool_results(model: ScriptedModel) -> list[dict[str, Any]]:
    """The tool results the model was last shown, parsed."""

    return [json.loads(str(m.content)) for m in model.seen[-1] if isinstance(m, ToolMessage)]


# --- policy -------------------------------------------------------------------------------------


def test_nothing_but_verification_may_run_first() -> None:
    run = InspectionRun(request=_request())

    assert policy.check(policy.VERIFY_IMAGE, run)[0]
    for step in (policy.VALIDATE_MEASUREMENTS, policy.CLASSIFY_REGION, policy.CLASSIFY_DEFECT):
        allowed, reason = policy.check(step, run)
        assert not allowed
        assert "verify_image" in reason


def test_measurements_are_only_validated_when_an_xml_is_attached() -> None:
    run = InspectionRun(request=_request(), image_quality={"readable": True})

    allowed, reason = policy.check(policy.VALIDATE_MEASUREMENTS, run)

    assert not allowed
    assert "no inspection XML" in reason


def test_a_defect_model_needs_a_confident_region() -> None:
    run = InspectionRun(request=_request(), image_quality={"readable": True})
    assert "classify_region" in policy.check(policy.CLASSIFY_DEFECT, run)[1]

    run.region_uncertain = True
    run.region = Stage(
        model="pcb_region", model_version="v1", label="Body", confidence=0.5, scores={}
    )
    allowed, reason = policy.check(policy.CLASSIFY_DEFECT, run)
    assert not allowed
    assert "not confident enough" in reason


def test_a_step_cannot_run_twice() -> None:
    run = InspectionRun(request=_request(), image_quality={"readable": True})

    assert not policy.check(policy.VERIFY_IMAGE, run)[0]


def test_nothing_runs_after_a_failure() -> None:
    run = InspectionRun(request=_request())
    run.fail("boom")

    allowed, reason = policy.check(policy.VERIFY_IMAGE, run)

    assert not allowed
    assert "boom" in reason


# --- the agent ----------------------------------------------------------------------------------


async def test_the_model_drives_the_steps_and_its_summary_is_returned(
    monkeypatch: pytest.MonkeyPatch, confident_inference: list[str]
) -> None:
    _script(
        monkeypatch,
        ai("", "verify_image"),
        ai("", "classify_region"),
        ai("", "classify_defect"),
        ai("A body defect: MissingPart, 90% confident."),
    )
    run = InspectionRun(request=_request())

    summary = await react.run_react_pass(run)

    assert summary == "A body defect: MissingPart, 90% confident."
    assert confident_inference == ["pcb_region", "pcb_body_defect"]
    assert run.defect is not None and run.defect.label == "MissingPart"


async def test_the_model_is_offered_the_inspection_tools_but_no_way_to_decide_or_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _script(monkeypatch, ai("nothing to do"))

    await react.run_react_pass(InspectionRun(request=_request()))

    assert {"verify_image", "classify_region", "classify_defect", "get_scores"} <= set(model.offered)
    assert not set(model.offered) & {"finalize", "persist_case", "decide_verdict"}


async def test_an_out_of_order_step_is_refused_with_the_reason_and_nothing_runs(
    monkeypatch: pytest.MonkeyPatch, confident_inference: list[str]
) -> None:
    model = _script(monkeypatch, ai("", "classify_defect"), ai("I could not do that."))
    run = InspectionRun(request=_request())

    await react.run_react_pass(run)

    [result] = _tool_results(model)
    assert "not allowed right now" in result["error"]
    assert "verify_image" in result["error"]
    assert confident_inference == []  # the classifier was never reached


async def test_several_tool_calls_in_one_turn_run_one_at_a_time_in_order(
    monkeypatch: pytest.MonkeyPatch, confident_inference: list[str]
) -> None:
    """Some providers batch tool calls. The steps share one run, so the second must see the first's
    result - here classify_region (asked first) is refused because verify_image has not run yet."""

    model = _script(monkeypatch, ai("", "classify_region", "verify_image"), ai("done"))
    run = InspectionRun(request=_request())

    await react.run_react_pass(run)

    refused, verified = _tool_results(model)
    assert "verify_image must run first" in refused["error"]
    assert "readable" in verified["result"][0]
    assert confident_inference == []


async def test_a_failed_step_means_no_summary_and_no_further_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing(**_: Any) -> Classification:
        raise InferenceError("service down")

    monkeypatch.setattr(classifier, "classify", failing)
    model = _script(
        monkeypatch,
        ai("", "verify_image"),
        ai("", "classify_region"),
        ai("", "classify_defect"),
        ai("Sorry, it failed."),
    )
    run = InspectionRun(request=_request())

    summary = await react.run_react_pass(run)

    assert summary is None
    assert run.error is not None and "service down" in run.error
    assert "the inspection already failed" in _tool_results(model)[-1]["error"]  # defect refused


async def test_an_llm_failure_is_recorded_and_never_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _script(monkeypatch, RuntimeError("gateway 401"))
    run = InspectionRun(request=_request())

    summary = await react.run_react_pass(run)

    assert summary is None
    assert run.error is None  # an LLM failure is not an inspection failure
    assert any("ran deterministically" in o for o in run.observations)


async def test_a_model_that_never_stops_calling_tools_is_cut_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _script(monkeypatch, *[ai("", "check_image_quality") for _ in range(60)])
    run = InspectionRun(request=_request())

    summary = await react.run_react_pass(run)

    assert summary is None
    assert any("budget exhausted" in o for o in run.observations)


async def test_read_only_helpers_report_what_the_classifiers_found(
    monkeypatch: pytest.MonkeyPatch, confident_inference: list[str]
) -> None:
    model = _script(
        monkeypatch,
        ai("", "verify_image"),
        ai("", "classify_region"),
        ai("", "classify_defect"),
        ai("", "get_scores"),
        ai("done"),
    )

    await react.run_react_pass(InspectionRun(request=_request()))

    scores = _tool_results(model)[-1]
    assert scores["defect"]["label"] == "MissingPart"
    assert [s["label"] for s in scores["defect"]["scores"]] == ["MissingPart", "Other"]  # best first


async def test_a_provider_that_returns_content_blocks_still_gives_a_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anthropic-style replies carry a list of blocks rather than a string."""

    blocks = AIMessage(content=[{"type": "text", "text": "A missing part."}])
    _script(monkeypatch, blocks)

    summary = await react.run_react_pass(InspectionRun(request=_request()))

    assert summary == "A missing part."


# --- the deterministic half finishes whatever the model skipped ---------------------------------


async def test_the_pipeline_completes_every_step_the_model_left_undone(
    confident_inference: list[str],
) -> None:
    run = InspectionRun(request=_request())

    await pipeline._complete(run)

    assert run.defect is not None
    assert confident_inference == ["pcb_region", "pcb_body_defect"]


async def test_the_pipeline_does_not_repeat_steps_the_model_already_ran(
    monkeypatch: pytest.MonkeyPatch, confident_inference: list[str]
) -> None:
    _script(monkeypatch, ai("", "verify_image"), ai("", "classify_region"), ai("x"))
    run = InspectionRun(request=_request())
    await react.run_react_pass(run)
    assert confident_inference == ["pcb_region"]

    await pipeline._complete(run)

    assert confident_inference == ["pcb_region", "pcb_body_defect"]  # region not asked twice


async def test_the_pipeline_skips_the_defect_model_for_an_uncertain_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[str] = []

    async def unsure(*, model: str, **_: Any) -> Classification:
        asked.append(model)
        return _classification(model, "Body", 0.4)

    monkeypatch.setattr(classifier, "classify", unsure)
    run = InspectionRun(request=_request())

    await pipeline._complete(run)

    assert asked == ["pcb_region"]
    assert run.region_uncertain and run.error is None


async def _user_and_conversation(session: AsyncSession) -> tuple[User, Conversation]:
    user = User(
        username="react-qa",
        email="react-qa@example.com",
        password_hash="x",
        employee_id="EMP-REACT",
        department_shift="Day",
        role=UserRole.QA,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    conversation = Conversation(user_id=user.id)
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return user, conversation


def _llm_on(monkeypatch: pytest.MonkeyPatch, *replies: Any) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "inspection_agent_llm_enabled", True)
    _script(monkeypatch, *replies)


async def test_a_summary_only_model_still_gets_a_draft_from_the_fixed_rules(
    db_async_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    confident_inference: list[str],
) -> None:
    """The model that does no work at all (just talks) cannot change the outcome."""

    _llm_on(monkeypatch, ai("Looks like a missing part."))
    user, conversation = await _user_and_conversation(db_async_session)
    request = replace(_request(), user_id=user.id, conversation_id=conversation.id)

    outcome = await pipeline.run_inspection(db_async_session, request)

    assert outcome.summary == "Looks like a missing part."
    assert outcome.draft is not None and outcome.draft.payload["status"] == "accepted"
    assert confident_inference == ["pcb_region", "pcb_body_defect"]


async def test_a_failed_llm_pass_still_produces_the_deterministic_draft(
    db_async_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    confident_inference: list[str],
) -> None:
    _llm_on(monkeypatch, RuntimeError("gateway down"))
    user, conversation = await _user_and_conversation(db_async_session)
    request = replace(_request(), user_id=user.id, conversation_id=conversation.id)

    outcome = await pipeline.run_inspection(db_async_session, request)

    assert outcome.summary is None
    assert outcome.draft is not None
    # the inspection is parked for the user's yes, not saved as a case
    assert (await db_async_session.scalars(select(Case))).all() == []
    assert len((await db_async_session.scalars(select(CaseDraft))).all()) == 1


async def test_the_llm_pass_is_skipped_without_a_key(
    db_async_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    confident_inference: list[str],
) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "")

    def must_not_be_built() -> Callable[..., Any]:
        raise AssertionError("the LLM pass must not run without a key")

    monkeypatch.setattr(react, "build_chat_model", must_not_be_built)
    user, conversation = await _user_and_conversation(db_async_session)
    request = replace(_request(), user_id=user.id, conversation_id=conversation.id)

    outcome = await pipeline.run_inspection(db_async_session, request)

    assert outcome.draft is not None
    assert outcome.summary is None
