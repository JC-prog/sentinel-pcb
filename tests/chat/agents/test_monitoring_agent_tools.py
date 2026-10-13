"""The monitoring agent's tools: reporting and summarising drift, drafting a
retraining plan, and the Admin overview. Nothing here may retrain or promote anything - the end
of the road is a job waiting for an Admin's approval."""

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.monitoring_agent import (
    DRAFT_RETRAINING_PLAN,
    GET_DRIFT_SUMMARY,
    GET_RETRAINING_JOB,
    LIST_DRIFT_REPORTS,
    MONITORING_STATUS,
    REPORT_MODEL_DRIFT,
)
from app.shared import inference
from app.shared.config.settings import settings
from app.shared.db.models import (
    DriftReport,
    RetrainingJob,
    RetrainingJobStatus,
    RetrainingTicketStatus,
    User,
)
from app.shared.modelops import jobs as job_repo
from app.shared.modelops.versions import sync_versions
from tests.chat.agents._helpers import call, tool_context
from tests.shared._modelops_helpers import make_case, make_ticket, make_user, model_info

MODEL = "pcb_body_defect"


@pytest.fixture(autouse=True)
def _no_inference_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """Status and job reads sync with the inference service first. A developer's .env may point at
    a running one, which would put its models into these tests' database - so default to none."""

    monkeypatch.setattr(settings, "inference_base_url", "")


async def _run(tool: Any, session: AsyncSession, user: User, **kwargs: Any) -> Any:
    return await call(tool, tool_context(session, user), **kwargs)


async def test_reporting_drift_files_a_report_with_a_snapshot_of_the_numbers(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    case = await make_case(db_async_session, user)
    await sync_versions(db_async_session, [model_info(MODEL, "JcProg/body@v2", "JcProg/body@v1")])

    result = await _run(
        REPORT_MODEL_DRIFT,
        db_async_session,
        user,
        model=MODEL,
        description="Lots of false Tombstone calls since Monday",
        case_numbers=[case.case_number, "CASE-999999", "garbage"],
    )

    assert result["evidence_cases"] == 1
    assert result["unknown_case_numbers"] == ["CASE-999999", "garbage"]
    assert result["model_version"] == "JcProg/body@v2"  # what is live now
    assert result["open_drift_reports_for_model"] == 1
    (report,) = (await db_async_session.scalars(select(DriftReport))).all()
    assert report.case_ids == [case.id]
    assert report.stats["model"] == MODEL
    assert report.stats["corrections"] == {"recent": 0, "previous": 0}  # no ticket yet
    assert "not the model's error rate" in report.stats["basis"]
    assert report.reported_by_user_id == user.id


async def test_reporting_drift_needs_a_model_and_a_description(
    db_async_session: AsyncSession,
) -> None:
    """Omitting either is caught by the tool's schema before it runs; blank values by the tool."""

    user = await make_user(db_async_session)
    schema = REPORT_MODEL_DRIFT.model_schema()
    assert set(schema["required"]) == {"model", "description"}

    for kwargs in ({"model": MODEL, "description": " "}, {"model": " ", "description": "x"}):
        result = await _run(REPORT_MODEL_DRIFT, db_async_session, user, **kwargs)
        assert "required" in result["error"]
    assert (await db_async_session.scalars(select(DriftReport))).all() == []


async def test_the_drift_summary_includes_the_live_version_and_open_counts(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    await make_case(db_async_session, user)
    await make_ticket(db_async_session, user)
    await sync_versions(db_async_session, [model_info(MODEL, "JcProg/body@v1")])
    await _run(REPORT_MODEL_DRIFT, db_async_session, user, model=MODEL, description="looks off")

    result = await _run(GET_DRIFT_SUMMARY, db_async_session, user, model=MODEL, window_days=14)

    assert result["model"] == MODEL
    assert result["window_days"] == 14
    assert result["live_version"] == "JcProg/body@v1"
    assert result["open_drift_reports"] == 1
    assert result["open_retraining_tickets"] == 1
    assert {"corrections", "by_version", "signals", "enough_data", "basis"} <= set(result)
    assert result["corrections"] == {"recent": 1, "previous": 0}
    assert result["by_version"] == {"JcProg/body@v1": {"recent": 1, "previous": 0}}


async def test_the_window_is_kept_within_sane_bounds(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)

    huge = await _run(GET_DRIFT_SUMMARY, db_async_session, user, model=MODEL, window_days=9999)
    negative = await _run(
        GET_DRIFT_SUMMARY, db_async_session, user, model=MODEL, window_days=-3
    )
    omitted = await _run(GET_DRIFT_SUMMARY, db_async_session, user, model=MODEL, window_days=0)

    assert huge["window_days"] == 90
    assert negative["window_days"] == 1
    assert omitted["window_days"] == 7  # 0 means "not given" - the default applies


async def test_drafting_a_plan_queues_a_pending_job_for_admin_approval(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    await make_ticket(db_async_session, user)
    await make_ticket(db_async_session, user)
    await sync_versions(db_async_session, [model_info(MODEL, "JcProg/body@v1")])
    await _run(REPORT_MODEL_DRIFT, db_async_session, user, model=MODEL, description="looks off")

    result = await _run(DRAFT_RETRAINING_PLAN, db_async_session, user, model=MODEL)

    assert result["status"] == "pending_approval"
    assert result["flagged_cases"] == 2
    assert result["drift_reports_linked"] == 1
    assert result["base_version"] == "JcProg/body@v1"
    assert "Admin must approve" in result["next_step"]
    (job,) = (await db_async_session.scalars(select(RetrainingJob))).all()
    assert job.status == RetrainingJobStatus.PENDING_APPROVAL
    assert job.approved_by_user_id is None and job.external_job_id is None  # nothing was sent
    assert "Retrain pcb_body_defect" in job.rationale
    assert "1 open drift report" in job.rationale


async def test_a_users_own_rationale_is_kept(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)
    await make_ticket(db_async_session, user)
    await sync_versions(db_async_session, [model_info(MODEL, "JcProg/body@v1")])

    await _run(
        DRAFT_RETRAINING_PLAN,
        db_async_session,
        user,
        model=MODEL,
        rationale="New supplier changed the package finish",
    )

    (job,) = (await db_async_session.scalars(select(RetrainingJob))).all()
    assert job.rationale == "New supplier changed the package finish"


async def test_drafting_with_nothing_flagged_says_so(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)

    result = await _run(DRAFT_RETRAINING_PLAN, db_async_session, user, model=MODEL)

    assert "no open retraining tickets" in result["error"]
    assert (await db_async_session.scalars(select(RetrainingJob))).all() == []


async def test_drafting_uses_each_flagged_case_only_once(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)
    await make_ticket(db_async_session, user)
    await sync_versions(db_async_session, [model_info(MODEL, "JcProg/body@v1")])
    await _run(DRAFT_RETRAINING_PLAN, db_async_session, user, model=MODEL)

    again = await _run(DRAFT_RETRAINING_PLAN, db_async_session, user, model=MODEL)

    assert "no open retraining tickets" in again["error"]  # the first plan already holds it


async def test_drafting_without_a_known_version_explains_how_to_fix_it(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    await make_ticket(db_async_session, user, model_version=None)

    result = await _run(DRAFT_RETRAINING_PLAN, db_async_session, user, model=MODEL)

    assert "Models tab" in result["error"]


async def test_monitoring_status_on_an_empty_system(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)

    result = await _run(MONITORING_STATUS, db_async_session, user)

    assert result["live_models"] == {}
    assert result["retraining_queue"] == []
    assert "No model versions are recorded" in result["note"]


async def test_monitoring_status_reports_versions_reports_tickets_and_the_queue(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    await make_ticket(db_async_session, user)
    await sync_versions(db_async_session, [model_info(MODEL, "JcProg/body@v1")])
    await _run(REPORT_MODEL_DRIFT, db_async_session, user, model=MODEL, description="looks off")
    plan = await _run(DRAFT_RETRAINING_PLAN, db_async_session, user, model=MODEL)
    await make_ticket(db_async_session, user)  # a fresh open ticket, not yet in any plan

    result = await _run(MONITORING_STATUS, db_async_session, user)

    assert result["live_models"] == {MODEL: "JcProg/body@v1"}
    assert result["open_drift_reports"] == {MODEL: 1}
    assert result["open_retraining_tickets"] == {MODEL: 1}
    (queued,) = result["retraining_queue"]
    assert queued["job_id"] == plan["job_id"]
    assert queued["status"] == "pending_approval"
    assert queued["samples"] == 1
    assert "note" not in result


async def test_a_cancelled_job_leaves_the_queue_and_frees_its_tickets(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    ticket = await make_ticket(db_async_session, user)
    await sync_versions(db_async_session, [model_info(MODEL, "JcProg/body@v1")])
    plan = await _run(DRAFT_RETRAINING_PLAN, db_async_session, user, model=MODEL)
    job = await job_repo.get_job(db_async_session, plan["job_id"])
    assert job is not None
    await job_repo.cancel_job(db_async_session, job)

    result = await _run(MONITORING_STATUS, db_async_session, user)

    assert result["retraining_queue"] == []
    await db_async_session.refresh(ticket)
    assert ticket.status == RetrainingTicketStatus.OPEN


async def test_list_drift_reports_shows_what_was_filed_and_filters_by_status(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    await _run(REPORT_MODEL_DRIFT, db_async_session, user, model=MODEL, description="false Golden calls")
    await _run(REPORT_MODEL_DRIFT, db_async_session, user, model="pcb_region", description="odd regions")
    report = (await db_async_session.scalars(select(DriftReport).where(DriftReport.model_name == MODEL))).one()
    report.status = "resolved"
    await db_async_session.commit()

    everything = await _run(LIST_DRIFT_REPORTS, db_async_session, user)
    only_open = await _run(LIST_DRIFT_REPORTS, db_async_session, user, status="open")
    one_model = await _run(LIST_DRIFT_REPORTS, db_async_session, user, model=MODEL)

    assert everything["shown"] == 2 and everything["more_not_shown"] is False
    assert [r["model"] for r in only_open["reports"]] == ["pcb_region"]
    (row,) = one_model["reports"]
    assert row["description"] == "false Golden calls" and row["status"] == "resolved"
    assert row["auto_raised"] is False
    assert "error" in await _run(LIST_DRIFT_REPORTS, db_async_session, user, status="bogus")


async def test_get_retraining_job_reports_a_plan_awaiting_approval(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    await make_ticket(db_async_session, user)
    await sync_versions(db_async_session, [model_info(MODEL, "JcProg/body@v1")])
    plan = await _run(DRAFT_RETRAINING_PLAN, db_async_session, user, model=MODEL)

    named = await _run(GET_RETRAINING_JOB, db_async_session, user, job_id=plan["job_id"])
    newest = await _run(GET_RETRAINING_JOB, db_async_session, user)

    assert named["job_id"] == newest["job_id"] == plan["job_id"]
    assert named["status"] == "pending_approval" and named["approval_needed"] is True
    assert named["flagged_total"] == 1 and len(named["flagged"]) == 1
    assert named["data_is_current"] is False  # no inference service configured in tests


async def test_get_retraining_job_says_when_a_finished_run_was_simulated(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    await make_ticket(db_async_session, user)
    await sync_versions(db_async_session, [model_info(MODEL, "JcProg/body@v1")])
    plan = await _run(DRAFT_RETRAINING_PLAN, db_async_session, user, model=MODEL)
    job = await job_repo.get_job(db_async_session, plan["job_id"])
    assert job is not None
    job.status, job.simulated = RetrainingJobStatus.SUCCEEDED, True
    job.artifact_repo_id, job.artifact_revision = "JcProg/body", "v1"
    await db_async_session.commit()

    result = await _run(GET_RETRAINING_JOB, db_async_session, user, job_id=job.id)

    assert result["status"] == "succeeded" and result["simulated"] is True
    assert result["result_version"] == "JcProg/body@v1"
    assert result["approval_needed"] is False


async def test_get_retraining_job_refuses_unknown_ids_and_an_empty_queue(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)

    assert "no retraining jobs yet" in (await _run(GET_RETRAINING_JOB, db_async_session, user))["error"]
    assert "no-such" in (await _run(GET_RETRAINING_JOB, db_async_session, user, job_id="no-such"))["error"]


async def test_a_job_the_inference_service_lost_is_failed_once_and_status_says_data_is_current(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await make_user(db_async_session)
    await make_ticket(db_async_session, user)
    await sync_versions(db_async_session, [model_info(MODEL, "JcProg/body@v1")])
    plan = await _run(DRAFT_RETRAINING_PLAN, db_async_session, user, model=MODEL)
    job = await job_repo.get_job(db_async_session, plan["job_id"])
    assert job is not None
    job.status, job.external_job_id = RetrainingJobStatus.RUNNING, "ext-1"
    await db_async_session.commit()

    async def no_models() -> list[Any]:
        return []

    async def unknown_job(job_id: str) -> Any:
        raise inference.InferenceNotFound(job_id)

    monkeypatch.setattr(inference, "list_models", no_models)
    monkeypatch.setattr(inference, "get_job", unknown_job)

    first = await _run(MONITORING_STATUS, db_async_session, user)
    second = await _run(MONITORING_STATUS, db_async_session, user)

    assert first["data_is_current"] is True and second["data_is_current"] is True
    assert first["retraining_queue"] == [] == second["retraining_queue"]
    await db_async_session.refresh(job)
    assert job.status == RetrainingJobStatus.FAILED
