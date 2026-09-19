"""Adapted from orchestrator-agent/adc_agentic_project's planner/llm_planner.py. That project's
Planner has an LLM-backed path (_plan_with_openai) with a deterministic fallback
(_deterministic_plan), because it plans across a whole batch of dataset samples where genuinely
ambiguous next-step choices can arise. A single case here never has that ambiguity - whether an
XML was attached, whether a golden image was found, whether region confidence cleared the
threshold are all concrete facts already sitting in state, not something an LLM needs to weigh in
on. So this Planner is deterministic only: a direct re-expression of _deterministic_plan's
state-machine logic for exactly one case. If genuine ambiguity ever shows up (e.g. choosing among
multiple candidate golden images), an LLM-backed branch can be added behind this same
Planner.plan() signature without touching graph.py.
"""

from dataclasses import dataclass

from app.agents.adc_inspection_agent.workflow_state import OrchestratorState

ALLOWED_DECISIONS = frozenset(
    {
        "verify_image",
        "lookup_golden_image",
        "validate_measurements",
        "align_and_check_quality",
        "classify_region",
        "classify_defect",
        "finalize",
        "escalate_review",
        "persist_case",
        "abort",
        "done",
    }
)


@dataclass(frozen=True)
class PlanDecision:
    observation: str
    decision: str
    reason: str


class Planner:
    def plan(self, state: OrchestratorState) -> PlanDecision:
        if state.get("error"):
            return PlanDecision(
                observation=f"error recorded: {state['error']}",
                decision="abort",
                reason="A fatal error was already recorded; no further steps can proceed.",
            )

        if state["image_readable"] is None:
            return PlanDecision(
                observation="image not yet verified",
                decision="verify_image",
                reason="Every run starts by confirming the uploaded file decodes as an image.",
            )

        if not state["golden_lookup_done"]:
            return PlanDecision(
                observation="golden reference lookup not yet attempted",
                decision="lookup_golden_image",
                reason="Look up a matching golden reference before classification.",
            )

        if state["inspection_xml_bytes"] is not None and state["measurement_validation"] is None:
            return PlanDecision(
                observation="inspection XML attached, not yet validated",
                decision="validate_measurements",
                reason="An inspection XML was attached; validate its measurements before finalizing.",
            )

        if state["golden_image_id"] is not None and not state["alignment_done"]:
            return PlanDecision(
                observation="golden reference found, alignment/quality not yet checked",
                decision="align_and_check_quality",
                reason="A golden reference exists; compare it against the case image before classifying.",
            )

        if not state["region"]:
            return PlanDecision(
                observation="region not yet classified",
                decision="classify_region",
                reason="Stage 1: classify which component region this image is a crop of.",
            )

        if not state["region_uncertain"] and not state["defect_label"]:
            return PlanDecision(
                observation="region confident, defect not yet classified",
                decision="classify_defect",
                reason="Stage 2: classify the defect using the model routed for this region.",
            )

        if not state["final_decision"]:
            return PlanDecision(
                observation="classification complete, not yet finalized",
                decision="finalize",
                reason="Combine confidence gates and measurement/alignment issues into a verdict.",
            )

        if state["final_decision"] == "REVIEW_REQUIRED" and not state["escalation_done"]:
            return PlanDecision(
                observation="verdict is REVIEW_REQUIRED, not yet escalated",
                decision="escalate_review",
                reason="A REVIEW_REQUIRED verdict must be handed to explainability before persisting.",
            )

        if not state["case_persisted"]:
            return PlanDecision(
                observation="finalized, not yet persisted",
                decision="persist_case",
                reason="Persist the case record with whatever the run has produced so far.",
            )

        return PlanDecision(
            observation="workflow complete",
            decision="done",
            reason="Every applicable step has run; nothing left to plan.",
        )
