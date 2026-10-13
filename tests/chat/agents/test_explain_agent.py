"""The explain agent: an advisory explanation of the image just inspected, asked of Agent 2's MCP
service. The service is the real launcher (app/workflow/services/agent2_mcp_service.py) with its
OpenAI-calling functions replaced, reached through an in-memory MCP client - so the tool, the
client, the MCP protocol and the guarded server tool are all exercised together."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents import tool_registry
from app.chat.agents.explain_agent import EXPLAIN_INSPECTION
from app.chat.db.models import Conversation
from app.chat.services import drafts, explain_client
from app.chat.services.cases import create_case
from app.chat.services.explain_client import ExplainRequest, ExplainUnavailable
from app.shared.config.settings import settings
from app.shared.db.models import UserRole
from app.workflow.services import agent2_mcp_service as service
from tests.chat.agents._helpers import call, tool_context
from tests.shared._modelops_helpers import make_user

PAYLOAD: dict[str, Any] = {
    "board_id": "BOARD-1",
    "component_ref": "C12",
    "package": None,
    "feature": None,
    "issue_symptom": "one end lifted",
    "image_id": "board.png",
    "inspection_xml_id": None,
    "region": "Body",
    "region_confidence": 0.9,
    "defect_model": "pcb_body_defect",
    "defect_label": "Tombstone",
    "defect_confidence": 0.71,
    "defect_scores": {},
    "measurement_validation": None,
    "observations": [],
    "status": "review_required",
}


@pytest.fixture
def uploads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "chat_upload_dir", str(tmp_path))
    (tmp_path / "board.png").write_bytes(b"png")
    return tmp_path


def _service(label: str = "TOMBSTONE") -> Any:
    def precedents(**kwargs: Any) -> dict[str, Any]:
        return {"ipc_standard": "IPC-A-610", "similar_cases": []}

    def visual(**kwargs: Any) -> dict[str, Any]:
        return {"visual_description": "One end of the part is lifted off its pad."}

    def measurement(**kwargs: Any) -> dict[str, Any]:
        return {"source": "fallback_nominal"}

    def grounding(**kwargs: Any) -> dict[str, Any]:
        return {
            "guardrail_status": "PASSED",
            "hallucination_flagged": False,
            "output": {
                "defect_classification": label,
                "confidence": 0.8,
                "visual_evidence_cited": "lifted at one end",
                "ipc_precedent_cited": "IPC-A-610 tombstone criteria",
                "explanation_summary": "One end of the part is lifted off its pad.",
            },
        }

    return service.build_server(service.Tools(precedents, visual, measurement, grounding))


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> list[ExplainRequest]:
    """Routes the tool's call to the in-memory service and records what it was asked."""

    asked: list[ExplainRequest] = []
    original = explain_client.explain

    async def via_server(request: ExplainRequest, *, transport: Any | None = None) -> dict[str, Any]:
        asked.append(request)
        return await original(request, transport=_service())

    monkeypatch.setattr(explain_client, "explain", via_server)
    return asked


def _ctx(session: AsyncSession, user: Any, conversation_id: str = "c1") -> Any:
    return tool_context(session, user, conversation_id=conversation_id)


async def _conversation(session: AsyncSession, user: Any) -> Conversation:
    conversation = Conversation(user_id=user.id)
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation


async def test_it_explains_the_image_just_inspected_from_the_draft_not_from_the_model(
    db_async_session: AsyncSession, uploads: Path, served: list[ExplainRequest]
) -> None:
    user = await make_user(db_async_session)
    conversation = await _conversation(db_async_session, user)
    await drafts.save_draft(
        db_async_session, conversation_id=conversation.id, user_id=user.id, payload=dict(PAYLOAD)
    )

    result = await call(EXPLAIN_INSPECTION, _ctx(db_async_session, user, conversation.id))

    (request,) = served
    assert request == ExplainRequest(
        image_name="board.png",
        board_id="BOARD-1",
        component_ref="C12",
        issue_symptom="one end lifted",
        model_label="Tombstone",
        model_confidence=0.71,
        xml_name="",
    )
    assert result["status"] == "ok" and result["advisory"] is True
    assert result["label"] == "tombstone" and result["agrees_with_model"] is True
    assert "advisory explanation" in result["instruction"]


async def test_unknown_board_and_component_are_sent_as_blank(
    db_async_session: AsyncSession, uploads: Path, served: list[ExplainRequest]
) -> None:
    user = await make_user(db_async_session)
    conversation = await _conversation(db_async_session, user)
    await drafts.save_draft(
        db_async_session,
        conversation_id=conversation.id,
        user_id=user.id,
        payload={**PAYLOAD, "board_id": "unknown", "component_ref": "unknown"},
    )

    await call(EXPLAIN_INSPECTION, _ctx(db_async_session, user, conversation.id))

    assert (served[0].board_id, served[0].component_ref) == ("", "")


async def test_it_falls_back_to_the_latest_case_when_the_draft_was_consumed(
    db_async_session: AsyncSession, uploads: Path, served: list[ExplainRequest]
) -> None:
    user = await make_user(db_async_session)
    conversation = await _conversation(db_async_session, user)
    await create_case(
        db_async_session,
        created_by_user_id=user.id,
        conversation_id=conversation.id,
        **{k: v for k, v in PAYLOAD.items()},
    )

    result = await call(EXPLAIN_INSPECTION, _ctx(db_async_session, user, conversation.id))

    assert served[0].image_name == "board.png" and served[0].model_label == "Tombstone"
    assert result["status"] == "ok"


async def test_with_nothing_inspected_it_says_so(
    db_async_session: AsyncSession, uploads: Path, served: list[ExplainRequest]
) -> None:
    user = await make_user(db_async_session)

    result = await call(EXPLAIN_INSPECTION, _ctx(db_async_session, user))

    assert "no inspection in this conversation" in result["error"]
    assert served == []


async def test_an_unreachable_service_leaves_the_inspection_standing(
    db_async_session: AsyncSession, uploads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await make_user(db_async_session)
    conversation = await _conversation(db_async_session, user)
    await drafts.save_draft(
        db_async_session, conversation_id=conversation.id, user_id=user.id, payload=dict(PAYLOAD)
    )
    monkeypatch.setattr(settings, "agent2_mcp_url", "http://127.0.0.1:9/mcp")  # nothing there

    result = await call(EXPLAIN_INSPECTION, _ctx(db_async_session, user, conversation.id))

    assert "could not be reached" in result["error"]
    assert "inspection result above still stands" in result["error"]


async def test_a_slow_service_times_out_instead_of_hanging_the_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = FastMCP("slow")

    @server.tool
    async def explain_inspection(
        image_name: str,
        board_id: str = "",
        component_ref: str = "",
        issue_symptom: str = "",
        model_label: str = "",
        xml_name: str = "",
    ) -> dict[str, Any]:
        await asyncio.sleep(5)
        return {}

    monkeypatch.setattr(settings, "agent2_mcp_timeout_seconds", 0.2)

    with pytest.raises(ExplainUnavailable, match="took too long"):
        await explain_client.explain(ExplainRequest(image_name="x.png"), transport=server)


async def test_not_configured_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agent2_mcp_url", "")

    with pytest.raises(ExplainUnavailable, match="not configured"):
        await explain_client.explain(ExplainRequest(image_name="x.png"))


def test_the_tool_is_only_offered_when_there_is_a_service_to_ask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "agent2_mcp_url", "")
    monkeypatch.setattr(settings, "explain_agent_enabled", True)
    assert "explain_inspection" not in {
        t.name for t in tool_registry.available(role=UserRole.QA, has_image=False)
    }

    monkeypatch.setattr(settings, "agent2_mcp_url", "http://agent2-mcp:8002/mcp")
    assert "explain_inspection" in {
        t.name for t in tool_registry.available(role=UserRole.QA, has_image=False)
    }

    monkeypatch.setattr(settings, "explain_agent_enabled", False)
    assert "explain_inspection" not in {
        t.name for t in tool_registry.available(role=UserRole.QA, has_image=False)
    }
