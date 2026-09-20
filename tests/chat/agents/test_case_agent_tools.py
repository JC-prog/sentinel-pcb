"""The case agent's everyday tools: find_similar_cases (and the ranking behind it) and get_case."""

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.case_agent import FindSimilarCasesTool, GetCaseTool
from app.chat.db.models import Case, Conversation
from app.chat.services.cases import create_case, find_similar_cases
from app.shared.db.models import User, UserRole


async def _user(session: AsyncSession, n: int = 1) -> User:
    user = User(
        username=f"case-user-{n}",
        email=f"case-user-{n}@example.com",
        password_hash="x",
        employee_id=f"EMP-C{n}",
        department_shift="Day",
        role=UserRole.QA,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _conversation(session: AsyncSession, user: User) -> Conversation:
    conversation = Conversation(user_id=user.id)
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation


async def _case(
    session: AsyncSession, user: User, conversation: Conversation, **overrides: Any
) -> Case:
    fields: dict[str, Any] = {
        "board_id": "BOARD-1",
        "component_ref": "U7",
        "package": "QFN32",
        "feature": "Pad1",
        "issue_symptom": None,
        "image_id": "board.png",
        "inspection_xml_id": None,
        "golden_image_id": None,
        "region": "Body",
        "region_confidence": 0.9,
        "defect_model": "pcb_body_defect",
        "defect_label": "MissingPart",
        "defect_confidence": 0.9,
        "defect_scores": {},
        "measurement_validation": None,
        "observations": [],
        "status": "accepted",
    }
    fields.update(overrides)
    return await create_case(
        session, created_by_user_id=user.id, conversation_id=conversation.id, **fields
    )


async def _run(tool: Any, session: AsyncSession, conversation: Conversation, **kwargs: Any) -> Any:
    return json.loads(await tool.run(session=session, conversation_id=conversation.id, **kwargs))


async def test_similar_cases_share_the_defect_and_are_ranked_by_how_much_else_matches(
    db_async_session: AsyncSession,
) -> None:
    user = await _user(db_async_session)
    conversation = await _conversation(db_async_session, user)
    reference = await _case(db_async_session, user, conversation)
    same_place = await _case(db_async_session, user, conversation)  # everything matches
    same_defect_elsewhere = await _case(
        db_async_session,
        user,
        conversation,
        component_ref="C3",
        package="0402",
        feature="Pad2",
        board_id="BOARD-2",
    )
    await _case(db_async_session, user, conversation, defect_label="Tombstone")  # different defect

    matches = await find_similar_cases(db_async_session, reference)

    assert [m.case.id for m in matches] == [same_place.id, same_defect_elsewhere.id]
    assert matches[0].score > matches[1].score
    assert "same defect: MissingPart" in matches[0].reasons
    assert "same component: U7" in matches[0].reasons
    assert "same component: C3" not in matches[1].reasons


async def test_a_case_is_never_similar_to_itself(db_async_session: AsyncSession) -> None:
    user = await _user(db_async_session)
    conversation = await _conversation(db_async_session, user)
    only = await _case(db_async_session, user, conversation)

    assert await find_similar_cases(db_async_session, only) == []


async def test_confidence_proximity_and_recency_break_ties(db_async_session: AsyncSession) -> None:
    user = await _user(db_async_session)
    conversation = await _conversation(db_async_session, user)
    reference = await _case(db_async_session, user, conversation, defect_confidence=0.9)
    far = await _case(db_async_session, user, conversation, defect_confidence=0.55)
    near = await _case(db_async_session, user, conversation, defect_confidence=0.88)

    matches = await find_similar_cases(db_async_session, reference)

    assert [m.case.id for m in matches] == [near.id, far.id]


async def test_a_case_with_no_defect_label_is_compared_on_region(
    db_async_session: AsyncSession,
) -> None:
    """An uncertain-region case never got a defect label; it is matched on region alone."""

    user = await _user(db_async_session)
    conversation = await _conversation(db_async_session, user)
    reference = await _case(
        db_async_session, user, conversation, defect_label=None, defect_model=None
    )
    same_region = await _case(db_async_session, user, conversation, region="Body")
    await _case(db_async_session, user, conversation, region="Lead")

    matches = await find_similar_cases(db_async_session, reference)

    assert [m.case.id for m in matches] == [same_region.id]


async def test_the_tool_defaults_to_the_latest_case_in_this_conversation(
    db_async_session: AsyncSession,
) -> None:
    user = await _user(db_async_session)
    other_conversation = await _conversation(db_async_session, user)
    conversation = await _conversation(db_async_session, user)
    await _case(db_async_session, user, other_conversation, defect_label="Tombstone")
    older = await _case(db_async_session, user, conversation, defect_label="Shift")
    latest = await _case(db_async_session, user, conversation)  # MissingPart
    twin = await _case(db_async_session, user, other_conversation)  # MissingPart elsewhere

    result = await _run(FindSimilarCasesTool(), db_async_session, conversation)

    assert result["compared_against"]["case_number"] == latest.case_number
    assert [c["case_number"] for c in result["similar_cases"]] == [twin.case_number]
    assert older.case_number not in json.dumps(result["similar_cases"])
    (only,) = result["similar_cases"]
    assert only["similarity"] > 0.5
    assert "same defect: MissingPart" in only["why_similar"]


async def test_the_tool_accepts_an_explicit_case_number_and_a_limit(
    db_async_session: AsyncSession,
) -> None:
    user = await _user(db_async_session)
    conversation = await _conversation(db_async_session, user)
    reference = await _case(db_async_session, user, conversation)
    for _ in range(4):
        await _case(db_async_session, user, conversation)
    elsewhere = await _conversation(db_async_session, user)

    result = await _run(
        FindSimilarCasesTool(),
        db_async_session,
        elsewhere,  # no case in this conversation - the number is what matters
        case_number=reference.case_number.lower(),
        limit=2,
    )

    assert result["compared_against"]["case_number"] == reference.case_number
    assert len(result["similar_cases"]) == 2


async def test_the_tool_explains_when_there_is_nothing_to_compare(
    db_async_session: AsyncSession,
) -> None:
    user = await _user(db_async_session)
    conversation = await _conversation(db_async_session, user)

    no_case = await _run(FindSimilarCasesTool(), db_async_session, conversation)
    bad_number = await _run(
        FindSimilarCasesTool(), db_async_session, conversation, case_number="nonsense"
    )
    unknown = await _run(
        FindSimilarCasesTool(), db_async_session, conversation, case_number="CASE-999999"
    )

    assert "no case in this conversation" in no_case["error"]
    assert "invalid case number" in bad_number["error"]
    assert unknown["error"] == "case not found"


async def test_a_case_without_any_classification_cannot_be_compared(
    db_async_session: AsyncSession,
) -> None:
    user = await _user(db_async_session)
    conversation = await _conversation(db_async_session, user)
    bare = await _case(
        db_async_session,
        user,
        conversation,
        region=None,
        defect_label=None,
        defect_model=None,
        defect_confidence=None,
        region_confidence=None,
    )

    result = await _run(FindSimilarCasesTool(), db_async_session, conversation)

    assert bare.case_number in result["error"]


async def test_get_case_returns_the_full_record(db_async_session: AsyncSession) -> None:
    user = await _user(db_async_session)
    conversation = await _conversation(db_async_session, user)
    case = await _case(
        db_async_session,
        user,
        conversation,
        defect_model_version="JcProg/body@v2",
        observations=["Image verification: readable (4x4)."],
        issue_symptom="component missing",
    )

    result = await _run(GetCaseTool(), db_async_session, conversation, case_number="CASE-999999")
    assert result == {"error": "case not found"}

    result = await _run(GetCaseTool(), db_async_session, conversation, case_number=case.case_number)
    assert result["case_number"] == case.case_number
    assert result["defect_model_version"] == "JcProg/body@v2"
    assert result["observations"] == ["Image verification: readable (4x4)."]
    assert result["issue_symptom"] == "component missing"
    assert result["status"] == "accepted"
