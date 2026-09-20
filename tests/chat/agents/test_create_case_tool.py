import json
from collections.abc import Callable
from io import BytesIO
from typing import Any
from xml.etree import ElementTree as ET

import httpx
import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.adc_inspection_agent import CreateCaseTool
from app.chat.agents.adc_inspection_agent.golden_images import save_golden_image
from app.chat.agents.adc_inspection_agent.repository import (
    get_case_by_sequence_number,
    list_cases,
    parse_case_number,
)
from app.chat.db.models import Case, Conversation
from app.shared.config.settings import settings
from app.shared.db.models import User, UserRole

_RealAsyncClient = httpx.AsyncClient


def _tiny_png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (4, 4), color=(200, 200, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


_VALID_IMAGE = _tiny_png_bytes()

_VALID_XML = b"""
<Boards>
  <Board Name="BOARD-1">
    <Component Name="U7" Package="QFN32" PartNumber="QFN32-PN">
      <Feature Identifier="Pad1" FeatureStatus="Failed">
        <FeatureResult>
          <Inspection Type="Lead Offset" status="Failed" FailedInspectionCriterias="Offset">
            <Measurements>
              <Offset Value="0.5" Minimum="0.0" Maximum="0.3" />
            </Measurements>
          </Inspection>
        </FeatureResult>
      </Feature>
    </Component>
  </Board>
</Boards>
"""

_INVALID_XML = b"""
<Boards>
  <Board Name="BOARD-1">
    <Component Name="U7" Package="QFN32" PartNumber="QFN32-PN">
      <Feature Identifier="Pad1" FeatureStatus="Failed">
        <FeatureResult>
          <Inspection Type="Lead Offset" status="Failed" FailedInspectionCriterias="Offset">
            <Measurements>
              <Offset Value="not-a-number" Minimum="0.0" Maximum="0.3" />
            </Measurements>
          </Inspection>
        </FeatureResult>
      </Feature>
    </Component>
  </Board>
</Boards>
"""

assert ET.fromstring(_VALID_XML) is not None  # sanity: fixtures are well-formed XML
assert ET.fromstring(_INVALID_XML) is not None


def _mock_async_client(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return _RealAsyncClient(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _classify_response(
    model: str, label: str, index: int, scores: dict[str, float]
) -> dict[str, object]:
    return {
        "model": model,
        "username": "jane-qa",
        "label": label,
        "index": index,
        "confidence": scores[label],
        "scores": scores,
        "request_id": f"req-{model}",
    }


def _confident_handler(request: httpx.Request) -> httpx.Response:
    body = request.content.decode(errors="ignore")
    if "pcb_region" in body:
        return httpx.Response(
            200, json=_classify_response("pcb_region", "Body", 0, {"Body": 0.9, "Lead": 0.05, "Text": 0.05})
        )
    return httpx.Response(
        200,
        json=_classify_response("pcb_body_defect", "MissingPart", 2, {"Golden": 0.1, "MissingPart": 0.8}),
    )


def _uncertain_region_handler(request: httpx.Request) -> httpx.Response:
    assert "pcb_region" in request.content.decode(errors="ignore")
    return httpx.Response(
        200,
        json=_classify_response("pcb_region", "Body", 0, {"Body": 0.5, "Lead": 0.3, "Text": 0.2}),
    )


@pytest.fixture(autouse=True)
def _inference_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "inference_base_url", "http://inference.test:8001")


@pytest.fixture(autouse=True)
def _no_openai_key_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Escalation is opt-in per test (set settings.openai_api_key explicitly) - by default it
    should always take the "skipped: no OpenAI key configured" path, never actually try to call
    OpenAI, regardless of what a developer's local .env happens to have set."""

    monkeypatch.setattr(settings, "openai_api_key", "")


async def _make_user(session: AsyncSession, *, role: UserRole = UserRole.QA) -> User:
    user = User(
        username=f"user-{role.value}",
        email=f"{role.value}@example.com",
        password_hash="x",
        employee_id=f"EMP-{role.value}",
        department_shift="Day Shift",
        role=role,
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


async def _run_create_case(
    session: AsyncSession,
    user: User,
    conversation: Conversation,
    *,
    inspection_xml_bytes: bytes | None = None,
) -> dict[str, Any]:
    result = await CreateCaseTool().run(
        session=session,
        image_bytes=_VALID_IMAGE,
        image_name="board.png",
        inspection_xml_bytes=inspection_xml_bytes,
        inspection_xml_id="inspection.xml" if inspection_xml_bytes else None,
        username=user.username,
        user_id=user.id,
        conversation_id=conversation.id,
        board_id="BOARD-1",
        component_ref="U7",
        package="QFN32",
        feature="Pad1",
        issue_symptom="looks off",
    )
    return dict(json.loads(result))


async def _fetch_case(session: AsyncSession, case_number: str) -> Case:
    sequence_number = parse_case_number(case_number)
    assert sequence_number is not None
    case = await get_case_by_sequence_number(session, sequence_number)
    assert case is not None
    return case


async def test_create_case_accepted_end_to_end(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_async_client(monkeypatch, _confident_handler)
    user = await _make_user(db_async_session)
    conversation = await _make_conversation(db_async_session, user)

    result = await _run_create_case(db_async_session, user, conversation)

    assert result["final_decision"] == "ACCEPTED"
    assert result["review_required"] is False
    assert result["region"] == "Body"
    assert result["defect_label"] == "MissingPart"
    assert result["case_number"].startswith("CASE-")
    assert result["golden_image_found"] is False
    assert result["measurement_validation"] is None
    assert result["explainability_review"] is None

    cases = await list_cases(db_async_session, status=None, limit=10)
    assert len(cases) == 1
    assert cases[0].status == "accepted"


async def test_create_case_review_required_on_uncertain_region(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_async_client(monkeypatch, _uncertain_region_handler)
    user = await _make_user(db_async_session)
    conversation = await _make_conversation(db_async_session, user)

    result = await _run_create_case(db_async_session, user, conversation)

    assert result["final_decision"] == "REVIEW_REQUIRED"
    assert result["review_required"] is True
    assert result["defect_model"] == ""  # stage 2 never ran

    cases = await list_cases(db_async_session, status=None, limit=10)
    assert len(cases) == 1
    assert cases[0].status == "review_required"


async def test_create_case_with_valid_xml_keeps_confident_verdict(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_async_client(monkeypatch, _confident_handler)
    user = await _make_user(db_async_session)
    conversation = await _make_conversation(db_async_session, user)

    result = await _run_create_case(
        db_async_session, user, conversation, inspection_xml_bytes=_VALID_XML
    )

    assert result["final_decision"] == "ACCEPTED"
    assert result["measurement_validation"]["valid"] is True


async def test_create_case_with_invalid_xml_downgrades_confident_verdict(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_async_client(monkeypatch, _confident_handler)
    user = await _make_user(db_async_session)
    conversation = await _make_conversation(db_async_session, user)

    result = await _run_create_case(
        db_async_session, user, conversation, inspection_xml_bytes=_INVALID_XML
    )

    assert result["final_decision"] == "REVIEW_REQUIRED"
    assert result["measurement_validation"]["valid"] is False


async def test_create_case_finds_a_registered_golden_image(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_async_client(monkeypatch, _confident_handler)
    user = await _make_user(db_async_session, role=UserRole.ADMIN)
    conversation = await _make_conversation(db_async_session, user)
    await save_golden_image(
        db_async_session,
        file_bytes=b"fake-png",
        filename="golden.png",
        board_id="BOARD-1",
        component_ref="U7",
        package="QFN32",
        feature="Pad1",
        notes=None,
        registered_by_user_id=user.id,
    )

    result = await _run_create_case(db_async_session, user, conversation)

    assert result["golden_image_found"] is True


async def test_create_case_downgrades_on_alignment_failure(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A registered golden image that clearly doesn't line up with the case image (a large
    phase-correlation shift) should downgrade an otherwise-confident verdict to REVIEW_REQUIRED -
    the align_and_check_quality node's whole reason for existing."""

    _mock_async_client(monkeypatch, _confident_handler)
    monkeypatch.setattr(
        "app.chat.agents.adc_inspection_agent.verification.estimate_translation",
        lambda golden_bytes, defect_bytes: {
            "dx": 40.0,
            "dy": 0.0,
            "shift_pixels": 40.0,
            "response": 0.9,
        },
    )
    user = await _make_user(db_async_session, role=UserRole.ADMIN)
    conversation = await _make_conversation(db_async_session, user)
    await save_golden_image(
        db_async_session,
        file_bytes=_VALID_IMAGE,
        filename="golden.png",
        board_id="BOARD-1",
        component_ref="U7",
        package="QFN32",
        feature="Pad1",
        notes=None,
        registered_by_user_id=user.id,
    )

    result = await _run_create_case(db_async_session, user, conversation)

    assert "IMAGE_PAIR_ALIGNMENT_FAILED" in result["quality_issues"]
    assert result["final_decision"] == "REVIEW_REQUIRED"


async def test_create_case_returns_error_for_unreadable_image(
    db_async_session: AsyncSession,
) -> None:
    user = await _make_user(db_async_session)
    conversation = await _make_conversation(db_async_session, user)

    result_json = await CreateCaseTool().run(
        session=db_async_session,
        image_bytes=b"\x89PNGfake",
        image_name="board.png",
        inspection_xml_bytes=None,
        inspection_xml_id=None,
        username=user.username,
        user_id=user.id,
        conversation_id=conversation.id,
        board_id="BOARD-1",
        component_ref="U7",
        package=None,
        feature=None,
        issue_symptom=None,
    )
    result = json.loads(result_json)

    assert "error" in result
    cases = await list_cases(db_async_session, status=None, limit=10)
    assert cases == []


async def test_create_case_returns_error_when_inference_not_configured(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "inference_base_url", "")
    user = await _make_user(db_async_session)
    conversation = await _make_conversation(db_async_session, user)

    result = await _run_create_case(db_async_session, user, conversation)

    assert "error" in result
    cases = await list_cases(db_async_session, status=None, limit=10)
    assert cases == []


async def test_create_case_escalates_to_explainability_on_review_required(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the verdict is REVIEW_REQUIRED, escalate_review should call the explainability
    pipeline in-process and attach its result to both the tool's JSON and the persisted Case."""

    class _FakeExplainabilityPipeline:
        def invoke(self, state: dict[str, Any]) -> dict[str, Any]:
            return {
                "final_defect_category": "missing part",
                "defect_location": None,
                "final_diagnosis_text": "Component appears absent from its pads.",
                "grounding_confidence": 0.81,
                "self_check_passed": True,
                "similar_cases": [],
                "errors": [],
            }

    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(
        "app.chat.agents.case_review_agent.graph.get_pipeline",
        lambda api_key: _FakeExplainabilityPipeline(),
    )
    _mock_async_client(monkeypatch, _uncertain_region_handler)
    user = await _make_user(db_async_session)
    conversation = await _make_conversation(db_async_session, user)

    result = await _run_create_case(db_async_session, user, conversation)

    assert result["final_decision"] == "REVIEW_REQUIRED"
    assert result["explainability_review"]["defect_category"] == "missing part"
    assert result["explainability_review"]["confidence_score"] == 0.81

    case = await _fetch_case(db_async_session, result["case_number"])
    assert case.explainability_result is not None
    assert case.explainability_result["defect_category"] == "missing part"


async def test_create_case_persists_review_required_even_without_openai_key(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Escalation must never block case persistence - a REVIEW_REQUIRED case with no OpenAI key
    configured still gets a Case row, just with a "skipped" explainability_result."""

    _mock_async_client(monkeypatch, _uncertain_region_handler)
    user = await _make_user(db_async_session)
    conversation = await _make_conversation(db_async_session, user)

    result = await _run_create_case(db_async_session, user, conversation)

    assert result["final_decision"] == "REVIEW_REQUIRED"
    assert result["explainability_review"] == {"skipped": True, "reason": "no OpenAI key configured"}

    case = await _fetch_case(db_async_session, result["case_number"])
    assert case.status == "review_required"
    assert case.explainability_result == {"skipped": True, "reason": "no OpenAI key configured"}
