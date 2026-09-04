# src/models/model_registry.py
import io
import os
import json
import logging
from dotenv import load_dotenv
from PIL import Image
from openai import OpenAI
import ollama

# Load .env variables into os.environ automatically
load_dotenv()

logger = logging.getLogger(__name__)

class OpenAIReasoningModel:
    """Uses OpenAI GPT-4o for high-precision diagnostic reasoning and self-check."""
    def __init__(self, model_name: str = "gpt-4o"):
        self.model_name = model_name
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "OPENAI_API_KEY is not set. Please set it in your .env file or environment variables."
            )
        self.client = OpenAI(api_key=api_key)

    def query(self, prompt: str, require_json: bool = True) -> str:
        logger.info(f"Querying OpenAI ({self.model_name}) for reasoning...")
        kwargs = {"response_format": {"type": "json_object"}} if require_json else {}
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {
                    "role": "system",
                    "content": "You are a master PCB Failure Analysis Engineer. You analyze evidence strictly and output valid JSON."
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            **kwargs
        )
        return response.choices[0].message.content or "{}"


# ── Vision Model (Local LLaVA via Ollama) ───────────────────────────────────

class LocalLLaVAModel:
    """Vision-Language Model using local LLaVA via Ollama."""
    def __init__(self, model_name: str = "llava"):
        self.model_name = model_name

    def query(self, image: Image.Image, prompt: str, max_new_tokens: int = 300) -> str:
        buffered = io.BytesIO()
        image.convert("RGB").save(buffered, format="JPEG")
        image_bytes = buffered.getvalue()

        logger.info(f"Querying Ollama ({self.model_name}) for visual description...")
        response = ollama.generate(
            model=self.model_name,
            prompt=prompt,
            images=[image_bytes],
            options={"num_predict": max_new_tokens}
        )
        return response.get('response', '')


class MockDetector:
    def detect(self, image):
        return {"defects": [{"box": [10, 10, 50, 50], "class": "anomaly"}]}

class MockImageEncoder:
    def encode(self, image):
        return [0.0] * 512


# ── Registry ─────────────────────────────────────────────────────────────────

class ModelRegistry:
    def __init__(self):
        self.llava = LocalLLaVAModel(model_name="llava")
        self.reasoning_llm = OpenAIReasoningModel(model_name="gpt-4o")
        self.pcb_detector = MockDetector()
        self.image_encoder = MockImageEncoder()

registry = ModelRegistry()
