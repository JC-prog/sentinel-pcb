import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.adc_inspection_agent.repository import create_case
from app.agents.monitoring_agent.tool import FlagCaseForRetrainingTool
from app.db.models import Conversation, RetrainingTicket, User, UserRole


async def _make_user(session: AsyncSession) -> User:
    user = User(
        username="qa-flagger",
        email="qa-flagger@example.com",
        password_hash="x",
        employee_id="EMP-300",
        department_shift="QA Day Shift",
        role=UserRole.QA,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _make_conversation(session: AsyncSession, user: User) -> Conversation:
    conversation = Conversation(user_id=user.id)
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation


async def _make_case(session: AsyncSession, user: User, conversation: Conversation) -> str:
    case = await create_case(
        session,
        created_by_user_id=user.id,
        conversation_id=conversation.id,
        board_id="BOARD-1",
        component_ref="U7",
        package="QFN32",
        feature="Pad1",
        issue_symptom="looks off",
        image_id="board.png",
        inspection_xml_id=None,
        golden_image_id=None,
        region="Body",
        region_confidence=0.9,
        defect_model="pcb_body_defect",
        defect_label="MissingPart",
        defect_confidence=0.9,
        defect_scores={},
        measurement_validation=None,
        explainability_result=None,
        observations=["seeded for test"],
        status="accepted",
    )
    return case.case_number


async def test_flag_case_for_retraining_creates_a_ticket(db_async_session: AsyncSession) -> None:
    user = await _make_user(db_async_session)
    conversation = await _make_conversation(db_async_session, user)
    case_number = await _make_case(db_async_session, user, conversation)

    result = json.loads(
        await FlagCaseForRetrainingTool().run(
            session=db_async_session,
            user_id=user.id,
            case_number=case_number,
            reason="Model called this a defect but it was a false positive.",
        )
    )

    assert result["case_number"] == case_number
    assert result["status"] == "open"

    tickets = (await db_async_session.scalars(select(RetrainingTicket))).all()
    assert len(tickets) == 1
    assert tickets[0].reason == "Model called this a defect but it was a false positive."
    assert tickets[0].flagged_by_user_id == user.id


async def test_flag_case_for_retraining_rejects_empty_reason(db_async_session: AsyncSession) -> None:
    user = await _make_user(db_async_session)
    conversation = await _make_conversation(db_async_session, user)
    case_number = await _make_case(db_async_session, user, conversation)

    result = json.loads(
        await FlagCaseForRetrainingTool().run(
            session=db_async_session, user_id=user.id, case_number=case_number, reason="   "
        )
    )

    assert result == {"error": "reason is required"}
    assert (await db_async_session.scalars(select(RetrainingTicket))).all() == []


async def test_flag_case_for_retraining_rejects_invalid_case_number(
    db_async_session: AsyncSession,
) -> None:
    user = await _make_user(db_async_session)

    result = json.loads(
        await FlagCaseForRetrainingTool().run(
            session=db_async_session,
            user_id=user.id,
            case_number="not-a-case-number",
            reason="bad call",
        )
    )

    assert result == {"error": "invalid case number: 'not-a-case-number'"}


async def test_flag_case_for_retraining_rejects_unknown_case(db_async_session: AsyncSession) -> None:
    user = await _make_user(db_async_session)

    result = json.loads(
        await FlagCaseForRetrainingTool().run(
            session=db_async_session, user_id=user.id, case_number="CASE-999999", reason="bad call"
        )
    )

    assert result == {"error": "case not found"}
