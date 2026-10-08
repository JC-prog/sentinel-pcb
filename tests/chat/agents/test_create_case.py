"""A Case is only created after the user says they want one: `inspect_image` parks the inspection as a
draft, and `create_case` - allowed only in a LATER chat turn than the one that parked it - turns it
into a Case. Tested at the tools, then end to end through the supervisor with a scripted model."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import HumanMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents import supervisor
from app.chat.agents.inspection_agent import CREATE_CASE, INSPECT_IMAGE, drafts
from app.chat.agents.inspection_agent import tool as inspection_tool
from app.chat.agents.inspection_agent.pipeline import InspectionOutcome
from app.chat.agents.inspection_agent.state import InspectionRequest, InspectionRun, Stage
from app.chat.agents.inspection_agent.verdict import Verdict
from app.chat.agents.supervisor import ToolFinished
from app.chat.db.models import Case, CaseDraft, CaseStatus, Conversation
from app.chat.services import repository as chat_repository
from app.chat.services.cases import resolve_case_ref
from app.shared.db.models import User
from tests.chat._llm import ScriptedModel, ai
from tests.chat.agents._helpers import VALID_IMAGE, call, tool_context, upload
from tests.shared._modelops_helpers import make_user


def _payload(**overrides: Any) -> dict[str, Any]:
    """The create_case arguments an inspection parks (pipeline._case_fields)."""

    fields: dict[str, Any] = {
        "board_id": "BOARD-1",
        "component_ref": "U7",
        "package": "QFN32",
        "feature": "Pad1",
        "issue_symptom": "looks off",
        "image_id": "board.png",
        "inspection_xml_id": None,
        "region": "Body",
        "region_confidence": 0.9,
        "region_model_version": "JcProg/pcb_region@v1",
        "defect_model": "pcb_body_defect",
        "defect_model_version": "JcProg/pcb_body_defect@v1",
        "defect_label": "MissingPart",
        "defect_confidence": 0.8,
        "defect_scores": {"Golden": 0.1, "MissingPart": 0.8},
        "measurement_validation": None,
        "observations": ["Workflow finalized as accepted."],
        "status": "accepted",
    }
    return {**fields, **overrides}


async def _conversation(session: AsyncSession, user: User) -> Conversation:
    conversation = Conversation(user_id=user.id)
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation


async def _cases(session: AsyncSession) -> list[Case]:
    return list((await session.scalars(select(Case))).all())


async def _draft_rows(session: AsyncSession) -> list[CaseDraft]:
    return list((await session.scalars(select(CaseDraft))).all())


def _earlier_turn() -> datetime:
    """A turn that began before the draft below was parked - i.e. the turn that parked it."""

    return datetime.now(UTC) - timedelta(seconds=1)


def _later_turn() -> datetime:
    return datetime.now(UTC) + timedelta(seconds=1)


async def _park(
    session: AsyncSession, user: User, conversation: Conversation, **overrides: Any
) -> CaseDraft:
    return await drafts.save_draft(
        session, conversation_id=conversation.id, user_id=user.id, payload=_payload(**overrides)
    )


async def _create(
    session: AsyncSession, user: User, conversation: Conversation, turn_started_at: datetime
) -> dict[str, Any]:
    ctx = tool_context(
        session, user, conversation_id=conversation.id, turn_started_at=turn_started_at
    )
    return await call(CREATE_CASE, ctx)


async def test_creating_in_a_later_turn_makes_exactly_one_case_from_the_inspection(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    conversation = await _conversation(db_async_session, user)
    await _park(db_async_session, user, conversation)
    assert await _cases(db_async_session) == []  # parking is not creating

    result = await _create(db_async_session, user, conversation, _later_turn())

    assert result["status"] == "created" and result["case_number"].startswith("CASE-")
    assert (result["verdict"], result["defect"]) == ("accepted", "MissingPart")
    assert result["case_number"] in result["instruction"]
    (case,) = await _cases(db_async_session)
    assert (case.board_id, case.component_ref, case.package) == ("BOARD-1", "U7", "QFN32")
    assert (case.region, case.defect_label, case.status) == ("Body", "MissingPart", "accepted")
    assert case.region_model_version == "JcProg/pcb_region@v1"
    assert case.defect_scores == {"Golden": 0.1, "MissingPart": 0.8}
    assert (case.created_by_user_id, case.conversation_id) == (user.id, conversation.id)
    assert await _draft_rows(db_async_session) == []  # consumed
    # the relabel / review tools find it as "the latest case in this conversation"
    latest, error = await resolve_case_ref(db_async_session, None, conversation.id)
    assert error is None and latest is not None and latest.id == case.id


async def test_a_flagged_inspection_becomes_a_case_awaiting_review(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    conversation = await _conversation(db_async_session, user)
    await _park(db_async_session, user, conversation, status="review_required")

    await _create(db_async_session, user, conversation, _later_turn())

    (case,) = await _cases(db_async_session)
    assert case.status == CaseStatus.REVIEW_REQUIRED


async def test_it_cannot_be_created_in_the_turn_the_inspection_was_parked(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    conversation = await _conversation(db_async_session, user)
    await _park(db_async_session, user, conversation)

    result = await _create(db_async_session, user, conversation, _earlier_turn())

    assert "has not said they want a case" in result["error"]
    assert await _cases(db_async_session) == []
    assert len(await _draft_rows(db_async_session)) == 1  # still waiting for the answer


async def test_with_nothing_parked_there_is_nothing_to_create(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    conversation = await _conversation(db_async_session, user)

    result = await _create(db_async_session, user, conversation, _later_turn())

    assert "inspect the image with inspect_image first" in result["error"]
    assert await _cases(db_async_session) == []


async def test_a_second_call_does_not_create_a_duplicate(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)
    conversation = await _conversation(db_async_session, user)
    await _park(db_async_session, user, conversation)

    first = await _create(db_async_session, user, conversation, _later_turn())
    second = await _create(db_async_session, user, conversation, _later_turn())

    assert first["status"] == "created" and "error" in second
    assert len(await _cases(db_async_session)) == 1


async def test_only_the_user_whose_inspection_it_was_can_create_the_case(
    db_async_session: AsyncSession,
) -> None:
    owner = await make_user(db_async_session)
    other = await make_user(db_async_session)
    conversation = await _conversation(db_async_session, owner)
    await _park(db_async_session, owner, conversation)

    result = await _create(db_async_session, other, conversation, _later_turn())

    assert "error" in result
    assert await _cases(db_async_session) == []


async def test_a_new_inspection_replaces_the_one_waiting(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)
    conversation = await _conversation(db_async_session, user)
    await _park(db_async_session, user, conversation, defect_label="MissingPart")
    await _park(db_async_session, user, conversation, defect_label="Tombstone")

    (draft,) = await _draft_rows(db_async_session)
    assert draft.payload["defect_label"] == "Tombstone"

    await _create(db_async_session, user, conversation, _later_turn())
    (case,) = await _cases(db_async_session)
    assert case.defect_label == "Tombstone"


async def test_deleting_the_conversation_discards_its_waiting_inspection(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    conversation = await _conversation(db_async_session, user)
    await _park(db_async_session, user, conversation)

    await chat_repository.delete_conversation(db_async_session, conversation)

    assert await _draft_rows(db_async_session) == []


def test_creating_a_case_takes_no_arguments_from_the_model() -> None:
    """What gets saved is whatever inspect_image found - the model cannot supply or alter it."""

    assert CREATE_CASE.model_schema().get("properties", {}) == {}


# --- end to end through the supervisor ----------------------------------------------------------


@pytest.fixture
def fake_inspection(monkeypatch: pytest.MonkeyPatch) -> None:
    """inspect_image with the classifiers stubbed out: it still parks a real draft."""

    async def run_inspection(
        session: AsyncSession, request: InspectionRequest, *, question: str | None = None
    ) -> InspectionOutcome:
        run = InspectionRun(request=request)
        scores = {"Golden": 0.1, "MissingPart": 0.8}
        run.region = Stage("pcb_region", "v1", "Body", 0.9, {"Body": 0.9})
        run.defect = Stage("pcb_body_defect", "v1", "MissingPart", 0.8, scores)
        draft = await drafts.save_draft(
            session,
            conversation_id=request.conversation_id,
            user_id=request.user_id,
            payload=_payload(),
        )
        return InspectionOutcome(run, Verdict(CaseStatus.ACCEPTED, []), draft)

    monkeypatch.setattr(inspection_tool, "run_inspection", run_inspection)


async def _turn(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    user: User,
    conversation: Conversation,
    turn_started_at: datetime,
    message: str,
    *replies: Any,
    image_ids: tuple[str, ...] = (),
) -> list[ToolFinished]:
    model = ScriptedModel(replies=list(replies))
    monkeypatch.setattr(supervisor, "build_chat_model", lambda *a, **k: model)
    ctx = tool_context(
        session,
        user,
        conversation_id=conversation.id,
        turn_started_at=turn_started_at,
        image_ids=image_ids,
    )
    return [
        event
        async for event in supervisor.run_turn(
            provider="ollama",
            messages=[HumanMessage(message)],
            ctx=ctx,
            tools=[INSPECT_IMAGE, CREATE_CASE],
        )
        if isinstance(event, ToolFinished)
    ]


@pytest.mark.usefixtures("fake_inspection")
async def test_a_model_that_inspects_and_creates_in_one_turn_creates_nothing(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The user is asked first: a model that goes straight on to create_case is refused, and only
    their answer in the next turn lets it through."""

    user = await make_user(db_async_session)
    conversation = await _conversation(db_async_session, user)
    image = upload(monkeypatch, tmp_path / "uploads", VALID_IMAGE, "board.png")

    first = await _turn(
        monkeypatch,
        db_async_session,
        user,
        conversation,
        datetime.now(UTC) - timedelta(seconds=1),
        "what defect is this?",
        ai("", "inspect_image"),
        ai("", "create_case"),
        ai("Missing part, 80%. Want me to create a case?"),
        image_ids=(image,),
    )
    inspected, refused = (json.loads(f.result) for f in first)
    assert inspected["case_created"] is False and "case_number" not in inspected
    assert first[0].card is not None  # the inspection is still shown as a card
    assert "has not said they want a case" in refused["error"]
    assert await _cases(db_async_session) == []

    second = await _turn(
        monkeypatch,
        db_async_session,
        user,
        conversation,
        _later_turn(),
        "yes please",
        ai("", "create_case"),
        ai("Created."),
    )
    (created,) = (json.loads(f.result) for f in second)
    assert created["status"] == "created"
    assert len(await _cases(db_async_session)) == 1
