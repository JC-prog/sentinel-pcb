from collections.abc import Callable

import httpx
import pytest

from app.config.settings import settings
from app.inference import Classification, InferenceError, InferenceNotConfigured, classify

_RealAsyncClient = httpx.AsyncClient


def _mock_async_client(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return _RealAsyncClient(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", factory)


async def test_classify_requires_a_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "inference_base_url", "")
    with pytest.raises(InferenceNotConfigured):
        await classify(model="m", username="u", image=b"x")


async def test_classify_posts_multipart_and_parses_the_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "inference_base_url", "http://inference.test:8001/")
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["content_type"] = request.headers["content-type"]
        seen["body"] = request.content
        return httpx.Response(
            200,
            json={
                "model": "classifier_1",
                "username": "jane-qa",
                "label": "shifted",
                "index": 2,
                "confidence": 0.87,
                "scores": {"ok": 0.13, "shifted": 0.87},
                "request_id": "req-1",
            },
        )

    _mock_async_client(monkeypatch, handler)

    result = await classify(
        model="classifier_1", username="jane-qa", image=b"\x89PNGfake", filename="board.png"
    )

    assert isinstance(result, Classification)
    assert result.label == "shifted"
    assert result.request_id == "req-1"
    assert seen["url"] == "http://inference.test:8001/classify"
    assert "multipart/form-data" in seen["content_type"]  # type: ignore[operator]
    assert b"jane-qa" in seen["body"]  # type: ignore[operator]
    assert b"board.png" in seen["body"]  # type: ignore[operator]


async def test_classify_raises_on_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "inference_base_url", "http://inference.test:8001")
    _mock_async_client(monkeypatch, lambda request: httpx.Response(404, text="unknown model 'x'"))
    with pytest.raises(InferenceError, match="404"):
        await classify(model="x", username="u", image=b"x")


async def test_classify_raises_on_transport_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "inference_base_url", "http://inference.test:8001")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    _mock_async_client(monkeypatch, handler)
    with pytest.raises(InferenceError, match="request failed"):
        await classify(model="x", username="u", image=b"x")
