"""
Agent 2 Explainability A2A Server.
Standard Agent2Agent (A2A) HTTP/JSON-RPC microservice exposing discovery and audit tasks.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
import uvicorn

# Relative / absolute import resolution for unified directory structure
try:
    from src.agent2_explainability.a2a.protocol import (
        AgentCard,
        AgentSkill,
        A2ATaskRequest,
        A2ATaskResponse,
    )
    from src.agent2_explainability.pipeline.review_graph import execute_explainability_review
except ImportError:
    from .protocol import (
        AgentCard,
        AgentSkill,
        A2ATaskRequest,
        A2ATaskResponse,
    )
    from ..pipeline.review_graph import execute_explainability_review

logger = logging.getLogger("agent2.a2a_server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

app = FastAPI(title="Agent 2 (Explainability & Grounding) A2A Server", version="2.1.0")

# Standard A2A Agent Card for Dynamic Discovery
AGENT_CARD = AgentCard(
    name="Agent 2 - PCB Explainability and Review Agent",
    endpoint="http://127.0.0.1:8001/a2a/tasks",
    description="Multimodal grounding agent reconciling local LLaVA visual evidence, 3D laser/ICT telemetry, and IPC-A-610 standards.",
    skills=[
        AgentSkill(
            id="pcb.explainability.audit",
            name="Explainability Root Cause Audit",
            description="Deep multimodal inspection with physics-grounded contradiction checking.",
            input_schema={
                "board_id": "str",
                "component_ref": "str",
                "defect_image_path": "str",
                "golden_image_path": "str (optional)",
                "feature_type": "str (optional)",
                "preliminary_defect": "str (optional)",
                "confidence": "float (optional)",
                "aoi_measurements": "dict (optional)"
            },
            output_schema={
                "predicted_defect": "str",
                "confidence": "float",
                "diagnosis": "str",
                "self_check_passed": "bool",
                "contradiction_detected": "bool",
                "ipc_citations": "list[str]"
            }
        )
    ]
)


@app.get("/.well-known/agent.json", response_model=AgentCard)
def get_card():
    """A2A Standard Discovery Endpoint."""
    return AGENT_CARD


@app.post("/a2a/tasks", response_model=A2ATaskResponse)
def execute_task(req: A2ATaskRequest):
    """A2A Standard Task Execution Endpoint."""
    # Resolve skill ID across standard A2A variants
    requested_skill = req.skill_id or req.task_type
    if requested_skill != "pcb.explainability.audit":
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported skill '{requested_skill}'. Supported: 'pcb.explainability.audit'"
        )

    # Resolve payload across input_data or parameters
    payload: Dict[str, Any] = req.input_data or req.parameters or {}
    task_id = req.task_id or f"task_{uuid.uuid4().hex[:8]}"

    # Normalize aliases: defect_image_path vs image_path
    if "image_path" in payload and "defect_image_path" not in payload:
        payload["defect_image_path"] = payload["image_path"]

    logger.info(f"Received A2A Task [{task_id}] for component: {payload.get('component_ref', 'UNKNOWN')}")

    try:
        # Execute LangGraph review state machine
        pipeline_output = execute_explainability_review(payload)

        # Standardized response payload
        artifact = {
            "predicted_defect": pipeline_output.get("predicted_defect"),
            "confidence": pipeline_output.get("final_confidence", 0.0),
            "diagnosis": pipeline_output.get("diagnosis", ""),
            "self_check_passed": pipeline_output.get("self_check_passed", False),
            "contradiction_detected": pipeline_output.get("contradiction_detected", False),
            "ipc_citations": pipeline_output.get("ipc_citations", []),
            "visual_evidence": pipeline_output.get("visual_evidence", "")
        }

        return A2ATaskResponse(
            task_id=task_id,
            state="completed",
            result=artifact,
            artifact=artifact
        )

    except Exception as exc:
        logger.error(f"Task [{task_id}] failed: {exc}", exc_info=True)
        return A2ATaskResponse(
            task_id=task_id,
            state="failed",
            result={"error": str(exc)},
            artifact={"self_check_passed": False, "diagnosis": f"Execution error: {exc}"}
        )


if __name__ == "__main__":
    uvicorn.run("src.agent2_explainability.a2a.agent2_a2a_server:app", host="127.0.0.1", port=8001, reload=False)