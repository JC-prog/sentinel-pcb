"""The inference client's model-operations calls: versions, activate/rollback, retraining jobs.
(classify itself is covered in test_inference_client.py.)"""

import json
from collections.abc import Awaitable, Callable

import httpx
import pytest

from app.shared import inference
from app.shared.config.settings import settings
from app.shared.inference import InferenceError, InferenceNotConfigured, classify

_RealAsyncClient = httpx.AsyncClient


def _mock_async_client(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return _RealAsyncClient(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", factory)


@pytest.fixture(autouse=True)
def _base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "inference_base_url", "http://inference.test:8001/")


def _model_json(**overrides: object) -> dict[str, object]:
    return {
        "name": "pcb_body_defect",
        "version": "JcProg/body@v2",
        "previous_version": "JcProg/body@v1",
        "loaded_at": "2026-09-20T10:00:00Z",
        "labels": ["Golden", "Shift"],
        "input_size": [640, 640],
        **overrides,
    }


def _job_json(**overrides: object) -> dict[str, object]:
    return {
        "id": "remote-1",
        "client_ref": "job-1",
        "model": "pcb_body_defect",
        "base_version": "JcProg/body@v1",
        "status": "queued",
        "progress": 0.0,
        "sample_count": 1,
        "created_at": "2026-09-20T10:00:00Z",
        **overrides,
    }


def _classify_json(**overrides: object) -> dict[str, object]:
    return {
        "model": "m",
        "username": "u",
        "label": "a",
        "index": 0,
        "confidence": 0.9,
        "scores": {"a": 0.9},
        "request_id": "r",
        **overrides,
    }


async def test_classify_reports_the_model_version(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_async_client(
        monkeypatch,
        lambda request: httpx.Response(200, json=_classify_json(model_version="JcProg/body@v2")),
    )

    assert (await classify(model="m", username="u", image=b"x")).model_version == "JcProg/body@v2"


async def test_classify_tolerates_a_service_that_predates_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_async_client(monkeypatch, lambda request: httpx.Response(200, json=_classify_json()))

    assert (await classify(model="m", username="u", image=b"x")).model_version == ""


async def test_list_models_parses_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=[_model_json()])

    _mock_async_client(monkeypatch, handler)

    (model,) = await inference.list_models()

    assert seen["url"] == "http://inference.test:8001/models"
    assert (model.name, model.version, model.previous_version) == (
        "pcb_body_defect",
        "JcProg/body@v2",
        "JcProg/body@v1",
    )


async def test_activate_model_posts_the_target_version(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json=_model_json())

    _mock_async_client(monkeypatch, handler)

    info = await inference.activate_model("pcb_body_defect", repo_id="JcProg/body", revision="v2")

    assert seen == {
        "method": "POST",
        "url": "http://inference.test:8001/models/pcb_body_defect/activate",
        "json": {"repo_id": "JcProg/body", "revision": "v2"},
    }
    assert info.version == "JcProg/body@v2"


async def test_rollback_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_async_client(monkeypatch, lambda request: httpx.Response(200, json=_model_json()))

    assert (await inference.rollback_model("pcb_body_defect")).name == "pcb_body_defect"


async def test_submit_job_sends_the_client_ref_and_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["json"] = json.loads(request.content)
        return httpx.Response(202, json=_job_json())

    _mock_async_client(monkeypatch, handler)

    job = await inference.submit_job(
        model="pcb_body_defect",
        client_ref="job-1",
        samples=[inference.JobSample(case_id="c1", observed_label="a", expected_label="b")],
        base_version="JcProg/body@v1",
    )

    assert seen["json"] == {
        "model": "pcb_body_defect",
        "client_ref": "job-1",
        "base_version": "JcProg/body@v1",
        "samples": [{"case_id": "c1", "observed_label": "a", "expected_label": "b"}],
        "notes": None,
    }
    assert (job.id, job.status) == ("remote-1", "queued")


async def test_get_job_parses_a_finished_job(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_async_client(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json=_job_json(
                status="succeeded",
                progress=1.0,
                result={
                    "simulated": True,
                    "artifact": {"repo_id": "JcProg/body", "revision": "v1"},
                },
            ),
        ),
    )

    job = await inference.get_job("remote-1")

    assert job.result is not None
    assert (job.result.simulated, job.result.artifact.revision) == (True, "v1")


async def test_an_unknown_job_is_reported_as_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_async_client(monkeypatch, lambda request: httpx.Response(404, text="unknown job"))

    with pytest.raises(inference.InferenceNotFound):
        await inference.get_job("gone")
    # ...which is still an InferenceError for callers that don't care about the difference
    with pytest.raises(InferenceError):
        await inference.get_job("gone")


async def test_a_server_error_is_an_inference_error_not_a_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_async_client(monkeypatch, lambda request: httpx.Response(502, text="bad gateway"))

    with pytest.raises(InferenceError, match="502") as excinfo:
        await inference.cancel_job("remote-1")
    assert not isinstance(excinfo.value, inference.InferenceNotFound)


async def test_an_unreadable_payload_is_an_inference_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_async_client(monkeypatch, lambda request: httpx.Response(200, json={"nonsense": True}))

    with pytest.raises(InferenceError, match="unreadable"):
        await inference.get_job("remote-1")


@pytest.mark.parametrize(
    "call",
    [
        lambda: inference.list_models(),
        lambda: inference.get_job("x"),
        lambda: inference.cancel_job("x"),
        lambda: inference.rollback_model("x"),
        lambda: inference.activate_model("x", repo_id="r", revision="v"),
        lambda: inference.submit_job(model="x", client_ref="c", samples=[]),
    ],
)
async def test_every_call_requires_a_base_url(
    monkeypatch: pytest.MonkeyPatch, call: Callable[[], Awaitable[object]]
) -> None:
    monkeypatch.setattr(settings, "inference_base_url", "")
    with pytest.raises(InferenceNotConfigured):
        await call()
