# src/models/model_registry.py
import os
import io
import base64
from openai import OpenAI
from PIL import Image

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

class OpenAIReferee:
    """Reasoning engine using GPT-4o with guaranteed JSON format."""
    def query(self, prompt: str, require_json: bool = True) -> str:
        response = client.chat.completions.create(
            model="gpt-4o",
            response_format={"type": "json_object"} if require_json else None,
            messages=[
                {"role": "system", "content": "You are a senior SMT Quality Engineer and IPC-A-610 certified specialist."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.0  # Deterministic reasoning
        )
        return response.choices[0].message.content

class VisionInspector:
    """VLM Vision node using GPT-4o Vision."""
    def query(self, image: Image.Image, prompt: str) -> str:
        # Encode image to base64
        buf = io.BytesIO()
        image.save(buf, format="JPEG")
        b64_image = base64.b64encode(buf.getvalue()).decode("utf-8")

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}}
                    ]
                }
            ],
            temperature=0.0,   # <── FIX: Added temperature=0.0 to stop hallucinations & randomness!
            max_tokens=500     # <── FIX: Increased from 300 to prevent truncating coordinates
        )
        return response.choices[0].message.content

class BoundingBoxDetector:
    """Simple detector stub (or your YOLO model)."""
    def detect(self, image: Image.Image) -> dict:
        # Provide realistic normalized coordinates [ymin, xmin, ymax, xmax] if this is a stub
        return {"defects": [{"label": "component_roi", "box": [250, 250, 750, 750], "confidence": 0.98}]}

class ModelRegistry:
    def __init__(self):
        self.reasoning_llm = OpenAIReferee()
        self.llava = VisionInspector()
        self.pcb_detector = BoundingBoxDetector()

registry = ModelRegistry()
