"""Chat input guardrails - a NeMo Guardrails check run once on the raw user message before it
reaches the intent router or any chat LLM call (see app/chat/services/streaming.py's chat_sse).
app/chat/core/guardrails.py defines the GuardrailsChecker interface this depends on;
nemo_checker.py is the only concrete implementation, and get_guardrails_checker() is the only
place that constructs one - mirrors app/chat/services/service.py's get_chat_service().
"""

from app.chat.guardrails.nemo_checker import get_guardrails_checker

__all__ = ["get_guardrails_checker"]
