"""Ported from Kenny's Explainability_Review_Agent/src/models/model_registry.py (see
Explainability_Review_Agent PR history) - unchanged behavior except that the OpenAI client is
now constructed per-instance from a caller-supplied key instead of a module-level global reading
OPENAI_API_KEY, so this agent can honor a per-request bring-your-own-key the same way the rest of
the app's OpenAI usage does (see app/chat/providers/openai.py) while still falling back to
settings.explainability_agent_openai_api_key - see tool.py for that resolution.

pcb_detector remains the hardcoded stub it was in the original code (not a real YOLO model) -
faithfully ported, not fixed. See DEVELOPMENT.md for known gaps in this agent.
"""

import base64
import io
from typing import Any

from openai import OpenAI, omit
from openai.types.chat import ChatCompletionMessageParam
from openai.types.shared_params import ResponseFormatJSONObject
from PIL import Image


class OpenAIReferee:
    """Reasoning engine using GPT-4o with guaranteed JSON format."""

    def __init__(self, api_key: str) -> None:
        self._client = OpenAI(api_key=api_key)

    def query(self, prompt: str, require_json: bool = True) -> str:
        messages: list[ChatCompletionMessageParam] = [
            {
                "role": "system",
                "content": "You are a senior SMT Quality Engineer and IPC-A-610 certified specialist.",
            },
            {"role": "user", "content": prompt},
        ]
        response_format: ResponseFormatJSONObject = {"type": "json_object"}
        response = self._client.chat.completions.create(
            model="gpt-4o",
            response_format=response_format if require_json else omit,
            messages=messages,
            temperature=0.0,  # Deterministic reasoning
        )
        return response.choices[0].message.content or ""


class VisionInspector:
    """VLM vision node - actually GPT-4o Vision, not Ollama LLaVA despite the folder's README."""

    def __init__(self, api_key: str) -> None:
        self._client = OpenAI(api_key=api_key)

    def query(self, image: Image.Image, prompt: str) -> str:
        buf = io.BytesIO()
        image.save(buf, format="JPEG")
        b64_image = base64.b64encode(buf.getvalue()).decode("utf-8")

        messages: list[ChatCompletionMessageParam] = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"},
                    },
                ],
            }
        ]
        response = self._client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.0,
            max_tokens=500,
        )
        return response.choices[0].message.content or ""


class BoundingBoxDetector:
    """Hardcoded stub in the original code (not a real detector) - kept as-is, faithfully ported."""

    def detect(self, image: Image.Image) -> dict[str, Any]:
        return {
            "defects": [{"label": "component_roi", "box": [250, 250, 750, 750], "confidence": 0.98}]
        }


class ModelRegistry:
    def __init__(self, api_key: str) -> None:
        self.reasoning_llm = OpenAIReferee(api_key)
        self.llava = VisionInspector(api_key)
        self.pcb_detector = BoundingBoxDetector()
