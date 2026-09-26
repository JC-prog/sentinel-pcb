"""Pure interface for chat input guardrails - no framework/IO imports, same reasoning as
app/chat/core/chat.py. The concrete implementation (app/chat/guardrails/nemo_checker.py) is backed
by NeMo Guardrails; app/chat/guardrails/service.py's get_guardrails_checker() factory is the only
place that constructs one, mirroring app/chat/services/service.py's get_chat_service().
"""

from typing import Protocol

from pydantic import BaseModel


class GuardrailResult(BaseModel):
    allowed: bool
    reason: str | None = None


class GuardrailsChecker(Protocol):
    async def check_input(self, message: str) -> GuardrailResult: ...
