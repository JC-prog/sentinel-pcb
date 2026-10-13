"""Work-tab routes: dataset/XML/image-root uploads and the streaming orchestrator run. Route
declarations only - logic lives in app/workflow/services/ and, for the actual agentic workflow,
in app/workflow/src/agent1_orchestrator/ (the teammate's ported code - see its module docstrings).
Never shares code with app/chat/ (see tests/test_module_boundaries.py).
"""

from fastapi import APIRouter

from app.workflow.api import orchestrator

router = APIRouter()
router.include_router(orchestrator.router)

# Never buffered by the request-logging middleware (app/main.py);
# app/workflow/services/streaming.py logs the run itself instead.
STREAMING_PATHS: frozenset[str] = frozenset({"/api/orchestrator/run/stream"})
