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

    # How many prior messages (app/chat/history.py) get sent to the LLM as context for a reply.
    # A turn-count budget, not a token budget - simplest thing that works at this app's scale;
    # revisit only if long pasted content makes that insufficient in practice. Persisted history
    # itself is never trimmed, only what's replayed to the model.
    chat_history_max_turns: int = 20

    # Postgres (infra/development/docker-compose.yml's "db" service, infra/production/rds.tf) -
    # now used by app/db/ for authentication (app/auth/).
    database_url: str = ""

    # Vector store (infra/development/docker-compose.yml's "qdrant" service) for long-term,
    # cross-conversation memory (app/memory/) - the production vector-store choice is still open
    # (Qdrant here is a dev-matching default, not a prod decision), so app/core/memory.py's
    # MemoryStore interface is what the rest of the app depends on, not Qdrant directly.
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection_name: str = "chat_memories"

    # Embedding models for app/memory/embeddings.py - reuses whichever chat provider
    # (ollama/openai) the request already selected, so there's no separate user-facing choice.
    ollama_embedding_model: str = "nomic-embed-text"
    openai_embedding_model: str = "text-embedding-3-small"

    # How often (in assistant turns per conversation) app/memory/service.py extracts durable
    # facts worth remembering long-term. Deliberately not every turn - keeps the extra LLM call
    # infrequent and avoids near-duplicate facts from consecutive turns.
    memory_extraction_interval_turns: int = 4
    # How many long-term memories get pulled into a brand-new conversation's system prompt.
    memory_retrieval_top_k: int = 5
    # Kill switch - lets long-term memory be turned off without a deploy if extraction/retrieval
    # ever misbehaves (bad extractions, Qdrant unavailable, cost). Short-term/per-conversation
    # memory is unaffected either way.
    memory_enabled: bool = True

    # Auth (app/auth/). Empty by default - the app refuses to issue tokens without a real secret;
    # generate one with `python -c "import secrets; print(secrets.token_hex(32))"` and set it in
    # .env. Never commit a real value.
    jwt_secret_key: str = ""
    jwt_access_token_expires_minutes: int = 15
    jwt_refresh_token_expires_days: int = 7

    # False in dev (plain HTTP over localhost); set True in production's .env, where the UI and
    # API are served over HTTPS from one CloudFront domain (infra/production/static_site.tf).
    cookie_secure: bool = False

    # Explainability & Review Agent (app/agents/explainability_review_agent/) - POST
    # /api/agents/explainability-review. Kill switch, same pattern as memory_enabled.
    explainability_agent_enabled: bool = True
    # Server-side fallback OpenAI key for this agent only, used when a request doesn't supply
    # its own bring-your-own-key (ExplainabilityReviewRequest.openai_api_key). Unlike chat, this
    # agent doesn't have a UI to enter a key into yet, so a fallback is what makes it usable at
    # all before that exists. Empty by default - never commit a real value.
    explainability_agent_openai_api_key: str = ""
    # PCB images (inputs/, admin-provided), the IPC-A-610 reference JSON (ipc_standards/,
    # committed), and generated artifacts (outputs/, qdrant_db/) for the agent above - same
    # cwd-relative convention as chat_upload_dir.
    explainability_agent_data_dir: str = "data/images"

    # Lets the chat LLM itself decide to call a registered Tool (app/agents/registry.py) mid-
    # conversation - e.g. current_time, get_weather, explainability_review. Kill switch, same
    # pattern as memory_enabled; disabling sends no `tools` field at all, byte-identical to the
    # pre-tool-calling request shape. chat_tool_max_rounds bounds how many tool-call round trips
    # one message can trigger before the loop gives up and answers with what it has, in case a
    # model keeps calling tools without ever producing a final answer.
    chat_tool_calling_enabled: bool = True
    chat_tool_max_rounds: int = 4


settings = Settings()
