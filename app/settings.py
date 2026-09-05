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

    # Postgres (infra/development/docker-compose.yml's "db" service, infra/production/rds.tf) -
    # now used by app/db/ for authentication (app/auth/).
    database_url: str = ""

    # Vector store (infra/development/docker-compose.yml's "qdrant" service) for a future
    # RAG/semantic-search feature - still provisioned ahead of need, no code reads this yet.
    qdrant_url: str = "http://localhost:6333"

    # Auth (app/auth/). Empty by default - the app refuses to issue tokens without a real secret;
    # generate one with `python -c "import secrets; print(secrets.token_hex(32))"` and set it in
    # .env. Never commit a real value.
    jwt_secret_key: str = ""
    jwt_access_token_expires_minutes: int = 15
    jwt_refresh_token_expires_days: int = 7

    # False in dev (plain HTTP over localhost); set True in production's .env, where the UI and
    # API are served over HTTPS from one CloudFront domain (infra/production/static_site.tf).
    cookie_secure: bool = False


settings = Settings()
