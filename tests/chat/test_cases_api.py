"""The Cases tab's read-only API: GET /api/cases and GET /api/cases/{case_number}. Cases are made and
corrected in chat; these routes only let QA and Admin find and read them, all of them (one shared
list, like Work-tab runs and the Models tab)."""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.db.models import Case
from app.shared.db.models import RetrainingTicket, User
from app.shared.modelops.tickets import create_ticket
from tests.shared._modelops_helpers import make_case

URL = "/api/cases"


async def _me(session: AsyncSession) -> User:
    return (await session.scalars(select(User).order_by(User.created_at))).first()  # type: ignore[return-value]


async def _case(session: AsyncSession, **changes: Any) -> Case:
    user = await _me(session)
    case = await make_case(session, user)
    if changes:
        await session.execute(update(Case).where(Case.id == case.id).values(**changes))
        await session.commit()
        await session.refresh(case)
    return case


def test_the_list_requires_login(client: TestClient) -> None:
    assert client.get(URL).status_code == 401
    assert client.get(f"{URL}/CASE-000001").status_code == 401


async def test_the_list_is_newest_first_and_paged(
    authenticated_client: TestClient, db_async_session: AsyncSession
) -> None:
    first = await _case(db_async_session)
    second = await _case(db_async_session)
    third = await _case(db_async_session)

    body = authenticated_client.get(URL, params={"limit": 2}).json()

    assert body["total"] == 3 and body["limit"] == 2
    assert [c["case_number"] for c in body["cases"]] == [third.case_number, second.case_number]
    page_two = authenticated_client.get(URL, params={"limit": 2, "offset": 2}).json()
    assert [c["case_number"] for c in page_two["cases"]] == [first.case_number]


async def test_the_list_filters(
    authenticated_client: TestClient, db_async_session: AsyncSession
) -> None:
    flagged = await _case(db_async_session, status="review_required", board_id="ALPHA")
    corrected = await _case(db_async_session, corrected_label="Golden", defect_model="pcb_text_defect")
    await _case(db_async_session, status="accepted", board_id="BETA")

    def numbers(**params: Any) -> list[str]:
        cases = authenticated_client.get(URL, params=params).json()["cases"]
        return [c["case_number"] for c in cases]

    assert numbers(status="review_required") == [flagged.case_number]
    assert numbers(corrected="true") == [corrected.case_number]
    assert len(numbers(corrected="false")) == 2
    assert numbers(model="pcb_text_defect") == [corrected.case_number]
    assert numbers(q="alph") == [flagged.case_number]  # board, any case
    assert numbers(q=corrected.case_number.lower()) == [corrected.case_number]  # a case number


async def test_the_detail_carries_the_correction_and_its_ticket(
    authenticated_client: TestClient, db_async_session: AsyncSession
) -> None:
    case = await _case(
        db_async_session,
        corrected_label="Golden",
        correction_reason="the pads are fine",
        defect_scores={"Golden": 0.1, "MissingPart": 0.9},
    )
    me = await _me(db_async_session)
    ticket = await create_ticket(
        db_async_session,
        case_id=case.id,
        case_number=case.case_number,
        flagged_by_user_id=me.id,
        reason="the pads are fine",
        model_name="pcb_body_defect",
        model_version="JcProg/body@v1",
        observed_label="MissingPart",
        correct_label="Golden",
    )

    detail = authenticated_client.get(f"{URL}/{case.case_number}").json()

    assert detail["case_number"] == case.case_number
    assert detail["defect_label"] == "MissingPart" and detail["corrected_label"] == "Golden"
    assert detail["correction_reason"] == "the pads are fine"
    assert detail["defect_scores"] == {"Golden": 0.1, "MissingPart": 0.9}
    assert detail["created_by"] == me.username
    assert detail["ticket"]["id"] == ticket.id and detail["ticket"]["status"] == "open"
    assert detail["conversation_id"] == case.conversation_id  # the viewer's own conversation


async def test_a_case_number_may_be_given_loosely(
    authenticated_client: TestClient, db_async_session: AsyncSession
) -> None:
    case = await _case(db_async_session)

    assert authenticated_client.get(f"{URL}/{case.sequence_number}").status_code == 200
    assert authenticated_client.get(f"{URL}/{case.case_number.lower()}").status_code == 200


def test_unknown_and_malformed_case_numbers_are_404(authenticated_client: TestClient) -> None:
    assert authenticated_client.get(f"{URL}/CASE-999999").status_code == 404
    assert authenticated_client.get(f"{URL}/not-a-case").status_code == 404


async def test_the_conversation_is_only_offered_to_its_owner(
    authenticated_client: TestClient,
    other_authenticated_client: TestClient,
    db_async_session: AsyncSession,
) -> None:
    case = await _case(db_async_session)  # made by the first user

    mine = authenticated_client.get(f"{URL}/{case.case_number}").json()
    theirs = other_authenticated_client.get(f"{URL}/{case.case_number}").json()

    assert mine["conversation_id"] == case.conversation_id
    assert theirs["case_number"] == case.case_number  # the case itself is shared...
    assert theirs["conversation_id"] is None  # ...the conversation is not


async def test_a_pending_relabel_is_shown_but_not_as_a_correction(
    authenticated_client: TestClient, db_async_session: AsyncSession
) -> None:
    case = await _case(db_async_session, pending_label="Tombstone", pending_reason="looks lifted")

    detail = authenticated_client.get(f"{URL}/{case.case_number}").json()

    assert detail["pending_label"] == "Tombstone" and detail["corrected_label"] is None


async def test_tickets_are_not_required(
    authenticated_client: TestClient, db_async_session: AsyncSession
) -> None:
    case = await _case(db_async_session)
    assert (await db_async_session.scalars(select(RetrainingTicket))).first() is None

    assert authenticated_client.get(f"{URL}/{case.case_number}").json()["ticket"] is None
