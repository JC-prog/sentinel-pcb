"""Routes every module needs regardless of feature: health and auth. Each module owns its own
`api/` package the same way (app/chat/api/, app/workflow/api/), exposing one aggregated `router`
that app/main.py mounts - see there for every API surface the app exposes. Route modules stay thin:
request validation, a call into the module's own services/agents, response shaping.
"""

from fastapi import APIRouter

from app.shared.api import auth, health

router = APIRouter()
router.include_router(health.router)
router.include_router(auth.router)

# Response paths whose bodies must never be buffered by the request-logging middleware.
STREAMING_PATHS: frozenset[str] = frozenset()
