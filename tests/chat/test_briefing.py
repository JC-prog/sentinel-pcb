"""GET /api/chat/briefing: what is waiting for an Admin in the Models tab, shown on chat's opening
screen. Admin only; QA is told no."""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models import User
from app.shared.modelops import drift as drift_repo
from app.shared.modelops import jobs as job_repo
from app.shared.modelops.versions import sync_versions
from tests.shared._modelops_helpers import make_ticket, model_info

URL = "/api/chat/briefing"


async def _me(session: AsyncSession) -> User:
    return (await session.scalars(select(User).order_by(User.created_at))).first()  # type: ignore[return-value]


def test_it_requires_login(client: TestClient) -> None:
    assert client.get(URL).status_code == 401


def test_qa_is_refused(qa_authenticated_client: TestClient) -> None:
    assert qa_authenticated_client.get(URL).status_code == 403


async def test_an_admin_sees_what_is_waiting(
    authenticated_client: TestClient, db_async_session: AsyncSession
) -> None:
    me = await _me(db_async_session)
    await make_ticket(db_async_session, me)
    await make_ticket(db_async_session, me)
    await sync_versions(db_async_session, [model_info("pcb_body_defect", "JcProg/body@v1")])
    await job_repo.draft_job(
        db_async_session, model_name="pcb_body_defect", created_by_user_id=me.id, rationale="r"
    )
    await make_ticket(db_async_session, me)  # a fresh one, not in the plan
    await drift_repo.create_drift_report(
        db_async_session,
        model_name="pcb_body_defect",
        reported_by_user_id=me.id,
        description="by hand",
    )
    await drift_repo.create_drift_report(
        db_async_session,
        model_name="pcb_body_defect",
        reported_by_user_id=me.id,
        description="raised itself",
        stats={"auto": True},
    )

    body = authenticated_client.get(URL).json()

    assert body == {
        "open_drift_reports": 2,
        "open_auto_reports": 1,
        "plans_awaiting_approval": 1,
        "corrections_waiting": 1,
    }


def test_an_admin_with_nothing_waiting_gets_zeros(authenticated_client: TestClient) -> None:
    assert authenticated_client.get(URL).json() == {
        "open_drift_reports": 0,
        "open_auto_reports": 0,
        "plans_awaiting_approval": 0,
        "corrections_waiting": 0,
    }
