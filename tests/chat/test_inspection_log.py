"""The record of chat inspections and the confirmation that a label was right: what gives chat a
denominator (how many were inspected) and the positive half of feedback (the label was right)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.inspection_agent import CONFIRM_LABEL_CORRECT
from app.chat.db.models import Case, Conversation, InspectionLog
from app.chat.services import inspection_log
from app.chat.services.cases import create_case
from app.shared.config.settings import settings
from app.shared.db.models import User
from tests.chat.agents._helpers import call, tool_context
from tests.shared._modelops_helpers import make_ticket, make_user

MODEL = "pcb_body_defect"


def _fields(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "board_id": "BOARD-1",
        "component_ref": "U7",
        "image_id": "board.png",
        "region": "Body",
        "region_confidence": 0.9,
        "region_model_version": "JcProg/region@v1",
        "defect_model": MODEL,
        "defect_model_version": "JcProg/body@v1",
        "defect_label": "MissingPart",
        "defect_confidence": 0.8,
        "status": "accepted",
    }
    return {**fields, **overrides}


async def _conversation(session: AsyncSession, user: User) -> Conversation:
    conversation = Conversation(user_id=user.id)
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation


async def _log(
    session: AsyncSession, user: User, conversation: Conversation, **overrides: Any
) -> InspectionLog:
    row = await inspection_log.record(
        session, user_id=user.id, conversation_id=conversation.id, case_fields=_fields(**overrides)
    )
    assert row is not None
    return row


def _ctx(session: AsyncSession, user: User, conversation: Conversation) -> Any:
    return tool_context(session, user, conversation_id=conversation.id)


async def test_an_inspection_is_recorded_without_the_image(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)
    conversation = await _conversation(db_async_session, user)

    row = await _log(db_async_session, user, conversation)

    assert (row.defect_model, row.defect_label, row.verdict) == (MODEL, "MissingPart", "accepted")
    assert row.defect_model_version == "JcProg/body@v1"
    assert row.verified is False
    assert not hasattr(row, "image_bytes")  # only the image's name, never the image


async def test_it_records_nothing_when_switched_off(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "inspection_log_enabled", False)
    user = await make_user(db_async_session)
    conversation = await _conversation(db_async_session, user)

    row = await inspection_log.record(
        db_async_session, user_id=user.id, conversation_id=conversation.id, case_fields=_fields()
    )

    assert row is None
    assert (await db_async_session.scalars(select(InspectionLog))).first() is None


async def test_confirming_marks_the_latest_inspection_right(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    conversation = await _conversation(db_async_session, user)
    older = await _log(db_async_session, user, conversation, image_id="a.png")
    newer = await _log(db_async_session, user, conversation, image_id="b.png")

    result = await call(CONFIRM_LABEL_CORRECT, _ctx(db_async_session, user, conversation))

    assert result["status"] == "confirmed" and result["label"] == "MissingPart"
    await db_async_session.refresh(older)
    await db_async_session.refresh(newer)
    assert (older.verified, newer.verified) == (False, True)
    assert newer.verified_by_user_id == user.id and newer.verified_at is not None
    assert (await db_async_session.scalars(select(Case))).first() is None  # no case was made


async def test_it_cannot_be_confirmed_twice(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)
    conversation = await _conversation(db_async_session, user)
    await _log(db_async_session, user, conversation)
    await call(CONFIRM_LABEL_CORRECT, _ctx(db_async_session, user, conversation))

    result = await call(CONFIRM_LABEL_CORRECT, _ctx(db_async_session, user, conversation))

    assert "already confirmed" in result["error"]


async def test_there_must_be_an_inspection_with_a_label(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)
    conversation = await _conversation(db_async_session, user)

    nothing = await call(CONFIRM_LABEL_CORRECT, _ctx(db_async_session, user, conversation))
    assert "no inspection in this conversation" in nothing["error"]

    await _log(db_async_session, user, conversation, defect_model=None, defect_label=None)
    unclassified = await call(CONFIRM_LABEL_CORRECT, _ctx(db_async_session, user, conversation))
    assert "has no defect label" in unclassified["error"]


async def test_an_inspection_that_was_corrected_cannot_be_confirmed_right(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    conversation = await _conversation(db_async_session, user)
    await _log(db_async_session, user, conversation)
    case = await create_case(
        db_async_session,
        created_by_user_id=user.id,
        conversation_id=conversation.id,
        board_id="BOARD-1",
        component_ref="U7",
        package=None,
        feature=None,
        issue_symptom=None,
        image_id="board.png",
        inspection_xml_id=None,
        region="Body",
        region_confidence=0.9,
        defect_model=MODEL,
        defect_label="MissingPart",
        defect_confidence=0.8,
        defect_scores={},
        measurement_validation=None,
        observations=[],
        status="accepted",
    )
    await db_async_session.execute(
        update(Case).where(Case.id == case.id).values(corrected_label="Golden")
    )
    await db_async_session.commit()

    result = await call(CONFIRM_LABEL_CORRECT, _ctx(db_async_session, user, conversation))

    assert "was corrected to 'Golden'" in result["error"]


async def test_chat_activity_counts_inspected_corrected_and_confirmed(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    conversation = await _conversation(db_async_session, user)
    rows = [await _log(db_async_session, user, conversation) for _ in range(4)]
    rows[0].verified = True  # confirmed right
    await db_async_session.execute(  # one was long ago
        update(InspectionLog)
        .where(InspectionLog.id == rows[1].id)
        .values(created_at=datetime.now(UTC) - timedelta(days=30))
    )
    await _log(db_async_session, user, conversation, defect_model="pcb_text_defect")  # another model
    await db_async_session.commit()
    await make_ticket(db_async_session, user)  # one chat correction of MODEL (it has a case)

    activity = await inspection_log.chat_activity(db_async_session, MODEL, days=7)

    assert activity["recent"]["inspected"] == 3
    assert activity["recent"]["confirmed_correct"] == 1
    assert activity["recent"]["corrected"] == 1
    assert activity["previous"]["inspected"] == 0
    assert activity["correction_share"] == pytest.approx(1 / 3, abs=0.001)
    assert "Chat inspections only" in activity["note"]


async def test_there_is_no_share_when_nothing_was_inspected(db_async_session: AsyncSession) -> None:
    activity = await inspection_log.chat_activity(db_async_session, MODEL, days=7)

    assert activity["recent"]["inspected"] == 0
    assert activity["correction_share"] is None
