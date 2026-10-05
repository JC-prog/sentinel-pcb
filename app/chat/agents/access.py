"""Which chat tools each UserRole may call. registry.py filters on this table when it decides which
tools a request may use, and only those tools are given to the supervisor model - so a call naming
any other tool cannot run, however the model was prompted. Hiding a tool from the model is not
itself the access control; not having given it one is.
"""

from app.shared.db.models import UserRole

TOOL_ROLES: dict[str, frozenset[UserRole]] = {
    "inspect_image": frozenset({UserRole.QA, UserRole.ADMIN}),
    # Proposing a relabel saves nothing; confirming it records the correction and queues a
    # retraining ticket - an Admin still approves the retraining itself in the Models tab.
    "relabel_case": frozenset({UserRole.QA, UserRole.ADMIN}),
    "confirm_relabel": frozenset({UserRole.QA, UserRole.ADMIN}),
    # Proposing a case review saves nothing; confirming it resolves the case (APPROVED/OVERRIDDEN).
    "review_case": frozenset({UserRole.QA, UserRole.ADMIN}),
    "confirm_review": frozenset({UserRole.QA, UserRole.ADMIN}),
    "report_model_drift": frozenset({UserRole.QA, UserRole.ADMIN}),
    "get_drift_summary": frozenset({UserRole.QA, UserRole.ADMIN}),
    # Drafting only queues a plan as pending approval; approving it (and promoting a retrained
    # model) are Admin-only actions in the Models tab, never chat tools.
    "draft_retraining_plan": frozenset({UserRole.QA, UserRole.ADMIN}),
    # Admin-only, not QA - infra/monitoring visibility is a configuration concern, not a QA
    # day-to-day action.
    "monitoring_status": frozenset({UserRole.ADMIN}),
}


def allowed_tool_names(role: UserRole) -> frozenset[str]:
    return frozenset(name for name, roles in TOOL_ROLES.items() if role in roles)
