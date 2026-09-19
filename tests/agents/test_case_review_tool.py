import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.adc_inspection_agent import ListCasesTool, ReviewCaseTool
from app.agents.adc_inspection_agent.repository import create_case
from app.db.models import Case, Conversation, User, UserRole


async def _make_user(session: AsyncSession) -> User:
    user = User(
        username="qa-reviewer",
        email="qa-reviewer@example.com",
        password_hash="x",
        employee_id="EMP-200",
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


async def _make_case(
    session: AsyncSession, user: User, conversation: Conversation, *, status: str = "review_required"
) -> Case:
    return await create_case(
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
        region_confidence=0.5,
        defect_model="",
        defect_label="",
        defect_confidence=0.0,
        defect_scores={},
        measurement_validation=None,
        explainability_result=None,
        observations=["seeded for test"],
        status=status,
    )


async def test_list_cases_returns_seeded_case(db_async_session: AsyncSession) -> None:
    user = await _make_user(db_async_session)
    conversation = await _make_conversation(db_async_session, user)
    case = await _make_case(db_async_session, user, conversation)

    result = json.loads(await ListCasesTool().run(session=db_async_session, status="review_required"))

    numbers = [c["case_number"] for c in result["cases"]]
    assert case.case_number in numbers


async def test_review_case_approve_transitions_status(db_async_session: AsyncSession) -> None:
    user = await _make_user(db_async_session)
    conversation = await _make_conversation(db_async_session, user)
    case = await _make_case(db_async_session, user, conversation)

    result = json.loads(
        await ReviewCaseTool().run(
            session=db_async_session,
            user_id=user.id,
            username=user.username,
            case_number=case.case_number,
            decision="approve",
            note="confirmed real defect",
        )
    )

    assert result["status"] == "approved"


async def test_review_case_override_transitions_status(db_async_session: AsyncSession) -> None:
    user = await _make_user(db_async_session)
    conversation = await _make_conversation(db_async_session, user)
    case = await _make_case(db_async_session, user, conversation)

    result = json.loads(
        await ReviewCaseTool().run(
            session=db_async_session,
            user_id=user.id,
            username=user.username,
            case_number=case.case_number,
            decision="override",
        )
    )

    assert result["status"] == "overridden"


async def test_review_case_rejects_double_resolution(db_async_session: AsyncSession) -> None:
    user = await _make_user(db_async_session)
    conversation = await _make_conversation(db_async_session, user)
    case = await _make_case(db_async_session, user, conversation)

    await ReviewCaseTool().run(
        session=db_async_session,
        user_id=user.id,
        username=user.username,
        case_number=case.case_number,
        decision="approve",
    )
    result = json.loads(
        await ReviewCaseTool().run(
            session=db_async_session,
            user_id=user.id,
            username=user.username,
            case_number=case.case_number,
            decision="approve",
        )
    )

    assert result == {"error": "case is not pending review"}


async def test_review_case_unknown_case_number_returns_error(db_async_session: AsyncSession) -> None:
    user = await _make_user(db_async_session)

    result = json.loads(
        await ReviewCaseTool().run(
            session=db_async_session,
            user_id=user.id,
            username=user.username,
            case_number="CASE-999999",
            decision="approve",
        )
    )

    assert result == {"error": "case not found"}


async def test_review_case_invalid_case_number_returns_error(db_async_session: AsyncSession) -> None:
    user = await _make_user(db_async_session)

    result = json.loads(
        await ReviewCaseTool().run(
            session=db_async_session,
            user_id=user.id,
            username=user.username,
            case_number="not-a-case-number",
            decision="approve",
        )
    )

    assert result == {"error": "invalid case number: 'not-a-case-number'"}
