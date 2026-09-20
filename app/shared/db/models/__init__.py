"""Models shared by every module: auth (`User`, `RefreshToken`) and model operations (versions,
drift reports, retraining jobs/tickets). Importing this package registers them on Base.metadata.
Each feature module's own models package (e.g. app/chat/db/models/) does the same for its tables -
whoever runs `create_all` (app/main.py, alembic/env.py, tests/conftest.py) must import all of them,
or the missing tables silently never get created.
"""

from app.shared.db.models.auth import RefreshToken, User, UserRole
from app.shared.db.models.modelops import (
    DriftReport,
    DriftReportStatus,
    ModelVersion,
    ModelVersionStatus,
    RetrainingJob,
    RetrainingJobStatus,
    RetrainingTicket,
    RetrainingTicketStatus,
)

__all__ = [
    "DriftReport",
    "DriftReportStatus",
    "ModelVersion",
    "ModelVersionStatus",
    "RefreshToken",
    "RetrainingJob",
    "RetrainingJobStatus",
    "RetrainingTicket",
    "RetrainingTicketStatus",
    "User",
    "UserRole",
]
