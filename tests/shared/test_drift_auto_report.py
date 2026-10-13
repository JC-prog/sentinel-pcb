"""A drift report raises itself when one model version collects enough corrections inside the
window (settings.drift_auto_report_threshold / _window_days). Both origins of a correction - chat's
relabel and the Work tab's queued corrections - end in tickets.create_ticket, which is the hook."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.config.settings import settings
from app.shared.db.models import DriftReport, DriftReportStatus, RetrainingTicket, User
from app.shared.modelops import drift as drift_repo
from app.shared.modelops.tickets import create_ticket
from tests.shared._modelops_helpers import make_case, make_user

MODEL = "pcb_body_defect"
VERSION = "JcProg/body@v1"


@pytest.fixture(autouse=True)
def _threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "drift_auto_report_threshold", 5)
    monkeypatch.setattr(settings, "drift_auto_report_window_days", 7)


async def _correct(
    session: AsyncSession,
    user: User,
    *,
    model: str | None = MODEL,
    version: str | None = VERSION,
    with_case: bool = True,
    sample: str = "S000001",
) -> RetrainingTicket:
    case = await make_case(session, user) if with_case else None
    return await create_ticket(
        session,
        case_id=case.id if case else None,
        case_number=case.case_number if case else None,
        sample_ref=None if case else sample,
        run_id=None if case else "run-a",
        flagged_by_user_id=user.id,
        reason="wrong",
        model_name=model,
        model_version=version,
        observed_label="MissingPart",
        correct_label="Golden",
    )


async def _reports(session: AsyncSession) -> list[DriftReport]:
    return list((await session.scalars(select(DriftReport).order_by(DriftReport.created_at))).all())


async def test_nothing_is_raised_below_the_threshold(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)

    for _ in range(4):
        await _correct(db_async_session, user)

    assert await _reports(db_async_session) == []


async def test_the_fifth_correction_raises_one_report_with_its_evidence(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)

    tickets = [await _correct(db_async_session, user) for _ in range(5)]

    (report,) = await _reports(db_async_session)
    assert (report.model_name, report.model_version) == (MODEL, VERSION)
    assert report.status == DriftReportStatus.OPEN
    assert report.reported_by_user_id == user.id
    assert report.stats["auto"] is True and report.stats["corrections"] == 5
    assert report.case_numbers == [t.case_number for t in tickets]
    assert "5 corrections to pcb_body_defect" in report.description


async def test_more_corrections_refresh_the_open_report_instead_of_adding_one(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    for _ in range(5):
        await _correct(db_async_session, user)

    for _ in range(3):
        await _correct(db_async_session, user)

    (report,) = await _reports(db_async_session)
    assert report.stats["corrections"] == 8 and len(report.case_numbers) == 8
    assert "8 corrections" in report.description


async def test_a_resolved_report_lets_the_next_correction_raise_a_new_one(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    for _ in range(5):
        await _correct(db_async_session, user)
    (first,) = await _reports(db_async_session)
    await drift_repo.resolve_drift_report(db_async_session, first)

    await _correct(db_async_session, user)

    reports = await _reports(db_async_session)
    assert [r.status for r in reports] == [DriftReportStatus.RESOLVED, DriftReportStatus.OPEN]


async def test_each_model_version_counts_on_its_own(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)

    for _ in range(3):
        await _correct(db_async_session, user, version="JcProg/body@v1")
    for _ in range(3):
        await _correct(db_async_session, user, version="JcProg/body@v2")
    for _ in range(4):
        await _correct(db_async_session, user, model="pcb_text_defect", version="JcProg/text@v1")

    assert await _reports(db_async_session) == []


async def test_old_corrections_fall_out_of_the_window(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)
    for _ in range(4):
        old = await _correct(db_async_session, user)
        await db_async_session.execute(
            update(RetrainingTicket)
            .where(RetrainingTicket.id == old.id)
            .values(created_at=datetime.now(UTC) - timedelta(days=30))
        )
    await db_async_session.commit()

    await _correct(db_async_session, user)

    assert await _reports(db_async_session) == []


async def test_work_tab_corrections_count_with_a_run_and_sample_reference(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)

    for n in range(5):
        await _correct(db_async_session, user, with_case=False, sample=f"S00000{n}")

    (report,) = await _reports(db_async_session)
    assert report.case_numbers == []
    assert report.stats["sample_refs"] == [f"run-a:S00000{n}" for n in range(5)]


async def test_a_report_filed_by_hand_is_left_alone(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)
    manual = await drift_repo.create_drift_report(
        db_async_session,
        model_name=MODEL,
        model_version=VERSION,
        reported_by_user_id=user.id,
        description="looks off",
        stats={"signals": []},
    )

    for _ in range(5):
        await _correct(db_async_session, user)

    reports = await _reports(db_async_session)
    assert len(reports) == 2
    assert next(r for r in reports if r.id == manual.id).description == "looks off"


async def test_zero_turns_it_off(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "drift_auto_report_threshold", 0)
    user = await make_user(db_async_session)

    for _ in range(8):
        await _correct(db_async_session, user)

    assert await _reports(db_async_session) == []


async def test_a_ticket_with_no_model_never_raises_anything(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)

    for _ in range(6):
        await _correct(db_async_session, user, model=None, version=None)

    assert await _reports(db_async_session) == []


async def test_a_failure_while_raising_does_not_lose_the_correction(
    db_async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(drift_repo, "maybe_raise_report", boom)
    user = await make_user(db_async_session)

    ticket = await _correct(db_async_session, user)

    saved = await db_async_session.get(RetrainingTicket, ticket.id)
    assert saved is not None and saved.correct_label == "Golden"
