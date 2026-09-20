"""Shared SQLAlchemy declarative base. Kept in its own module (no engine/session imports) so
`app/db/models.py` can import it without pulling in engine setup, and vice versa.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
