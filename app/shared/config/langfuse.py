"""Optional self-hosted LangFuse tracing (infra/development/docker-compose.yml's "langfuse"
profile) for the chat agents.

Three pieces, all degrading to "no tracing" rather than raising:

- `langfuse_callbacks()` - the LangChain callback handler to splice into an agent's
  `config={"callbacks": ...}`; records every model call and tool call as nested observations.
- `@traced(name)` - wraps an async function in a span, so one inspection or relabel is a single
  trace with its LLM calls nested under it.
- `trace_attributes()` - stamps everything created inside (a chat turn's model calls, tool calls
  and nested spans) with the user and conversation, so a conversation's traces group together.

With `langfuse_enabled` off, or a blank key, the callback list is empty, `@traced` calls the
function straight through, and `trace_attributes()` does nothing - so no call site needs a branch
of its own. The `langfuse` import is deferred so a disabled setup never pays for its OTEL exporter.
"""

import functools
import logging
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import ParamSpec, TypeVar

from langchain_core.callbacks import BaseCallbackHandler

from app.shared.config.settings import settings

logger = logging.getLogger(__name__)

_P = ParamSpec("_P")
_R = TypeVar("_R")

_initialised = False


def tracing_enabled() -> bool:
    return bool(
        settings.langfuse_enabled and settings.langfuse_public_key and settings.langfuse_secret_key
    )


def init_tracing() -> bool:
    """Registers the process-wide Langfuse client that the callback handler and `observe` both
    pick up. Idempotent; returns whether tracing is on. A failure to initialise is logged and
    means "off"."""

    global _initialised
    if not tracing_enabled():
        return False
    if _initialised:
        return True
    try:
        from langfuse import Langfuse

        Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            base_url=settings.langfuse_host,
        )
    except Exception:  # tracing is optional; never take the app down with it
        logger.exception("Langfuse could not be initialised - continuing without tracing")
        return False
    _initialised = True
    return True


def flush_tracing() -> None:
    """Sends any buffered spans - called on shutdown so the last requests are not lost."""

    if not _initialised:
        return
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception:
        logger.exception("Langfuse flush failed")


def langfuse_callbacks() -> list[BaseCallbackHandler]:
    """[] when tracing is off. Built per call: the handler is cheap and attaches to whatever span
    is active, which is what nests an agent's model calls under its `@traced` parent."""

    if not init_tracing():
        return []
    from langfuse.langchain import CallbackHandler

    return [CallbackHandler()]


def traced(name: str) -> Callable[[Callable[_P, Awaitable[_R]]], Callable[_P, Awaitable[_R]]]:
    """Wraps an async function in a Langfuse span named `name` when tracing is on. The decision is
    made per call, so toggling the setting (or a test monkeypatching it) takes effect at once."""

    def decorator(fn: Callable[_P, Awaitable[_R]]) -> Callable[_P, Awaitable[_R]]:
        observed: Callable[_P, Awaitable[_R]] | None = None

        @functools.wraps(fn)
        async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            nonlocal observed
            if not init_tracing():
                return await fn(*args, **kwargs)
            if observed is None:
                from langfuse import observe

                observed = observe(name=name, capture_input=False, capture_output=False)(fn)
            return await observed(*args, **kwargs)

        return wrapper

    return decorator


@contextmanager
def trace_attributes(
    *, user_id: str, session_id: str, tags: list[str] | None = None
) -> Iterator[None]:
    """Attributes every observation created inside to this user and conversation. A no-op when
    tracing is off."""

    if not init_tracing():
        yield
        return
    from langfuse import propagate_attributes

    with propagate_attributes(user_id=user_id, session_id=session_id, tags=tags or []):
        yield
