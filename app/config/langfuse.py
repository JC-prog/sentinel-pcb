"""Optional self-hosted LangFuse tracing (infra/development/docker-compose.yml's "langfuse"
profile) for the app's LangGraph pipelines - app/agents/{time_agent,weather_agent,router_agent,
explainability_review_agent,adc_inspection_agent}. Deliberately scoped to those 5 compiled
graphs only: the raw (non-LangChain) OpenAI() clients in router_agent/graph.py,
weather_agent/graph.py, and explainability_review_agent/models.py, and app/chat/providers/
openai.py's raw httpx streaming path, are not instrumented here.

get_langfuse_callbacks() returns [] whenever settings.langfuse_enabled is False or either key is
blank - "disabled" and "unconfigured" both degrade to no tracing rather than raising, so every
call site can unconditionally splice the result into `config={"callbacks": ...}` with no branch
of its own, the same way weather_advisory_enabled's absence degrades to a templated summary
instead of every call site checking the flag itself.
"""

from langchain_core.callbacks import BaseCallbackHandler

from app.config.settings import settings

_callbacks: list[BaseCallbackHandler] | None = None


def get_langfuse_callbacks() -> list[BaseCallbackHandler]:
    """Lazy, process-wide singleton - cheap to build, but no reason to rebuild it per call
    (mirrors each graph.py's get_pipeline()). The `langfuse` import is deferred into the enabled
    branch so a disabled/unconfigured setup never pays for initializing its OTEL exporter."""

    global _callbacks
    if _callbacks is None:
        if settings.langfuse_enabled and settings.langfuse_public_key and settings.langfuse_secret_key:
            from langfuse import Langfuse
            from langfuse.langchain import CallbackHandler

            # Registers the process-wide Langfuse client singleton; CallbackHandler() below picks
            # it up via get_client() rather than needing keys passed to it directly.
            Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
            )
            _callbacks = [CallbackHandler()]
        else:
            _callbacks = []
    return _callbacks
