from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    database_url: str = ""
    redis_url: str = ""
    langsmith_api_key: str = ""
    langsmith_project: str = "sentinel-pcb"
    ollama_base_url: str = "http://localhost:11434"


settings = Settings()
