"""Pure interface for an agent tool. `parameters` is a JSON Schema object describing the tool's
keyword arguments, in the shape LLM providers' function-calling APIs expect - actually wiring a
provider to advertise/invoke these is out of scope until a concrete tool worth calling exists
(e.g. Qdrant retrieval).
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Tool(Protocol):
    name: str
    description: str
    parameters: dict[str, Any]

    async def run(self, **kwargs: Any) -> str: ...
