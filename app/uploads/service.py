import uuid
from pathlib import Path

from fastapi import UploadFile
from pydantic import BaseModel

from app.config.settings import settings


class UploadRecord(BaseModel):
    id: str
    url: str


async def save_upload(file: UploadFile) -> UploadRecord:
    upload_dir = Path(settings.chat_upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(file.filename or "").suffix
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    (upload_dir / stored_name).write_bytes(await file.read())

    return UploadRecord(id=stored_name, url=f"/api/uploads/{stored_name}")


def resolve_upload_path(filename: str) -> Path | None:
    """Resolves a stored filename to its path on disk, or None if it doesn't exist or the name
    would escape chat_upload_dir (e.g. a path-traversal attempt like "../../etc/passwd")."""

    upload_dir = Path(settings.chat_upload_dir).resolve()
    candidate = (upload_dir / filename).resolve()
    if not candidate.is_relative_to(upload_dir) or not candidate.is_file():
        return None
    return candidate
