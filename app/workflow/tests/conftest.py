"""Conftest for the ported Agent 2 / Agent 1 tests (run from app/workflow/, see INTEGRATION_NOTES.md).

The ported MCP server (src/agent2_explainability/mcp/agent2_mcp_server.py) decorates plain functions
with `@agent2_mcp.tool()` and, upstream, ran without FastMCP installed (it falls back to a no-op
decorator). The backend now depends on `fastmcp` for the explanation service, and a real FastMCP
refuses that module (`case_context_retrieval_tool` takes `**kwargs`), so collecting these tests failed
the moment `fastmcp` became importable.

These tests call the tools as ordinary functions, so give the ported module the same stand-in it
gets without FastMCP: `tool()` returns the function unchanged. app/workflow/services/
agent2_mcp_service.py does the same for the one import it makes.
"""

import sys
import types


class _PlainFastMCP:
    def __init__(self, name: str = "") -> None:
        self.name = name

    def tool(self, *args, **kwargs):
        return lambda fn: fn


_stub = types.ModuleType("fastmcp")
_stub.FastMCP = _PlainFastMCP  # type: ignore[attr-defined]
sys.modules["fastmcp"] = _stub
