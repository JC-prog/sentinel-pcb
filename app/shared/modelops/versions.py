"""The model registry: one ModelVersion row per (model, version) the app has seen, with exactly
one LIVE per model - kept in step with what the inference service actually reports serving.

The inference service is the authority on what is loaded; this table is the app's durable memory of
it (history, who activated what, candidates a retraining job produced). `sync_versions` is how the
two are reconciled.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models import ModelVersion, ModelVersionStatus
from app.shared.inference import ModelInfo


def split_version(version: str) -> tuple[str, str]:
    """ "repo/name@rev" -> ("repo/name", "rev"). A string with no "@" is all repo, empty revision."""

    repo_id, sep, revision = version.rpartition("@")
    return (repo_id, revision) if sep else (version, "")


def _new_row(model_name: str, version: str, status: ModelVersionStatus) -> ModelVersion:
    repo_id, revision = split_version(version)
    return ModelVersion(
        model_name=model_name,
        version=version,
        repo_id=repo_id,
        revision=revision,
        status=status,
    )


async def sync_versions(session: AsyncSession, infos: Sequence[ModelInfo]) -> None:
    """Makes the table agree with what the inference service reports (`GET /models`): for each
    model, `info.version` becomes its LIVE row, `info.previous_version` its PREVIOUS row, and
    anything that was live/previous but isn't anymore is RETIRED. Versions the app has never seen
    are created; CANDIDATE rows are left alone unless one is what just went live. Idempotent."""

    now = datetime.now(UTC)
    for info in infos:
        result = await session.scalars(
            select(ModelVersion).where(ModelVersion.model_name == info.name)
        )
        rows = {row.version: row for row in result}

        # Demote first and flush, so the "one live per model" partial unique index is free
        # before the new live row is written (a rollback swaps two rows' roles in one sync).
        for row in rows.values():
            if row.version == info.version:
                continue
            if row.status == ModelVersionStatus.LIVE:
                row.status = (
                    ModelVersionStatus.PREVIOUS
                    if row.version == info.previous_version
                    else ModelVersionStatus.RETIRED
                )
            elif row.status == ModelVersionStatus.PREVIOUS and row.version != info.previous_version:
                row.status = ModelVersionStatus.RETIRED
        await session.flush()

        live = rows.get(info.version)
        if live is None:
            live = _new_row(info.name, info.version, ModelVersionStatus.LIVE)
            live.activated_at = now
            session.add(live)
        elif live.status != ModelVersionStatus.LIVE:
            live.status = ModelVersionStatus.LIVE
            live.activated_at = now

        if info.previous_version:
            previous = rows.get(info.previous_version)
            if previous is None:
                session.add(_new_row(info.name, info.previous_version, ModelVersionStatus.PREVIOUS))
            elif previous.status != ModelVersionStatus.PREVIOUS:
                previous.status = ModelVersionStatus.PREVIOUS

    await session.commit()


async def record_candidate(
    session: AsyncSession, *, model_name: str, version: str, source_job_id: str | None
) -> ModelVersion:
    """Registers the weights a succeeded retraining job produced as a CANDIDATE, ready for an
    Admin to activate. If the version is already known (the stub trainer "produces" the base
    version it started from, which is live) the existing row is returned unchanged.

    Idempotent at the database level (INSERT .. ON CONFLICT DO NOTHING) rather than check-then-
    insert: two overlapping refreshes of the queue can both see the same job succeed, and the
    second must not fail on the unique (model, version) constraint."""

    repo_id, revision = split_version(version)
    await session.execute(
        insert(ModelVersion)
        .values(
            id=str(uuid.uuid4()),
            model_name=model_name,
            version=version,
            repo_id=repo_id,
            revision=revision,
            status=ModelVersionStatus.CANDIDATE,
            source_job_id=source_job_id,
            first_seen_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(constraint="uq_model_versions_name_version")
    )
    await session.commit()
    result = await session.scalars(
        select(ModelVersion).where(
            ModelVersion.model_name == model_name, ModelVersion.version == version
        )
    )
    return result.one()


async def get_live_version(session: AsyncSession, model_name: str) -> ModelVersion | None:
    result = await session.scalars(
        select(ModelVersion).where(
            ModelVersion.model_name == model_name,
            ModelVersion.status == ModelVersionStatus.LIVE,
        )
    )
    return result.first()


async def list_versions(
    session: AsyncSession, *, model_name: str | None = None
) -> list[ModelVersion]:
    """Newest first."""

    stmt = select(ModelVersion).order_by(ModelVersion.first_seen_at.desc())
    if model_name is not None:
        stmt = stmt.where(ModelVersion.model_name == model_name)
    return list(await session.scalars(stmt))
