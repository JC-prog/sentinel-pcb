"""The golden reference image bank - looked up automatically by graph.py's lookup_golden_image
node so a flagging QA/Admin user never has to supply a golden image by hand, and registered by
Admin users through POST /api/admin/golden-images (app/main.py)."""

import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.db.models import GoldenImage


async def find_golden_image(
    session: AsyncSession,
    *,
    board_id: str,
    component_ref: str,
    package: str | None,
    feature: str | None,
) -> GoldenImage | None:
    """Exact match on board_id+component_ref, narrowed by package/feature when supplied. None on
    zero matches; also None (not an arbitrary pick) on more than one - GoldenImage's unique
    constraint on (board_id, component_ref, package, feature) should make that unreachable when
    package/feature are both given, but a caller can omit either."""

    stmt = select(GoldenImage).where(
        GoldenImage.board_id == board_id, GoldenImage.component_ref == component_ref
    )
    if package:
        stmt = stmt.where(GoldenImage.package == package)
    if feature:
        stmt = stmt.where(GoldenImage.feature == feature)

    rows = (await session.execute(stmt)).scalars().all()
    return rows[0] if len(rows) == 1 else None


async def save_golden_image(
    session: AsyncSession,
    *,
    file_bytes: bytes,
    filename: str,
    board_id: str,
    component_ref: str,
    package: str,
    feature: str,
    notes: str | None,
    registered_by_user_id: str,
) -> GoldenImage:
    """Writes under settings.case_golden_image_dir with the same uuid4().hex+suffix convention as
    app.uploads.service.save_upload(), then inserts the GoldenImage row."""

    golden_dir = Path(settings.case_golden_image_dir)
    golden_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(filename).suffix
    stored_filename = f"{uuid.uuid4().hex}{suffix}"
    (golden_dir / stored_filename).write_bytes(file_bytes)

    golden = GoldenImage(
        board_id=board_id,
        component_ref=component_ref,
        package=package,
        feature=feature,
        stored_filename=stored_filename,
        notes=notes,
        registered_by_user_id=registered_by_user_id,
    )
    session.add(golden)
    await session.commit()
    await session.refresh(golden)
    return golden


def resolve_golden_image_path(golden_image: GoldenImage) -> Path:
    """Resolves a GoldenImage row's stored_filename to a Path under settings.case_golden_image_dir
    - used by graph.py's align_and_check_quality node to read the reference image's bytes."""

    return Path(settings.case_golden_image_dir) / golden_image.stored_filename
