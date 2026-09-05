import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from app.core.chat import ChatMessage, ChatTurn, TextDelta, ToolCallRequest, ToolCallsReady
from app.settings import settings


def _to_ollama_message(message: ChatMessage) -> dict[str, Any]:
    """Only includes keys that are actually set - tests assert exact request-payload dicts, and
    a stray `null` tool_calls/name would show up in every plain (non-tool) message otherwise."""

    wire: dict[str, Any] = {"role": message.role}
    if message.content is not None:
        wire["content"] = message.content
    if message.tool_calls:
        wire["tool_calls"] = [
            {"function": {"name": tc.name, "arguments": tc.arguments}} for tc in message.tool_calls
        ]
    if message.name is not None:
        wire["name"] = message.name
    return wire


def _to_ollama_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            },
        }
        for t in tools
    ]


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

    async def stream_with_tools(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None,
    ) -> AsyncGenerator[TextDelta | ToolCallsReady, None]:
        """Same NDJSON framing as stream_reply, but tool-aware. Ollama's tool-calls aren't
        delivered incrementally even with stream:true - the full `message.tool_calls` list only
        shows up on the final (`done:true`) line, already parsed (arguments is a dict, not a
        JSON string like OpenAI's), and with no id field - one is synthesized per call below."""

        payload: dict[str, Any] = {
            "model": settings.ollama_model,
            "messages": [_to_ollama_message(m) for m in messages],
            "stream": True,
        }
        if tools:
            payload["tools"] = _to_ollama_tools(tools)

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
                    yield TextDelta(text=content)
                if data.get("done"):
                    tool_calls = data.get("message", {}).get("tool_calls") or []
                    if tool_calls:
                        yield ToolCallsReady(
                            calls=[
                                ToolCallRequest(
                                    id=f"call_{i}",
                                    name=tc["function"]["name"],
                                    arguments=tc["function"]["arguments"],
                                )
                                for i, tc in enumerate(tool_calls)
                            ]
                        )
                    return
