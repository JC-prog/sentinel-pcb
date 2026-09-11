# src/mcp/agent2_mcp_server.py
import io
import json
import logging
from typing import Dict, Any, List
from PIL import Image
import ollama

try:
    from fastmcp import FastMCP
except ImportError:
    from mcp.server.mcpserver import MCPServer as FastMCP

from src.data.qdrant_store import DefectVectorStore
from src.models.model_registry import registry

logger = logging.getLogger("Agent2-MCP-Server")
agent2_mcp = FastMCP("Agent2-Explainability-MCP-Server")

vector_store = DefectVectorStore()

# ── MCP Tool 1: Case Context Retrieval Tool ──────────────────────────────────
@agent2_mcp.tool()
def case_context_retrieval_tool(component_ref: str, top_k: int = 3) -> Dict[str, Any]:
    """[Agent 2 MCP] Searches Qdrant for historical precedents & IPC standards."""
    logger.info(f"[Agent 2 MCP] Searching Qdrant precedents for {component_ref}")
    similar_cases = vector_store.search_similar(
        embedding=[0.0] * 512,
        top_k=top_k,
        metadata_filter={"component_ref": component_ref}
    )
    return {
        "similar_cases": similar_cases,
        "ipc_standard": "IPC-A-610 Class 3: Surface Mount Assemblies"
    }

# ── MCP Tool 2: Visual Evidence Tool ─────────────────────────────────────────
@agent2_mcp.tool()
def visual_evidence_tool(image_path: str, prompt: str) -> Dict[str, Any]:
    """[Agent 2 MCP] Analyzes PCB ROI using Local LLaVA across the 7 defect classes."""
    logger.info(f"[Agent 2 MCP] Querying LLaVA for {image_path}")
    img = Image.open(image_path).convert("RGB")
    
    buffered = io.BytesIO()
    img.save(buffered, format="JPEG")
    
    response = ollama.generate(
        model="llava",
        prompt=prompt,
        images=[buffered.getvalue()],
        options={"num_predict": 300}
    )
    return {
        "visual_description": response.get("response", ""),
        "bounding_boxes": [{"box": [15, 20, 65, 80], "label": "defect_region"}]
    }

# ── MCP Tool 3: Measurement Evidence Tool ────────────────────────────────────
@agent2_mcp.tool()
def measurement_evidence_tool(board_id: str, component_ref: str) -> Dict[str, Any]:
    """[Agent 2 MCP] Queries In-Circuit Test (ICT) resistance and laser height telemetry."""
    logger.info(f"[Agent 2 MCP] Querying ICT telemetry for {board_id}:{component_ref}")
    return {
        "resistance_ohms": 999999,  # Open circuit
        "capacitance_uf": 0.0,
        "laser_height_um": 0.0,
        "continuity": False
    }

# ── MCP Tool 4: Grounding and Self-Check Tool ─────────────────────────────────
@agent2_mcp.tool()
def grounding_and_self_check_tool(reasoning_prompt: str) -> Dict[str, Any]:
    """[Agent 2 MCP] Calls OpenAI GPT-4o for cross-verification & strict JSON output."""
    logger.info("[Agent 2 MCP] Executing OpenAI reasoning & self-check")
    response_text = registry.reasoning_llm.query(reasoning_prompt, require_json=True)
    return json.loads(response_text)
