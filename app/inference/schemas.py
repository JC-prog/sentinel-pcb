from pydantic import BaseModel, ConfigDict


class Classification(BaseModel):
    """One model's verdict on one image. Mirrors the inference service's ClassifyResponse
    (inference/src/inference_service/schemas.py)."""

    model_config = ConfigDict(protected_namespaces=())

    model: str
    username: str
    label: str
    index: int
    confidence: float
    scores: dict[str, float]
    request_id: str
