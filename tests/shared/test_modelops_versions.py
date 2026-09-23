import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models import ModelVersion, ModelVersionStatus
from app.shared.modelops.versions import (
    get_live_version,
    list_versions,
    record_candidate,
    split_version,
    sync_versions,
)
from tests.shared._modelops_helpers import model_info

NAME = "pcb_body_defect"
V1 = "JcProg/body@v1"
V2 = "JcProg/body@v2"
V3 = "JcProg/body@v3"


async def _statuses(session: AsyncSession) -> dict[str, str]:
    return {v.version: v.status for v in await list_versions(session, model_name=NAME)}


def test_split_version() -> None:
    assert split_version("JcProg/body@v2") == ("JcProg/body", "v2")
    assert split_version("no-revision") == ("no-revision", "")


async def test_sync_records_the_live_version(db_async_session: AsyncSession) -> None:
    await sync_versions(db_async_session, [model_info(NAME, V1)])

    live = await get_live_version(db_async_session, NAME)
    assert live is not None
    assert (live.version, live.repo_id, live.revision) == (V1, "JcProg/body", "v1")
    assert live.activated_at is not None


async def test_sync_is_idempotent(db_async_session: AsyncSession) -> None:
    infos = [model_info(NAME, V2, previous=V1)]
    await sync_versions(db_async_session, infos)
    await sync_versions(db_async_session, infos)

    assert await _statuses(db_async_session) == {V2: "live", V1: "previous"}


async def test_activation_demotes_live_to_previous_and_retires_the_older_one(
    db_async_session: AsyncSession,
) -> None:
    await sync_versions(db_async_session, [model_info(NAME, V1)])
    await sync_versions(db_async_session, [model_info(NAME, V2, previous=V1)])
    await sync_versions(db_async_session, [model_info(NAME, V3, previous=V2)])

    assert await _statuses(db_async_session) == {V3: "live", V2: "previous", V1: "retired"}


async def test_rollback_swaps_live_and_previous(db_async_session: AsyncSession) -> None:
    await sync_versions(db_async_session, [model_info(NAME, V2, previous=V1)])

    await sync_versions(db_async_session, [model_info(NAME, V1, previous=V2)])

    assert await _statuses(db_async_session) == {V1: "live", V2: "previous"}


async def test_a_candidate_that_goes_live_becomes_live(db_async_session: AsyncSession) -> None:
    await sync_versions(db_async_session, [model_info(NAME, V1)])
    await record_candidate(db_async_session, model_name=NAME, version=V2, source_job_id=None)
    assert (await _statuses(db_async_session))[V2] == "candidate"

    await sync_versions(db_async_session, [model_info(NAME, V2, previous=V1)])

    assert await _statuses(db_async_session) == {V2: "live", V1: "previous"}


async def test_sync_leaves_unrelated_candidates_alone(db_async_session: AsyncSession) -> None:
    await record_candidate(db_async_session, model_name=NAME, version=V3, source_job_id=None)

    await sync_versions(db_async_session, [model_info(NAME, V1)])

    assert (await _statuses(db_async_session))[V3] == "candidate"


async def test_sync_keeps_models_independent(db_async_session: AsyncSession) -> None:
    await sync_versions(
        db_async_session,
        [model_info("pcb_region", "JcProg/region@v1"), model_info(NAME, V1)],
    )

    assert (await get_live_version(db_async_session, "pcb_region")) is not None
    assert (await get_live_version(db_async_session, NAME)) is not None


async def test_record_candidate_returns_the_existing_row_for_a_known_version(
    db_async_session: AsyncSession,
) -> None:
    await sync_versions(db_async_session, [model_info(NAME, V1)])

    row = await record_candidate(db_async_session, model_name=NAME, version=V1, source_job_id=None)

    assert row.status == ModelVersionStatus.LIVE
    assert len(await list_versions(db_async_session, model_name=NAME)) == 1


async def test_recording_the_same_candidate_twice_keeps_a_single_row(
    db_async_session: AsyncSession,
) -> None:
    """Two overlapping queue refreshes can both see a job succeed; the second registration must
    be a no-op, not a unique-constraint failure."""

    first = await record_candidate(
        db_async_session, model_name=NAME, version=V2, source_job_id=None
    )
    second = await record_candidate(
        db_async_session, model_name=NAME, version=V2, source_job_id=None
    )

    assert first.id == second.id
    assert [v.version for v in await list_versions(db_async_session, model_name=NAME)] == [V2]


async def test_the_database_allows_only_one_live_version_per_model(
    db_async_session: AsyncSession,
) -> None:
    await sync_versions(db_async_session, [model_info(NAME, V1)])

    db_async_session.add(
        ModelVersion(
            model_name=NAME,
            version=V2,
            repo_id="JcProg/body",
            revision="v2",
            status=ModelVersionStatus.LIVE,
        )
    )
    with pytest.raises(IntegrityError):
        await db_async_session.commit()
    await db_async_session.rollback()
