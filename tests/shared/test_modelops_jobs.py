import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models import (
    RetrainingJob,
    RetrainingJobStatus,
    RetrainingTicket,
    RetrainingTicketStatus,
    User,
    UserRole,
)
from app.shared.modelops import jobs
from app.shared.modelops.versions import list_versions, sync_versions
from tests.shared._modelops_helpers import (
    make_ticket,
    make_user,
    make_workflow_ticket,
    model_info,
    remote_job,
)

NAME = "pcb_body_defect"
V1 = "JcProg/body@v1"


async def _draft(session: AsyncSession, user: User) -> RetrainingJob:
    return await jobs.draft_job(
        session, model_name=NAME, created_by_user_id=user.id, rationale="drift on Body"
    )


async def _ticket_states(session: AsyncSession) -> set[tuple[str, str | None]]:
    rows = (await session.scalars(select(RetrainingTicket))).all()
    return {(t.status, t.job_id) for t in rows}


async def test_draft_collects_open_tickets_into_a_pending_job(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    ticket = await make_ticket(db_async_session, user)
    await sync_versions(db_async_session, [model_info(NAME, V1)])

    job = await _draft(db_async_session, user)

    assert job.status == RetrainingJobStatus.PENDING_APPROVAL
    assert job.base_version == V1  # the live version
    assert job.samples == [
        {
            "case_id": ticket.case_id,
            "case_number": ticket.case_number,
            "sample_ref": None,
            "ticket_id": ticket.id,
            "observed_label": "MissingPart",
            "expected_label": "Golden",
        }
    ]
    await db_async_session.refresh(ticket)
    assert (ticket.status, ticket.job_id) == (RetrainingTicketStatus.ACKNOWLEDGED, job.id)


async def test_draft_mixes_chat_and_workflow_origin_tickets_for_the_same_model(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    chat_ticket = await make_ticket(db_async_session, user)
    workflow_ticket = await make_workflow_ticket(db_async_session, user, sample_ref="S1")
    await sync_versions(db_async_session, [model_info(NAME, V1)])

    job = await _draft(db_async_session, user)

    assert {(s["case_id"], s["sample_ref"]) for s in job.samples} == {
        (chat_ticket.case_id, None),
        (None, "S1"),
    }
    assert {s["ticket_id"] for s in job.samples} == {chat_ticket.id, workflow_ticket.id}
    await db_async_session.refresh(chat_ticket)
    await db_async_session.refresh(workflow_ticket)
    assert chat_ticket.status == RetrainingTicketStatus.ACKNOWLEDGED
    assert workflow_ticket.status == RetrainingTicketStatus.ACKNOWLEDGED
    assert {chat_ticket.job_id, workflow_ticket.job_id} == {job.id}


async def test_draft_ignores_other_models_and_tickets_already_in_a_job(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    await make_ticket(db_async_session, user)
    await make_ticket(db_async_session, user, model_name="pcb_lead_defect")
    await sync_versions(db_async_session, [model_info(NAME, V1)])
    first = await _draft(db_async_session, user)
    assert len(first.samples) == 1

    with pytest.raises(jobs.NothingToRetrain):  # the body ticket is spoken for
        await _draft(db_async_session, user)


async def test_draft_with_no_open_tickets_raises(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)
    with pytest.raises(jobs.NothingToRetrain):
        await _draft(db_async_session, user)


async def test_draft_falls_back_to_the_ticket_version_when_no_live_version_is_known(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    await make_ticket(db_async_session, user, model_version="JcProg/body@v0")

    job = await _draft(db_async_session, user)

    assert job.base_version == "JcProg/body@v0"


async def test_draft_without_any_version_information_raises(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    await make_ticket(db_async_session, user, model_version=None)

    with pytest.raises(jobs.BaseVersionUnknown):
        await _draft(db_async_session, user)


async def test_approval_records_who_and_cannot_repeat(db_async_session: AsyncSession) -> None:
    qa = await make_user(db_async_session)
    admin = await make_user(db_async_session, UserRole.ADMIN)
    await make_ticket(db_async_session, qa)
    await sync_versions(db_async_session, [model_info(NAME, V1)])
    job = await _draft(db_async_session, qa)

    approved = await jobs.approve_job(db_async_session, job, approved_by_user_id=admin.id)

    assert approved.status == RetrainingJobStatus.APPROVED
    assert approved.approved_by_user_id == admin.id
    assert approved.approved_at is not None
    with pytest.raises(jobs.InvalidTransition):
        await jobs.approve_job(db_async_session, approved, approved_by_user_id=admin.id)


async def test_cancelling_releases_the_tickets(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)
    await make_ticket(db_async_session, user)
    await sync_versions(db_async_session, [model_info(NAME, V1)])
    job = await _draft(db_async_session, user)

    cancelled = await jobs.cancel_job(db_async_session, job)

    assert cancelled.status == RetrainingJobStatus.CANCELLED
    assert cancelled.finished_at is not None
    assert await _ticket_states(db_async_session) == {("open", None)}
    # ...so they can go into a fresh plan
    assert len((await _draft(db_async_session, user)).samples) == 1


async def test_a_finished_job_cannot_be_cancelled(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)
    await make_ticket(db_async_session, user)
    await sync_versions(db_async_session, [model_info(NAME, V1)])
    job = await _draft(db_async_session, user)
    await jobs.cancel_job(db_async_session, job)

    with pytest.raises(jobs.InvalidTransition):
        await jobs.cancel_job(db_async_session, job)


async def _approved_job(session: AsyncSession) -> RetrainingJob:
    user = await make_user(session, UserRole.ADMIN)
    await make_ticket(session, user)
    await sync_versions(session, [model_info(NAME, V1)])
    job = await _draft(session, user)
    return await jobs.approve_job(session, job, approved_by_user_id=user.id)


async def test_mark_submitted_queues_the_job_and_is_safe_to_repeat(
    db_async_session: AsyncSession,
) -> None:
    job = await _approved_job(db_async_session)

    await jobs.mark_submitted(db_async_session, job, external_job_id="remote-1")
    await jobs.mark_submitted(db_async_session, job, external_job_id="remote-1")

    assert job.status == RetrainingJobStatus.QUEUED
    assert job.external_job_id == "remote-1"


async def test_remote_progress_moves_the_job_to_running(db_async_session: AsyncSession) -> None:
    job = await _approved_job(db_async_session)
    await jobs.mark_submitted(db_async_session, job, external_job_id="remote-1")

    await jobs.apply_remote(db_async_session, job, remote_job("running", progress=0.4))

    assert job.status == RetrainingJobStatus.RUNNING
    assert job.progress == 0.4
    assert job.started_at is not None


async def test_remote_success_records_the_artifact_and_resolves_tickets(
    db_async_session: AsyncSession,
) -> None:
    job = await _approved_job(db_async_session)
    await jobs.mark_submitted(db_async_session, job, external_job_id="remote-1")

    await jobs.apply_remote(
        db_async_session,
        job,
        remote_job("succeeded", artifact=("JcProg/body", "v2"), simulated=False),
    )

    assert job.status == RetrainingJobStatus.SUCCEEDED
    assert job.progress == 1.0
    assert (job.artifact_repo_id, job.artifact_revision, job.simulated) == (
        "JcProg/body",
        "v2",
        False,
    )
    assert job.finished_at is not None
    ((status, _),) = await _ticket_states(db_async_session)
    assert status == "resolved"
    # a genuinely new version is registered as a candidate, ready for an Admin to activate
    candidates = {
        v.version: (v.status, v.source_job_id) for v in await list_versions(db_async_session)
    }
    assert candidates["JcProg/body@v2"] == ("candidate", job.id)


async def test_a_simulated_success_that_reuses_the_live_version_adds_no_candidate(
    db_async_session: AsyncSession,
) -> None:
    job = await _approved_job(db_async_session)

    await jobs.apply_remote(
        db_async_session,
        job,
        remote_job("succeeded", artifact=("JcProg/body", "v1"), simulated=True),
    )

    assert job.simulated is True
    assert {v.status for v in await list_versions(db_async_session)} == {"live"}


async def test_remote_failure_keeps_the_error_and_releases_tickets(
    db_async_session: AsyncSession,
) -> None:
    job = await _approved_job(db_async_session)

    await jobs.apply_remote(db_async_session, job, remote_job("failed", error="out of memory"))

    assert job.status == RetrainingJobStatus.FAILED
    assert job.error == "out of memory"
    assert await _ticket_states(db_async_session) == {("open", None)}


async def test_remote_cancellation_releases_tickets(db_async_session: AsyncSession) -> None:
    job = await _approved_job(db_async_session)

    await jobs.apply_remote(db_async_session, job, remote_job("cancelled"))

    assert job.status == RetrainingJobStatus.CANCELLED
    assert await _ticket_states(db_async_session) == {("open", None)}


async def test_a_stale_remote_status_never_moves_a_job_backwards(
    db_async_session: AsyncSession,
) -> None:
    job = await _approved_job(db_async_session)
    await jobs.apply_remote(db_async_session, job, remote_job("running", progress=0.5))

    await jobs.apply_remote(db_async_session, job, remote_job("queued"))

    assert job.status == RetrainingJobStatus.RUNNING


async def test_a_terminal_job_ignores_further_remote_reports(
    db_async_session: AsyncSession,
) -> None:
    job = await _approved_job(db_async_session)
    await jobs.apply_remote(db_async_session, job, remote_job("failed", error="boom"))

    await jobs.apply_remote(
        db_async_session, job, remote_job("succeeded", artifact=("JcProg/body", "v2"))
    )

    assert job.status == RetrainingJobStatus.FAILED


async def test_lost_jobs_fail_and_release_their_tickets(db_async_session: AsyncSession) -> None:
    job = await _approved_job(db_async_session)
    await jobs.mark_submitted(db_async_session, job, external_job_id="remote-1")

    await jobs.mark_lost(db_async_session, job, "inference service no longer knows this job")

    assert job.status == RetrainingJobStatus.FAILED
    assert job.error == "inference service no longer knows this job"
    assert await _ticket_states(db_async_session) == {("open", None)}


async def test_marking_a_finished_job_lost_changes_nothing(
    db_async_session: AsyncSession,
) -> None:
    job = await _approved_job(db_async_session)
    await jobs.apply_remote(
        db_async_session, job, remote_job("succeeded", artifact=("JcProg/body", "v1"))
    )

    await jobs.mark_lost(db_async_session, job, "gone")

    assert job.status == RetrainingJobStatus.SUCCEEDED
    assert job.error is None


async def test_list_jobs_filters_by_status_and_model(db_async_session: AsyncSession) -> None:
    job = await _approved_job(db_async_session)

    pending = await jobs.list_jobs(
        db_async_session, statuses=[RetrainingJobStatus.PENDING_APPROVAL]
    )
    approved = await jobs.list_jobs(db_async_session, statuses=[RetrainingJobStatus.APPROVED])
    other_model = await jobs.list_jobs(db_async_session, model_name="pcb_lead_defect")

    assert pending == []
    assert [j.id for j in approved] == [job.id]
    assert other_model == []
