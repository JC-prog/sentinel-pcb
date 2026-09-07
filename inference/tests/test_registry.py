from pathlib import Path

import pytest
from tests._tiny_onnx import build_tiny_onnx

from inference_service.manifest import Manifest, ModelSpec
from inference_service.registry import ModelRegistry


def _spec(name: str) -> ModelSpec:
    return ModelSpec(
        name=name, repo_id=f"JcProg/{name}", labels=["a", "b", "c"], input_size=(16, 16)
    )


def test_loads_models_that_have_files(tmp_path: Path) -> None:
    build_tiny_onnx(tmp_path / "one.onnx", num_classes=3)
    manifest = Manifest(models=[_spec("one")])

    registry = ModelRegistry.from_manifest(manifest, tmp_path)

    assert registry.names() == ["one"]
    assert registry.get("one") is not None
    assert registry.get("missing") is None
    assert len(registry) == 1


def test_require_all_raises_when_a_file_is_missing(tmp_path: Path) -> None:
    manifest = Manifest(models=[_spec("present"), _spec("absent")])
    build_tiny_onnx(tmp_path / "present.onnx", num_classes=3)

    with pytest.raises(FileNotFoundError, match="absent"):
        ModelRegistry.from_manifest(manifest, tmp_path, require_all=True)


def test_missing_file_is_skipped_when_not_required(tmp_path: Path) -> None:
    manifest = Manifest(models=[_spec("present"), _spec("absent")])
    build_tiny_onnx(tmp_path / "present.onnx", num_classes=3)

    registry = ModelRegistry.from_manifest(manifest, tmp_path, require_all=False)

    assert registry.names() == ["present"]
