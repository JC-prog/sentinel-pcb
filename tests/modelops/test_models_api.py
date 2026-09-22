"""The Models tab's API (app/modelops/): reading versions, drift and the retraining queue, and the
Admin-only actions on them, against a stateful fake of the inference service.

Client roles (tests/conftest.py): `authenticated_client` is the first user registered, so an Admin;
`qa_authenticated_client` is a separate QA user with its own cookie jar.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.config.settings import settings
from app.shared.db.models import (
    RetrainingJob,
    RetrainingJobStatus,
    RetrainingTicket,
    RetrainingTicketStatus,
    User,
    UserRole,
)
from app.shared.modelops import drift as drift_repo
from app.shared.modelops import jobs as job_repo
from app.shared.modelops.versions import record_candidate
from tests.modelops.fake_inference import FakeInference, install, succeeded
from tests.shared._modelops_helpers import make_ticket, make_workflow_ticket

MODEL = "pcb_body_defect"
V1 = "JcProg/body@v1"
V2 = "JcProg/body@v2"


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeInference:
    fake = FakeInference()
    install(monkeypatch, fake)
    return fake


async def _user(session: AsyncSession, username: str) -> User:
    user = await session.scalar(select(User).where(User.username == username))
    assert user is not None
    return user


async def _admin(session: AsyncSession) -> User:
    return await _user(session, "test-qa")  # registered first, so promoted to Admin


async def _qa(session: AsyncSession) -> User:
    """The QA user `qa_authenticated_client` registers - or, in tests that only log in as the
    Admin, an equivalent QA user created on the spot (some tests need a plan drafted by QA)."""

    existing = await session.scalar(select(User).where(User.username == "second-qa"))
    if existing is not None:
        return existing
    user = User(
        username="second-qa",
        email="second-qa@example.com",
        password_hash="x",
        employee_id="EMP-003",
        department_shift="QA Day Shift",
        role=UserRole.QA,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _plan(session: AsyncSession, author: User, tickets: int = 1) -> RetrainingJob:
    for _ in range(tickets):
        await make_ticket(session, author)
    return await job_repo.draft_job(
        session, model_name=MODEL, created_by_user_id=author.id, rationale="false positives"
    )


def _job_url(job_id: str, action: str = "") -> str:
    return f"/api/models/retraining/jobs/{job_id}" + (f"/{action}" if action else "")


# ------------------------------------------------------------------------------------ access


def test_the_api_requires_a_login(client: TestClient) -> None:
    assert client.get("/api/models").status_code == 401
    assert client.post(f"/api/models/{MODEL}/rollback").status_code == 401


def test_the_whole_api_can_be_switched_off(
    authenticated_client: TestClient, fake: FakeInference, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "modelops_enabled", False)

    assert authenticated_client.get("/api/models").status_code == 503
    assert authenticated_client.post(f"/api/models/{MODEL}/rollback").status_code == 503


async def test_qa_can_read_but_no_action_is_open_to_them(
    authenticated_client: TestClient,
    qa_authenticated_client: TestClient,
    fake: FakeInference,
    db_async_session: AsyncSession,
) -> None:
    job = await _plan(db_async_session, await _qa(db_async_session))
    qa = qa_authenticated_client

    for path in (
        "/api/models",
        "/api/models/drift",
        "/api/models/retraining/queue",
        _job_url(job.id),
    ):
        assert qa.get(path).status_code == 200, path

    for path in (
        _job_url(job.id, "approve"),
        _job_url(job.id, "submit"),
        "/api/models/drift/x/resolve",
        f"/api/models/{MODEL}/rollback",
    ):
        assert qa.post(path).status_code == 403, path
    assert qa.post(f"/api/models/{MODEL}/promote", json={"version": V2}).status_code == 403
    assert fake.jobs == {}  # nothing reached the inference service


# ------------------------------------------------------------------------------------ overview


def test_the_overview_syncs_the_live_version_from_the_inference_service(
    authenticated_client: TestClient, fake: FakeInference
) -> None:
    body = authenticated_client.get("/api/models").json()

    assert body["inference"] == {"configured": True, "reachable": True, "error": None}
    (model,) = body["models"]
    assert model["name"] == MODEL
    assert model["live"]["version"] == V1
    assert model["live"]["status"] == "live"
    assert model["previous"] is None
    assert model["labels"] == ["Golden", "MissingPart"]
    assert (model["open_drift_reports"], model["open_tickets"], model["active_jobs"]) == (0, 0, 0)


def test_the_overview_follows_a_version_change_made_outside_the_app(
    authenticated_client: TestClient, fake: FakeInference
) -> None:
    authenticated_client.get("/api/models")
    fake.models[MODEL].previous, fake.models[MODEL].version = V1, V2  # e.g. an operator's activate

    (model,) = authenticated_client.get("/api/models").json()["models"]

    assert (model["live"]["version"], model["previous"]["version"]) == (V2, V1)
    assert [v["status"] for v in model["versions"]].count("live") == 1


def test_an_unreachable_inference_service_degrades_to_what_the_database_knows(
    authenticated_client: TestClient, fake: FakeInference
) -> None:
    authenticated_client.get("/api/models")  # records the live version
    fake.down = True

    response = authenticated_client.get("/api/models")

    assert response.status_code == 200
    body = response.json()
    assert body["inference"]["configured"] is True
    assert body["inference"]["reachable"] is False
    assert "request failed" in body["inference"]["error"]
    assert body["models"][0]["live"]["version"] == V1  # last known
    assert body["models"][0]["labels"] is None  # only the live service can say


def test_an_unconfigured_inference_service_is_reported_as_such(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "inference_base_url", "")

    body = authenticated_client.get("/api/models").json()

    assert body["inference"] == {"configured": False, "reachable": False, "error": None}
    assert body["models"] == []


async def test_the_overview_counts_open_reports_tickets_and_jobs_in_progress(
    authenticated_client: TestClient, fake: FakeInference, db_async_session: AsyncSession
) -> None:
    admin = await _admin(db_async_session)
    await _plan(db_async_session, admin)  # a pending job holding one ticket
    await make_ticket(db_async_session, admin)  # one more, still open
    await drift_repo.create_drift_report(
        db_async_session, model_name=MODEL, reported_by_user_id=admin.id, description="x"
    )

    (model,) = authenticated_client.get("/api/models").json()["models"]

    assert (model["open_drift_reports"], model["open_tickets"], model["active_jobs"]) == (1, 1, 1)


# ----------------------------------------------------------------------------------- drift


async def test_the_drift_view_lists_reports_with_who_filed_them(
    authenticated_client: TestClient, fake: FakeInference, db_async_session: AsyncSession
) -> None:
    qa = await _qa(db_async_session)
    await drift_repo.create_drift_report(
        db_async_session,
        model_name=MODEL,
        reported_by_user_id=qa.id,
        description="False Tombstone calls",
        case_ids=["c1"],
        case_numbers=["CASE-000007"],
        stats={"signals": ["override rate rose from 0% to 80%"]},
    )
    await drift_repo.create_drift_report(
        db_async_session, model_name="pcb_lead_defect", reported_by_user_id=qa.id, description="y"
    )

    body = authenticated_client.get("/api/models/drift").json()

    assert body["open_by_model"] == {MODEL: 1, "pcb_lead_defect": 1}
    assert body["reported_in_window_by_model"] == {MODEL: 1, "pcb_lead_defect": 1}
    only = authenticated_client.get(f"/api/models/drift?model={MODEL}").json()["reports"]
    assert len(only) == 1
    assert only[0]["reported_by"] == "second-qa"
    assert only[0]["case_numbers"] == ["CASE-000007"]
    assert only[0]["stats"]["signals"] == ["override rate rose from 0% to 80%"]


async def test_an_admin_can_resolve_a_drift_report_once(
    authenticated_client: TestClient, fake: FakeInference, db_async_session: AsyncSession
) -> None:
    report = await drift_repo.create_drift_report(
        db_async_session,
        model_name=MODEL,
        reported_by_user_id=(await _qa(db_async_session)).id,
        description="x",
    )
    url = f"/api/models/drift/{report.id}/resolve"

    first = authenticated_client.post(url)
    assert first.status_code == 200
    assert first.json()["status"] == "resolved" and first.json()["resolved_at"]
    assert authenticated_client.post(url).status_code == 409
    assert authenticated_client.post("/api/models/drift/nope/resolve").status_code == 404
    assert authenticated_client.get("/api/models/drift").json()["open_by_model"] == {}


# ---------------------------------------------------------------------------------- queue


async def test_the_queue_lists_jobs_with_ticket_counts_and_the_detail_shows_the_samples(
    authenticated_client: TestClient, fake: FakeInference, db_async_session: AsyncSession
) -> None:
    author = await _qa(db_async_session)
    job = await _plan(db_async_session, author, tickets=2)

    queue = authenticated_client.get("/api/models/retraining/queue").json()

    assert queue["tickets"] == {"open": 0, "acknowledged": 2, "resolved": 0}
    (listed,) = queue["jobs"]
    assert listed["id"] == job.id
    assert (listed["status"], listed["sample_count"], listed["created_by"]) == (
        "pending_approval",
        2,
        "second-qa",
    )
    detail = authenticated_client.get(_job_url(job.id)).json()
    assert len(detail["samples"]) == 2
    assert detail["samples"][0]["case_number"].startswith("CASE-")
    assert detail["samples"][0]["observed_label"] == "MissingPart"
    assert detail["samples"][0]["expected_label"] == "Golden"
    assert authenticated_client.get(_job_url("nope")).status_code == 404


async def test_a_workflow_origin_samples_case_id_is_null_with_a_sample_ref(
    authenticated_client: TestClient, fake: FakeInference, db_async_session: AsyncSession
) -> None:
    author = await _qa(db_async_session)
    await make_workflow_ticket(db_async_session, author, sample_ref="S1")
    job = await job_repo.draft_job(
        db_async_session, model_name=MODEL, created_by_user_id=author.id, rationale="bulk run"
    )

    detail = authenticated_client.get(_job_url(job.id)).json()

    (sample,) = detail["samples"]
    assert sample["case_id"] is None
    assert sample["case_number"] is None
    assert sample["sample_ref"] == "S1"


async def test_the_queue_can_be_filtered_by_status(
    authenticated_client: TestClient, fake: FakeInference, db_async_session: AsyncSession
) -> None:
    await _plan(db_async_session, await _qa(db_async_session))

    pending = authenticated_client.get("/api/models/retraining/queue?status=pending_approval")
    running = authenticated_client.get("/api/models/retraining/queue?status=running&status=queued")

    assert len(pending.json()["jobs"]) == 1
    assert running.json()["jobs"] == []


# --------------------------------------------------------------------------- approving a job


async def test_approving_sends_the_job_to_the_inference_service(
    authenticated_client: TestClient, fake: FakeInference, db_async_session: AsyncSession
) -> None:
    job = await _plan(db_async_session, await _qa(db_async_session), tickets=2)

    response = authenticated_client.post(_job_url(job.id, "approve"))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["approved_by"] == "test-qa"
    assert body["approved_at"] and body["error"] is None
    (remote,) = fake.jobs.values()
    assert remote["client_ref"] == job.id  # our id is what makes a retry safe
    assert remote["sample_count"] == 2
    assert body["external_job_id"] == remote["id"]
    assert remote["base_version"] == V1


async def test_approval_holds_when_the_inference_service_is_down_and_can_be_retried(
    authenticated_client: TestClient, fake: FakeInference, db_async_session: AsyncSession
) -> None:
    job = await _plan(db_async_session, await _qa(db_async_session))
    fake.down = True

    approved = authenticated_client.post(_job_url(job.id, "approve")).json()

    assert approved["status"] == "approved"
    assert approved["approved_by"] == "test-qa"
    assert "not sent to the inference service" in approved["error"]
    assert fake.jobs == {}

    fake.down = False
    retried = authenticated_client.post(_job_url(job.id, "submit")).json()

    assert retried["status"] == "queued"
    assert retried["error"] is None
    assert len(fake.jobs) == 1


async def test_retrying_a_send_never_duplicates_the_job(
    authenticated_client: TestClient, fake: FakeInference, db_async_session: AsyncSession
) -> None:
    job = await _plan(db_async_session, await _qa(db_async_session))
    authenticated_client.post(_job_url(job.id, "approve"))

    # an operator resets the row as if the response to the first send had been lost
    stored = await db_async_session.get(RetrainingJob, job.id)
    assert stored is not None
    await db_async_session.refresh(stored)
    stored.status = RetrainingJobStatus.APPROVED
    stored.external_job_id = None
    await db_async_session.commit()

    authenticated_client.post(_job_url(job.id, "submit"))

    assert len(fake.jobs) == 1


async def test_only_a_pending_job_can_be_approved_and_only_an_approved_one_resent(
    authenticated_client: TestClient, fake: FakeInference, db_async_session: AsyncSession
) -> None:
    job = await _plan(db_async_session, await _qa(db_async_session))

    assert authenticated_client.post(_job_url(job.id, "submit")).status_code == 409  # not approved
    assert authenticated_client.post(_job_url(job.id, "approve")).status_code == 200
    assert authenticated_client.post(_job_url(job.id, "approve")).status_code == 409  # already
    assert authenticated_client.post(_job_url("nope", "approve")).status_code == 404


async def test_a_job_the_service_rejects_stays_approved_with_the_reason(
    authenticated_client: TestClient, fake: FakeInference, db_async_session: AsyncSession
) -> None:
    job = await _plan(db_async_session, await _qa(db_async_session))
    del fake.models[MODEL]  # the service no longer has this model

    body = authenticated_client.post(_job_url(job.id, "approve")).json()

    assert body["status"] == "approved"
    assert "404" in body["error"]


# ------------------------------------------------------------------- progress and completion


async def test_the_queue_follows_a_job_through_to_success_and_registers_the_candidate(
    authenticated_client: TestClient, fake: FakeInference, db_async_session: AsyncSession
) -> None:
    job = await _plan(db_async_session, await _qa(db_async_session))
    authenticated_client.post(_job_url(job.id, "approve"))
    remote_id = fake.only_job_id()

    fake.set_job(remote_id, status="running", progress=0.4)
    running = authenticated_client.get("/api/models/retraining/queue").json()["jobs"][0]
    assert (running["status"], running["progress"]) == ("running", 0.4)

    succeeded(fake, remote_id, repo_id="JcProg/body", revision="v2")
    queue = authenticated_client.get("/api/models/retraining/queue").json()

    done = queue["jobs"][0]
    assert done["status"] == "succeeded"
    assert (done["artifact_repo_id"], done["artifact_revision"], done["simulated"]) == (
        "JcProg/body",
        "v2",
        False,
    )
    assert queue["tickets"]["resolved"] == 1
    (model,) = authenticated_client.get("/api/models").json()["models"]
    candidate = next(v for v in model["versions"] if v["version"] == V2)
    assert (candidate["status"], candidate["source_job_id"]) == ("candidate", job.id)


async def test_a_job_the_service_has_forgotten_is_failed_and_its_tickets_released(
    authenticated_client: TestClient, fake: FakeInference, db_async_session: AsyncSession
) -> None:
    job = await _plan(db_async_session, await _qa(db_async_session))
    authenticated_client.post(_job_url(job.id, "approve"))
    fake.forget_jobs()  # the service restarted

    queue = authenticated_client.get("/api/models/retraining/queue").json()

    lost = queue["jobs"][0]
    assert lost["status"] == "failed"
    assert "no longer knows" in lost["error"]
    assert queue["tickets"] == {"open": 1, "acknowledged": 0, "resolved": 0}


async def test_a_transient_error_leaves_a_running_job_alone(
    authenticated_client: TestClient, fake: FakeInference, db_async_session: AsyncSession
) -> None:
    job = await _plan(db_async_session, await _qa(db_async_session))
    authenticated_client.post(_job_url(job.id, "approve"))
    fake.down = True

    queue = authenticated_client.get("/api/models/retraining/queue").json()

    assert queue["inference"]["reachable"] is False
    assert queue["jobs"][0]["status"] == "queued"  # not failed just because we couldn't ask


# ------------------------------------------------------------------------------- cancelling


async def test_an_admin_can_cancel_a_pending_plan_and_its_tickets_are_freed(
    authenticated_client: TestClient, fake: FakeInference, db_async_session: AsyncSession
) -> None:
    job = await _plan(db_async_session, await _qa(db_async_session))

    body = authenticated_client.post(_job_url(job.id, "cancel")).json()

    assert body["status"] == "cancelled"
    tickets = (await db_async_session.scalars(select(RetrainingTicket))).all()
    await db_async_session.refresh(tickets[0])
    assert (tickets[0].status, tickets[0].job_id) == (RetrainingTicketStatus.OPEN, None)


async def test_cancelling_a_queued_job_stops_it_on_the_inference_service(
    authenticated_client: TestClient, fake: FakeInference, db_async_session: AsyncSession
) -> None:
    job = await _plan(db_async_session, await _qa(db_async_session))
    authenticated_client.post(_job_url(job.id, "approve"))
    remote_id = fake.only_job_id()

    body = authenticated_client.post(_job_url(job.id, "cancel")).json()

    assert body["status"] == "cancelled"
    assert fake.jobs[remote_id]["status"] == "cancelled"


async def test_a_job_is_not_cancelled_on_paper_while_it_keeps_training(
    authenticated_client: TestClient, fake: FakeInference, db_async_session: AsyncSession
) -> None:
    job = await _plan(db_async_session, await _qa(db_async_session))
    authenticated_client.post(_job_url(job.id, "approve"))
    fake.down = True

    response = authenticated_client.post(_job_url(job.id, "cancel"))

    assert response.status_code == 502
    fake.down = False
    assert authenticated_client.get(_job_url(job.id)).json()["status"] == "queued"


async def test_cancelling_a_job_that_finished_meanwhile_reports_how_it_ended(
    authenticated_client: TestClient, fake: FakeInference, db_async_session: AsyncSession
) -> None:
    job = await _plan(db_async_session, await _qa(db_async_session))
    authenticated_client.post(_job_url(job.id, "approve"))
    succeeded(fake, fake.only_job_id(), repo_id="JcProg/body", revision="v2")

    body = authenticated_client.post(_job_url(job.id, "cancel")).json()

    assert body["status"] == "succeeded"  # the truth, not a cancellation


async def test_a_finished_job_cannot_be_cancelled(
    authenticated_client: TestClient, fake: FakeInference, db_async_session: AsyncSession
) -> None:
    job = await _plan(db_async_session, await _qa(db_async_session))
    authenticated_client.post(_job_url(job.id, "cancel"))

    assert authenticated_client.post(_job_url(job.id, "cancel")).status_code == 409


async def test_a_qa_user_can_withdraw_their_own_pending_plan_and_only_that(
    authenticated_client: TestClient,
    qa_authenticated_client: TestClient,
    fake: FakeInference,
    db_async_session: AsyncSession,
) -> None:
    admin, qa = await _admin(db_async_session), await _qa(db_async_session)
    mine = await _plan(db_async_session, qa)
    theirs = await _plan(db_async_session, admin)
    approved = await _plan(db_async_session, qa)

    assert qa_authenticated_client.post(_job_url(theirs.id, "cancel")).status_code == 403
    authenticated_client.post(_job_url(approved.id, "approve"))
    assert qa_authenticated_client.post(_job_url(approved.id, "cancel")).status_code == 403
    assert qa_authenticated_client.post(_job_url(mine.id, "cancel")).status_code == 200


# ------------------------------------------------------------------- promoting and rolling back


async def test_promoting_a_candidate_makes_it_live(
    authenticated_client: TestClient, fake: FakeInference, db_async_session: AsyncSession
) -> None:
    authenticated_client.get("/api/models")
    await record_candidate(db_async_session, model_name=MODEL, version=V2, source_job_id=None)

    response = authenticated_client.post(f"/api/models/{MODEL}/promote", json={"version": V2})

    assert response.status_code == 200
    assert response.json()["version"] == V2 and response.json()["status"] == "live"
    assert fake.models[MODEL].version == V2  # the service really swapped
    (model,) = authenticated_client.get("/api/models").json()["models"]
    assert (model["live"]["version"], model["live"]["activated_by"]) == (V2, "test-qa")
    assert model["previous"]["version"] == V1


async def test_promoting_an_unknown_or_already_live_version_is_refused(
    authenticated_client: TestClient, fake: FakeInference, db_async_session: AsyncSession
) -> None:
    authenticated_client.get("/api/models")

    unknown = authenticated_client.post(f"/api/models/{MODEL}/promote", json={"version": V2})
    live = authenticated_client.post(f"/api/models/{MODEL}/promote", json={"version": V1})

    assert unknown.status_code == 404
    assert live.status_code == 409
    assert all(path != "/models/pcb_body_defect/activate" for _, path in fake.calls)


async def test_a_version_the_service_cannot_load_leaves_the_current_one_live(
    authenticated_client: TestClient, fake: FakeInference, db_async_session: AsyncSession
) -> None:
    authenticated_client.get("/api/models")
    await record_candidate(db_async_session, model_name=MODEL, version=V2, source_job_id=None)
    fake.activate_error = (422, "model produced 5 outputs but the manifest lists 2 labels")

    response = authenticated_client.post(f"/api/models/{MODEL}/promote", json={"version": V2})

    assert response.status_code == 422
    assert "5 outputs" in response.json()["detail"]
    (model,) = authenticated_client.get("/api/models").json()["models"]
    assert model["live"]["version"] == V1


async def test_promoting_while_the_service_is_down_is_a_bad_gateway(
    authenticated_client: TestClient, fake: FakeInference, db_async_session: AsyncSession
) -> None:
    authenticated_client.get("/api/models")
    await record_candidate(db_async_session, model_name=MODEL, version=V2, source_job_id=None)
    fake.down = True

    response = authenticated_client.post(f"/api/models/{MODEL}/promote", json={"version": V2})

    assert response.status_code == 502


async def test_rolling_back_restores_the_previous_version(
    authenticated_client: TestClient, fake: FakeInference, db_async_session: AsyncSession
) -> None:
    authenticated_client.get("/api/models")
    await record_candidate(db_async_session, model_name=MODEL, version=V2, source_job_id=None)
    authenticated_client.post(f"/api/models/{MODEL}/promote", json={"version": V2})

    response = authenticated_client.post(f"/api/models/{MODEL}/rollback")

    assert response.status_code == 200
    assert response.json()["version"] == V1
    (model,) = authenticated_client.get("/api/models").json()["models"]
    assert (model["live"]["version"], model["previous"]["version"]) == (V1, V2)


async def test_rolling_back_with_nothing_to_roll_back_to_is_a_conflict(
    authenticated_client: TestClient, fake: FakeInference
) -> None:
    authenticated_client.get("/api/models")

    response = authenticated_client.post(f"/api/models/{MODEL}/rollback")

    assert response.status_code == 409
    assert "no previous version" in response.json()["detail"]
    assert authenticated_client.post("/api/models/nope/rollback").status_code == 404
