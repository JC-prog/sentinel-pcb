"""Model versions: what /models and /classify report, and hot-swapping a new version in
(POST /models/{name}/activate) or back out (POST /models/{name}/rollback)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from inference_service.classifier import OnnxClassifier
from inference_service.main import app, get_fetcher
from inference_service.manifest import ModelSpec
from inference_service.registry import ModelRegistry, NoPreviousVersion
from inference_service.settings import settings
from tests._tiny_onnx import build_tiny_onnx

OLD_VERSION = "JcProg/example@main"
NEW = {"repo_id": "JcProg/example", "revision": "v2"}


class FakeFetcher:
    """Stands in for Hugging Face: writes a tiny ONNX with `num_classes` outputs, records calls."""

    def __init__(self, num_classes: int = 3, error: Exception | None = None) -> None:
        self.num_classes = num_classes
        self.error = error
        self.specs: list[ModelSpec] = []

    def __call__(self, spec: ModelSpec, dest: Path) -> None:
        self.specs.append(spec)
        if self.error is not None:
            raise self.error
        dest.parent.mkdir(parents=True, exist_ok=True)
        build_tiny_onnx(dest, num_classes=self.num_classes)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "model_store_dir", str(tmp_path))
    return tmp_path


@pytest.fixture
def fetcher(client: TestClient, store: Path) -> Iterator[FakeFetcher]:
    fake = FakeFetcher()
    app.dependency_overrides[get_fetcher] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_fetcher, None)


def test_models_reports_version_and_load_time(client: TestClient) -> None:
    (model,) = client.get("/models").json()
    assert model["version"] == OLD_VERSION
    assert model["previous_version"] is None
    assert model["loaded_at"]


def test_classify_reports_the_version_that_answered(client: TestClient, png_bytes: bytes) -> None:
    response = client.post(
        "/classify",
        data={"model": "classifier_1", "username": "u"},
        files={"file": ("b.png", png_bytes, "image/png")},
    )
    assert response.json()["model_version"] == OLD_VERSION


def test_activate_swaps_to_the_new_version(
    client: TestClient, fetcher: FakeFetcher, png_bytes: bytes
) -> None:
    response = client.post("/models/classifier_1/activate", json=NEW)

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == "JcProg/example@v2"
    assert body["previous_version"] == OLD_VERSION
    (spec,) = fetcher.specs
    assert (spec.repo_id, spec.revision) == ("JcProg/example", "v2")

    classified = client.post(
        "/classify",
        data={"model": "classifier_1", "username": "u"},
        files={"file": ("b.png", png_bytes, "image/png")},
    )
    assert classified.json()["model_version"] == "JcProg/example@v2"


def test_activating_the_live_version_is_a_no_op(client: TestClient, fetcher: FakeFetcher) -> None:
    response = client.post(
        "/models/classifier_1/activate", json={"repo_id": "JcProg/example", "revision": "main"}
    )

    assert response.status_code == 200
    assert response.json()["version"] == OLD_VERSION
    assert response.json()["previous_version"] is None
    assert fetcher.specs == []


def test_activate_reuses_a_file_already_in_the_store(
    client: TestClient, fetcher: FakeFetcher
) -> None:
    client.post("/models/classifier_1/activate", json=NEW)
    client.post("/models/classifier_1/rollback")
    client.post("/models/classifier_1/activate", json=NEW)

    assert len(fetcher.specs) == 1  # second activation found it on disk


def test_activate_unknown_model_is_404(client: TestClient, fetcher: FakeFetcher) -> None:
    response = client.post("/models/nope/activate", json=NEW)
    assert response.status_code == 404
    assert fetcher.specs == []


def test_activate_incompatible_output_classes_is_422_and_keeps_the_old_version(
    client: TestClient, fetcher: FakeFetcher, store: Path
) -> None:
    fetcher.num_classes = 5  # the spec lists 3 labels

    response = client.post("/models/classifier_1/activate", json=NEW)

    assert response.status_code == 422
    (model,) = client.get("/models").json()
    assert model["version"] == OLD_VERSION
    assert model["previous_version"] is None
    assert list(store.glob("*.onnx")) == []  # the bad download was cleaned up


def test_activate_fetch_failure_is_502_and_keeps_the_old_version(
    client: TestClient, fetcher: FakeFetcher
) -> None:
    fetcher.error = RuntimeError("hub unreachable")

    response = client.post("/models/classifier_1/activate", json=NEW)

    assert response.status_code == 502
    assert "hub unreachable" in response.json()["detail"]
    (model,) = client.get("/models").json()
    assert model["version"] == OLD_VERSION


def test_rollback_restores_the_previous_version(client: TestClient, fetcher: FakeFetcher) -> None:
    client.post("/models/classifier_1/activate", json=NEW)

    response = client.post("/models/classifier_1/rollback")

    assert response.status_code == 200
    assert response.json()["version"] == OLD_VERSION
    assert response.json()["previous_version"] == "JcProg/example@v2"


def test_rollback_without_a_previous_version_is_409(client: TestClient) -> None:
    assert client.post("/models/classifier_1/rollback").status_code == 409


def test_rollback_unknown_model_is_404(client: TestClient) -> None:
    assert client.post("/models/nope/rollback").status_code == 404


def test_registry_rollback_twice_returns_to_the_newer_version(
    registry: ModelRegistry, tmp_path: Path
) -> None:
    original = registry.get("classifier_1")
    assert original is not None
    build_tiny_onnx(tmp_path / "v2.onnx", num_classes=3)
    newer = OnnxClassifier.load(
        original.spec.model_copy(update={"revision": "v2"}), tmp_path / "v2.onnx"
    )
    registry.activate("classifier_1", newer)

    registry.rollback("classifier_1")
    assert registry.get("classifier_1") is original
    registry.rollback("classifier_1")
    assert registry.get("classifier_1") is newer


def test_registry_rollback_with_nothing_to_roll_back_raises(registry: ModelRegistry) -> None:
    with pytest.raises(NoPreviousVersion):
        registry.rollback("classifier_1")
