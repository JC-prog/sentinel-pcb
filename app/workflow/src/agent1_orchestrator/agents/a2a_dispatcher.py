import json
import logging
import requests
from typing import Any, Dict

logger = logging.getLogger(__name__)

class Agent2ReviewClient:
    def __init__(self, agent2_url: str = "http://127.0.0.1:8001"):
        self.agent2_url = agent2_url.rstrip("/")
        self.agent_card = None

    def discover(self) -> bool:
        """Check Agent 2 availability via standard A2A agent card."""
        try:
            resp = requests.get(f"{self.agent2_url}/.well-known/agent.json", timeout=3.0)
            if resp.status_code == 200:
                self.agent_card = resp.json()
                logger.info(f"Connected to Agent 2: {self.agent_card.get('name')}")
                return True
        except Exception as e:
            logger.warning(f"Could not connect to Agent 2 at {self.agent2_url}: {e}")
        return False

    def delegate_review(self, sample_data: Dict[str, Any], baseline_prediction: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatches an audit task to Agent 2 via A2A protocol."""
        payload = {
            "task_type": "pcb.explainability.audit",
            "parameters": {
                "board_id": sample_data.get("board_id", "UNKNOWN"),
                "component_ref": sample_data.get("component_id", "UNKNOWN"),
                "defect_image_path": sample_data.get("defect_image_path"),
                "golden_image_path": sample_data.get("golden_image_path"),
                "feature_type": baseline_prediction.get("feature_class"),
                "preliminary_defect": baseline_prediction.get("defect_class"),
                "confidence": baseline_prediction.get("confidence", 0.0),
                "aoi_measurements": sample_data.get("failed_inspections", {})
            }
        }

        try:
            resp = requests.post(f"{self.agent2_url}/a2a/tasks", json=payload, timeout=60.0)
            if resp.status_code == 200:
                return resp.json()
            else:
                return {
                    "status": "FAILED",
                    "error": f"Agent 2 returned HTTP {resp.status_code}: {resp.text}"
                }
        except Exception as e:
            return {"status": "ERROR", "error": str(e)}