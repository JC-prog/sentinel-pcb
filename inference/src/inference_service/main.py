import io
import logging
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, UnidentifiedImageError

from inference_service.activation import ModelFetcher, fetch_from_hub, load_version
from inference_service.jobs import JobManager
from inference_service.logging_config import configure_logging
from inference_service.registry import ModelRegistry, NoPreviousVersion
from inference_service.schemas import (
    ActivateRequest,
    ClassifyResponse,
    HealthResponse,
    JobRequest,
    JobResponse,
    JobState,
    ModelInfo,
)
from inference_service.settings import settings
from inference_service.trainer import get_trainer

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
    app.state.fetcher = fetch_from_hub
    app.state.jobs = JobManager(get_trainer(settings), history_limit=settings.job_history_limit)
    yield
    app.state.jobs.shutdown()


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


def get_jobs(request: Request) -> JobManager:
    return request.app.state.jobs  # type: ignore[no-any-return]


def get_fetcher(request: Request) -> ModelFetcher:
    return request.app.state.fetcher  # type: ignore[no-any-return]


RegistryDep = Annotated[ModelRegistry, Depends(get_registry)]
JobsDep = Annotated[JobManager, Depends(get_jobs)]
FetcherDep = Annotated[ModelFetcher, Depends(get_fetcher)]


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


def _model_info(registry: ModelRegistry, name: str) -> ModelInfo:
    classifier = registry.get(name)
    assert classifier is not None  # callers pass a name they just looked up
    previous = registry.previous(name)
    spec = classifier.spec
    return ModelInfo(
        name=name,
        version=spec.version,
        previous_version=previous.spec.version if previous is not None else None,
        loaded_at=classifier.loaded_at,
        labels=list(spec.labels),
        input_size=spec.input_size,
    )


@app.get("/models", response_model=list[ModelInfo])
def list_models(registry: RegistryDep) -> list[ModelInfo]:
    return [_model_info(registry, name) for name in registry.names()]


@app.post("/models/{name}/activate", response_model=ModelInfo)
async def activate_model(
    name: str, body: ActivateRequest, registry: RegistryDep, fetcher: FetcherDep
) -> ModelInfo:
    """Hot-swaps `name` to `repo_id@revision`. The new weights are fetched, loaded and smoke-tested
    before the swap, so a failure leaves the current version serving untouched. Idempotent:
    activating what is already live changes nothing."""

    current = registry.get(name)
    if current is None:
        raise HTTPException(
            status_code=404, detail=f"unknown model {name!r}; available: {registry.names()}"
        )
    if (current.spec.repo_id, current.spec.revision) == (body.repo_id, body.revision):
        return _model_info(registry, name)

    try:
        classifier = await run_in_threadpool(
            load_version,
            current,
            repo_id=body.repo_id,
            revision=body.revision,
            store_dir=Path(settings.model_store_dir),
            fetch=fetcher,
        )
    except (ValueError, OSError) as exc:
        # ValueError: loaded but doesn't fit the spec (output classes / input shape); OSError
        # covers a fetch that couldn't produce a file or one onnxruntime can't parse.
        raise HTTPException(status_code=422, detail=f"cannot activate: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"could not fetch model: {exc}") from exc

    registry.activate(name, classifier)
    return _model_info(registry, name)


@app.post("/models/{name}/rollback", response_model=ModelInfo)
def rollback_model(name: str, registry: RegistryDep) -> ModelInfo:
    if registry.get(name) is None:
        raise HTTPException(
            status_code=404, detail=f"unknown model {name!r}; available: {registry.names()}"
        )
    try:
        registry.rollback(name)
    except NoPreviousVersion:
        raise HTTPException(status_code=409, detail=f"{name!r} has no previous version") from None
    return _model_info(registry, name)


@app.post("/jobs", response_model=JobResponse, status_code=202)
def submit_job(body: JobRequest, registry: RegistryDep, jobs: JobsDep) -> JobResponse:
    classifier = registry.get(body.model)
    if classifier is None:
        raise HTTPException(
            status_code=404, detail=f"unknown model {body.model!r}; available: {registry.names()}"
        )
    return jobs.submit(
        model=body.model,
        client_ref=body.client_ref,
        base_version=body.base_version or classifier.spec.version,
        samples=body.samples,
        notes=body.notes,
    )


@app.get("/jobs", response_model=list[JobResponse])
def list_jobs(jobs: JobsDep) -> list[JobResponse]:
    return jobs.list_jobs()


@app.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str, jobs: JobsDep) -> JobResponse:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job {job_id!r}")
    return job


@app.post("/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel_job(job_id: str, jobs: JobsDep) -> JobResponse:
    job = jobs.cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job {job_id!r}")
    if job.status in (JobState.SUCCEEDED, JobState.FAILED):
        raise HTTPException(status_code=409, detail=f"job already {job.status.value}")
    return job


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
            "model_version": classifier.spec.version,
            "username": username,
            "label": prediction.label,
            "confidence": round(prediction.confidence, 4),
            "duration_ms": round(duration_ms, 1),
            "request_id": request_id,
        },
    )

    return ClassifyResponse(
        model=model,
        model_version=classifier.spec.version,
        username=username,
        label=prediction.label,
        index=prediction.index,
        confidence=prediction.confidence,
        scores=prediction.scores,
        request_id=request_id,
    )
