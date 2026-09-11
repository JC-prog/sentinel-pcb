# src/a2a/agent2_a2a_server.py
from fastapi import FastAPI, HTTPException
import uvicorn
from src.a2a.protocol import AgentCard, AgentSkill, A2ATaskRequest, A2ATaskResponse
from agent import pcb_graph, PCBInspectionState

app = FastAPI(title="Agent 2 (Explainability) A2A Server")

AGENT_CARD = AgentCard(
    name="Agent 2 - Explainability and Review Agent",
    endpoint="http://127.0.0.1:8001/a2a/tasks",
    description="Gathers multi-modal evidence via MCP tools and performs OpenAI grounding.",
    skills=[
        AgentSkill(
            id="pcb.explainability.audit",
            name="Explainability Root Cause Audit",
            description="Deep inspection using 4 internal MCP tools.",
            input_schema={"board_id": "str", "component_ref": "str", "image_path": "str", "issue_symptom": "str"},
            output_schema={"defect_category": "str", "diagnosis_text": "str", "confidence_score": "float", "self_check_passed": "bool"}
        )
    ]
)

@app.get("/.well-known/agent.json", response_model=AgentCard)
def get_card():
    """A2A Standard Discovery Endpoint"""
    return AGENT_CARD

@app.post("/a2a/tasks", response_model=A2ATaskResponse)
def execute_task(req: A2ATaskRequest):
    """A2A Standard Task Execution Endpoint"""
    if req.skill_id != "pcb.explainability.audit":
        raise HTTPException(status_code=400, detail="Unknown skill")

    d = req.input_data
    init_state: PCBInspectionState = {
        "image_path": d["image_path"],
        "board_id": d["board_id"],
        "component_ref": d["component_ref"],
        "issue_symptom": d.get("issue_symptom", "Low confidence"),
        "historical_context": "", "reference_standards": "",
        "visual_bounding_boxes": [], "visual_description": "",
        "measurements": {}, "final_defect_category": "unknown",
        "final_diagnosis_text": "", "grounding_confidence": 0.0,
        "self_check_passed": False, "errors": []
    }

    # Execute internal LangGraph (which invokes MCP tools internally)
    final_state = pcb_graph.invoke(init_state)

    return A2ATaskResponse(
        task_id=req.task_id,
        state="completed",
        result={
            "defect_category": final_state["final_defect_category"],
            "diagnosis_text": final_state["final_diagnosis_text"],
            "confidence_score": final_state["grounding_confidence"],
            "self_check_passed": final_state["self_check_passed"],
            "visual_evidence": final_state["visual_description"]
        }
    )

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8001)
