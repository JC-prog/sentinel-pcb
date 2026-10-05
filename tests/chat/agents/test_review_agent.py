"""The review agent: a reviewer approves or overrides a case flagged REVIEW_REQUIRED. The agent
proposes the decision, and only a LATER chat turn can confirm it - which moves the case to APPROVED
or OVERRIDDEN. Tested at the tools and again end to end through the supervisor."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from langchain_core.messages import HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents import supervisor
from app.chat.agents.review_agent import CONFIRM_REVIEW, REVIEW_CASE
from app.chat.agents.supervisor import ToolFinished
from app.chat.db.models import Case, CaseStatus
from app.chat.services.drift import window_stats
from app.shared.db.models import User, UserRole
from tests.chat._llm import ScriptedModel, ai
from tests.chat.agents._helpers import call, tool_context
from tests.shared._modelops_helpers import make_case, make_user


async def _flagged_case(session: AsyncSession, user: User) -> Case:
    """A case the pipeline flagged for review (make_case builds an accepted one)."""

    case = await make_case(session, user)
    case.status = CaseStatus.REVIEW_REQUIRED
    await session.commit()
    return case


def _ctx(session: AsyncSession, user: User, case: Case, turn_started_at: datetime) -> Any:
    return tool_context(
        session, user, conversation_id=case.conversation_id, turn_started_at=turn_started_at
    )


def _next_turn() -> datetime:
    return datetime.now(UTC) + timedelta(seconds=1)


async def _propose(
    session: AsyncSession, user: User, case: Case, **kwargs: Any
) -> tuple[dict[str, Any], datetime]:
    """Proposes in 'turn one'; returns the result and when that turn began."""

    turn_one = datetime.now(UTC) - timedelta(seconds=1)
    args = {"case_number": case.case_number, "decision": "override", "note": "just a shadow"}
    args.update(kwargs)
    return await call(REVIEW_CASE, _ctx(session, user, case, turn_one), **args), turn_one


async def _confirm(
    session: AsyncSession, user: User, case: Case, turn_started_at: datetime
) -> dict[str, Any]:
    return await call(
        CONFIRM_REVIEW,
        _ctx(session, user, case, turn_started_at),
        case_number=case.case_number,
    )


# --- propose ------------------------------------------------------------------------------------


async def test_a_proposal_saves_nothing_but_waits_on_the_case(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    case = await _flagged_case(db_async_session, user)

    result, _ = await _propose(db_async_session, user, case)

    assert result["status"] == "awaiting_confirmation"
    assert (result["decision"], result["note"]) == ("override", "just a shadow")
    await db_async_session.refresh(case)
    assert case.pending_resolution == "override"
    assert case.status == CaseStatus.REVIEW_REQUIRED  # unchanged
    assert case.resolved_at is None


async def test_the_decision_is_limited_to_approve_or_override() -> None:
    """The model cannot invent a third outcome - it is not even in the tool's schema."""

    schema = REVIEW_CASE.model_schema()

    assert set(schema["required"]) == {"decision"}
    assert schema["properties"]["decision"]["enum"] == ["approve", "override"]


async def test_only_a_case_awaiting_review_can_be_reviewed(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    accepted = await make_case(db_async_session, user)  # ACCEPTED by the pipeline

    result, _ = await _propose(db_async_session, user, accepted)

    assert "accepted by the pipeline" in result["error"]
    await db_async_session.refresh(accepted)
    assert accepted.pending_resolution is None


async def test_without_a_case_number_it_uses_the_latest_case_in_the_conversation(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    case = await _flagged_case(db_async_session, user)

    turn = datetime.now(UTC) - timedelta(seconds=1)
    result = await call(
        REVIEW_CASE, _ctx(db_async_session, user, case, turn), decision="approve"
    )

    assert result["case_number"] == case.case_number


async def test_proposing_again_replaces_the_pending_proposal(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    case = await _flagged_case(db_async_session, user)

    await _propose(db_async_session, user, case, decision="override")
    await _propose(db_async_session, user, case, decision="approve", note="on reflection")

    await db_async_session.refresh(case)
    assert (case.pending_resolution, case.pending_resolution_note) == ("approve", "on reflection")


# --- confirm ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("decision", "status"), [("approve", CaseStatus.APPROVED), ("override", CaseStatus.OVERRIDDEN)]
)
async def test_confirming_in_a_later_turn_resolves_the_case(
    db_async_session: AsyncSession, decision: str, status: CaseStatus
) -> None:
    user = await make_user(db_async_session)
    case = await _flagged_case(db_async_session, user)
    await _propose(db_async_session, user, case, decision=decision)

    result = await _confirm(db_async_session, user, case, _next_turn())

    assert (result["status"], result["case_status"]) == ("reviewed", status.value)
    await db_async_session.refresh(case)
    assert case.status == status
    assert case.resolved_by_user_id == user.id
    assert case.resolved_at is not None
    assert case.resolution_note == "just a shadow"
    assert case.pending_resolution is None  # the proposal is spent


async def test_it_cannot_be_confirmed_in_the_same_turn_it_was_proposed(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    case = await _flagged_case(db_async_session, user)
    _, turn_one = await _propose(db_async_session, user, case)

    result = await _confirm(db_async_session, user, case, turn_one)

    assert "has not confirmed yet" in result["error"]
    await db_async_session.refresh(case)
    assert case.status == CaseStatus.REVIEW_REQUIRED
    assert case.pending_resolution == "override"  # still waiting


async def test_there_is_nothing_to_confirm_without_a_proposal(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    case = await _flagged_case(db_async_session, user)

    result = await _confirm(db_async_session, user, case, _next_turn())

    assert "no review waiting" in result["error"]


async def test_only_the_user_who_proposed_can_confirm(db_async_session: AsyncSession) -> None:
    proposer = await make_user(db_async_session)
    someone_else = await make_user(db_async_session, UserRole.QA)
    case = await _flagged_case(db_async_session, proposer)
    await _propose(db_async_session, proposer, case)

    result = await _confirm(db_async_session, someone_else, case, _next_turn())

    assert "proposed by someone else" in result["error"]
    await db_async_session.refresh(case)
    assert case.status == CaseStatus.REVIEW_REQUIRED


async def test_a_case_resolved_since_the_proposal_cannot_be_resolved_again(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    case = await _flagged_case(db_async_session, user)
    await _propose(db_async_session, user, case)
    case.status = CaseStatus.APPROVED  # someone else resolved it in the meantime
    await db_async_session.commit()

    result = await _confirm(db_async_session, user, case, _next_turn())

    assert "already approved" in result["error"]


async def test_a_confirmed_proposal_cannot_be_confirmed_twice(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    case = await _flagged_case(db_async_session, user)
    await _propose(db_async_session, user, case)
    await _confirm(db_async_session, user, case, _next_turn())

    again = await _confirm(db_async_session, user, case, _next_turn())

    assert "no review waiting" in again["error"]


# --- drift: an override is the signal the drift numbers watch ---------------------------------


async def test_an_override_counts_in_the_override_rate_drift_watches(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    approved = await _flagged_case(db_async_session, user)
    overridden = await _flagged_case(db_async_session, user)
    for case, decision in ((approved, "approve"), (overridden, "override")):
        await _propose(db_async_session, user, case, decision=decision)
        await _confirm(db_async_session, user, case, _next_turn())

    now = datetime.now(UTC)
    stats = await window_stats(
        db_async_session, "pcb_body_defect", since=now - timedelta(days=1), until=now + timedelta(days=1)
    )

    assert (stats.approved, stats.overridden) == (1, 1)
    assert stats.override_rate == 0.5


# --- end to end through the supervisor ----------------------------------------------------------


async def _turn(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    user: User,
    case: Case,
    turn_started_at: datetime,
    *replies: Any,
) -> list[ToolFinished]:
    model = ScriptedModel(replies=list(replies))
    monkeypatch.setattr(supervisor, "build_chat_model", lambda *a, **k: model)
    return [
        event
        async for event in supervisor.run_turn(
            provider="ollama",
            messages=[HumanMessage("override it")],
            ctx=_ctx(session, user, case, turn_started_at),
            tools=[REVIEW_CASE, CONFIRM_REVIEW],
        )
        if isinstance(event, ToolFinished)
    ]


async def test_the_turn_rule_holds_end_to_end_through_the_supervisor(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model that proposes and confirms in one turn is refused; confirming next turn works."""

    user = await make_user(db_async_session)
    case = await _flagged_case(db_async_session, user)

    first = await _turn(
        monkeypatch,
        db_async_session,
        user,
        case,
        datetime.now(UTC) - timedelta(seconds=1),
        ai("", ("review_case", {"decision": "override", "note": "false positive"})),
        ai("", "confirm_review"),
        ai("Shall I override it?"),
    )
    second = await _turn(
        monkeypatch, db_async_session, user, case, _next_turn(), ai("", "confirm_review"), ai("Done.")
    )

    proposed, same_turn = (json.loads(f.result) for f in first)
    (later,) = (json.loads(f.result) for f in second)
    assert proposed["status"] == "awaiting_confirmation"
    assert first[0].card is not None  # the proposal is shown to the user as a card
    assert "has not confirmed yet" in same_turn["error"]
    assert first[1].card is None  # a refusal is explained in words, not shown as a card
    assert later["case_status"] == "overridden"
    await db_async_session.refresh(case)
    assert case.status == CaseStatus.OVERRIDDEN
