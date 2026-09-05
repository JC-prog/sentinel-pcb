"""A trivial, dependency-free example Tool - proves the Tool protocol/registry work end-to-end.
Not the interesting agent capability; that arrives once a concrete tool (e.g. Qdrant retrieval)
exists.
"""

from datetime import UTC, datetime
from typing import Any


class CurrentTimeTool:
    name = "current_time"
    description = "Returns the current UTC date and time in ISO 8601 format."

    def __init__(self) -> None:
        self.parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def run(self, **kwargs: Any) -> str:
        return datetime.now(UTC).isoformat()
