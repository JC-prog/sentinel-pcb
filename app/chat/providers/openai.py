import json
from collections.abc import AsyncGenerator

import httpx
from pydantic import SecretStr

from app.settings import settings

_OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"


class OpenAiChatService:
    """Streams a reply from OpenAI's chat completions API using a caller-supplied,
    bring-your-own key (never persisted, never logged - see ChatStreamRequest.openai_api_key).
    The response body is OpenAI's own SSE framing (`data: {...}\\n\\n`, terminated by
    `data: [DONE]`)."""

    def __init__(self, api_key: SecretStr) -> None:
        self._api_key = api_key

    async def stream_reply(self, message: str, image_ids: list[str]) -> AsyncGenerator[str, None]:
        payload = {
            "model": settings.openai_model,
            "messages": [{"role": "user", "content": message}],
            "stream": True,
        }
        headers = {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}

        async with httpx.AsyncClient(timeout=60.0) as client, client.stream(
            "POST", _OPENAI_CHAT_COMPLETIONS_URL, json=payload, headers=headers
        ) as response:
            if response.status_code != 200:
                body = await response.aread()
                raise RuntimeError(
                    f"OpenAI request failed ({response.status_code}): {body.decode(errors='replace')}"
                )

            buffer = ""
            async for chunk in response.aiter_text():
                buffer += chunk
                while "\n\n" in buffer:
                    frame, buffer = buffer.split("\n\n", 1)
                    for line in frame.splitlines():
                        if not line.startswith("data:"):
                            continue
                        data_str = line.removeprefix("data:").strip()
                        if data_str == "[DONE]":
                            return
                        data = json.loads(data_str)
                        delta = data["choices"][0]["delta"].get("content")
                        if delta:
                            yield delta
