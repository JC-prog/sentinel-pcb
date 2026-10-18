"""Which chat tools each UserRole may call. registry.py filters on this table when it decides which
tools a request may use, and only those tools are given to the supervisor model - so a call naming
any other tool cannot run, however the model was prompted. Hiding a tool from the model is not
itself the access control; not having given it one is.
"""

from app.shared.db.models import UserRole

TOOL_ROLES: dict[str, frozenset[UserRole]] = {
    "inspect_image": frozenset({UserRole.QA, UserRole.ADMIN}),
    # Same as inspect_image, on an image already uploaded to this conversation. Saves nothing.
    "reinspect_image": frozenset({UserRole.QA, UserRole.ADMIN}),
    # Inspecting saves nothing; creating the Case (only after the user says yes) is what makes the
    # inspection something to relabel or review.
    "create_case": frozenset({UserRole.QA, UserRole.ADMIN}),
    # The positive half of feedback: the label was right. Saves a confirmation, not a case.
    "confirm_label_correct": frozenset({UserRole.QA, UserRole.ADMIN}),
    # Advisory only: Agent 2's explanation of the inspection the user is looking at. Nothing saved.
    "explain_inspection": frozenset({UserRole.QA, UserRole.ADMIN}),
    # Proposing a relabel saves nothing; confirming it records the correction and queues a
    # retraining ticket - an Admin still approves the retraining itself in the Models tab.
    "relabel_case": frozenset({UserRole.QA, UserRole.ADMIN}),
    "confirm_relabel": frozenset({UserRole.QA, UserRole.ADMIN}),
    # Discarding the user's own unconfirmed proposal; nothing was saved, nothing is saved.
    "cancel_relabel": frozenset({UserRole.QA, UserRole.ADMIN}),
    # The user's own corrections: reading them, and taking one back while it is not in a plan yet.
    "list_my_corrections": frozenset({UserRole.QA, UserRole.ADMIN}),
    "withdraw_correction": frozenset({UserRole.QA, UserRole.ADMIN}),
    # Proposing a case review saves nothing; confirming it resolves the case (APPROVED/OVERRIDDEN).
    "review_case": frozenset({UserRole.QA, UserRole.ADMIN}),
    "confirm_review": frozenset({UserRole.QA, UserRole.ADMIN}),
    "cancel_review": frozenset({UserRole.QA, UserRole.ADMIN}),
    # Taking back a review the user confirmed themselves: the case awaits review again.
    "withdraw_review": frozenset({UserRole.QA, UserRole.ADMIN}),
    "report_model_drift": frozenset({UserRole.QA, UserRole.ADMIN}),
    "get_drift_summary": frozenset({UserRole.QA, UserRole.ADMIN}),
    # Drafting only queues a plan as pending approval; approving it (and promoting a retrained
    # model) are Admin-only actions in the Models tab, never chat tools.
    "draft_retraining_plan": frozenset({UserRole.QA, UserRole.ADMIN}),
    # Admin-only, not QA - infra/monitoring visibility is a configuration concern, not a QA
    # day-to-day action.
    "monitoring_status": frozenset({UserRole.ADMIN}),
    # Same reason: what has been reported and how a retraining job is doing is Admin visibility.
    # A QA user is told to contact an administrator (messages.py). Read-only; approving stays in
    # the Models tab.
    "list_drift_reports": frozenset({UserRole.ADMIN}),
    "get_retraining_job": frozenset({UserRole.ADMIN}),
    # Read-only views of the Work tab's stored runs, which are QA/Admin only there too.
    "get_sample": frozenset({UserRole.QA, UserRole.ADMIN}),
    # Read-only views of saved cases - the same list the Cases tab shows to QA and Admin.
    "get_case": frozenset({UserRole.QA, UserRole.ADMIN}),
    "list_cases": frozenset({UserRole.QA, UserRole.ADMIN}),
    "get_component_history": frozenset({UserRole.QA, UserRole.ADMIN}),
    # Counts of what happened over a period - cases, inspections, ratings are shared QA/Admin data.
    "get_activity_summary": frozenset({UserRole.QA, UserRole.ADMIN}),
    "list_runs": frozenset({UserRole.QA, UserRole.ADMIN}),
    "list_review_cases": frozenset({UserRole.QA, UserRole.ADMIN}),
    "get_run_drift": frozenset({UserRole.QA, UserRole.ADMIN}),
}


def allowed_tool_names(role: UserRole) -> frozenset[str]:
    return frozenset(name for name, roles in TOOL_ROLES.items() if role in roles)
