"""Shared fixtures. A real (tiny) ONNX model is built on disk once per session so tests run
against an actual onnxruntime session; the FastAPI app's registry is swapped for one backed by
that model.
"""

import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from tests._tiny_onnx import build_tiny_onnx

from inference_service.classifier import OnnxClassifier
from inference_service.main import app, get_registry
from inference_service.manifest import Manifest, ModelSpec
from inference_service.registry import ModelRegistry

TEST_LABELS = ["alpha", "beta", "gamma"]


@pytest.fixture(scope="session")
def tiny_model_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("models") / "classifier_1.onnx"
    build_tiny_onnx(path, num_classes=len(TEST_LABELS))
    return path


@pytest.fixture
def spec() -> ModelSpec:
    return ModelSpec(
        name="classifier_1",
        repo_id="JcProg/example",
        labels=TEST_LABELS,
        input_size=(32, 32),
    )


@pytest.fixture
def manifest(spec: ModelSpec) -> Manifest:
    return Manifest(models=[spec])


@pytest.fixture
def registry(tiny_model_path: Path, spec: ModelSpec) -> ModelRegistry:
    return ModelRegistry({spec.name: OnnxClassifier.load(spec, tiny_model_path)})


@pytest.fixture
def client(registry: ModelRegistry) -> Iterator[TestClient]:
    # Plain TestClient(app), not `with TestClient(app)` - the context-manager form runs the
    # lifespan, which loads models.toml + model_store/ off disk. Tests inject the registry via
    # this override instead.
    app.dependency_overrides[get_registry] = lambda: registry
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (48, 40), (120, 30, 200)).save(buffer, format="PNG")
    return buffer.getvalue()
