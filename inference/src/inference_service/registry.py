"""Builds the name -> OnnxClassifier map the API dispatches on, from a Manifest plus the
directory scripts/fetch_models.py populated at build time."""

import logging
import threading
from pathlib import Path

from inference_service.classifier import OnnxClassifier
from inference_service.manifest import Manifest, load_manifest

logger = logging.getLogger(__name__)


class NoPreviousVersion(LookupError):
    """rollback() was asked for a model that has never been activated over."""


class ModelRegistry:
    def __init__(self, classifiers: dict[str, OnnxClassifier]) -> None:
        self._classifiers = dict(classifiers)
        # The classifier each name was serving before its most recent activate()/rollback() -
        # kept loaded so a rollback is an instant swap, not a re-download.
        self._previous: dict[str, OnnxClassifier] = {}
        # Guards the two dicts above across a swap. Readers (get/names) don't take it: a plain
        # dict lookup is atomic, and a request that already holds a classifier keeps using it for
        # its whole predict() even if a swap lands mid-request.
        self._lock = threading.Lock()

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

    def previous(self, name: str) -> OnnxClassifier | None:
        return self._previous.get(name)

    def activate(self, name: str, classifier: OnnxClassifier) -> None:
        """Makes `classifier` the one served for `name`, atomically. The classifier it replaces
        becomes the rollback target."""

        with self._lock:
            current = self._classifiers.get(name)
            if current is not None:
                self._previous[name] = current
            self._classifiers[name] = classifier
        logger.info("activated %r -> %s", name, classifier.spec.version)

    def rollback(self, name: str) -> OnnxClassifier:
        """Swaps `name` back to the classifier it served before its last activation. Rolling back
        twice returns to where you started - current and previous simply trade places."""

        with self._lock:
            previous = self._previous.get(name)
            current = self._classifiers.get(name)
            if previous is None or current is None:
                raise NoPreviousVersion(name)
            self._classifiers[name] = previous
            self._previous[name] = current
        logger.info("rolled back %r -> %s", name, previous.spec.version)
        return previous

    def __len__(self) -> int:
        return len(self._classifiers)
