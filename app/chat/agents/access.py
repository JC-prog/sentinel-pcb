"""Which chat tools each UserRole may call - enforced in app/main.py both when building the
`tools` offered to the LLM (_available_tool_specs) and, as defense in depth, again right before
dispatch (_run_tool_call), so a client can't reach a tool merely by naming it in a tool-call
request that was never actually offered.

generic tools (current_time, get_weather) stay universal across every role - none of them touch
inspection/review/monitoring data, so there's no reason to gate them.
"""

from app.shared.db.models import UserRole

TOOL_ROLES: dict[str, frozenset[UserRole]] = {
    "current_time": frozenset(UserRole),
    "get_weather": frozenset(UserRole),
    "create_case": frozenset({UserRole.QA, UserRole.ADMIN}),
    "list_cases": frozenset({UserRole.QA, UserRole.ADMIN}),
    "review_case": frozenset({UserRole.QA, UserRole.ADMIN}),
    "explainability_review": frozenset({UserRole.QA, UserRole.ADMIN}),
    "investigate_case": frozenset({UserRole.QA, UserRole.ADMIN}),
    "flag_case_for_retraining": frozenset({UserRole.QA, UserRole.ADMIN}),
    # Admin-only, not QA - infra/monitoring visibility is a configuration concern, not a QA
    # day-to-day action (unlike flag_case_for_retraining, which lives in the same agent folder but
    # is a QA judgment call about a specific case).
    "monitoring_status": frozenset({UserRole.ADMIN}),
}


def allowed_tool_names(role: UserRole) -> frozenset[str]:
    return frozenset(name for name, roles in TOOL_ROLES.items() if role in roles)
