from typing import Any

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.shared.config.settings import settings
from app.shared.inference.schemas import Classification, JobSample, ModelInfo, RemoteJob


class InferenceNotConfigured(RuntimeError):
    """settings.inference_base_url is empty - there's no inference service to call."""


class InferenceError(RuntimeError):
    """The inference service was unreachable, returned an error, or sent back something
    unreadable. `status_code` is the HTTP status when the service answered with an error (so a
    caller can tell "refused: nothing to roll back" from "unreachable"), None otherwise."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class InferenceNotFound(InferenceError):
    """The service answered 404 - an unknown model, or (for jobs) one it no longer remembers,
    which it also does after a restart: jobs are held in its memory only."""


def _base_url() -> str:
    if not settings.inference_base_url:
        raise InferenceNotConfigured("settings.inference_base_url is not set")
    return settings.inference_base_url.rstrip("/")


async def classify(
    *, model: str, username: str, image: bytes, filename: str = "image"
) -> Classification:
    """POST one image to the inference service's /classify for the named model.

    `username` is passed straight through for request tracking on the inference side. Raises
    InferenceNotConfigured if no base URL is set, InferenceError for anything else that goes
    wrong.
    """

    url = f"{_base_url()}/classify"
    data = {"model": model, "username": username}
    files = {"file": (filename, image, "application/octet-stream")}

    async with httpx.AsyncClient(timeout=settings.inference_timeout_seconds) as client:
        try:
            response = await client.post(url, data=data, files=files)
        except httpx.HTTPError as exc:
            raise InferenceError(f"inference request failed: {exc}") from exc

    if response.status_code != 200:
        raise InferenceError(f"inference service returned {response.status_code}: {response.text}")

    try:
        return Classification.model_validate(response.json())
    except ValueError as exc:
        raise InferenceError(f"unreadable inference response: {exc}") from exc


async def _call(
    method: str, path: str, *, timeout: float, json: dict[str, Any] | None = None
) -> Any:
    """One JSON request to the inference service; returns the decoded body. Raises
    InferenceNotConfigured / InferenceNotFound (404) / InferenceError (anything else)."""

    url = f"{_base_url()}{path}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.request(method, url, json=json)
        except httpx.HTTPError as exc:
            raise InferenceError(f"inference request failed: {exc}") from exc

    if response.status_code == 404:
        raise InferenceNotFound(f"inference service returned 404: {response.text}", 404)
    if response.status_code >= 400:
        raise InferenceError(
            f"inference service returned {response.status_code}: {response.text}",
            response.status_code,
        )
    try:
        return response.json()
    except ValueError as exc:
        raise InferenceError(f"unreadable inference response: {exc}") from exc


def _parse[T: BaseModel](model: type[T], payload: Any) -> T:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise InferenceError(f"unreadable inference response: {exc}") from exc


async def list_models() -> list[ModelInfo]:
    """What the service is serving right now, with each model's version."""

    payload = await _call("GET", "/models", timeout=settings.inference_timeout_seconds)
    try:
        return TypeAdapter(list[ModelInfo]).validate_python(payload)
    except ValidationError as exc:
        raise InferenceError(f"unreadable inference response: {exc}") from exc


async def activate_model(name: str, *, repo_id: str, revision: str) -> ModelInfo:
    """Hot-swap `name` to `repo_id@revision`. Downloads weights, so it gets the longer admin
    timeout. On failure the service keeps serving the current version."""

    payload = await _call(
        "POST",
        f"/models/{name}/activate",
        timeout=settings.inference_admin_timeout_seconds,
        json={"repo_id": repo_id, "revision": revision},
    )
    return _parse(ModelInfo, payload)


async def rollback_model(name: str) -> ModelInfo:
    payload = await _call(
        "POST", f"/models/{name}/rollback", timeout=settings.inference_timeout_seconds
    )
    return _parse(ModelInfo, payload)


async def submit_job(
    *,
    model: str,
    client_ref: str,
    samples: list[JobSample],
    base_version: str | None = None,
    notes: str | None = None,
) -> RemoteJob:
    """Queue a retraining job. Idempotent on `client_ref`: resubmitting returns the existing job,
    so a retry after a timeout can't create a duplicate."""

    payload = await _call(
        "POST",
        "/jobs",
        timeout=settings.inference_timeout_seconds,
        json={
            "model": model,
            "client_ref": client_ref,
            "base_version": base_version,
            "samples": [s.model_dump() for s in samples],
            "notes": notes,
        },
    )
    return _parse(RemoteJob, payload)


async def get_job(job_id: str) -> RemoteJob:
    """Raises InferenceNotFound if the service doesn't know the job (never had it, or restarted)."""

    payload = await _call("GET", f"/jobs/{job_id}", timeout=settings.inference_timeout_seconds)
    return _parse(RemoteJob, payload)


async def cancel_job(job_id: str) -> RemoteJob:
    payload = await _call(
        "POST", f"/jobs/{job_id}/cancel", timeout=settings.inference_timeout_seconds
    )
    return _parse(RemoteJob, payload)
