"""Persistence models for authentication: registered users and their refresh tokens."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.db.base import Base


class UserRole(StrEnum):
    """Stored as a plain string column, not a native Postgres enum, so adding or removing a role
    is a code change, not a schema migration (as this one was, when Operator/Engineer were folded
    into QA/Admin). QA is the one operating the app day to day (inspecting, reviewing, flagging);
    Admin is a superset of QA plus configuration-only actions (registering golden images,
    infra/monitoring visibility) - see DEVELOPMENT.md and app/chat/agents/access.py for what each role
    can actually call.
    """

    QA = "qa"
    ADMIN = "admin"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    employee_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    department_shift: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, default=UserRole.QA, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class RefreshToken(Base):
    """A hash of each issued refresh token, not the token itself - same reasoning as never
    storing a plaintext password. Lets logout and rotation actually revoke access, which a
    stateless JWT alone can't do."""

    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped[User] = relationship()
