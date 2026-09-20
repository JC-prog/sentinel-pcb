"""Ported verbatim from orchestrator-agent/adc_agentic_project's policy/policy_engine.py."""


class PolicyEngine:
    """Deterministic guardrails. The LLM proposes; policy allows/rejects."""

    def validate_action(self, decision, state):
        if decision == "dataset_preparation":
            if state.prepared_samples:
                return False, "Preparation already produced samples; repeating it would not advance state."
            return True, "Raw ADC inputs may be prepared."

        if decision == "dataset_verification":
            if not state.prepared_samples:
                return False, "Verification requires prepared samples."
            if state.verified_samples:
                return False, "Verified samples already exist; repeating verification would not advance state."
            return True, "Prepared samples may be verified."

        if decision == "multimodal_inference":
            if not state.verified_samples:
                return False, "Inference requires at least one verified sample."
            if state.inference_results:
                return False, "Inference results already exist; repeating inference would not advance state."
            return True, "Verified samples are available for inference."

        if decision == "finalize":
            if not state.inference_results:
                return False, "Finalize requires inference results."
            return True, "Inference results are available for terminal decision aggregation."

        if decision == "abort":
            # Do not allow the LLM to abort while a valid deterministic next
            # step still exists. This keeps termination under policy control.
            if not state.prepared_samples:
                return False, "Abort rejected: dataset preparation is still available."
            if state.prepared_samples and not state.verified_samples:
                return False, "Abort rejected: dataset verification is still available."
            if state.verified_samples and not state.inference_results:
                return False, "Abort rejected: verified samples are available for inference."
            if state.inference_results:
                return False, "Abort rejected: inference results should be finalized."
            return True, "No recoverable workflow action remains."

        return False, f"Unsupported planner action: {decision}"

    def allow_inference(self, verification_result):
        if not verification_result.data.get("verified_samples"):
            return False, "No verified samples are available."
        return True, "Verified samples are available."

    def allow_stage2(self, feature_confidence, threshold):
        if feature_confidence < threshold:
            return False, "Feature confidence is below policy threshold."
        return True, "Feature confidence satisfies policy."
