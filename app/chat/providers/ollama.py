import json
from collections.abc import AsyncGenerator

import httpx

from app.core.chat import ChatTurn
from app.settings import settings


class OllamaChatService:
    """Streams a reply from a local Ollama server (settings.ollama_base_url). See
    https://github.com/ollama/ollama/blob/main/docs/api.md#generate-a-chat-completion - the
    response body is newline-delimited JSON, one object per token/chunk, ending with
    `"done": true`."""

    async def stream_reply(
        self,
        history: list[ChatTurn],
        message: str,
        image_ids: list[str],
        system_prompt: str | None = None,
    ) -> AsyncGenerator[str, None]:
        messages = [{"role": "system", "content": system_prompt}] if system_prompt else []
        messages += [{"role": turn.role, "content": turn.content} for turn in history]
        messages.append({"role": "user", "content": message})
        payload = {
            "model": settings.ollama_model,
            "messages": messages,
            "stream": True,
        }
        async with (
            httpx.AsyncClient(timeout=60.0) as client,
            client.stream("POST", f"{settings.ollama_base_url}/api/chat", json=payload) as response,
        ):
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
