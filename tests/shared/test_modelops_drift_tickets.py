from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models import DriftReportStatus, RetrainingTicket, RetrainingTicketStatus
from app.shared.modelops import drift, tickets
from tests.shared._modelops_helpers import (
    make_case,
    make_ticket,
    make_user,
    make_workflow_ticket,
)


async def test_a_drift_report_stores_the_evidence_and_the_numbers(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)

    report = await drift.create_drift_report(
        db_async_session,
        model_name="pcb_body_defect",
        reported_by_user_id=user.id,
        description="lots of false Tombstone calls since Monday",
        model_version="JcProg/body@v1",
        case_ids=["c1", "c2"],
        stats={"override_rate": 0.4, "cases": 25},
    )

    assert report.status == DriftReportStatus.OPEN
    assert report.case_ids == ["c1", "c2"]
    assert report.stats == {"override_rate": 0.4, "cases": 25}


async def test_drift_reports_filter_count_and_resolve(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)
    body = await drift.create_drift_report(
        db_async_session, model_name="pcb_body_defect", reported_by_user_id=user.id, description="a"
    )
    await drift.create_drift_report(
        db_async_session, model_name="pcb_body_defect", reported_by_user_id=user.id, description="b"
    )
    await drift.create_drift_report(
        db_async_session, model_name="pcb_lead_defect", reported_by_user_id=user.id, description="c"
    )

    assert await drift.count_reports_by_model(db_async_session) == {
        "pcb_body_defect": 2,
        "pcb_lead_defect": 1,
    }
    assert len(await drift.list_drift_reports(db_async_session, model_name="pcb_lead_defect")) == 1

    await drift.resolve_drift_report(db_async_session, body)

    assert body.status == DriftReportStatus.RESOLVED and body.resolved_at is not None
    assert await drift.count_reports_by_model(
        db_async_session, status=DriftReportStatus.OPEN
    ) == {"pcb_body_defect": 1, "pcb_lead_defect": 1}


async def test_drift_report_counts_can_be_limited_to_a_window(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    await drift.create_drift_report(
        db_async_session, model_name="pcb_body_defect", reported_by_user_id=user.id, description="a"
    )

    tomorrow = datetime.now(UTC) + timedelta(days=1)
    yesterday = datetime.now(UTC) - timedelta(days=1)

    assert await drift.count_reports_by_model(db_async_session, since=tomorrow) == {}
    assert await drift.count_reports_by_model(db_async_session, since=yesterday) == {
        "pcb_body_defect": 1
    }


async def test_a_ticket_records_the_verdict_it_disputes(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)

    ticket = await make_ticket(db_async_session, user)

    assert ticket.status == RetrainingTicketStatus.OPEN
    assert ticket.job_id is None
    assert (ticket.model_name, ticket.model_version) == ("pcb_body_defect", "JcProg/body@v1")
    assert (ticket.observed_label, ticket.correct_label) == ("MissingPart", "Golden")


async def test_a_ticket_may_omit_the_model_and_the_correct_label(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    case = await make_case(db_async_session, user)

    ticket = await tickets.create_ticket(
        db_async_session, case_id=case.id, flagged_by_user_id=user.id, reason="just wrong"
    )

    assert ticket.model_name is None and ticket.correct_label is None


async def test_a_workflow_origin_ticket_has_a_sample_ref_instead_of_a_case(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)

    ticket = await make_workflow_ticket(db_async_session, user, sample_ref="S1")

    assert (ticket.case_id, ticket.case_number) == (None, None)
    assert ticket.sample_ref == "S1"
    assert ticket.model_name == "pcb_body_defect"


async def test_creating_a_ticket_with_neither_case_nor_sample_is_rejected(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)

    with pytest.raises(ValueError, match="case_id or sample_ref"):
        await tickets.create_ticket(
            db_async_session, flagged_by_user_id=user.id, reason="no reference given"
        )


async def test_the_database_rejects_a_ticket_with_neither_case_nor_sample(
    db_async_session: AsyncSession,
) -> None:
    """Backstop below the app-level check in create_ticket - a raw insert must still be refused."""

    user = await make_user(db_async_session)

    db_async_session.add(
        RetrainingTicket(flagged_by_user_id=user.id, reason="bypassing create_ticket")
    )
    with pytest.raises(IntegrityError):
        await db_async_session.commit()
    await db_async_session.rollback()


async def test_only_open_unassigned_tickets_of_the_model_are_listed(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    open_ticket = await make_ticket(db_async_session, user)
    await make_ticket(db_async_session, user, model_name="pcb_lead_defect")
    taken = await make_ticket(db_async_session, user)
    taken.status = RetrainingTicketStatus.ACKNOWLEDGED
    await db_async_session.commit()

    listed = await tickets.list_open_tickets(db_async_session, "pcb_body_defect")

    assert [t.id for t in listed] == [open_ticket.id]


async def test_tickets_are_counted_per_model_and_status(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)
    await make_ticket(db_async_session, user)
    await make_ticket(db_async_session, user)
    await make_ticket(db_async_session, user, model_name=None, model_version=None)

    assert await tickets.count_tickets(db_async_session) == {"pcb_body_defect": 2, "": 1}
    assert await tickets.count_tickets(
        db_async_session, status=RetrainingTicketStatus.RESOLVED
    ) == {}
