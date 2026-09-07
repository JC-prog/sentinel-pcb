import io
import logging
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, UnidentifiedImageError

from inference_service.logging_config import configure_logging
from inference_service.registry import ModelRegistry
from inference_service.schemas import ClassifyResponse, HealthResponse, ModelInfo
from inference_service.settings import settings

configure_logging()

_access_logger = logging.getLogger("inference.access")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    app.state.registry = ModelRegistry.load(
        settings.manifest_path,
        settings.model_store_dir,
        require_all=settings.require_models_on_startup,
    )
    logger.info("model registry ready: %s", app.state.registry.names())
    yield


app = FastAPI(title="SentinelChat Inference", lifespan=lifespan)

if settings.cors_allow_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def get_registry(request: Request) -> ModelRegistry:
    return request.app.state.registry  # type: ignore[no-any-return]


RegistryDep = Annotated[ModelRegistry, Depends(get_registry)]


@app.middleware("http")
async def _log_requests(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """One structured line per request (method, path, status, duration, request id). The
    /classify handler logs its own line with the model, username, and predicted label - those
    aren't visible here without consuming the multipart body."""

    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    request.state.request_id = request_id

    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000

    _access_logger.info(
        "%s %s -> %s (%.1fms)",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": round(duration_ms, 1),
            "request_id": request_id,
        },
    )
    response.headers["x-request-id"] = request_id
    return response


@app.get("/health", response_model=HealthResponse)
def health(registry: RegistryDep) -> HealthResponse:
    return HealthResponse(status="ok", models=registry.names())


@app.get("/models", response_model=list[ModelInfo])
def list_models(registry: RegistryDep) -> list[ModelInfo]:
    infos: list[ModelInfo] = []
    for name in registry.names():
        classifier = registry.get(name)
        assert classifier is not None  # name came straight from registry.names()
        spec = classifier.spec
        infos.append(ModelInfo(name=name, labels=list(spec.labels), input_size=spec.input_size))
    return infos


@app.post("/classify", response_model=ClassifyResponse)
async def classify(
    request: Request,
    registry: RegistryDep,
    model: Annotated[str, Form(description="Which loaded model to run.")],
    username: Annotated[str, Form(description="Caller identity, for request tracking.")],
    file: Annotated[UploadFile, File(description="Image to classify.")],
) -> ClassifyResponse:
    classifier = registry.get(model)
    if classifier is None:
        raise HTTPException(
            status_code=404,
            detail=f"unknown model {model!r}; available: {registry.names()}",
        )

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty image upload")
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="image exceeds max_upload_bytes")

    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(status_code=400, detail="could not decode image") from None

    start = time.perf_counter()
    prediction = await run_in_threadpool(classifier.predict, image)
    duration_ms = (time.perf_counter() - start) * 1000

    request_id: str = request.state.request_id
    logger.info(
        "inference model=%s user=%s -> %s (%.3f, %.1fms)",
        model,
        username,
        prediction.label,
        prediction.confidence,
        duration_ms,
        extra={
            "event": "inference",
            "model": model,
            "username": username,
            "label": prediction.label,
            "confidence": round(prediction.confidence, 4),
            "duration_ms": round(duration_ms, 1),
            "request_id": request_id,
        },
    )

    return ClassifyResponse(
        model=model,
        username=username,
        label=prediction.label,
        index=prediction.index,
        confidence=prediction.confidence,
        scores=prediction.scores,
        request_id=request_id,
    )
