"""correction_trend: confirmed corrections for a model, now against the window before, per model
version. A count of corrections (retraining tickets), not an error rate - and it says so."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.config.settings import settings
from app.shared.db.models import RetrainingTicket, User
from app.shared.modelops.corrections import BASIS, correction_trend
from app.shared.modelops.tickets import create_ticket
from tests.shared._modelops_helpers import make_user

MODEL = "pcb_body_defect"
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_auto_report(monkeypatch: pytest.MonkeyPatch) -> None:
    # these tests are about the counts; the automatic report is covered in test_drift_auto_report
    monkeypatch.setattr(settings, "drift_auto_report_threshold", 5)


async def _ticket(
    session: AsyncSession,
    user: User,
    *,
    days_ago: float,
    model: str = MODEL,
    version: str | None = "JcProg/body@v1",
    n: int = 0,
) -> None:
    ticket = await create_ticket(
        session,
        sample_ref=f"S{n:06d}",
        run_id=f"run-{n}-{days_ago}",
        flagged_by_user_id=user.id,
        reason="wrong",
        model_name=model,
        model_version=version,
        observed_label="MissingPart",
        correct_label="Golden",
    )
    await session.execute(
        update(RetrainingTicket)
        .where(RetrainingTicket.id == ticket.id)
        .values(created_at=NOW - timedelta(days=days_ago))
    )
    await session.commit()


async def test_it_counts_the_recent_window_against_the_one_before(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    for n in range(2):
        await _ticket(db_async_session, user, days_ago=1, n=n)  # recent
    await _ticket(db_async_session, user, days_ago=10, n=9)  # the 7 days before that

    trend = await correction_trend(db_async_session, MODEL, days=7, now=NOW)

    assert trend["corrections"] == {"recent": 2, "previous": 1}
    assert trend["enough_data"] is True
    assert trend["basis"] == BASIS


async def test_older_corrections_and_other_models_do_not_count(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    await _ticket(db_async_session, user, days_ago=30, n=1)
    await _ticket(db_async_session, user, days_ago=1, model="pcb_text_defect", n=2)

    trend = await correction_trend(db_async_session, MODEL, days=7, now=NOW)

    assert trend["corrections"] == {"recent": 0, "previous": 0}
    assert trend["enough_data"] is False
    assert trend["signals"] == []


async def test_it_splits_the_corrections_by_model_version(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)
    await _ticket(db_async_session, user, days_ago=1, version="JcProg/body@v1", n=1)
    await _ticket(db_async_session, user, days_ago=1, version="JcProg/body@v2", n=2)
    await _ticket(db_async_session, user, days_ago=2, version="JcProg/body@v2", n=3)
    await _ticket(db_async_session, user, days_ago=1, version=None, n=4)

    trend = await correction_trend(db_async_session, MODEL, days=7, now=NOW)

    assert trend["by_version"] == {
        "JcProg/body@v1": {"recent": 1, "previous": 0},
        "JcProg/body@v2": {"recent": 2, "previous": 0},
        "unknown version": {"recent": 1, "previous": 0},
    }


async def test_a_rise_against_the_previous_window_is_called_out(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    await _ticket(db_async_session, user, days_ago=10, n=100)
    for n in range(5):
        await _ticket(db_async_session, user, days_ago=1, n=n)

    trend = await correction_trend(db_async_session, MODEL, days=7, now=NOW)

    assert any("rose from 1 to 5" in s for s in trend["signals"])


async def test_a_first_burst_with_nothing_before_is_called_out(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    for n in range(3):
        await _ticket(db_async_session, user, days_ago=1, n=n)

    trend = await correction_trend(db_async_session, MODEL, days=7, now=NOW)

    assert any("none in the 7 before" in s for s in trend["signals"])


async def test_steady_low_numbers_raise_no_signal(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)
    await _ticket(db_async_session, user, days_ago=1, n=1)
    await _ticket(db_async_session, user, days_ago=9, n=2)

    trend = await correction_trend(db_async_session, MODEL, days=7, now=NOW)

    assert trend["signals"] == []


async def test_a_version_at_the_report_threshold_is_named(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)
    for n in range(5):
        await _ticket(db_async_session, user, days_ago=1, version="JcProg/body@v2", n=n)

    trend = await correction_trend(db_async_session, MODEL, days=7, now=NOW)

    assert any("JcProg/body@v2 has 5 corrections" in s for s in trend["signals"])
    assert trend["auto_report"] == {"enabled": True, "threshold": 5, "window_days": 7}
