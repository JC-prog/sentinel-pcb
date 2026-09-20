"""Adapted from orchestrator-agent/adc_agentic_project's policy/policy_engine.py: a centralized,
independently testable guardrail layer that validates each action the planner proposes before
graph.py dispatches to it. Replaces what the old case_agent/graph.py expressed as a scattering of
ad hoc `_route_after_*` conditional-edge functions.

One deliberate divergence from the source project: there, a policy rejection loops back to the
planner, which is fine when the planner can be LLM-backed (a different proposal might come back
next time). Planner.plan() here is a pure function of state, so a rejection must route straight to
`abort` instead (see graph.py) - asking a pure function the same question against unchanged state
would just propose the same rejected action forever. PolicyEngine is therefore a safety net
against planner bugs here, not a retry mechanism.
"""

from app.chat.agents.adc_inspection_agent.workflow_state import OrchestratorState


class PolicyEngine:
    def validate_action(self, decision: str, state: OrchestratorState) -> tuple[bool, str]:
        if decision == "verify_image":
            if state["image_readable"] is not None:
                return False, "Image already verified."
            return True, "OK"

        if decision == "lookup_golden_image":
            if state["image_readable"] is not True:
                return False, "Golden lookup requires a readable image."
            if state["golden_lookup_done"]:
                return False, "Golden lookup already performed."
            return True, "OK"

        if decision == "validate_measurements":
            if state["inspection_xml_bytes"] is None:
                return False, "No inspection XML attached."
            if state["measurement_validation"] is not None:
                return False, "Measurements already validated."
            return True, "OK"

        if decision == "align_and_check_quality":
            if state["golden_image_id"] is None:
                return False, "No golden image found; nothing to align against."
            if state["alignment_done"]:
                return False, "Alignment already performed."
            return True, "OK"

        if decision == "classify_region":
            if not state["golden_lookup_done"]:
                return False, "Region classification requires golden lookup to run first."
            if state["region"]:
                return False, "Region already classified."
            return True, "OK"

        if decision == "classify_defect":
            if not state["region"] or state["region_uncertain"]:
                return False, "Defect classification requires a confident region result."
            if state["defect_label"]:
                return False, "Defect already classified."
            return True, "OK"

        if decision == "finalize":
            if not state["region"]:
                return False, "Finalize requires classification to have run."
            if not state["region_uncertain"] and not state["defect_label"]:
                return False, "Finalize requires defect classification when region was confident."
            if state["final_decision"]:
                return False, "Already finalized."
            return True, "OK"

        if decision == "escalate_review":
            if state["final_decision"] != "REVIEW_REQUIRED":
                return False, "Escalation only applies to REVIEW_REQUIRED verdicts."
            if state["escalation_done"]:
                return False, "Escalation already performed."
            return True, "OK"

        if decision == "persist_case":
            if not state["final_decision"]:
                return False, "Nothing to persist before finalize."
            if state["final_decision"] == "REVIEW_REQUIRED" and not state["escalation_done"]:
                return False, "REVIEW_REQUIRED cases must escalate before persisting."
            if state["case_persisted"]:
                return False, "Case already persisted."
            return True, "OK"

        if decision == "abort":
            if state.get("error"):
                return True, "OK"
            return False, "Abort rejected: no fatal error and a valid next step exists."

        return False, f"Unsupported action: {decision!r}"
