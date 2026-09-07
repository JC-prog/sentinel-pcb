import httpx

from app.config.settings import settings
from app.inference.schemas import Classification


class InferenceNotConfigured(RuntimeError):
    """settings.inference_base_url is empty - there's no inference service to call."""


class InferenceError(RuntimeError):
    """The inference service was unreachable, returned an error, or sent back something
    unreadable."""


async def classify(
    *, model: str, username: str, image: bytes, filename: str = "image"
) -> Classification:
    """POST one image to the inference service's /classify for the named model.

    `username` is passed straight through for request tracking on the inference side. Raises
    InferenceNotConfigured if no base URL is set, InferenceError for anything else that goes
    wrong.
    """

    if not settings.inference_base_url:
        raise InferenceNotConfigured("settings.inference_base_url is not set")

    url = f"{settings.inference_base_url.rstrip('/')}/classify"
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
