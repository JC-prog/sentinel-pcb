"""Ported from orchestrator-agent/adc_agentic_project's planner/llm_planner.py. Two changes from
the source, both required by this integration (see app/agents/orchestrator_agent's package
docstring for the rest):

1. Config comes from app.config.settings (orchestrator_openai_api_key/orchestrator_openai_model)
   instead of os.getenv + python-dotenv's own load_dotenv() call - pydantic-settings already loads
   .env once at process start.
2. The OpenAI client gets an explicit request timeout (the source had none).

Otherwise unchanged, including staying isolated from settings.openai_base_url/the LiteLLM gateway
every other agent in this codebase goes through - this one keeps its own direct OpenAI() client.
"""

import json
from dataclasses import dataclass
from typing import Any

from app.config.settings import settings

ALLOWED_DECISIONS = {
    "dataset_preparation",
    "dataset_verification",
    "multimodal_inference",
    "finalize",
    "abort",
}


@dataclass
class PlanDecision:
    observation: str
    constraint: str
    decision: str
    reason: str
    source: str = "deterministic"
    raw_response: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "observation": self.observation,
            "constraint": self.constraint,
            "decision": self.decision,
            "reason": self.reason,
            "source": self.source,
        }


class Planner:
    """
    Planner facade.

    When use_llm=True, the planner calls the OpenAI Responses API and asks the
    LLM to choose the next workflow action. The PolicyEngine remains fully
    deterministic and can reject an unsafe/invalid LLM proposal.

    If the API is unavailable and allow_fallback=True, the planner uses a
    deterministic fallback so the workflow can still run offline.
    """

    def __init__(
        self,
        use_llm: bool = False,
        model: str | None = None,
        allow_fallback: bool = True,
    ):
        self.use_llm = use_llm
        self.model = model or settings.orchestrator_openai_model
        self.allow_fallback = allow_fallback
        self.last_error: str | None = None

    def plan(self, state) -> PlanDecision:
        if self.use_llm:
            try:
                return self._plan_with_openai(state)
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                if not self.allow_fallback:
                    raise
                fallback = self._deterministic_plan(state)
                fallback.source = "deterministic_fallback"
                fallback.constraint = (
                    f"LLM planner unavailable ({self.last_error}). "
                    + fallback.constraint
                )
                return fallback

        return self._deterministic_plan(state)

    def _plan_with_openai(self, state) -> PlanDecision:
        from openai import OpenAI

        if not settings.orchestrator_openai_api_key:
            raise RuntimeError(
                "settings.orchestrator_openai_api_key is not set. Put it in the server's .env."
            )

        client = OpenAI(api_key=settings.orchestrator_openai_api_key, timeout=30.0)
        state_json = json.dumps(state.summary_for_planner(), indent=2)

        system_prompt = """You are the planning component of an Agentic Automated Defect Classification (ADC) workflow.

Your job is ONLY to choose the next workflow action from this exact set:
- dataset_preparation
- dataset_verification
- multimodal_inference
- finalize
- abort

The deterministic Policy Engine will validate your proposal before execution.
Do not fabricate data. Do not perform inspection, verification, inference, or policy decisions yourself.

Workflow semantics:
1. dataset_preparation converts dataset.csv + AOI XML into prepared samples.
2. dataset_verification validates prepared samples and image pairs.
3. multimodal_inference runs the two-stage feature classifier and specialist defect classifier on verified samples.
4. finalize computes the terminal workflow outcome from inference decisions.
5. abort is only appropriate when no safe/recoverable action remains.

Partial results are allowed: if at least one verified sample is available, inference may proceed even when other samples failed preparation/verification.
After inference results exist, choose finalize.

Return ONLY a JSON object with exactly these string fields:
{
  \"observation\": \"what the current state shows\",
  \"constraint\": \"the most important workflow/policy constraint\",
  \"decision\": \"one allowed action\",
  \"reason\": \"why this is the next action\"
}
"""

        user_prompt = f"Current ADC workflow state:\n{state_json}\n\nChoose the next action."

        response = client.responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        raw = response.output_text.strip()
        payload = self._parse_json_object(raw)

        decision = str(payload.get("decision", "")).strip()
        if decision not in ALLOWED_DECISIONS:
            raise ValueError(f"LLM returned unsupported decision: {decision!r}")

        return PlanDecision(
            observation=str(payload.get("observation", "")).strip(),
            constraint=str(payload.get("constraint", "")).strip(),
            decision=decision,
            reason=str(payload.get("reason", "")).strip(),
            source="openai",
            raw_response=raw,
        )

    @staticmethod
    def _parse_json_object(text: str) -> dict[str, Any]:
        """Parse a JSON object, tolerating a fenced JSON response."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end < start:
            raise ValueError("LLM response did not contain a JSON object")
        return json.loads(cleaned[start:end + 1])

    @staticmethod
    def _deterministic_plan(state) -> PlanDecision:
        if not state.prepared_samples and state.current_step != "dataset_preparation":
            return PlanDecision(
                observation="Raw ADC inputs are available but no prepared samples exist.",
                constraint="Inference cannot run before preparation and verification.",
                decision="dataset_preparation",
                reason="Prepare and enrich the inference manifest before verification.",
            )

        if state.prepared_samples and not state.verified_samples and state.current_step != "dataset_verification":
            # Even a partial preparation may contain READY samples. Verification
            # determines which of them may proceed.
            return PlanDecision(
                observation=f"Preparation produced {state.preparation_ready} READY sample(s).",
                constraint="Only verified samples may proceed to inference.",
                decision="dataset_verification",
                reason="Validate prepared samples, image pairs, and measurements.",
            )

        if state.verified_samples and not state.inference_results:
            return PlanDecision(
                observation=f"{len(state.verified_samples)} verified sample(s) are available.",
                constraint="Only verified samples may enter model inference.",
                decision="multimodal_inference",
                reason="Run hierarchical feature routing and specialist defect classification.",
            )

        if state.inference_results:
            return PlanDecision(
                observation="Inference results are available.",
                constraint="Terminal workflow status must reflect ACCEPTED, REVIEW_REQUIRED, or ABORTED results.",
                decision="finalize",
                reason="Aggregate per-sample decisions and terminate the workflow correctly.",
            )

        return PlanDecision(
            observation="No executable sample remains.",
            constraint="The workflow must not loop without state progress.",
            decision="abort",
            reason="No verified sample or recoverable next action is available.",
        )
