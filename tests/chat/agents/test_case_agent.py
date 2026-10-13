"""The case agent: read-only lookups of saved cases, for QA and Admin - the Cases tab's data, asked
for in the conversation."""

from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.case_agent import GET_CASE, GET_COMPONENT_HISTORY, LIST_CASES
from app.chat.db.models import Case
from app.chat.services import inspection_log
from app.shared.db.models import User
from tests.chat.agents._helpers import call, tool_context
from tests.shared._modelops_helpers import make_case, make_user


async def _case(session: AsyncSession, user: User, **changes: Any) -> Case:
    case = await make_case(session, user)
    if changes:
        await session.execute(update(Case).where(Case.id == case.id).values(**changes))
        await session.commit()
        await session.refresh(case)
    return case


def _ctx(session: AsyncSession, user: User) -> Any:
    return tool_context(session, user)


async def test_get_case_reports_what_the_model_said_and_any_correction(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    case = await _case(
        db_async_session,
        user,
        corrected_label="Golden",
        correction_reason="pads are fine",
        corrected_by_user_id=user.id,
    )

    result = await call(GET_CASE, _ctx(db_async_session, user), case_number=case.case_number)

    assert result["case_number"] == case.case_number
    assert (result["defect_label"], result["corrected_label"]) == ("MissingPart", "Golden")
    assert result["correction_reason"] == "pads are fine"
    assert result["corrected_by"] == user.username
    # file names and the conversation are not for the model to repeat
    assert "image_id" not in result and "conversation_id" not in result


async def test_get_case_accepts_a_loose_case_number(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)
    case = await _case(db_async_session, user)

    result = await call(
        GET_CASE, _ctx(db_async_session, user), case_number=case.case_number.lower()
    )

    assert result["case_number"] == case.case_number


async def test_an_unknown_case_is_refused_with_the_format(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)

    result = await call(GET_CASE, _ctx(db_async_session, user), case_number="CASE-999999")

    assert "CASE-999999" in result["error"] and "CASE-000123" in result["error"]
    assert "required" in (await call(GET_CASE, _ctx(db_async_session, user), case_number=" "))["error"]


async def test_list_cases_is_newest_first_and_says_how_many_there_are(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    made = [await _case(db_async_session, user) for _ in range(12)]

    result = await call(LIST_CASES, _ctx(db_async_session, user))

    assert result["total"] == 12 and result["shown"] == 10
    assert result["cases"][0]["case_number"] == made[-1].case_number


async def test_list_cases_filters_by_board_component_status_and_correction(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    target = await _case(
        db_async_session, user, board_id="BOARD-7", component_ref="U77", status="review_required"
    )
    corrected = await _case(db_async_session, user, board_id="BOARD-8", corrected_label="Golden")
    await _case(db_async_session, user, board_id="BOARD-9")

    async def numbers(**kw: Any) -> list[str]:
        result = await call(LIST_CASES, _ctx(db_async_session, user), **kw)
        return [c["case_number"] for c in result["cases"]]

    assert await numbers(board_id="board-7") == [target.case_number]
    assert await numbers(component_ref="u77") == [target.case_number]
    assert await numbers(status="review_required") == [target.case_number]
    assert await numbers(corrected=True) == [corrected.case_number]
    assert len(await numbers(corrected=False)) == 2


async def test_list_cases_can_be_limited_to_the_users_own(db_async_session: AsyncSession) -> None:
    me = await make_user(db_async_session)
    mine = await _case(db_async_session, me)
    other = User(
        username="someone-else",
        email="else@example.com",
        password_hash="x",
        employee_id="EMP-else",
        role=me.role,
    )
    db_async_session.add(other)
    await db_async_session.commit()
    await db_async_session.refresh(other)
    await _case(db_async_session, other)

    everyone = await call(LIST_CASES, _ctx(db_async_session, me))
    only_mine = await call(LIST_CASES, _ctx(db_async_session, me), mine=True)

    assert everyone["total"] == 2
    assert [c["case_number"] for c in only_mine["cases"]] == [mine.case_number]


async def test_an_unknown_status_is_refused(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)

    result = await call(LIST_CASES, _ctx(db_async_session, user), status="done")

    assert "status must be one of" in result["error"]


async def test_component_history_combines_what_was_inspected_with_the_cases(
    db_async_session: AsyncSession,
) -> None:
    user = await make_user(db_async_session)
    fields: dict[str, Any] = {
        "board_id": "BOARD-1",
        "component_ref": "U7",
        "image_id": "a.png",
        "region": "Body",
        "defect_model": "pcb_body_defect",
        "defect_model_version": "JcProg/body@v1",
        "defect_label": "MissingPart",
        "defect_confidence": 0.8,
        "status": "review_required",
    }
    first = await inspection_log.record(
        db_async_session, user_id=user.id, conversation_id="c1", case_fields=fields
    )
    await inspection_log.record(
        db_async_session,
        user_id=user.id,
        conversation_id="c1",
        case_fields={**fields, "defect_label": "Golden", "status": "accepted"},
    )
    await inspection_log.record(  # a different part is not part of U7's history
        db_async_session,
        user_id=user.id,
        conversation_id="c1",
        case_fields={**fields, "component_ref": "U9"},
    )
    assert first is not None
    first.verified = True
    await db_async_session.commit()
    case = await _case(db_async_session, user, board_id="BOARD-1", component_ref="U7")

    result = await call(
        GET_COMPONENT_HISTORY, _ctx(db_async_session, user), board_id="board-1", component_ref="u7"
    )

    assert result["inspected"] == 2 and result["confirmed_right"] == 1
    assert result["model_labels"] == {"MissingPart": 1, "Golden": 1}
    assert len(result["recent"]) == 2
    assert result["cases_total"] == 1 and result["cases"][0]["case_number"] == case.case_number


async def test_a_component_never_seen_has_an_empty_history(db_async_session: AsyncSession) -> None:
    user = await make_user(db_async_session)

    result = await call(
        GET_COMPONENT_HISTORY, _ctx(db_async_session, user), board_id="BOARD-X", component_ref="U1"
    )

    assert result["inspected"] == 0 and result["recent"] == [] and result["cases_total"] == 0
    assert "required" in (
        await call(GET_COMPONENT_HISTORY, _ctx(db_async_session, user), board_id=" ", component_ref="U1")
    )["error"]


async def test_activity_summary_counts_what_happened_in_the_window(
    db_async_session: AsyncSession,
) -> None:
    from datetime import UTC, datetime, timedelta

    from app.chat.agents.case_agent import GET_ACTIVITY_SUMMARY
    from app.shared.modelops import drift as drift_repo

    user = await make_user(db_async_session)
    fields: dict[str, Any] = {
        "board_id": "BOARD-1",
        "component_ref": "U7",
        "image_id": "a.png",
        "region": "Body",
        "defect_model": "pcb_body_defect",
        "defect_label": "MissingPart",
        "status": "review_required",
    }
    rows = []
    for status in ("review_required", "accepted", "accepted"):
        row = await inspection_log.record(
            db_async_session,
            user_id=user.id,
            conversation_id="c1",
            case_fields={**fields, "status": status},
        )
        assert row is not None
        rows.append(row)
    rows[1].verified = True
    old = await inspection_log.record(
        db_async_session, user_id=user.id, conversation_id="c1", case_fields=fields
    )
    assert old is not None
    old.created_at = datetime.now(UTC) - timedelta(days=40)
    await _case(db_async_session, user)
    await drift_repo.create_drift_report(
        db_async_session,
        model_name="pcb_body_defect",
        reported_by_user_id=user.id,
        description="raised itself",
        stats={"auto": True},
    )
    await db_async_session.commit()

    result = await call(GET_ACTIVITY_SUMMARY, _ctx(db_async_session, user))

    assert result["window_days"] == 7
    assert result["inspected"] == 3
    assert result["by_verdict"] == {"review_required": 1, "accepted": 2}
    assert result["by_model"] == {"pcb_body_defect": 3}
    assert result["confirmed_right"] == 1
    assert result["cases_created"] == 1
    assert result["drift_reports_opened"] == 1
    assert result["drift_reports_raised_automatically"] == 1
    assert result["replies_rated"] == {}
    # the run store is not configured in these tests; "unknown" is not the same as none
    assert "work_runs_saved" in result

    wide = await call(GET_ACTIVITY_SUMMARY, _ctx(db_async_session, user), days=90)
    assert wide["inspected"] == 4 and wide["window_days"] == 90
    assert (await call(GET_ACTIVITY_SUMMARY, _ctx(db_async_session, user), days=9999))[
        "window_days"
    ] == 90
