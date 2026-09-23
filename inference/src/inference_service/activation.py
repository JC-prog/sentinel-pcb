"""Loading a new version of an already-served model at runtime (POST /models/{name}/activate).

Everything the model needs besides the weights - labels, input size, normalization - comes from
the spec of the classifier it replaces, so a new version must be a drop-in with the same output
classes. The weights are fetched by an injectable ModelFetcher (Hugging Face in production, a
fake in tests), loaded, and smoke-tested *before* the registry is touched: a bad file never
displaces a working model.
"""

import hashlib
import os
import shutil
from pathlib import Path
from typing import Protocol

from huggingface_hub import hf_hub_download

from inference_service.classifier import OnnxClassifier
from inference_service.manifest import ModelSpec


class ModelFetcher(Protocol):
    def __call__(self, spec: ModelSpec, dest: Path) -> None:
        """Writes the ONNX file for `spec` (its repo_id @ revision / filename) to `dest`."""


def fetch_from_hub(spec: ModelSpec, dest: Path) -> None:
    """Downloads `spec`'s file from Hugging Face. HF_TOKEN in the environment covers private repos
    - the same variable scripts/fetch_models.py uses at image build time."""

    downloaded = hf_hub_download(
        repo_id=spec.repo_id,
        filename=spec.filename,
        revision=spec.revision,
        token=os.environ.get("HF_TOKEN") or None,
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(downloaded, dest)


def version_path(store_dir: Path, spec: ModelSpec) -> Path:
    """Where a runtime-activated version lives. Hashed so a repo id or revision with odd characters
    can't escape the store, and so it can never collide with the build-time `<name>.onnx`."""

    digest = hashlib.sha256(spec.version.encode()).hexdigest()[:12]
    return store_dir / f"{spec.name}@{digest}.onnx"


def load_version(
    current: OnnxClassifier,
    *,
    repo_id: str,
    revision: str,
    store_dir: Path,
    fetch: ModelFetcher,
) -> OnnxClassifier:
    """Builds a ready-to-serve classifier for `repo_id@revision` in place of `current`. Raises
    whatever `fetch` raises for a download problem, and ValueError (or an onnxruntime error) for a
    file that doesn't fit `current`'s spec."""

    spec = current.spec.model_copy(update={"repo_id": repo_id, "revision": revision})
    path = version_path(store_dir, spec)
    fetched_now = not path.is_file()
    if fetched_now:
        fetch(spec, path)

    try:
        classifier = OnnxClassifier.load(spec, path)
        classifier.smoke_test()
    except Exception:
        # Don't leave a file we just wrote that can't serve: the next attempt would find it on
        # disk, skip the download, and fail the same way.
        if fetched_now:
            path.unlink(missing_ok=True)
        raise
    return classifier
