"""Upload storage for the Work tab's orchestrator inputs: a dataset CSV, an inspection XML, and
an image-root folder uploaded as multiple files (the browser has no direct equivalent of the
source project's filedialog.askdirectory, so the Work page uses <input webkitdirectory multiple>
and sends each file's webkitRelativePath alongside it - see save_image_root_files).

Deliberately separate from app/chat/uploads/service.py (chat's upload storage) - keeps this agent's
storage and path-traversal handling self-contained rather than extending code the chat agents
depend on, consistent with the ported-code carve-out for app/workflow/src/agent1_orchestrator/.
"""

import uuid
from pathlib import Path

from fastapi import UploadFile

from app.shared.config.settings import settings


def _upload_root() -> Path:
    root = Path(settings.orchestrator_data_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


async def save_dataset_csv(file: UploadFile) -> str:
    upload_id = uuid.uuid4().hex
    directory = _upload_root() / upload_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "dataset.csv").write_bytes(await file.read())
    return upload_id


async def save_inspection_xml(file: UploadFile) -> str:
    upload_id = uuid.uuid4().hex
    directory = _upload_root() / upload_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "inspection.xml").write_bytes(await file.read())
    return upload_id


async def save_image_root_files(files: list[UploadFile], relative_paths: list[str]) -> str:
    """`relative_paths[i]` is `files[i]`'s browser-reported webkitRelativePath (e.g.
    "sample_data/35-.../Golden/image.jpg"). Unlike a real folder on disk, that path always starts
    with the picked folder's own name - stripped here so the stored tree's root matches what a
    desktop filedialog.askdirectory() selection would give DatasetPreparationService's image-root
    remapping (resolve_image_root_path() below returns the upload id's directory *as* image_root,
    i.e. already "inside" the picked folder). Rejects any relative path that would escape the
    upload directory (e.g. "../../etc/passwd")."""

    upload_id = uuid.uuid4().hex
    root = (_upload_root() / upload_id).resolve()
    root.mkdir(parents=True, exist_ok=True)

    for file, relative_path in zip(files, relative_paths, strict=True):
        parts = [part for part in relative_path.replace("\\", "/").split("/") if part]
        if ".." in parts:
            raise ValueError(f"path traversal attempt in upload: {relative_path!r}")
        if len(parts) > 1:
            parts = parts[1:]
        candidate = (root / Path(*parts)).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError(f"path traversal attempt in upload: {relative_path!r}")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(await file.read())

    return upload_id


def resolve_dataset_path(upload_id: str) -> Path | None:
    return _resolve_file(upload_id, "dataset.csv")


def resolve_xml_path(upload_id: str) -> Path | None:
    return _resolve_file(upload_id, "inspection.xml")


def resolve_image_root_path(upload_id: str) -> Path | None:
    upload_root = _upload_root().resolve()
    candidate = (upload_root / upload_id).resolve()
    if not candidate.is_relative_to(upload_root) or not candidate.is_dir():
        return None
    return candidate


def _resolve_file(upload_id: str, filename: str) -> Path | None:
    upload_root = _upload_root().resolve()
    candidate = (upload_root / upload_id / filename).resolve()
    if not candidate.is_relative_to(upload_root) or not candidate.is_file():
        return None
    return candidate
