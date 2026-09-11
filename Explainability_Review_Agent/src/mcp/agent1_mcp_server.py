# src/mcp/agent1_mcp_server.py
import logging
from typing import Dict, Any

try:
    from fastmcp import FastMCP
except ImportError:
    from mcp.server.mcpserver import MCPServer as FastMCP

logger = logging.getLogger("Agent1-MCP-Server")
agent1_mcp = FastMCP("Agent1-Orchestrator-MCP-Server")

@agent1_mcp.tool()
def confidence_decision_tool(confidence: float, threshold: float = 0.85) -> Dict[str, Any]:
    """[Agent 1 MCP] Evaluates if prediction confidence satisfies auto-acceptance criteria."""
    escalate = confidence < threshold
    return {
        "confidence": confidence,
        "threshold": threshold,
        "escalation_required": escalate,
        "decision": "ESCALATE_TO_AGENT_2" if escalate else "AUTO_ACCEPT"
    }

@agent1_mcp.tool()
def audit_logging_tool(event_type: str, payload: Dict[str, Any]) -> Dict[str, str]:
    """[Agent 1 MCP] Commits workflow decisions to the MES audit trail."""
    logger.info(f"[Agent 1 MCP Audit Log] Event: {event_type} | Payload: {payload}")
    return {"status": "LOGGED", "event": event_type}
