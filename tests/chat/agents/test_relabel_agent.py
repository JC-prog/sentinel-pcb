"""The relabel agent: a reviewer says the model's defect label on a Case is wrong, the agent proposes
the correction, and only a LATER chat turn can confirm it - which records the correction on the
Case and queues the retraining ticket. The turn rule is enforced in code, so it is tested at the
service and again end to end through the supervisor agent."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from langchain_core.messages import HumanMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents import supervisor
from app.chat.agents.relabel_agent import CONFIRM_RELABEL, RELABEL_CASE
from app.chat.agents.supervisor import ToolFinished
from app.chat.db.models import Case, Conversation
from app.chat.services.cases import create_case
from app.shared import inference
from app.shared.db.models import RetrainingTicket, User, UserRole
from app.shared.inference import InferenceError, ModelInfo
from tests.chat._llm import ScriptedModel, ai
from tests.chat.agents._helpers import call, tool_context
from tests.shared._modelops_helpers import make_case, make_user

MODEL = "pcb_body_defect"
LABELS = ["Golden", "MissingPart", "Tombstone"]


@pytest.fixture(autouse=True)
def _served_models(monkeypatch: pytest.MonkeyPatch) -> None:
    async def list_models() -> list[ModelInfo]:
        return [
            ModelInfo(
                name=MODEL,
                version="JcProg/body@v1",
                loaded_at=datetime.now(UTC),
                labels=LABELS,
                input_size=(224, 224),
            )
        ]

    monkeypatch.setattr(inference, "list_models", list_models)


def _ctx(session: AsyncSession, user: User, case: Case, turn_started_at: datetime) -> Any:
    return tool_context(
        session, user, conversation_id=case.conversation_id, turn_started_at=turn_started_at
    )


async def _propose(
    session: AsyncSession, user: User, case: Case, **kwargs: Any
) -> tuple[dict[str, Any], datetime]:
    """Proposes in 'turn one'; returns the result and when that turn began."""

    turn_one = datetime.now(UTC) - timedelta(seconds=1)
    args = {"case_number": case.case_number, "correct_label": "Golden", "reason": "it is fine"}
    args.update(kwargs)
    return await call(RELABEL_CASE, _ctx(session, user, case, turn_one), **args), turn_one


async def _confirm(
    session: AsyncSession, user: User, case: Case, turn_started_at: datetime, **kwargs: Any
) -> dict[str, Any]:
    args = {"case_number": case.case_number, **kwargs}
    return await call(CONFIRM_RELABEL, _ctx(session, user, case, turn_started_at), **args)


def _next_turn() -> datetime:
    return datetime.now(UTC) + timedelta(seconds=1)


async def _tickets(session: AsyncSession) -> list[RetrainingTicket]:
    return list((await session.scalars(select(RetrainingTicket))).all())


# --- propose ------------------------------------------------------------------------------------


async def test_a_proposal_saves_nothing_but_waits_on_the_case(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    case = await make_case(db_async_session, user)

    result, _ = await _propose(db_async_session, user, case)

    assert result["status"] == "awaiting_confirmation"
    assert result["model_label"] == "MissingPart"
    assert result["proposed_label"] == "Golden"
    await db_async_session.refresh(case)
    assert case.pending_label == "Golden"
    assert case.corrected_label is None
    assert await _tickets(db_async_session) == []


async def test_the_label_is_matched_to_the_models_own_spelling(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    case = await make_case(db_async_session, user)

    result, _ = await _propose(db_async_session, user, case, correct_label="missing_part ")
    assert "nothing to correct" in result["error"]  # same label, whatever the spelling

    result, _ = await _propose(db_async_session, user, case, correct_label="tomb stone")

    assert result["proposed_label"] == "Tombstone"


async def test_a_label_the_model_cannot_output_is_rejected_with_the_valid_ones(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    case = await make_case(db_async_session, user)

    result, _ = await _propose(db_async_session, user, case, correct_label="Scratch")

    assert "not a label" in result["error"]
    assert result["valid_labels"] == LABELS
    await db_async_session.refresh(case)
    assert case.pending_label is None


async def test_the_label_is_never_trusted_unchecked_when_the_service_is_down(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def down() -> list[ModelInfo]:
        raise InferenceError("service down")

    monkeypatch.setattr(inference, "list_models", down)
    user = await make_user(db_async_session)
    case = await make_case(db_async_session, user)

    result, _ = await _propose(db_async_session, user, case)

    assert "cannot check the label" in result["error"]
    await db_async_session.refresh(case)
    assert case.pending_label is None


async def test_a_label_and_a_reason_are_both_required(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)
    case = await make_case(db_async_session, user)

    no_label, _ = await _propose(db_async_session, user, case, correct_label=" ")
    no_reason, _ = await _propose(db_async_session, user, case, reason="")

    assert "correct_label is required" in no_label["error"]
    assert "reason is required" in no_reason["error"]


async def test_correcting_to_what_the_model_already_said_is_refused(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    case = await make_case(db_async_session, user)

    result, _ = await _propose(db_async_session, user, case, correct_label="MissingPart")

    assert "nothing to correct" in result["error"]


async def test_a_case_with_no_defect_classification_cannot_be_relabelled(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    conversation = Conversation(user_id=user.id)
    db_async_session.add(conversation)
    await db_async_session.commit()
    case = await create_case(
        db_async_session,
        created_by_user_id=user.id,
        conversation_id=conversation.id,
        board_id="b",
        component_ref="U1",
        package=None,
        feature=None,
        issue_symptom=None,
        image_id="x.png",
        inspection_xml_id=None,
        region="Body",
        region_confidence=0.4,
        defect_model=None,
        defect_label=None,
        defect_confidence=None,
        defect_scores={},
        measurement_validation=None,
        observations=[],
        status="review_required",
    )

    result, _ = await _propose(db_async_session, user, case)

    assert "no defect classification" in result["error"]


async def test_without_a_case_number_it_uses_the_latest_case_in_the_conversation(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    case = await make_case(db_async_session, user)

    turn = datetime.now(UTC) - timedelta(seconds=1)
    result = await call(
        RELABEL_CASE,
        _ctx(db_async_session, user, case, turn),
        correct_label="Golden",
        reason="false positive",
    )

    assert result["case_number"] == case.case_number


async def test_proposing_again_replaces_the_pending_proposal(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    case = await make_case(db_async_session, user)

    await _propose(db_async_session, user, case, correct_label="Golden")
    await _propose(db_async_session, user, case, correct_label="Tombstone", reason="actually")

    await db_async_session.refresh(case)
    assert (case.pending_label, case.pending_reason) == ("Tombstone", "actually")


# --- confirm ------------------------------------------------------------------------------------


async def test_confirming_in_a_later_turn_records_the_correction_and_queues_the_ticket(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    case = await make_case(db_async_session, user)  # MissingPart, pcb_body_defect@JcProg/body@v1
    await _propose(db_async_session, user, case, reason="it is a golden part")

    result = await _confirm(db_async_session, user, case, _next_turn())

    assert result["status"] == "relabelled"
    assert result["corrected_label"] == "Golden"
    await db_async_session.refresh(case)
    assert case.corrected_label == "Golden"
    assert case.corrected_by_user_id == user.id
    assert case.corrected_at is not None
    assert case.correction_reason == "it is a golden part"
    assert case.defect_label == "MissingPart"  # what the model said is never overwritten
    assert case.pending_label is None

    (ticket,) = await _tickets(db_async_session)
    assert ticket.case_id == case.id
    assert (ticket.observed_label, ticket.correct_label) == ("MissingPart", "Golden")
    assert (ticket.model_name, ticket.model_version) == (MODEL, "JcProg/body@v1")
    assert ticket.reason == "it is a golden part"
    assert ticket.status == "open"


async def test_it_cannot_be_confirmed_in_the_same_turn_it_was_proposed(
    db_async_session: AsyncSession,
) -> None:
    """The model cannot propose and confirm in one breath - the user has to have had a turn."""

    user = await make_user(db_async_session)
    case = await make_case(db_async_session, user)
    _, turn_one = await _propose(db_async_session, user, case)

    result = await _confirm(db_async_session, user, case, turn_one)

    assert "has not confirmed yet" in result["error"]
    await db_async_session.refresh(case)
    assert case.corrected_label is None
    assert case.pending_label == "Golden"  # still waiting
    assert await _tickets(db_async_session) == []


async def test_there_is_nothing_to_confirm_without_a_proposal(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    case = await make_case(db_async_session, user)

    result = await _confirm(db_async_session, user, case, _next_turn())

    assert "no relabel waiting" in result["error"]


async def test_only_the_user_who_proposed_can_confirm(db_async_session: AsyncSession) -> None:
    proposer = await make_user(db_async_session)
    someone_else = await make_user(db_async_session, UserRole.QA)
    case = await make_case(db_async_session, proposer)
    await _propose(db_async_session, proposer, case)

    result = await _confirm(db_async_session, someone_else, case, _next_turn())

    assert "proposed by someone else" in result["error"]
    assert await _tickets(db_async_session) == []


async def test_a_confirmed_proposal_cannot_be_confirmed_twice(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    case = await make_case(db_async_session, user)
    await _propose(db_async_session, user, case)
    await _confirm(db_async_session, user, case, _next_turn())

    again = await _confirm(db_async_session, user, case, _next_turn())

    assert "no relabel waiting" in again["error"]
    assert len(await _tickets(db_async_session)) == 1


async def test_a_failed_ticket_leaves_the_case_uncorrected(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The correction and its ticket land together or not at all."""

    async def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("db hiccup")

    monkeypatch.setattr("app.chat.agents.relabel_agent.recorder.ticket_repo.create_ticket", boom)
    user = await make_user(db_async_session)
    case = await make_case(db_async_session, user)
    await _propose(db_async_session, user, case)

    result = await _confirm(db_async_session, user, case, _next_turn())

    assert "failed unexpectedly" in result["error"]  # logged, and the model told only that
    await db_async_session.refresh(case)
    assert case.corrected_label is None
    assert await _tickets(db_async_session) == []


# --- through the real supervisor (LangGraph injects the context) ------------------------------


async def _turn(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    user: User,
    case: Case,
    turn_started_at: datetime,
    *replies: Any,
) -> list[ToolFinished]:
    """One chat turn driven by a scripted model; returns each tool call's result."""

    model = ScriptedModel(replies=list(replies))
    monkeypatch.setattr(supervisor, "build_chat_model", lambda *a, **k: model)
    ctx = _ctx(session, user, case, turn_started_at)
    return [
        event
        async for event in supervisor.run_turn(
            provider="ollama",
            messages=[HumanMessage("it should be golden")],
            ctx=ctx,
            tools=[RELABEL_CASE, CONFIRM_RELABEL],
        )
        if isinstance(event, ToolFinished)
    ]


async def test_the_turn_rule_holds_end_to_end_through_the_supervisor(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model that proposes and confirms in one turn is refused; confirming next turn works."""

    user = await make_user(db_async_session)
    case = await make_case(db_async_session, user)
    propose = ("relabel_case", {"correct_label": "Golden", "reason": "false positive"})

    first = await _turn(
        monkeypatch,
        db_async_session,
        user,
        case,
        datetime.now(UTC) - timedelta(seconds=1),
        ai("", propose),
        ai("", "confirm_relabel"),
        ai("Shall I record Golden?"),
    )
    second = await _turn(
        monkeypatch,
        db_async_session,
        user,
        case,
        _next_turn(),
        ai("", "confirm_relabel"),
        ai("Done."),
    )

    proposed, same_turn = (json.loads(f.result) for f in first)
    (later,) = (json.loads(f.result) for f in second)
    assert proposed["status"] == "awaiting_confirmation"
    assert first[0].card is not None  # the proposal is shown to the user as a card
    assert "has not confirmed yet" in same_turn["error"]
    assert first[1].card is None  # a refusal is explained in words, not shown as a card
    assert later["status"] == "relabelled"
    assert len(await _tickets(db_async_session)) == 1
