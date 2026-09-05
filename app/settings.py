from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ui/ (Angular) dev server origin. Add the deployed CloudFront origin here once one exists.
    cors_allow_origins: list[str] = ["http://localhost:4200"]

    # Where uploaded chat images are stored on disk (app.uploads.service). Swap for S3 before
    # running more than one instance - a later task, not needed for this scaffold.
    chat_upload_dir: str = "data/uploads"

    # Local LLM provider (app.chat.providers.ollama). Backend-only config, not exposed in the
    # Settings UI - swapping to an AWS-hosted Ollama service later is a .env change here, not a
    # UI change.
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"

    # OpenAI provider (app.chat.providers.openai). No server-side API key setting - it's
    # bring-your-own-key, entered in the Settings UI and sent with each request. Only the model
    # name is configured here.
    openai_model: str = "gpt-4o-mini"


settings = Settings()
