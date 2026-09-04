# src/mcp/pcb_mcp_server.py
import logging
from typing import Dict, Any, List

# Standalone FastMCP import
try:
    from fastmcp import FastMCP
except ImportError:
    # Fallback for older/alternative MCP SDK versions
    from mcp.server.mcpserver import MCPServer as FastMCP

from src.data.qdrant_store import DefectVectorStore

logger = logging.getLogger(__name__)

# Initialize the FastMCP Server
mcp = FastMCP("PCB-Inspection-MCP-Server")

# Internal mock instances
vector_store = DefectVectorStore()

# ── MCP Tool Definitions ─────────────────────────────────────────────────────

@mcp.tool()
def search_historical_defects(component_ref: str, top_k: int = 3) -> List[Dict[str, Any]]:
    """Search Qdrant vector database for visually similar past defect cases for a component."""
    logger.info(f"[MCP Server] Querying historical cases for {component_ref}")
    results = vector_store.search_similar(
        embedding=[0.0] * 512,
        top_k=top_k,
        metadata_filter={"component_ref": component_ref}
    )
    return results

@mcp.tool()
def get_ipc_standards(component_ref: str) -> Dict[str, str]:
    """Retrieve IPC acceptance standards (e.g., IPC-A-610) for a component class."""
    logger.info(f"[MCP Server] Looking up IPC standards for {component_ref}")
    return {
        "component_ref": component_ref,
        "standard_id": "IPC-A-610 Class 3",
        "tolerance": "Maximum 25% pad overhang allowed for chip components."
    }

@mcp.tool()
def get_ict_measurements(board_id: str, component_ref: str) -> Dict[str, Any]:
    """Query In-Circuit Testing (ICT) electrical telemetry and 3D AOI laser profiles."""
    logger.info(f"[MCP Server] Fetching ICT measurements for {board_id} - {component_ref}")
    return {
        "board_id": board_id,
        "component_ref": component_ref,
        "resistance_ohms": 999999,  # Open circuit
        "capacitance_uf": 0.0,
        "laser_height_profile_um": 0.0,
        "solder_volume_percentage": 15.2
    }

# ── Client Interface for LangGraph ───────────────────────────────────────────

class PCBToolClient:
    """Client interface for invoking MCP tools inside LangGraph nodes."""
    def search_historical(self, component_ref: str) -> List[Dict[str, Any]]:
        return search_historical_defects(component_ref=component_ref)
        
    def get_standards(self, component_ref: str) -> Dict[str, str]:
        return get_ipc_standards(component_ref=component_ref)
        
    def get_measurements(self, board_id: str, component_ref: str) -> Dict[str, Any]:
        return get_ict_measurements(board_id=board_id, component_ref=component_ref)

mcp_client = PCBToolClient()
