"""A relabel on an inspection that is not a Case yet: the first human judgment of an inspection is
what creates its Case. Proposing saves nothing; only a LATER turn's confirmation creates the Case
with the correction already on it and queues the ticket, in one commit, and consumes the draft."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.inspection_agent import CREATE_CASE
from app.chat.agents.relabel_agent import CONFIRM_RELABEL, RELABEL_CASE
from app.chat.db.models import Case, CaseDraft, Conversation
from app.chat.services import cases, drafts
from app.shared import inference
from app.shared.db.models import RetrainingTicket, User
from app.shared.inference import ModelInfo
from tests.chat.agents._helpers import call, tool_context
from tests.shared._modelops_helpers import make_user

MODEL = "pcb_body_defect"


@pytest.fixture(autouse=True)
def _served_models(monkeypatch: pytest.MonkeyPatch) -> None:
    async def list_models() -> list[ModelInfo]:
        return [
            ModelInfo(
                name=MODEL,
                version="JcProg/body@v1",
                loaded_at=datetime.now(UTC),
                labels=["Golden", "MissingPart", "Tombstone"],
                input_size=(224, 224),
            )
        ]

    monkeypatch.setattr(inference, "list_models", list_models)


def _payload(**overrides: Any) -> dict[str, Any]:
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
        "defect_model": MODEL,
        "defect_model_version": "JcProg/body@v1",
        "defect_label": "MissingPart",
        "defect_confidence": 0.8,
        "defect_scores": {"Golden": 0.1, "MissingPart": 0.8},
        "measurement_validation": None,
        "observations": [],
        "status": "review_required",
    }
    return {**fields, **overrides}


async def _conversation(session: AsyncSession, user: User) -> Conversation:
    conversation = Conversation(user_id=user.id)
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation


async def _setup(session: AsyncSession, **payload: Any) -> tuple[User, Conversation]:
    user = await make_user(session)
    conversation = await _conversation(session, user)
    await drafts.save_draft(
        session, conversation_id=conversation.id, user_id=user.id, payload=_payload(**payload)
    )
    return user, conversation


def _ctx(session: AsyncSession, user: User, conversation: Conversation, turn: datetime) -> Any:
    return tool_context(session, user, conversation_id=conversation.id, turn_started_at=turn)


def _earlier() -> datetime:
    return datetime.now(UTC) - timedelta(seconds=1)


def _later() -> datetime:
    return datetime.now(UTC) + timedelta(seconds=1)


async def _propose(session: AsyncSession, user: User, conversation: Conversation, **kw: Any) -> Any:
    args = {"correct_label": "Golden", "reason": "it is a golden part", **kw}
    return await call(RELABEL_CASE, _ctx(session, user, conversation, _earlier()), **args)


async def _counts(session: AsyncSession) -> tuple[int, int, int]:
    return (
        len((await session.scalars(select(Case))).all()),
        len((await session.scalars(select(CaseDraft))).all()),
        len((await session.scalars(select(RetrainingTicket))).all()),
    )


async def test_proposing_on_an_inspection_saves_no_case(db_async_session: AsyncSession) -> None:
    user, conversation = await _setup(db_async_session)

    result = await _propose(db_async_session, user, conversation)

    assert result["status"] == "awaiting_confirmation"
    assert result["case_number"] is None  # not a case yet
    assert result["model_label"] == "MissingPart" and result["proposed_label"] == "Golden"
    assert "new case" in result["instruction"]
    assert await _counts(db_async_session) == (0, 1, 0)
    draft = (await db_async_session.scalars(select(CaseDraft))).one()
    assert (draft.pending_label, draft.pending_reason) == ("Golden", "it is a golden part")


async def test_confirming_in_a_later_turn_creates_the_case_correction_and_ticket_together(
    db_async_session: AsyncSession,
) -> None:
    user, conversation = await _setup(db_async_session)
    await _propose(db_async_session, user, conversation)

    result = await call(
        CONFIRM_RELABEL, _ctx(db_async_session, user, conversation, _later())
    )

    assert result["status"] == "relabelled" and result["case_created"] is True
    assert result["case_number"] and result["corrected_label"] == "Golden"
    assert await _counts(db_async_session) == (1, 0, 1)  # one case, draft consumed, one ticket
    case = (await db_async_session.scalars(select(Case))).one()
    assert case.defect_label == "MissingPart"  # what the model said is never overwritten
    assert (case.corrected_label, case.corrected_by_user_id) == ("Golden", user.id)
    assert case.correction_reason == "it is a golden part"
    ticket = (await db_async_session.scalars(select(RetrainingTicket))).one()
    assert ticket.case_id == case.id
    assert (ticket.observed_label, ticket.correct_label) == ("MissingPart", "Golden")


async def test_it_cannot_be_confirmed_in_the_turn_it_was_proposed(
    db_async_session: AsyncSession,
) -> None:
    user, conversation = await _setup(db_async_session)
    turn = _earlier()
    await call(
        RELABEL_CASE,
        _ctx(db_async_session, user, conversation, turn),
        correct_label="Golden",
        reason="fine",
    )

    result = await call(CONFIRM_RELABEL, _ctx(db_async_session, user, conversation, turn))

    assert "has not confirmed yet" in result["error"]
    assert await _counts(db_async_session) == (0, 1, 0)


async def test_there_is_nothing_to_confirm_without_a_proposal(
    db_async_session: AsyncSession,
) -> None:
    user, conversation = await _setup(db_async_session)

    result = await call(CONFIRM_RELABEL, _ctx(db_async_session, user, conversation, _later()))

    assert "no relabel waiting for confirmation" in result["error"]
    assert await _counts(db_async_session) == (0, 1, 0)


async def test_a_new_inspection_discards_a_proposal_about_the_previous_image(
    db_async_session: AsyncSession,
) -> None:
    user, conversation = await _setup(db_async_session)
    await _propose(db_async_session, user, conversation)

    await drafts.save_draft(
        db_async_session,
        conversation_id=conversation.id,
        user_id=user.id,
        payload=_payload(image_id="other.png"),
    )
    result = await call(CONFIRM_RELABEL, _ctx(db_async_session, user, conversation, _later()))

    assert "no relabel waiting for confirmation" in result["error"]
    assert await _counts(db_async_session) == (0, 1, 0)


async def test_an_unclassified_inspection_has_nothing_to_relabel(
    db_async_session: AsyncSession,
) -> None:
    user, conversation = await _setup(db_async_session, defect_model=None, defect_label=None)

    result = await _propose(db_async_session, user, conversation)

    assert "no defect classification" in result["error"]


async def test_a_label_the_model_already_gave_is_refused(db_async_session: AsyncSession) -> None:
    user, conversation = await _setup(db_async_session)

    result = await _propose(db_async_session, user, conversation, correct_label="MissingPart")

    assert "nothing to correct" in result["error"]


async def test_without_a_case_number_the_newer_of_draft_and_case_is_used(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    conversation = await _conversation(db_async_session, user)
    old = await cases.create_case(
        db_async_session,
        created_by_user_id=user.id,
        conversation_id=conversation.id,
        **_payload(),
    )
    # an inspection run after that case was made is what "it" means now
    await drafts.save_draft(
        db_async_session, conversation_id=conversation.id, user_id=user.id, payload=_payload()
    )

    on_draft = await _propose(db_async_session, user, conversation)
    on_case = await _propose(db_async_session, user, conversation, case_number=old.case_number)

    assert on_draft["case_number"] is None
    assert on_case["case_number"] == old.case_number


async def test_with_no_draft_it_still_uses_the_latest_case(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)
    conversation = await _conversation(db_async_session, user)
    case = await cases.create_case(
        db_async_session,
        created_by_user_id=user.id,
        conversation_id=conversation.id,
        **_payload(),
    )

    result = await _propose(db_async_session, user, conversation)

    assert result["case_number"] == case.case_number


async def test_saving_the_inspection_as_a_case_keeps_a_pending_relabel(
    db_async_session: AsyncSession,
) -> None:
    user, conversation = await _setup(db_async_session)
    await _propose(db_async_session, user, conversation)

    created = await call(CREATE_CASE, _ctx(db_async_session, user, conversation, _later()))

    assert created["case_number"]
    case = (await db_async_session.scalars(select(Case))).one()
    assert (case.pending_label, case.pending_by_user_id) == ("Golden", user.id)
    assert case.corrected_label is None  # still needs the user's confirmation in a later turn


async def test_no_ticket_and_no_case_when_the_ticket_cannot_be_written(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, conversation = await _setup(db_async_session)
    await _propose(db_async_session, user, conversation)

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr("app.chat.agents.relabel_agent.recorder.ticket_repo.create_ticket", boom)

    result = await call(CONFIRM_RELABEL, _ctx(db_async_session, user, conversation, _later()))

    assert "error" in result
    assert await _counts(db_async_session) == (0, 1, 0)  # rolled back: still just the draft
