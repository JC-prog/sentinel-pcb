"""Loads and validates inference/models.toml into typed ModelSpec objects.

The manifest is the single source of truth for both scripts/fetch_models.py (what to download at
build time) and the running service (how to preprocess and label each model's output).
"""

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PLACEHOLDER = "REPLACE_ME"


class ModelSpec(BaseModel):
    model_config = ConfigDict(protected_namespaces=(), frozen=True)

    # The value callers pass in the request's "model" field, and the on-disk file stem
    # (<model_store_dir>/<name>.onnx).
    name: str = Field(min_length=1)

    # Hugging Face source, resolved at image build time by scripts/fetch_models.py.
    repo_id: str = Field(min_length=1)
    revision: str = "main"
    filename: str = "model.onnx"

    # Output index -> label, in the model's own class order.
    labels: list[str] = Field(min_length=1)

    # "" means "use the model's sole input". Set it when a model has more than one.
    input_name: str = ""
    # [height, width] the model expects.
    input_size: tuple[int, int] = (224, 224)
    layout: Literal["NCHW", "NHWC"] = "NCHW"
    color: Literal["RGB", "BGR"] = "RGB"
    # Applied after pixels are scaled to 0..1. Defaults are the common ImageNet values.
    mean: tuple[float, float, float] = (0.485, 0.456, 0.406)
    std: tuple[float, float, float] = (0.229, 0.224, 0.225)
    # False if the model already ends in a softmax/sigmoid and its outputs are probabilities.
    apply_softmax: bool = True

    @property
    def is_placeholder(self) -> bool:
        return PLACEHOLDER in self.repo_id


class Manifest(BaseModel):
    models: list[ModelSpec] = Field(min_length=1)

    def names(self) -> list[str]:
        return [m.name for m in self.models]


def load_manifest(path: str | Path) -> Manifest:
    raw = tomllib.loads(Path(path).read_text())
    manifest = Manifest.model_validate(raw)
    duplicates = _duplicates(manifest.names())
    if duplicates:
        raise ValueError(f"duplicate model name(s) in {path}: {', '.join(sorted(duplicates))}")
    return manifest


def _duplicates(values: list[str]) -> set[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for value in values:
        (dupes if value in seen else seen).add(value)
    return dupes
