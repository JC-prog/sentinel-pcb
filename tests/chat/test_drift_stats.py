"""Drift indicators computed from Cases (app/chat/services/drift.py)."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.db.models import Case, Conversation
from app.chat.services.cases import create_case
from app.chat.services.drift import MIN_CASES_FOR_SIGNALS, drift_summary, window_stats
from app.shared.db.models import User, UserRole
from tests.shared._modelops_helpers import make_ticket

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
DEFECT_MODEL = "pcb_body_defect"


async def _setup(session: AsyncSession) -> tuple[User, Conversation]:
    user = User(
        username="drift-user",
        email="drift@example.com",
        password_hash="x",
        employee_id="EMP-D1",
        department_shift="Day",
        role=UserRole.QA,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    conversation = Conversation(user_id=user.id)
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return user, conversation


async def _case(
    session: AsyncSession,
    user: User,
    conversation: Conversation,
    *,
    days_ago: float,
    status: str = "accepted",
    confidence: float = 0.9,
    version: str = "JcProg/body@v1",
    **overrides: Any,
) -> Case:
    fields: dict[str, Any] = {
        "board_id": "B",
        "component_ref": "U1",
        "package": None,
        "feature": None,
        "issue_symptom": None,
        "image_id": "x.png",
        "inspection_xml_id": None,
        "golden_image_id": None,
        "region": "Body",
        "region_confidence": 0.9,
        "region_model_version": "JcProg/region@v1",
        "defect_model": DEFECT_MODEL,
        "defect_model_version": version,
        "defect_label": "MissingPart",
        "defect_confidence": confidence,
        "defect_scores": {},
        "measurement_validation": None,
        "observations": [],
        "status": status,
    }
    fields.update(overrides)
    case = await create_case(
        session, created_by_user_id=user.id, conversation_id=conversation.id, **fields
    )
    case.created_at = NOW - timedelta(days=days_ago)
    await session.commit()
    return case


async def _stats(session: AsyncSession, **kwargs: Any) -> Any:
    return await window_stats(
        session,
        kwargs.pop("model", DEFECT_MODEL),
        since=NOW - timedelta(days=7),
        until=NOW + timedelta(seconds=1),
        **kwargs,
    )


async def test_window_stats_counts_and_rates(db_async_session: AsyncSession) -> None:
    user, conversation = await _setup(db_async_session)
    await _case(
        db_async_session, user, conversation, days_ago=1, status="accepted", confidence=0.95
    )
    await _case(db_async_session, user, conversation, days_ago=1, status="accepted", confidence=0.9)
    await _case(db_async_session, user, conversation, days_ago=2, status="approved", confidence=0.5)
    await _case(
        db_async_session, user, conversation, days_ago=2, status="overridden", confidence=0.6
    )
    await _case(
        db_async_session, user, conversation, days_ago=3, status="review_required", confidence=0.4
    )

    stats = await _stats(db_async_session)

    assert stats.total == 5
    assert stats.needs_review == 3  # everything but the two accepted
    assert (stats.approved, stats.overridden, stats.resolved) == (1, 1, 2)
    assert stats.low_confidence == 3  # 0.5, 0.6 and 0.4 are under the 0.70 threshold
    assert stats.override_rate == 0.5  # of the two a human ruled on
    assert stats.review_rate == 0.6
    assert stats.low_confidence_rate == 0.6
    assert stats.mean_confidence is not None and abs(stats.mean_confidence - 0.67) < 1e-9


async def test_rates_are_none_when_there_is_nothing_to_divide_by(
    db_async_session: AsyncSession,
) -> None:
    user, conversation = await _setup(db_async_session)

    empty = await _stats(db_async_session)
    assert (empty.total, empty.override_rate, empty.review_rate, empty.mean_confidence) == (
        0,
        None,
        None,
        None,
    )

    await _case(db_async_session, user, conversation, days_ago=1, status="review_required")
    unresolved = await _stats(db_async_session)
    assert unresolved.override_rate is None  # nobody has ruled on it yet
    assert unresolved.review_rate == 1.0


async def test_only_cases_inside_the_window_and_for_the_model_count(
    db_async_session: AsyncSession,
) -> None:
    user, conversation = await _setup(db_async_session)
    await _case(db_async_session, user, conversation, days_ago=1)
    await _case(db_async_session, user, conversation, days_ago=20)  # too old
    await _case(
        db_async_session,
        user,
        conversation,
        days_ago=1,
        defect_model="pcb_lead_defect",
        defect_label="SolderInsufficient",
    )

    assert (await _stats(db_async_session)).total == 1
    assert (await _stats(db_async_session, model="pcb_lead_defect")).total == 1


async def test_the_region_model_is_measured_on_its_own_columns(
    db_async_session: AsyncSession,
) -> None:
    user, conversation = await _setup(db_async_session)
    await _case(
        db_async_session, user, conversation, days_ago=1, region_confidence=0.5, confidence=0.99
    )
    await _case(
        db_async_session, user, conversation, days_ago=1, region_confidence=0.95, confidence=0.99
    )

    stats = await _stats(db_async_session, model="pcb_region")

    assert stats.total == 2
    assert stats.low_confidence == 1  # judged on region_confidence, not the defect's


async def test_a_single_version_can_be_isolated(db_async_session: AsyncSession) -> None:
    user, conversation = await _setup(db_async_session)
    await _case(db_async_session, user, conversation, days_ago=1, version="JcProg/body@v1")
    await _case(db_async_session, user, conversation, days_ago=1, version="JcProg/body@v2")
    await _case(db_async_session, user, conversation, days_ago=1, version="JcProg/body@v2")

    assert (await _stats(db_async_session, model_version="JcProg/body@v2")).total == 2


async def _fill(
    session: AsyncSession,
    user: User,
    conversation: Conversation,
    *,
    days_ago: float,
    overridden: int,
    approved: int,
) -> None:
    for status, count in (("overridden", overridden), ("approved", approved)):
        for _ in range(count):
            await _case(session, user, conversation, days_ago=days_ago, status=status)


async def test_a_jump_in_the_override_rate_is_called_out(db_async_session: AsyncSession) -> None:
    user, conversation = await _setup(db_async_session)
    await _fill(db_async_session, user, conversation, days_ago=10, overridden=0, approved=10)
    await _fill(db_async_session, user, conversation, days_ago=2, overridden=8, approved=2)

    summary = await drift_summary(db_async_session, DEFECT_MODEL, days=7, now=NOW)

    assert summary["enough_data"] is True
    assert summary["previous"]["override_rate"] == 0.0
    assert summary["recent"]["override_rate"] == 0.8
    assert any("override rate" in s and "0%" in s and "80%" in s for s in summary["signals"])


async def test_nothing_is_flagged_when_the_metrics_hold_steady(
    db_async_session: AsyncSession,
) -> None:
    user, conversation = await _setup(db_async_session)
    await _fill(db_async_session, user, conversation, days_ago=10, overridden=2, approved=8)
    await _fill(db_async_session, user, conversation, days_ago=2, overridden=2, approved=8)

    summary = await drift_summary(db_async_session, DEFECT_MODEL, days=7, now=NOW)

    assert summary["signals"] == []


async def test_too_few_recent_cases_never_raise_a_signal(db_async_session: AsyncSession) -> None:
    user, conversation = await _setup(db_async_session)
    await _fill(db_async_session, user, conversation, days_ago=10, overridden=0, approved=10)
    await _fill(
        db_async_session,
        user,
        conversation,
        days_ago=2,
        overridden=MIN_CASES_FOR_SIGNALS - 2,
        approved=1,
    )

    summary = await drift_summary(db_async_session, DEFECT_MODEL, days=7, now=NOW)

    assert summary["enough_data"] is False
    assert summary["signals"] == []


async def test_a_confidence_drop_is_called_out(db_async_session: AsyncSession) -> None:
    user, conversation = await _setup(db_async_session)
    for _ in range(10):
        await _case(db_async_session, user, conversation, days_ago=10, confidence=0.95)
        await _case(db_async_session, user, conversation, days_ago=2, confidence=0.75)

    summary = await drift_summary(db_async_session, DEFECT_MODEL, days=7, now=NOW)

    assert any("mean confidence fell" in s for s in summary["signals"])


async def test_the_summary_breaks_the_recent_window_down_by_version(
    db_async_session: AsyncSession,
) -> None:
    user, conversation = await _setup(db_async_session)
    await _case(db_async_session, user, conversation, days_ago=1, version="JcProg/body@v1")
    await _case(db_async_session, user, conversation, days_ago=1, version="JcProg/body@v2")
    await _case(db_async_session, user, conversation, days_ago=1, version="JcProg/body@v2")

    summary = await drift_summary(db_async_session, DEFECT_MODEL, days=7, now=NOW)

    assert {v: s["cases"] for v, s in summary["by_version"].items()} == {
        "JcProg/body@v1": 1,
        "JcProg/body@v2": 2,
    }


async def test_the_summary_counts_tickets_flagged_in_the_window(
    db_async_session: AsyncSession,
) -> None:
    user, _ = await _setup(db_async_session)
    await make_ticket(db_async_session, user, model_name=DEFECT_MODEL)
    await make_ticket(db_async_session, user, model_name="pcb_lead_defect")

    summary = await drift_summary(db_async_session, DEFECT_MODEL, days=7, now=datetime.now(UTC))

    assert summary["tickets_flagged_in_window"] == 1
