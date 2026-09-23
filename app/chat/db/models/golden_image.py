"""A minimal admin-curated bank of golden (reference, non-defective) images, looked up by
app/chat/agents/inspection_agent/golden_images.py when a QA user flags an ambiguous image and no
golden reference was supplied by hand. Registered one at a time via POST /api/admin/golden-images
(app/chat/api/admin.py) - not a bulk import.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class GoldenImage(Base):
    __tablename__ = "golden_images"
    __table_args__ = (
        UniqueConstraint(
            "board_id", "component_ref", "package", "feature", name="uq_golden_images_lookup_key"
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    board_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    component_ref: Mapped[str] = mapped_column(String, nullable=False, index=True)
    package: Mapped[str] = mapped_column(String, nullable=False)
    feature: Mapped[str] = mapped_column(String, nullable=False)
    # References app.chat.uploads' stored-filename convention (uuid4().hex + suffix), not blob data -
    # see app/chat/agents/inspection_agent/golden_images.py's save_golden_image(). Stored under
    # settings.case_golden_image_dir, not settings.chat_upload_dir - a separate, admin-curated
    # directory rather than the free-for-all chat upload one.
    stored_filename: Mapped[str] = mapped_column(String, nullable=False)
    registered_by_user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
