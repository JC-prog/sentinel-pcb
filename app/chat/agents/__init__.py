"""The chat's agents and the one registry of their tools.

The supervisor (supervisor.py) is the chat model the user talks to - a LangGraph agent that picks
among the agents' tools and writes the reply. Each agent is a package that owns its tools,
its logic and its rules:

- inspection_agent - `inspect_image` / `create_case`: classify an attached image and decide the
  verdict; after the user says they want one, save it as a Case.
- relabel_agent    - `relabel_case` / `confirm_relabel`: record a QA's correction of a wrong label.
- review_agent     - `review_case` / `confirm_review`: approve or override a case flagged for review.
- monitoring_agent - drift summaries and reports, retraining plans, the Admin status overview.
- case_agent       - `get_case` / `list_cases`: read the cases chat has saved (the Cases tab's data).
- sample_agent     - `get_sample` / `list_review_cases` / `get_run_drift`: read what the Work tab
  stored about a sample or a run (including how often the operator corrected the model).

Agents never import each other (tests/chat/test_agent_boundaries.py); what they share lives in
app/chat/services/. This module, supervisor.py, registry.py, access.py and toolkit.py are the
plumbing beside them.
"""

from app.chat.agents.case_agent import GET_CASE, GET_COMPONENT_HISTORY, LIST_CASES
from app.chat.agents.inspection_agent import CONFIRM_LABEL_CORRECT, CREATE_CASE, INSPECT_IMAGE
from app.chat.agents.monitoring_agent import (
    DRAFT_RETRAINING_PLAN,
    GET_DRIFT_SUMMARY,
    GET_RETRAINING_JOB,
    LIST_DRIFT_REPORTS,
    MONITORING_STATUS,
    REPORT_MODEL_DRIFT,
)
from app.chat.agents.registry import ToolNotFound, ToolRegistry
from app.chat.agents.relabel_agent import (
    CONFIRM_RELABEL,
    LIST_MY_CORRECTIONS,
    RELABEL_CASE,
    WITHDRAW_CORRECTION,
)
from app.chat.agents.review_agent import CONFIRM_REVIEW, REVIEW_CASE
from app.chat.agents.sample_agent import GET_RUN_DRIFT, GET_SAMPLE, LIST_REVIEW_CASES, LIST_RUNS

tool_registry = ToolRegistry(
    [
        INSPECT_IMAGE,
        CONFIRM_LABEL_CORRECT,
        CREATE_CASE,
        RELABEL_CASE,
        CONFIRM_RELABEL,
        LIST_MY_CORRECTIONS,
        WITHDRAW_CORRECTION,
        REVIEW_CASE,
        CONFIRM_REVIEW,
        GET_DRIFT_SUMMARY,
        REPORT_MODEL_DRIFT,
        DRAFT_RETRAINING_PLAN,
        MONITORING_STATUS,
        LIST_DRIFT_REPORTS,
        GET_RETRAINING_JOB,
        GET_CASE,
        LIST_CASES,
        GET_COMPONENT_HISTORY,
        GET_SAMPLE,
        LIST_RUNS,
        LIST_REVIEW_CASES,
        GET_RUN_DRIFT,
    ]
)

__all__ = ["ToolNotFound", "ToolRegistry", "tool_registry"]
