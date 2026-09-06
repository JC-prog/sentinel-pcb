import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from app.config.settings import settings
from app.core.chat import ChatMessage, ChatTurn, TextDelta, ToolCallRequest, ToolCallsReady

logger = logging.getLogger(__name__)

_OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"


def _to_openai_message(message: ChatMessage) -> dict[str, Any]:
    """Only includes keys that are actually set - tests assert exact request-payload dicts, and
    a stray `null` tool_calls/tool_call_id would show up in every plain (non-tool) message
    otherwise."""

    wire: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        wire["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in message.tool_calls
        ]
    if message.tool_call_id is not None:
        wire["tool_call_id"] = message.tool_call_id
    return wire


def _to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


class OpenAiChatService:
    """Streams a reply from OpenAI's chat completions API using the server-side
    settings.openai_api_key (no per-request bring-your-own-key - see app/config/settings.py).
    The response body is OpenAI's own SSE framing (`data: {...}\\n\\n`, terminated by
    `data: [DONE]`)."""

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
            "model": settings.openai_model,
            "messages": messages,
            "stream": True,
        }
        headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
        logger.debug("OpenAI request: %s", payload, extra={"payload": payload})

        text_parts: list[str] = []
        async with (
            httpx.AsyncClient(timeout=60.0) as client,
            client.stream(
                "POST", _OPENAI_CHAT_COMPLETIONS_URL, json=payload, headers=headers
            ) as response,
        ):
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
                            full_content = "".join(text_parts)
                            logger.debug(
                                "OpenAI response: %r", full_content, extra={"content": full_content}
                            )
                            return
                        data = json.loads(data_str)
                        delta = data["choices"][0]["delta"].get("content")
                        if delta:
                            text_parts.append(delta)
                            yield delta

    async def stream_with_tools(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None,
    ) -> AsyncGenerator[TextDelta | ToolCallsReady, None]:
        """Same SSE framing as stream_reply, but tool-aware. OpenAI's streamed tool-calls arrive
        as incremental argument-string fragments keyed by index (id/function.name only on the
        first delta for that index) - accumulated here and only parsed once finish_reason ==
        "tool_calls" confirms the round is complete."""

        payload: dict[str, Any] = {
            "model": settings.openai_model,
            "messages": [_to_openai_message(m) for m in messages],
            "stream": True,
        }
        if tools:
            payload["tools"] = _to_openai_tools(tools)
        headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
        logger.debug("OpenAI request: %s", payload, extra={"payload": payload})

        text_parts: list[str] = []
        tool_calls_acc: dict[int, dict[str, Any]] = {}

        async with (
            httpx.AsyncClient(timeout=60.0) as client,
            client.stream(
                "POST", _OPENAI_CHAT_COMPLETIONS_URL, json=payload, headers=headers
            ) as response,
        ):
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
                            full_content = "".join(text_parts)
                            logger.debug(
                                "OpenAI response: %r", full_content, extra={"content": full_content}
                            )
                            return
                        data = json.loads(data_str)
                        choice = data["choices"][0]
                        delta = choice["delta"]

                        content = delta.get("content")
                        if content:
                            text_parts.append(content)
                            yield TextDelta(text=content)

                        for tc_delta in delta.get("tool_calls") or []:
                            index = tc_delta["index"]
                            entry = tool_calls_acc.setdefault(
                                index, {"id": None, "name": None, "arguments": ""}
                            )
                            if tc_delta.get("id"):
                                entry["id"] = tc_delta["id"]
                            function = tc_delta.get("function") or {}
                            if function.get("name"):
                                entry["name"] = function["name"]
                            if function.get("arguments"):
                                entry["arguments"] += function["arguments"]

                        if choice.get("finish_reason") == "tool_calls" and tool_calls_acc:
                            full_content = "".join(text_parts)
                            logger.debug(
                                "OpenAI response: content=%r tool_calls=%s",
                                full_content,
                                list(tool_calls_acc.values()),
                                extra={
                                    "content": full_content,
                                    "tool_calls": list(tool_calls_acc.values()),
                                },
                            )
                            yield ToolCallsReady(
                                calls=[
                                    ToolCallRequest(
                                        id=entry["id"],
                                        name=entry["name"],
                                        arguments=json.loads(entry["arguments"]),
                                    )
                                    for entry in tool_calls_acc.values()
                                ]
                            )
                            return
