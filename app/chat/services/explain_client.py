"""Asking Agent 2's MCP service (app/workflow/services/agent2_mcp_service.py) to explain an image.

Chat never imports the workflow module: it calls a URL (AGENT2_MCP_URL) with an uploaded file's
*name*, the model's own call and the identifying details, and gets a grounded, advisory explanation
back. Any failure - not configured, unreachable, slow, a malformed answer - is `ExplainUnavailable`
with a reason fit to show, so the inspection result the user already has still stands.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from fastmcp import Client

from app.shared.config.settings import settings

logger = logging.getLogger(__name__)

TOOL = "explain_inspection"


class ExplainUnavailable(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class ExplainRequest:
    image_name: str
    board_id: str = ""
    component_ref: str = ""
    issue_symptom: str = ""
    model_label: str = ""
    model_confidence: float | None = None
    xml_name: str = ""

    def arguments(self) -> dict[str, Any]:
        args: dict[str, Any] = {
            "image_name": self.image_name,
            "board_id": self.board_id,
            "component_ref": self.component_ref,
            "issue_symptom": self.issue_symptom,
            "model_label": self.model_label,
            "xml_name": self.xml_name,
        }
        if self.model_confidence is not None:
            args["model_confidence"] = self.model_confidence
        return args


async def explain(request: ExplainRequest, *, transport: Any | None = None) -> dict[str, Any]:
    """The service's answer. `transport` replaces the configured URL (a test passes the server)."""

    target = transport if transport is not None else settings.agent2_mcp_url.strip()
    if not target:
        raise ExplainUnavailable("the explanation service is not configured")

    async def call() -> dict[str, Any]:
        async with Client(target) as client:
            result = await client.call_tool(TOOL, request.arguments())
        data = result.data
        if not isinstance(data, dict):
            raise ExplainUnavailable("the explanation service gave an answer that was not usable")
        return data

    try:
        return await asyncio.wait_for(call(), timeout=settings.agent2_mcp_timeout_seconds)
    except ExplainUnavailable:
        raise
    except TimeoutError as exc:
        raise ExplainUnavailable("the explanation service took too long to answer") from exc
    except Exception as exc:
        logger.warning("explanation service call failed: %s", exc)
        raise ExplainUnavailable("the explanation service could not be reached") from exc
