# src/a2a/explainability_server.py
import logging
from PIL import Image
from fastapi import FastAPI, HTTPException
import uvicorn

from src.a2a.protocol import AgentCard, AgentSkill, A2ATaskRequest, A2ATaskResponse
from agent import pcb_graph, PCBInspectionState

logger = logging.getLogger("A2A-Explainability-Server")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="A2A Explainability and Review Agent Server")

# ── 1. Expose the A2A Agent Card ────────────────────────────────────────────

AGENT_2_CARD = AgentCard(
    name="Agent 2 - Explainability and Review Agent",
    version="1.0.0",
    description="Gathers multi-modal evidence, reasons over telemetry, and produces rooted explanations.",
    endpoint="http://127.0.0.1:8001/a2a/tasks",
    skills=[
        AgentSkill(
            id="pcb.explainability.audit",
            name="Deep Evidence Gathering and Root-Cause Grounding",
            description="Executes 4-node inspection: Context RAG, LLaVA visual analysis, ICT telemetry, and OpenAI grounding.",
            input_schema={
                "board_id": "string",
                "component_ref": "string",
                "image_path": "string",
                "issue_symptom": "string"
            },
            output_schema={
                "defect_category": "string",
                "diagnosis_text": "string",
                "confidence_score": "float",
                "self_check_passed": "boolean",
                "visual_evidence": "string",
                "historical_context": "string"
            }
        )
    ]
)

@app.get("/.well-known/agent.json", response_model=AgentCard)
def get_agent_card():
    """A2A Standard: Agent Discovery Endpoint."""
    return AGENT_2_CARD

# ── 2. A2A Task Execution Endpoint ──────────────────────────────────────────

@app.post("/a2a/tasks", response_model=A2ATaskResponse)
def handle_a2a_task(request: A2ATaskRequest):
    """A2A Standard: Receives task delegation from Orchestrator Agent."""
    logger.info(f"[A2A Server] Received Task {request.task_id} for skill: {request.skill_id}")

    if request.skill_id != "pcb.explainability.audit":
        raise HTTPException(status_code=400, detail=f"Unsupported skill: {request.skill_id}")

    try:
        data = request.input_data
        
        # Initialize internal LangGraph inspection state
        initial_state: PCBInspectionState = {
            "image": Image.open(data["image_path"]).convert("RGB"),
            "board_id": data["board_id"],
            "component_ref": data["component_ref"],
            "issue_symptom": data.get("issue_symptom", "AOI low confidence"),
            "historical_context": "",
            "reference_standards": "",
            "visual_bounding_boxes": [],
            "visual_description": "",
            "measurements": {},
            "final_defect_category": "unknown",
            "final_diagnosis_text": "",
            "grounding_confidence": 0.0,
            "self_check_passed": False,
            "errors": []
        }

        # Run internal 4-tool StateGraph
        final_state = pcb_graph.invoke(initial_state)

        # Return standardized A2A response
        return A2ATaskResponse(
            task_id=request.task_id,
            state="completed",
            result={
                "defect_category": final_state["final_defect_category"],
                "diagnosis_text": final_state["final_diagnosis_text"],
                "confidence_score": final_state["grounding_confidence"],
                "self_check_passed": final_state["self_check_passed"],
                "visual_evidence": final_state["visual_description"],
                "historical_context": final_state["historical_context"],
                "errors": final_state["errors"]
            }
        )

    except Exception as exc:
        logger.exception("Failed to execute A2A task.")
        return A2ATaskResponse(
            task_id=request.task_id,
            state="failed",
            error=str(exc)
        )

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8001)
