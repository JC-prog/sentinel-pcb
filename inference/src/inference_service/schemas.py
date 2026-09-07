from pydantic import BaseModel, ConfigDict


class ModelInfo(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    name: str
    labels: list[str]
    input_size: tuple[int, int]


class HealthResponse(BaseModel):
    status: str
    models: list[str]


class ClassifyResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model: str
    username: str
    label: str
    index: int
    confidence: float
    scores: dict[str, float]
    request_id: str
