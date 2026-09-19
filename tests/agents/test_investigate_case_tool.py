import io
import json
import shutil
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.adc_inspection_agent.repository import create_case
from app.agents.explainability_review_agent import InvestigateCaseTool
from app.agents.explainability_review_agent.graph import PCBInspectionState
from app.config.settings import settings
from app.db.models import Conversation, User, UserRole


@pytest.fixture(autouse=True)
def _clean_upload_dir() -> Generator[None, None, None]:
    yield
    shutil.rmtree(settings.chat_upload_dir, ignore_errors=True)


def _write_upload(filename: str, content: bytes) -> None:
    upload_dir = Path(settings.chat_upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / filename).write_bytes(content)


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


async def _make_user(session: AsyncSession) -> User:
    user = User(
        username="qa-investigator",
        email="qa-investigator@example.com",
        password_hash="x",
        employee_id="EMP-400",
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
    session: AsyncSession,
    user: User,
    conversation: Conversation,
    *,
    image_id: str,
    inspection_xml_id: str | None = None,
) -> str:
    case = await create_case(
        session,
        created_by_user_id=user.id,
        conversation_id=conversation.id,
        board_id="BOARD-1",
        component_ref="U7",
        package="QFN32",
        feature="Pad1",
        issue_symptom="looks off",
        image_id=image_id,
        inspection_xml_id=inspection_xml_id,
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
        status="review_required",
    )
    return case.case_number


class _FakePipeline:
    def __init__(self, overrides: dict[str, Any]) -> None:
        self._overrides = overrides

    def invoke(self, initial_state: PCBInspectionState, **_kwargs: Any) -> dict[str, Any]:
        merged: dict[str, Any] = dict(initial_state)
        merged.update(self._overrides)
        return merged


def _final_state_overrides() -> dict[str, Any]:
    return {
        "final_defect_category": "solder insufficient",
        "defect_location": None,
        "final_diagnosis_text": "Insufficient solder observed.",
        "grounding_confidence": 0.7,
        "self_check_passed": True,
        "similar_cases": [],
        "errors": [],
    }


async def test_investigate_case_rejects_invalid_case_number(db_async_session: AsyncSession) -> None:
    result = json.loads(
        await InvestigateCaseTool().run(
            session=db_async_session, case_number="not-a-case-number", openai_api_key="sk-test"
        )
    )
    assert result == {"error": "invalid case number: 'not-a-case-number'"}


async def test_investigate_case_rejects_unknown_case(db_async_session: AsyncSession) -> None:
    result = json.loads(
        await InvestigateCaseTool().run(
            session=db_async_session, case_number="CASE-999999", openai_api_key="sk-test"
        )
    )
    assert result == {"error": "case not found"}


async def test_investigate_case_resolves_image_and_runs_pipeline(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_upload("board.png", _png_bytes())
    monkeypatch.setattr(
        "app.agents.explainability_review_agent.tools.get_pipeline",
        lambda api_key: _FakePipeline(_final_state_overrides()),
    )

    user = await _make_user(db_async_session)
    conversation = await _make_conversation(db_async_session, user)
    case_number = await _make_case(db_async_session, user, conversation, image_id="board.png")

    result = json.loads(
        await InvestigateCaseTool().run(
            session=db_async_session, case_number=case_number, openai_api_key="sk-test"
        )
    )

    assert result["case_number"] == case_number
    assert result["defect_category"] == "solder insufficient"
    assert result["confidence_score"] == 0.7


async def test_investigate_case_resolves_attached_inspection_xml(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_upload("board.png", _png_bytes())
    _write_upload("inspection.xml", b"<Boards/>")
    captured: dict[str, Any] = {}

    class _CapturingPipeline:
        def invoke(self, initial_state: PCBInspectionState, **_kwargs: Any) -> dict[str, Any]:
            captured["inspection_xml_bytes"] = initial_state["inspection_xml_bytes"]
            merged: dict[str, Any] = dict(initial_state)
            merged.update(_final_state_overrides())
            return merged

    monkeypatch.setattr(
        "app.agents.explainability_review_agent.tools.get_pipeline",
        lambda api_key: _CapturingPipeline(),
    )

    user = await _make_user(db_async_session)
    conversation = await _make_conversation(db_async_session, user)
    case_number = await _make_case(
        db_async_session,
        user,
        conversation,
        image_id="board.png",
        inspection_xml_id="inspection.xml",
    )

    await InvestigateCaseTool().run(
        session=db_async_session, case_number=case_number, openai_api_key="sk-test"
    )

    assert captured["inspection_xml_bytes"] == b"<Boards/>"


async def test_investigate_case_returns_error_when_image_missing_on_disk(
    db_async_session: AsyncSession,
) -> None:
    user = await _make_user(db_async_session)
    conversation = await _make_conversation(db_async_session, user)
    case_number = await _make_case(db_async_session, user, conversation, image_id="missing.png")

    result = json.loads(
        await InvestigateCaseTool().run(
            session=db_async_session, case_number=case_number, openai_api_key="sk-test"
        )
    )

    assert result == {"error": "case image not found on disk"}
