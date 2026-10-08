from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    # ui/ (Angular) dev server origin. Add the deployed CloudFront origin here once one exists.
    cors_allow_origins: list[str] = ["http://localhost:4200"]

    # app/shared/config/logging_config.py - "console" is a human-readable local-terminal format; "json" emits
    # one parseable object per line, for ECS Fargate's awslogs log driver to ship to CloudWatch.
    # Same stdout destination either way - only the format changes.
    log_format: Literal["console", "json"] = "console"
    # Gates all DEBUG-level logging - including verbose API request/response bodies (app/main.py's
    # request-logging middleware and _chat_sse) and LLM request/response payloads
    # (app/shared/config/llm.py). Separate from log_format since this controls how much gets logged,
    # not how it's rendered. Default INFO - flip to DEBUG locally when you need the detail; full
    # bodies are noisy and can contain fields worth not logging by default.
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Also persist logs to a rotating file under log_dir, in addition to stdout - lets past log
    # lines be inspected after the fact (e.g. by a tool reading the file), not just from a live
    # terminal. Off by default: it's a debugging convenience, not something every deployment
    # needs. Same cwd-relative convention as chat_upload_dir - only host-visible for bare
    # `uv run uvicorn`, not the containerized `app` service, since data/ isn't volume-mounted
    # there (same caveat as chat_upload_dir).
    log_to_file: bool = False
    log_dir: str = "data/logs"

    # Where uploaded chat images are stored on disk (app.chat.uploads.service). Swap for S3 before
    # running more than one instance - a later task, not needed for this scaffold.
    chat_upload_dir: str = "data/uploads"

    # Local LLM provider (app.chat.services.providers.ollama). Backend-only config, not exposed in the
    # Settings UI - swapping to an AWS-hosted Ollama service later is a .env change here, not a
    # UI change.
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"

    # OpenAI provider (app.chat.services.providers.openai). Single server-side key, used wherever OpenAI is
    # needed: chat (LlmProvider == "openai"), memory's embeddings/fact-extraction when the
    # conversation's provider is "openai", and the Explainability & Review Agent. No per-request
    # bring-your-own-key - whoever runs this server configures this once. Empty by default -
    # never commit a real value.
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Base URL for every OpenAI-compatible call - chat (app.chat.services.providers.openai), memory
    # embeddings (app/chat/memory/embeddings.py) and the Explainability & Review Agent's SDK client
    # (app/chat/agents/case_agent/models.py). Default is OpenAI direct. Point it at a
    # LiteLLM proxy to keep real provider keys out of this service: the shared team proxy or the
    # local `offline-llm` compose service in dev, and the Cloud Map address
    # (http://litellm.sentinelchat.internal:4000/v1) set by the backend task definition in prod
    # (infra/production/litellm.tf). When it points at a proxy, openai_api_key holds a LiteLLM
    # key rather than an sk- provider key. Must include the /v1 suffix.
    openai_base_url: str = "https://api.openai.com/v1"

    # How many prior messages (app/chat/services/history.py) get sent to the LLM as context for a reply.
    # A turn-count budget, not a token budget - simplest thing that works at this app's scale;
    # revisit only if long pasted content makes that insufficient in practice. Persisted history
    # itself is never trimmed, only what's replayed to the model.
    chat_history_max_turns: int = 20

    # Postgres (infra/development/docker-compose.yml's "db" service, infra/production/rds.tf) -
    # now used by app/shared/db/ for authentication (app/shared/auth/).
    database_url: str = ""

    # Vector store (infra/development/docker-compose.yml's "qdrant" service) for long-term,
    # cross-conversation memory (app/chat/memory/) - the production vector-store choice is still open
    # (Qdrant here is a dev-matching default, not a prod decision), so app/chat/core/memory.py's
    # MemoryStore interface is what the rest of the app depends on, not Qdrant directly.
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection_name: str = "chat_memories"

    # Embedding models for app/chat/memory/embeddings.py - reuses whichever chat provider
    # (ollama/openai) the request already selected, so there's no separate user-facing choice.
    ollama_embedding_model: str = "nomic-embed-text"
    openai_embedding_model: str = "text-embedding-3-small"

    # How often (in assistant turns per conversation) app/chat/memory/service.py extracts durable
    # facts worth remembering long-term. Deliberately not every turn - keeps the extra LLM call
    # infrequent and avoids near-duplicate facts from consecutive turns.
    memory_extraction_interval_turns: int = 4
    # How many long-term memories get pulled into a brand-new conversation's system prompt.
    memory_retrieval_top_k: int = 5
    # Kill switch - lets long-term memory be turned off without a deploy if extraction/retrieval
    # ever misbehaves (bad extractions, Qdrant unavailable, cost). Short-term/per-conversation
    # memory is unaffected either way.
    memory_enabled: bool = True

    # Auth (app/shared/auth/). Empty by default - the app refuses to issue tokens without a real secret;
    # generate one with `python -c "import secrets; print(secrets.token_hex(32))"` and set it in
    # .env. Never commit a real value.
    jwt_secret_key: str = ""
    jwt_access_token_expires_minutes: int = 15
    jwt_refresh_token_expires_days: int = 7

    # False in dev (plain HTTP over localhost); set True in production's .env, where the UI and
    # API are served over HTTPS from one CloudFront domain (infra/production/static_site.tf).
    cookie_secure: bool = False

    # Explainability Review Agent (app/workflow/src/agent2_explainability/, Work-tab-only) -
    # dropped in as-is from a teammate's standalone pcb_agentic_inspector prototype's "Agent 2"
    # (its own config/agent2_config.yaml and OPENAI_API_KEY env var read are kept unchanged, not
    # routed through settings). Never a chat tool. Gates the Work tab's automatic Agent 2 review of a
    # finished run's REVIEW_REQUIRED samples and its Review Console routes
    # (/api/orchestrator/reviews/*, app/workflow/services/reviews.py) - alongside
    # orchestrator_agent_enabled, since they act on a finished orchestrator run.
    explainability_review_agent_enabled: bool = True

    # Internal ONNX classification service (inference/, infra/production/inference.tf). Empty
    # means "not configured" - app.shared.inference.client raises rather than guessing a URL, and
    # nothing in the app calls it yet. In production this is the Cloud Map address
    # (http://inference.sentinelchat.internal:8001), set by the backend task definition.
    inference_base_url: str = ""
    # How long to wait on a single /classify call before giving up.
    inference_timeout_seconds: float = 10.0
    # Longer allowance for the calls that do real work on the service's side - activating a model
    # version downloads its weights from Hugging Face first.
    inference_admin_timeout_seconds: float = 120.0

    # Model operations: model versions, drift reports and the retraining queue (app/shared/modelops/,
    # the Models tab's app/modelops/, and the chat monitoring agent). Kill switch, same pattern as
    # the others - off hides the Models tab's API and removes the drift/retraining chat tools.
    modelops_enabled: bool = True

    # Lets the chat LLM itself decide to call a registered Tool (app/chat/agents/registry.py) mid-
    # conversation - e.g. inspect_image, get_drift_summary. Kill switch, same
    # pattern as memory_enabled; disabling sends no `tools` field at all, byte-identical to the
    # pre-tool-calling request shape. chat_tool_max_rounds bounds how many tool-call round trips
    # one message can trigger before the loop gives up and answers with what it has, in case a
    # model keeps calling tools without ever producing a final answer.
    chat_tool_calling_enabled: bool = True
    chat_tool_max_rounds: int = 4
    # How long one chat-model call may take (the supervisor's reply, memory's fact extraction).
    # Generous: a local model answering with tools on a laptop can be slow.
    chat_llm_timeout_seconds: float = 60.0

    # ADC inspection agent (app/chat/agents/inspection_agent/) - a QA/Admin-triggered
    # tool that runs a deterministic plan/policy loop (workflow_state.py/planner.py/policy_engine.py)
    # over an uploaded image: a verifier sub-agent, a classifier sub-agent (two-stage region/defect
    # classification through the inference/ microservice) and fixed verdict rules, optionally
    # validating an attached inspection XML - always persisting the result as a Case. The only tool
    # offered for submitting an image for inspection through chat. Kill switch, same pattern as the
    # others; only offered as a tool when an image is attached.
    adc_inspection_agent_enabled: bool = True
    # Whether the inspect agent runs its LLM-driven ReAct pass (app/chat/agents/inspection_agent/
    # react.py) before the deterministic pipeline. Off (or no openai_api_key) it runs unassisted;
    # the verdict and the persisted Case come from the same fixed rules either way.
    inspection_agent_llm_enabled: bool = True
    # Confidence gates mirroring orchestrator-agent/adc_agentic_project's OrchestratorAgent
    # (feature_threshold/defect_threshold, both 0.70 there too). Below adc_region_confidence_threshold,
    # the classifier stops after stage 1 and the verdict is REVIEW_REQUIRED instead of routing to a
    # defect model on a guess. Below adc_defect_confidence_threshold, the stage-2 result is still
    # returned but the verdict is REVIEW_REQUIRED rather than ACCEPTED.
    adc_region_confidence_threshold: float = 0.70
    adc_defect_confidence_threshold: float = 0.70

    # Relabel agent (app/chat/agents/relabel_agent/) - a QA/Admin says the model's defect label on a
    # Case is wrong; after an explicit confirmation the Case records the correction and a
    # RetrainingTicket is queued. Actual retraining happens on the separate inference server, never
    # here. Kill switch, same pattern as the others.
    relabel_agent_enabled: bool = True

    # Review agent (app/chat/agents/review_agent/) - a QA/Admin approves or overrides a case flagged
    # REVIEW_REQUIRED; after an explicit confirmation the case becomes APPROVED or OVERRIDDEN (the
    # override rate is a drift signal). Kill switch, same pattern as the others.
    review_agent_enabled: bool = True

    # Monitoring agent (app/chat/agents/monitoring_agent/) - drift summaries and reports, drafting a
    # retraining plan from open tickets (QA/Admin) and an Admin-only status overview. Retraining
    # itself is approved in the Models tab and runs on the separate inference server, never here.
    # Kill switch, same pattern as the others.
    monitoring_agent_enabled: bool = True

    # Sample agent (app/chat/agents/sample_agent/) - read-only lookups of what the Work tab
    # stored in Qdrant about a dataset sample (get_sample, list_review_cases). Kill switch, same
    # pattern as the others.
    sample_lookup_agent_enabled: bool = True

    # Chat guardrails (app/chat/guardrails/) - a NeMo Guardrails input rail run on the raw user
    # message before it reaches any chat LLM call, checking for
    # jailbreak/prompt-injection attempts and off-topic (non-PCB-inspection) requests in a single
    # combined check. Kill switch, same pattern as the others; disabling it reproduces today's
    # behavior exactly - the message goes straight to the chat model with no check. Uses the shared
    # openai_api_key/openai_base_url settings (the LiteLLM proxy), not a key of its own. Fails open
    # on any error (proxy unreachable, NeMo internal failure) - same convention as every other
    # kill-switchable agent in this repo.
    chat_guardrails_enabled: bool = True
    # A separate, independently tunable model from openai_model - the guardrails check is a small
    # classification task, so a cheaper/faster model is usually enough.
    chat_guardrails_model: str = "gpt-4o-mini"

    # LangFuse (infra/development/docker-compose.yml's "langfuse" profile) - self-hosted LLM
    # observability/tracing for the chat agents' OpenAI SDK calls (app/chat/agents/) - see
    # app/shared/config/langfuse.py. Deliberately not wired into
    # every chat model, via app/shared/config/llm.py. Kill switch, default False (unlike
    # the "on by default" feature switches above) since this is optional tooling, not something a
    # fresh dev environment needs working out of the box; a blank key is also treated as "not
    # configured" and traces nothing even when this is True, so a half-configured .env never raises.
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    # Bare local dev (uv run uvicorn ...) reaches langfuse-web's published host port directly;
    # the containerized `app` service overrides this to http://langfuse-web:3000 (that file's own
    # `environment:` block), same pattern as openai_base_url/qdrant_url above.
    # LANGFUSE_BASE_URL is the name Langfuse's own SDK documents; LANGFUSE_HOST is what the compose file sets.
    langfuse_host: str = Field(
        default="http://localhost:3000",
        validation_alias=AliasChoices("LANGFUSE_HOST", "LANGFUSE_BASE_URL"),
    )
    # The chat agents' LLM (app/shared/config/llm.py): a LangChain provider name and a model name.
    # Blank model -> openai_model. "openai" goes through openai_base_url (the LiteLLM gateway), so a
    # model change behind the gateway is just this setting; another provider ("anthropic", "ollama",
    # ...) needs its langchain-<name> package installed and its own credentials in the environment.
    agent_llm_provider: str = "openai"
    agent_llm_model: str = ""
    # How long one agent LLM call may take.
    agent_llm_timeout_seconds: float = 30.0

    # orchestrator_agent (app/workflow/src/agent1_orchestrator/) - a QA/Admin-only, chat-invisible
    # agent driven from the UI's "Work" tab, not chat. A close-to-verbatim drop-in of
    # pcb_agentic_inspector's Agent 1 - see app/workflow/INTEGRATION_NOTES.md: runs its plan/policy
    # loop over a whole uploaded dataset CSV (many rows at once), unlike inspection_agent's
    # inspect_image, which handles one chat-attached image. Never registered in
    # app/chat/agents/registry.py/access.py - structurally unreachable from the chat tool-calling
    # loop, not just settings-gated. Kill switch, same pattern as the others.
    orchestrator_agent_enabled: bool = True
    # Its own direct OpenAI client, isolated from openai_api_key/openai_base_url above (which may
    # point at a LiteLLM proxy key rather than a real sk-... key) - this one always calls
    # api.openai.com directly, so it needs a real OpenAI key here regardless of what
    # openai_base_url is set to elsewhere. Empty by default - never commit a real value.
    orchestrator_openai_api_key: str = ""
    orchestrator_openai_model: str = "gpt-5.6-luna"
    # Uploaded dataset CSVs, inspection XMLs, and image-root folders for Work-tab runs - same
    # cwd-relative convention/caveat as chat_upload_dir.
    orchestrator_data_dir: str = "data/orchestrator"


settings = Settings()
