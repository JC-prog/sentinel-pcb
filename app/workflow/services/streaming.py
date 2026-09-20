"""Business logic for the Work tab's streaming run: resolves uploaded dataset/XML/image-root
paths and turns runner.run_stream()'s events into SSE frame text. Kept separate from
app/workflow/api/orchestrator.py (the thin route layer) the same way app/chat/services/streaming.py is kept
separate from app/chat/api/chat.py.
"""

import json
import logging
from collections.abc import AsyncGenerator

from app.workflow.agents.orchestrator_agent.runner import run_stream
from app.workflow.services import uploads as orchestrator_uploads
from app.workflow.services.schemas import OrchestratorRunRequest

logger = logging.getLogger(__name__)


async def orchestrator_sse(request: OrchestratorRunRequest, username: str) -> AsyncGenerator[str, None]:
    dataset_path = orchestrator_uploads.resolve_dataset_path(request.dataset_id)
    xml_path = orchestrator_uploads.resolve_xml_path(request.xml_id)
    image_root_path = (
        orchestrator_uploads.resolve_image_root_path(request.image_root_id)
        if request.image_root_id
        else None
    )

    if dataset_path is None:
        yield f"event: error\ndata: {json.dumps({'message': 'dataset upload not found'})}\n\n"
        yield "event: done\ndata: {}\n\n"
        return
    if xml_path is None:
        yield f"event: error\ndata: {json.dumps({'message': 'inspection XML upload not found'})}\n\n"
        yield "event: done\ndata: {}\n\n"
        return

    try:
        async for event in run_stream(
            mode=request.mode,
            dataset_csv=str(dataset_path),
            inspection_xml=str(xml_path),
            image_root=str(image_root_path) if image_root_path else None,
            username=username,
            feature_threshold=request.feature_threshold,
            defect_threshold=request.defect_threshold,
            use_llm=request.use_llm,
            llm_model=request.llm_model,
            llm_fallback=request.llm_fallback,
        ):
            yield f"event: {event['event']}\ndata: {json.dumps(event['data'])}\n\n"
    except Exception as exc:
        logger.exception("orchestrator_agent run failed")
        yield f"event: error\ndata: {json.dumps({'message': str(exc)})}\n\n"
        yield "event: done\ndata: {}\n\n"
