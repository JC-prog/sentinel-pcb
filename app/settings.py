from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ui/ (Angular) dev server origin. Add the deployed CloudFront origin here once one exists.
    cors_allow_origins: list[str] = ["http://localhost:4200"]

    # Where uploaded chat images are stored on disk (app.uploads.service). Swap for S3 before
    # running more than one instance - a later task, not needed for this scaffold.
    chat_upload_dir: str = "data/uploads"

    # Artificial per-word pause in the placeholder chat stream, so streaming is visibly paced
    # instead of flashing through in under a millisecond. Mirrors the original app's
    # demo_phase_delay_seconds. Real LLM output paces itself - this only matters for the
    # placeholder.
    chat_stream_delay_seconds: float = 0.05


settings = Settings()
