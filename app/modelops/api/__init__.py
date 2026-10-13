"""Models-tab routes: model versions, drift reports, the retraining queue, and the Admin actions on
them. Route declarations only - logic lives in app/modelops/services/. Imports only app/shared/
(see tests/test_module_boundaries.py).
"""

from fastapi import APIRouter

from app.modelops.api import models

router = APIRouter()
router.include_router(models.router)

# Nothing here streams, so nothing needs to be exempted from the request-logging middleware's
# body buffering (app/main.py).
STREAMING_PATHS: frozenset[str] = frozenset()
