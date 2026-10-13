"""A user's own corrections: reading where each one stands, and taking one back while its ticket is
not in any plan. The ticket and the correction go together, in one commit."""

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.relabel_agent import LIST_MY_CORRECTIONS, WITHDRAW_CORRECTION
from app.chat.db.models import Case
from app.shared.db.models import RetrainingTicket, User, UserRole
from app.shared.modelops import jobs as job_repo
from app.shared.modelops.tickets import create_ticket
from app.shared.modelops.versions import sync_versions
from tests.chat.agents._helpers import call, tool_context
from tests.shared._modelops_helpers import make_case, make_user, model_info

MODEL = "pcb_body_defect"


async def _corrected(session: AsyncSession, user: User) -> tuple[Case, RetrainingTicket]:
    case = await make_case(session, user)
    case.corrected_label = "Golden"
    case.corrected_by_user_id = user.id
    case.correction_reason = "pads are fine"
    await session.commit()
    ticket = await create_ticket(
        session,
        case_id=case.id,
        case_number=case.case_number,
        flagged_by_user_id=user.id,
        reason="pads are fine",
        model_name=MODEL,
        model_version="JcProg/body@v1",
        observed_label="MissingPart",
        correct_label="Golden",
    )
    return case, ticket


def _ctx(session: AsyncSession, user: User, case: Case | None = None) -> Any:
    return tool_context(session, user, conversation_id=case.conversation_id if case else "c1")


async def _other_user(session: AsyncSession) -> User:
    other = User(
        username="other-qa",
        email="other@example.com",
        password_hash="x",
        employee_id="EMP-other",
        role=UserRole.QA,
    )
    session.add(other)
    await session.commit()
    await session.refresh(other)
    return other


async def test_it_lists_the_users_corrections_and_where_each_stands(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    waiting_case, waiting = await _corrected(db_async_session, user)
    in_plan_case, _ = await _corrected(db_async_session, user)
    await sync_versions(db_async_session, [model_info(MODEL, "JcProg/body@v1")])
    job = await job_repo.draft_job(
        db_async_session, model_name=MODEL, created_by_user_id=user.id, rationale="r"
    )
    await db_async_session.refresh(waiting)

    result = await call(LIST_MY_CORRECTIONS, _ctx(db_async_session, user))

    assert result["total"] == 2
    states = {c["case_number"]: c for c in result["corrections"]}
    # both were swept into the one plan the draft made
    assert all("retraining plan (pending approval)" in c["where_it_stands"] for c in states.values())
    assert not any(c["can_withdraw"] for c in states.values())
    assert job.status == "pending_approval"
    assert {waiting_case.case_number, in_plan_case.case_number} == set(states)


async def test_a_correction_not_in_a_plan_can_be_withdrawn(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)
    case, ticket = await _corrected(db_async_session, user)

    listed = await call(LIST_MY_CORRECTIONS, _ctx(db_async_session, user))
    assert listed["corrections"][0]["where_it_stands"] == "waiting for a retraining plan"
    assert listed["corrections"][0]["can_withdraw"] is True

    result = await call(
        WITHDRAW_CORRECTION, _ctx(db_async_session, user, case), case_number=case.case_number
    )

    assert result["status"] == "withdrawn" and result["model_label"] == "MissingPart"
    await db_async_session.refresh(case)
    assert case.corrected_label is None and case.correction_reason is None
    assert case.defect_label == "MissingPart"  # what the model said is untouched
    assert (await db_async_session.scalars(select(RetrainingTicket))).first() is None
    assert ticket.id  # the row is gone, not just hidden


async def test_without_a_case_number_the_latest_withdrawable_one_is_taken_back(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    older, _ = await _corrected(db_async_session, user)
    newer, _ = await _corrected(db_async_session, user)

    result = await call(WITHDRAW_CORRECTION, _ctx(db_async_session, user))

    assert result["case_number"] == newer.case_number
    await db_async_session.refresh(older)
    assert older.corrected_label == "Golden"


async def test_a_correction_in_a_plan_cannot_be_withdrawn(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)
    case, _ = await _corrected(db_async_session, user)
    await sync_versions(db_async_session, [model_info(MODEL, "JcProg/body@v1")])
    await job_repo.draft_job(
        db_async_session, model_name=MODEL, created_by_user_id=user.id, rationale="r"
    )

    result = await call(
        WITHDRAW_CORRECTION, _ctx(db_async_session, user, case), case_number=case.case_number
    )

    assert "already part of a retraining plan" in result["error"]
    assert "Models tab" in result["error"]
    await db_async_session.refresh(case)
    assert case.corrected_label == "Golden"


async def test_someone_elses_correction_is_not_yours_to_withdraw(
    db_async_session: AsyncSession,
) -> None:
    owner = await make_user(db_async_session)
    case, _ = await _corrected(db_async_session, owner)
    other = await _other_user(db_async_session)

    result = await call(
        WITHDRAW_CORRECTION, _ctx(db_async_session, other, case), case_number=case.case_number
    )

    assert "made by someone else" in result["error"]
    await db_async_session.refresh(case)
    assert case.corrected_label == "Golden"


async def test_an_uncorrected_case_has_nothing_to_withdraw(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)
    case = await make_case(db_async_session, user)

    result = await call(
        WITHDRAW_CORRECTION, _ctx(db_async_session, user, case), case_number=case.case_number
    )

    assert "no correction to withdraw" in result["error"]
    none_left = await call(WITHDRAW_CORRECTION, _ctx(db_async_session, user))
    assert "no correction waiting" in none_left["error"]


async def test_a_failed_withdrawal_leaves_both_the_correction_and_the_ticket(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await make_user(db_async_session)
    case, _ = await _corrected(db_async_session, user)

    async def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(db_async_session, "commit", boom)

    result = await call(
        WITHDRAW_CORRECTION, _ctx(db_async_session, user, case), case_number=case.case_number
    )
    monkeypatch.undo()

    assert "error" in result
    await db_async_session.refresh(case)
    assert case.corrected_label == "Golden"
    assert len((await db_async_session.scalars(select(RetrainingTicket))).all()) == 1
