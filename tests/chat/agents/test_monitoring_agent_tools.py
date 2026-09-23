"""The monitoring agent's tools: flagging cases, reporting and summarising drift, drafting a
retraining plan, and the Admin overview. Nothing here may retrain or promote anything - the end
of the road is a job waiting for an Admin's approval."""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.monitoring_agent import (
    DraftRetrainingPlanTool,
    FlagCaseForRetrainingTool,
    GetDriftSummaryTool,
    MonitoringAgentTool,
    ReportModelDriftTool,
)
from app.shared.db.models import (
    DriftReport,
    RetrainingJob,
    RetrainingJobStatus,
    RetrainingTicket,
    RetrainingTicketStatus,
    User,
)
from app.shared.modelops import jobs as job_repo
from app.shared.modelops.versions import sync_versions
from tests.shared._modelops_helpers import make_case, make_ticket, make_user, model_info

MODEL = "pcb_body_defect"


async def _run(tool: Any, session: AsyncSession, user: User, **kwargs: Any) -> Any:
    return json.loads(await tool.run(session=session, user_id=user.id, **kwargs))


async def test_flagging_a_case_records_the_model_version_and_the_correct_label(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    case = await make_case(db_async_session, user)  # classified by pcb_body_defect@v1

    result = await _run(
        FlagCaseForRetrainingTool(),
        db_async_session,
        user,
        case_number=case.case_number,
        reason="It is a golden part",
        correct_label="Golden",
    )

    assert result["model"] == MODEL
    assert result["model_version"] == "JcProg/body@v1"
    (ticket,) = (await db_async_session.scalars(select(RetrainingTicket))).all()
    assert (ticket.observed_label, ticket.correct_label) == ("MissingPart", "Golden")


async def test_reporting_drift_files_a_report_with_a_snapshot_of_the_numbers(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    case = await make_case(db_async_session, user)
    await sync_versions(db_async_session, [model_info(MODEL, "JcProg/body@v2", "JcProg/body@v1")])

    result = await _run(
        ReportModelDriftTool(),
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
    assert report.stats["recent"]["cases"] == 1
    assert report.reported_by_user_id == user.id


async def test_reporting_drift_needs_a_model_and_a_description(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)

    for kwargs in ({"model": MODEL}, {"description": "x"}, {"model": " ", "description": " "}):
        result = await _run(ReportModelDriftTool(), db_async_session, user, **kwargs)
        assert "required" in result["error"]
    assert (await db_async_session.scalars(select(DriftReport))).all() == []


async def test_the_drift_summary_includes_the_live_version_and_open_counts(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    await make_case(db_async_session, user)
    await make_ticket(db_async_session, user)
    await sync_versions(db_async_session, [model_info(MODEL, "JcProg/body@v1")])
    await _run(ReportModelDriftTool(), db_async_session, user, model=MODEL, description="looks off")

    result = await _run(GetDriftSummaryTool(), db_async_session, user, model=MODEL, window_days=14)

    assert result["model"] == MODEL
    assert result["window_days"] == 14
    assert result["live_version"] == "JcProg/body@v1"
    assert result["open_drift_reports"] == 1
    assert result["open_retraining_tickets"] == 1
    assert {"recent", "previous", "by_version", "signals", "enough_data"} <= set(result)


async def test_the_window_is_kept_within_sane_bounds(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)

    huge = await _run(GetDriftSummaryTool(), db_async_session, user, model=MODEL, window_days=9999)
    negative = await _run(
        GetDriftSummaryTool(), db_async_session, user, model=MODEL, window_days=-3
    )
    omitted = await _run(GetDriftSummaryTool(), db_async_session, user, model=MODEL, window_days=0)

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
    await _run(ReportModelDriftTool(), db_async_session, user, model=MODEL, description="looks off")

    result = await _run(DraftRetrainingPlanTool(), db_async_session, user, model=MODEL)

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
        DraftRetrainingPlanTool(),
        db_async_session,
        user,
        model=MODEL,
        rationale="New supplier changed the package finish",
    )

    (job,) = (await db_async_session.scalars(select(RetrainingJob))).all()
    assert job.rationale == "New supplier changed the package finish"


async def test_drafting_with_nothing_flagged_says_so(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)

    result = await _run(DraftRetrainingPlanTool(), db_async_session, user, model=MODEL)

    assert "no open retraining tickets" in result["error"]
    assert (await db_async_session.scalars(select(RetrainingJob))).all() == []


async def test_drafting_uses_each_flagged_case_only_once(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)
    await make_ticket(db_async_session, user)
    await sync_versions(db_async_session, [model_info(MODEL, "JcProg/body@v1")])
    await _run(DraftRetrainingPlanTool(), db_async_session, user, model=MODEL)

    again = await _run(DraftRetrainingPlanTool(), db_async_session, user, model=MODEL)

    assert "no open retraining tickets" in again["error"]  # the first plan already holds it


async def test_drafting_without_a_known_version_explains_how_to_fix_it(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    await make_ticket(db_async_session, user, model_version=None)

    result = await _run(DraftRetrainingPlanTool(), db_async_session, user, model=MODEL)

    assert "Models tab" in result["error"]


async def test_monitoring_status_on_an_empty_system(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)

    result = await _run(MonitoringAgentTool(), db_async_session, user)

    assert result["live_models"] == {}
    assert result["retraining_queue"] == []
    assert "No model versions are recorded" in result["note"]


async def test_monitoring_status_reports_versions_reports_tickets_and_the_queue(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    await make_ticket(db_async_session, user)
    await sync_versions(db_async_session, [model_info(MODEL, "JcProg/body@v1")])
    await _run(ReportModelDriftTool(), db_async_session, user, model=MODEL, description="looks off")
    plan = await _run(DraftRetrainingPlanTool(), db_async_session, user, model=MODEL)
    await make_ticket(db_async_session, user)  # a fresh open ticket, not yet in any plan

    result = await _run(MonitoringAgentTool(), db_async_session, user)

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
    plan = await _run(DraftRetrainingPlanTool(), db_async_session, user, model=MODEL)
    job = await job_repo.get_job(db_async_session, plan["job_id"])
    assert job is not None
    await job_repo.cancel_job(db_async_session, job)

    result = await _run(MonitoringAgentTool(), db_async_session, user)

    assert result["retraining_queue"] == []
    await db_async_session.refresh(ticket)
    assert ticket.status == RetrainingTicketStatus.OPEN
