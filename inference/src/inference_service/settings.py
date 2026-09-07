from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # protected_namespaces=() - several fields here are naturally "model_*"; this service is all
    # about models, and none of them collide with a pydantic BaseModel attribute.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", protected_namespaces=())

    # Same env-driven logging story as the backend (app/config/logging_config.py): "console" for a
    # readable local terminal, "json" for one object per line so ECS Fargate's awslogs driver
    # ships something CloudWatch can query. Only the format differs, both go to stdout.
    log_format: Literal["console", "json"] = "console"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # The model manifest (see inference/models.toml) and the directory the ONNX files were
    # downloaded into at image build time (scripts/fetch_models.py, run from the Dockerfile).
    manifest_path: str = "models.toml"
    model_store_dir: str = "model_store"

    # Refuse to start if any manifest entry has no ONNX file on disk. True in production so a bad
    # image fails its deployment loudly instead of serving 404s; flip to False locally to bring
    # the API up with whatever subset of models you have.
    require_models_on_startup: bool = True

    # Hard ceiling on a decoded request image, before it reaches Pillow.
    max_upload_bytes: int = 10 * 1024 * 1024

    # Internal service - the browser never calls it, so there is normally no CORS to allow. Kept
    # configurable only for the odd case of hitting it directly from a dev tool.
    cors_allow_origins: list[str] = []


settings = Settings()
