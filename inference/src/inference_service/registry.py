"""Builds the name -> OnnxClassifier map the API dispatches on, from a Manifest plus the
directory scripts/fetch_models.py populated at build time."""

import logging
from pathlib import Path

from inference_service.classifier import OnnxClassifier
from inference_service.manifest import Manifest, load_manifest

logger = logging.getLogger(__name__)


class ModelRegistry:
    def __init__(self, classifiers: dict[str, OnnxClassifier]) -> None:
        self._classifiers = classifiers

    @classmethod
    def load(
        cls, manifest_path: str | Path, store_dir: str | Path, *, require_all: bool = True
    ) -> "ModelRegistry":
        manifest = load_manifest(manifest_path)
        return cls.from_manifest(manifest, store_dir, require_all=require_all)

    @classmethod
    def from_manifest(
        cls, manifest: Manifest, store_dir: str | Path, *, require_all: bool = True
    ) -> "ModelRegistry":
        store = Path(store_dir)
        classifiers: dict[str, OnnxClassifier] = {}
        missing: list[str] = []

        for spec in manifest.models:
            model_path = store / f"{spec.name}.onnx"
            if not model_path.is_file():
                missing.append(f"{spec.name} ({model_path})")
                continue
            classifiers[spec.name] = OnnxClassifier.load(spec, model_path)
            logger.info(
                "loaded model %r (%d classes) from %s",
                spec.name,
                len(spec.labels),
                model_path,
            )

        if missing and require_all:
            raise FileNotFoundError(
                "missing ONNX file(s) for: "
                + ", ".join(missing)
                + " - run scripts/fetch_models.py or set REQUIRE_MODELS_ON_STARTUP=false"
            )
        if missing:
            logger.warning("starting without model(s): %s", ", ".join(missing))

        return cls(classifiers)

    def names(self) -> list[str]:
        return sorted(self._classifiers)

    def get(self, name: str) -> OnnxClassifier | None:
        return self._classifiers.get(name)

    def __len__(self) -> int:
        return len(self._classifiers)
