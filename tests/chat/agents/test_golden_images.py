from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.inspection_agent.golden_images import find_golden_image, save_golden_image
from app.shared.db.models import User, UserRole


async def _make_user(session: AsyncSession) -> User:
    user = User(
        username="golden-admin",
        email="golden-admin@example.com",
        password_hash="x",
        employee_id="EMP-100",
        department_shift="Admin Day Shift",
        role=UserRole.ADMIN,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def test_find_golden_image_returns_none_when_nothing_registered(
    db_async_session: AsyncSession,
) -> None:
    result = await find_golden_image(
        db_async_session, board_id="BOARD-1", component_ref="U7", package="QFN32", feature="Pad1"
    )
    assert result is None


async def test_save_then_find_golden_image_matches_on_all_four_fields(
    db_async_session: AsyncSession,
) -> None:
    user = await _make_user(db_async_session)
    saved = await save_golden_image(
        db_async_session,
        file_bytes=b"fake-png-bytes",
        filename="golden.png",
        board_id="BOARD-1",
        component_ref="U7",
        package="QFN32",
        feature="Pad1",
        notes=None,
        registered_by_user_id=user.id,
    )

    found = await find_golden_image(
        db_async_session, board_id="BOARD-1", component_ref="U7", package="QFN32", feature="Pad1"
    )
    assert found is not None
    assert found.id == saved.id


async def test_find_golden_image_returns_none_on_partial_match(
    db_async_session: AsyncSession,
) -> None:
    user = await _make_user(db_async_session)
    await save_golden_image(
        db_async_session,
        file_bytes=b"fake-png-bytes",
        filename="golden.png",
        board_id="BOARD-1",
        component_ref="U7",
        package="QFN32",
        feature="Pad1",
        notes=None,
        registered_by_user_id=user.id,
    )

    found = await find_golden_image(
        db_async_session,
        board_id="BOARD-1",
        component_ref="U7",
        package="SOT23",  # different package - no match
        feature="Pad1",
    )
    assert found is None
