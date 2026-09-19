"""orchestrator_agent: a Work-tab-only port of orchestrator-agent/adc_agentic_project's bulk
dataset plan/policy/inference workflow (agents/orchestrator.py). Deliberately NOT a chat Tool -
this package is never imported by app/agents/__init__.py, app/agents/registry.py, or
app/agents/access.py, so it is structurally unreachable from the chat tool-calling loop. It is
invoked only via the dedicated /api/orchestrator/* routes in app/main.py.

Distinct from app/agents/adc_inspection_agent/ (whose internal state type is named
OrchestratorState) - that agent runs one image at a time from chat; this one runs a whole CSV
dataset from the Work tab. See adc_inspection_agent/workflow_state.py's docstring for how the two
relate.
"""

from app.agents.orchestrator_agent.orchestrator import OrchestratorAgent
from app.agents.orchestrator_agent.runner import run_stream
from app.agents.orchestrator_agent.workflow_state import WorkflowState

__all__ = ["OrchestratorAgent", "WorkflowState", "run_stream"]
