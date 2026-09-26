"""NeMo Guardrails-backed GuardrailsChecker (app/chat/core/guardrails.py). Colang config lives in
app/chat/guardrails/config/ - a single "self check input" rail combining jailbreak/prompt-injection
detection and an off-topic (non-PCB-inspection) check into one prompt, so a checked message costs
one extra LiteLLM round trip, not two. See config/flows.co for how the $allowed context variable
is produced and read back below - the built-in rail only ever returns a canned refusal string, so
reading a boolean out of it directly (rather than string-matching a response) is the only robust
option.
"""

import logging
from pathlib import Path

from nemoguardrails import LLMRails, RailsConfig
from nemoguardrails.rails.llm.config import Model
from nemoguardrails.rails.llm.options import GenerationOptions

from app.chat.core.guardrails import GuardrailResult
from app.shared.config.settings import settings

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parent / "config"

_DEFAULT_REFUSAL = (
    "I'm built for PCB defect inspection and quality workflows, so I can't help with that here."
)

_rails: LLMRails | None = None


def _get_rails() -> LLMRails:
    """Lazy, process-wide singleton - RailsConfig.from_path parses the Colang config, which has
    real startup cost, so this must not run at import time (mirrors app/chat/agents/case_agent/
    graph.py's get_mcp_client()). The model is injected here rather than hardcoded into
    config.yml so it always reflects the current settings.chat_guardrails_model/openai_base_url/
    openai_api_key - the same LiteLLM proxy every other OpenAI-compatible call in this app uses,
    never api.openai.com directly."""

    global _rails
    if _rails is None:
        config = RailsConfig.from_path(str(_CONFIG_DIR))
        config.models = [
            Model(
                type="main",
                engine="openai",
                model=settings.chat_guardrails_model,
                parameters={
                    "base_url": settings.openai_base_url,
                    "api_key": settings.openai_api_key,
                },
            )
        ]
        _rails = LLMRails(config)
    return _rails


class NemoGuardrailsChecker:
    async def check_input(self, message: str) -> GuardrailResult:
        rails = _get_rails()
        options = GenerationOptions(rails=["input"], output_vars=["allowed"])
        response = await rails.generate_async(
            messages=[{"role": "user", "content": message}], options=options
        )
        allowed = bool((response.output_data or {}).get("allowed", True))
        if allowed:
            return GuardrailResult(allowed=True)

        reason = _DEFAULT_REFUSAL
        if isinstance(response.response, list) and response.response:
            reason = response.response[-1].get("content") or _DEFAULT_REFUSAL
        elif isinstance(response.response, str) and response.response:
            reason = response.response
        return GuardrailResult(allowed=False, reason=reason)


def get_guardrails_checker() -> NemoGuardrailsChecker:
    return NemoGuardrailsChecker()
