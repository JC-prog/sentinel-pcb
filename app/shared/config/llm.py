"""Every chat model the app uses, from one factory, so the model - or the provider - can change
without touching a caller.

- `build_chat_model("openai")` - the model behind a conversation: the chat supervisor and memory's
  fact extraction.
- `build_chat_model()` - the agents' own model (the inspect agent's ReAct pass), from
  `AGENT_LLM_PROVIDER` / `AGENT_LLM_MODEL` (blank model -> that provider's chat model).

"openai" goes through `openai_base_url` (OpenAI direct unless overridden) with `openai_api_key`.
Any other provider LangChain knows ("anthropic", ...) works for the agents too, with its
`langchain-<name>` package installed and its credentials in the environment.

Every model carries `LlmDebugLogger`, which logs each request and response at DEBUG (and does
nothing above it) - see app/shared/config/logging_config.py.
"""

import logging
from typing import Any
from uuid import UUID

from langchain.chat_models import init_chat_model
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult
from pydantic import SecretStr

from app.shared.config.settings import settings

logger = logging.getLogger(__name__)


class LlmDebugLogger(AsyncCallbackHandler):
    """Logs what is sent to and returned by a model, at DEBUG only - the request's messages (and the
    tools on offer) as `payload`, the reply's text as `content`, as structured `extra` fields."""

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        invocation_params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if not logger.isEnabledFor(logging.DEBUG):
            return
        payload = {
            "messages": [{"role": m.type, "content": m.content} for m in messages[0]],
            "tools": [
                t.get("function", t).get("name") for t in (invocation_params or {}).get("tools") or []
            ],
        }
        logger.debug("LLM request: %s", payload, extra={"payload": payload})

    async def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        if not logger.isEnabledFor(logging.DEBUG):
            return
        content = response.generations[0][0].text if response.generations else ""
        logger.debug("LLM response: %r", content, extra={"content": content})


def build_chat_model(
    provider: str | None = None,
    *,
    temperature: float | None = 0.0,
    timeout: float | None = None,
    model: str | None = None,
) -> BaseChatModel:
    """`model` overrides the configured one (the chat reply's model, picked in Settings)."""

    if provider is None:
        provider = settings.agent_llm_provider
        chosen = settings.agent_llm_model or _default_model(provider)
    else:
        chosen = _default_model(provider)
    chosen = model or chosen

    kwargs: dict[str, Any] = {"callbacks": [LlmDebugLogger()]}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if provider == "openai" and chosen.startswith("gpt-6"):
        # On the Chat Completions API a GPT-6 model only takes function calls with reasoning off, and
        # every chat turn here is a tool-calling turn - so ask for none rather than fail on the first
        # tool call.
        kwargs["reasoning_effort"] = "none"
    timeout = timeout or settings.agent_llm_timeout_seconds

    model_obj: BaseChatModel
    if provider == "openai":
        model_obj = init_chat_model(
            chosen,
            model_provider="openai",
            api_key=SecretStr(settings.openai_api_key),
            base_url=settings.openai_base_url,
            timeout=timeout,
            **kwargs,
        )
        return model_obj
    model_obj = init_chat_model(chosen, model_provider=provider, timeout=timeout, **kwargs)
    return model_obj


def _default_model(provider: str) -> str:
    return settings.openai_model
