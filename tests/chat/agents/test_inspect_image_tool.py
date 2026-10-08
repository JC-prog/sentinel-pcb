"""`inspect_image` end to end: the user's attached upload in, the result the user sees out, and the
inspection parked as a draft - no Case yet (create_case makes one after the user says yes, see
test_create_case.py). The inference service is faked at the HTTP boundary; uploads are real files."""

from collections.abc import Callable
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.inspection_agent import INSPECT_IMAGE
from app.chat.db.models import Case, CaseDraft, Conversation
from app.shared.config.settings import settings
from app.shared.db.models import User, UserRole
from tests.chat.agents._helpers import VALID_IMAGE, call, tool_context, upload

_RealAsyncClient = httpx.AsyncClient

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

_INVALID_XML = _VALID_XML.replace(b'Value="0.5"', b'Value="not-a-number"')

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
        "model_version": f"JcProg/{model}@v1",
        "username": "jane-qa",
        "label": label,
        "index": index,
        "confidence": scores[label],
        "scores": scores,
        "request_id": f"req-{model}",
    }


def _confident_handler(request: httpx.Request) -> httpx.Response:
    if "pcb_region" in request.content.decode(errors="ignore"):
        scores = {"Body": 0.9, "Lead": 0.05, "Text": 0.05}
        return httpx.Response(200, json=_classify_response("pcb_region", "Body", 0, scores))
    scores = {"Golden": 0.1, "MissingPart": 0.8}
    return httpx.Response(200, json=_classify_response("pcb_body_defect", "MissingPart", 2, scores))


def _uncertain_region_handler(request: httpx.Request) -> httpx.Response:
    assert "pcb_region" in request.content.decode(errors="ignore")
    scores = {"Body": 0.5, "Lead": 0.3, "Text": 0.2}
    return httpx.Response(200, json=_classify_response("pcb_region", "Body", 0, scores))


@pytest.fixture(autouse=True)
def _inference_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "inference_base_url", "http://inference.test:8001")


@pytest.fixture
def uploads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[[bytes, str], str]:
    return lambda data, name: upload(monkeypatch, tmp_path / "uploads", data, name)


async def _user_and_conversation(session: AsyncSession) -> tuple[User, Conversation]:
    user = User(
        username="user-qa",
        email="qa@example.com",
        password_hash="x",
        employee_id="EMP-qa",
        department_shift="Day Shift",
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


async def _inspect(
    session: AsyncSession,
    uploads: Callable[[bytes, str], str],
    *,
    image: bytes = VALID_IMAGE,
    xml: bytes | None = None,
    **arguments: Any,
) -> dict[str, Any]:
    user, conversation = await _user_and_conversation(session)
    ctx = tool_context(
        session,
        user,
        conversation_id=conversation.id,
        image_ids=(uploads(image, "board.png"),),
        xml_ids=(uploads(xml, "inspection.xml"),) if xml is not None else (),
    )
    defaults = {
        "board_id": "BOARD-1",
        "component_ref": "U7",
        "package": "QFN32",
        "feature": "Pad1",
        "issue_symptom": "looks off",
    }
    return await call(INSPECT_IMAGE, ctx, **{**defaults, **arguments})


async def _all_cases(session: AsyncSession) -> list[Case]:
    return list((await session.scalars(select(Case))).all())


async def _drafts(session: AsyncSession) -> list[CaseDraft]:
    return list((await session.scalars(select(CaseDraft))).all())


async def _only_draft(session: AsyncSession) -> dict[str, Any]:
    """What the inspection parked for the user's yes - the arguments create_case will be given."""

    (draft,) = await _drafts(session)
    return dict(draft.payload)


async def test_inspect_image_accepted_end_to_end(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, uploads: Any
) -> None:
    _mock_async_client(monkeypatch, _confident_handler)

    result = await _inspect(db_async_session, uploads)

    assert result["verdict"] == "accepted"
    assert result["review_required"] is False
    assert result["review_reasons"] == []
    assert result["region"]["label"] == "Body"
    assert result["defect"]["label"] == "MissingPart"
    assert result["defect"]["confidence"] == 0.8
    assert result["measurement_validation"] is None
    # nothing is saved as a case - the model is told to ask the user first
    assert result["case_created"] is False and "case_number" not in result
    assert "ask the user whether they want a case" in result["instruction"]
    assert await _all_cases(db_async_session) == []

    payload = await _only_draft(db_async_session)
    assert (payload["status"], payload["defect_label"], payload["image_id"]) == (
        "accepted",
        "MissingPart",
        "board.png",
    )


async def test_inspect_image_shows_the_runner_up_scores_best_first(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, uploads: Any
) -> None:
    """The user sees what else the model considered, not just its top label."""

    _mock_async_client(monkeypatch, _confident_handler)

    result = await _inspect(db_async_session, uploads)

    assert [s["label"] for s in result["defect"]["top_scores"]] == ["MissingPart", "Golden"]


async def test_inspect_image_stamps_the_model_versions_that_answered(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, uploads: Any
) -> None:
    """A verdict must be attributable to specific weights, so drift can be measured per version."""

    _mock_async_client(monkeypatch, _confident_handler)

    result = await _inspect(db_async_session, uploads)

    assert result["region"]["model_version"] == "JcProg/pcb_region@v1"
    assert result["defect"]["model_version"] == "JcProg/pcb_body_defect@v1"
    payload = await _only_draft(db_async_session)
    assert payload["region_model_version"] == "JcProg/pcb_region@v1"
    assert payload["defect_model_version"] == "JcProg/pcb_body_defect@v1"


async def test_inspect_image_review_required_on_uncertain_region(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, uploads: Any
) -> None:
    _mock_async_client(monkeypatch, _uncertain_region_handler)

    result = await _inspect(db_async_session, uploads)

    assert result["verdict"] == "review_required"
    assert result["review_required"] is True
    assert result["defect"] is None  # stage 2 never ran
    assert "region confidence 0.50" in result["review_reasons"][0]
    assert (await _only_draft(db_async_session))["status"] == "review_required"


async def test_inspect_image_with_valid_xml_keeps_confident_verdict(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, uploads: Any
) -> None:
    _mock_async_client(monkeypatch, _confident_handler)

    result = await _inspect(db_async_session, uploads, xml=_VALID_XML)

    assert result["verdict"] == "accepted"
    assert result["measurement_validation"]["valid"] is True
    assert (await _only_draft(db_async_session))["inspection_xml_id"] == "inspection.xml"


async def test_inspect_image_with_invalid_xml_downgrades_confident_verdict(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, uploads: Any
) -> None:
    _mock_async_client(monkeypatch, _confident_handler)

    result = await _inspect(db_async_session, uploads, xml=_INVALID_XML)

    assert result["verdict"] == "review_required"
    assert result["measurement_validation"]["valid"] is False
    assert any("measurement validation failed" in r for r in result["review_reasons"])


async def test_inspect_image_returns_error_for_unreadable_image(
    db_async_session: AsyncSession, uploads: Any
) -> None:
    result = await _inspect(db_async_session, uploads, image=b"\x89PNGfake")

    assert result["error"].startswith("IMAGE_UNREADABLE")
    assert await _all_cases(db_async_session) == [] and await _drafts(db_async_session) == []


async def test_inspect_image_returns_error_when_inference_not_configured(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, uploads: Any
) -> None:
    monkeypatch.setattr(settings, "inference_base_url", "")

    result = await _inspect(db_async_session, uploads)

    assert "not configured" in result["error"]
    assert await _all_cases(db_async_session) == [] and await _drafts(db_async_session) == []


async def test_inspect_image_returns_error_when_the_service_fails(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, uploads: Any
) -> None:
    """An outage is an error with nothing to save, not a case waiting for review."""

    _mock_async_client(monkeypatch, lambda request: httpx.Response(500, json={"detail": "boom"}))

    result = await _inspect(db_async_session, uploads)

    assert "Region classification failed" in result["error"]
    assert await _all_cases(db_async_session) == [] and await _drafts(db_async_session) == []


async def test_inspect_image_defaults_unknown_board_and_component(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, uploads: Any
) -> None:
    """A user asking "what defect is this?" is not blocked for lack of a reference designator."""

    _mock_async_client(monkeypatch, _confident_handler)

    result = await _inspect(db_async_session, uploads, board_id=None, component_ref="  ")

    assert result["case_created"] is False
    payload = await _only_draft(db_async_session)
    assert (payload["board_id"], payload["component_ref"]) == ("unknown", "unknown")


async def test_an_attached_image_that_is_gone_is_a_refusal_not_a_crash(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "chat_upload_dir", str(tmp_path))
    user, conversation = await _user_and_conversation(db_async_session)
    ctx = tool_context(
        db_async_session, user, conversation_id=conversation.id, image_ids=("missing.png",)
    )

    result = await call(INSPECT_IMAGE, ctx)

    assert "could not be found" in result["error"]
    assert await _all_cases(db_async_session) == [] and await _drafts(db_async_session) == []


def test_the_model_cannot_name_an_image_or_a_user() -> None:
    """The image is the user's attached upload and the user is the request's - neither is a
    parameter the model could fill in."""

    properties = INSPECT_IMAGE.model_schema()["properties"]

    assert not {"image_bytes", "image_name", "image_id", "user_id", "session", "runtime"} & set(
        properties
    )
