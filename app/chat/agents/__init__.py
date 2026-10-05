"""The chat's agents and the one registry of their tools.

The supervisor (supervisor.py) is the chat model the user talks to - a LangGraph agent that picks
among the four agents' tools and writes the reply. Each agent is a package that owns its tools,
its logic and its rules:

- inspection_agent - `inspect_image`: classify an attached image, decide the verdict, save a Case.
- relabel_agent    - `relabel_case` / `confirm_relabel`: record a QA's correction of a wrong label.
- review_agent     - `review_case` / `confirm_review`: approve or override a case flagged for review.
- monitoring_agent - drift summaries and reports, retraining plans, the Admin status overview.

Agents never import each other (tests/chat/test_agent_boundaries.py); what they share lives in
app/chat/services/. This module, supervisor.py, registry.py, access.py and toolkit.py are the
plumbing beside them.
"""

from app.chat.agents.inspection_agent import INSPECT_IMAGE
from app.chat.agents.monitoring_agent import (
    DRAFT_RETRAINING_PLAN,
    GET_DRIFT_SUMMARY,
    MONITORING_STATUS,
    REPORT_MODEL_DRIFT,
)
from app.chat.agents.registry import ToolNotFound, ToolRegistry
from app.chat.agents.relabel_agent import CONFIRM_RELABEL, RELABEL_CASE
from app.chat.agents.review_agent import CONFIRM_REVIEW, REVIEW_CASE

tool_registry = ToolRegistry(
    [
        INSPECT_IMAGE,
        RELABEL_CASE,
        CONFIRM_RELABEL,
        REVIEW_CASE,
        CONFIRM_REVIEW,
        GET_DRIFT_SUMMARY,
        REPORT_MODEL_DRIFT,
        DRAFT_RETRAINING_PLAN,
        MONITORING_STATUS,
    ]
)

__all__ = ["ToolNotFound", "ToolRegistry", "tool_registry"]
