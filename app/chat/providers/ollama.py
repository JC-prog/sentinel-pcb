import json
from collections.abc import AsyncGenerator

import httpx

from app.settings import settings


class OllamaChatService:
    """Streams a reply from a local Ollama server (settings.ollama_base_url). See
    https://github.com/ollama/ollama/blob/main/docs/api.md#generate-a-chat-completion - the
    response body is newline-delimited JSON, one object per token/chunk, ending with
    `"done": true`."""

    async def stream_reply(self, message: str, image_ids: list[str]) -> AsyncGenerator[str, None]:
        payload = {
            "model": settings.ollama_model,
            "messages": [{"role": "user", "content": message}],
            "stream": True,
        }
        async with httpx.AsyncClient(timeout=60.0) as client, client.stream(
            "POST", f"{settings.ollama_base_url}/api/chat", json=payload
        ) as response:
            if response.status_code != 200:
                body = await response.aread()
                raise RuntimeError(
                    f"Ollama request failed ({response.status_code}): {body.decode(errors='replace')}"
                )
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                data = json.loads(line)
                content = data.get("message", {}).get("content", "")
                if content:
                    yield content
                if data.get("done"):
                    return
