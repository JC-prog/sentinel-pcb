# orchestrator_agent.py
import uuid
import logging
import httpx

# 1. Intra-Agent: Import Agent 1 MCP Tools
from src.mcp.agent1_mcp_server import confidence_decision_tool, audit_logging_tool

# 2. Inter-Agent: A2A Protocol Models
from src.a2a.protocol import AgentCard, A2ATaskRequest, A2ATaskResponse

logger = logging.getLogger("Agent1-Orchestrator")
logging.basicConfig(level=logging.INFO)

AGENT_2_DISCOVERY = "http://127.0.0.1:8001/.well-known/agent.json"

class A2AInterAgentClient:
    """Handles communication with other agents using the A2A protocol."""
    def __init__(self, discovery_url: str):
        self.discovery_url = discovery_url
        self.card: AgentCard = None

    def discover(self):
        with httpx.Client() as client:
            resp = client.get(self.discovery_url)
            resp.raise_for_status()
            self.card = AgentCard(**resp.json())
            logger.info(f"[A2A Client] Discovered: {self.card.name} at {self.card.endpoint}")

    def delegate_task(self, skill_id: str, input_data: dict) -> dict:
        if not self.card:
            self.discover()

        req = A2ATaskRequest(
            task_id=f"a2a_{uuid.uuid4().hex[:8]}",
            skill_id=skill_id,
            input_data=input_data
        )

        with httpx.Client(timeout=120.0) as client:
            resp = client.post(self.card.endpoint, json=req.model_dump())
            resp.raise_for_status()
            task_resp = A2ATaskResponse(**resp.json())

            if task_resp.state == "completed":
                return task_resp.result
            raise RuntimeError(f"A2A Task error: {task_resp.error}")

a2a_agent2 = A2AInterAgentClient(AGENT_2_DISCOVERY)


def orchestrator_handle_event(board_id: str, component_ref: str, image_path: str):
    logger.info(f"\n=== [AOI Inspection Event] Board: {board_id}, Component: {component_ref} ===")

    # Step 1: Core ADC Service (simulated 1st-stage baseline classifier)
    adc_prediction = "missing part"
    adc_confidence = 0.58  # Low confidence

    # Step 2: Use Agent 1's MCP Tool to decide next step
    decision = confidence_decision_tool(confidence=adc_confidence, threshold=0.85)
    audit_logging_tool("CORE_ADC_INFERENCE", {"prediction": adc_prediction, "confidence": adc_confidence})

    # Step 3: Conditional Routing
    if decision["escalation_required"]:
        logger.warning("[Agent 1] Low confidence detected. Initiating A2A delegation to Agent 2...")

        # ⭐ Inter-Agent Communication via A2A Protocol
        agent2_result = a2a_agent2.delegate_task(
            skill_id="pcb.explainability.audit",
            input_data={
                "board_id": board_id,
                "component_ref": component_ref,
                "image_path": image_path,
                "issue_symptom": f"ADC reported {adc_prediction} with low confidence ({adc_confidence})"
            }
        )

        logger.info("[Agent 1] Received A2A completed audit from Agent 2:")
        logger.info(f"  • Category: {agent2_result['defect_category']}")
        logger.info(f"  • Grounding Conf: {agent2_result['confidence_score']}")
        logger.info(f"  • Self-Check Passed: {agent2_result['self_check_passed']}")
        logger.info(f"  • Diagnosis: {agent2_result['diagnosis_text']}")

        # Log escalation to audit tool via MCP
        audit_logging_tool("A2A_ESCALATION_COMPLETE", {"result": agent2_result})
        logger.info("--> Routing packet to [Human Reviewer] for final signoff.")
    else:
        logger.info("[Agent 1] High confidence. Auto-accepted.")
        audit_logging_tool("AUTO_ACCEPT_CLASSIFICATION", {"prediction": adc_prediction})
