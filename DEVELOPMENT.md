# Development Guide

How to actually build in this repo: environment setup, branching/PR workflow, architecture at a
glance, and Definition of Done for a PR. `README.md` is the quick start; this is the longer
version for someone about to write code.

## 1. One-time setup

```bash
bash infra/development/scripts/unix/setup-dev.sh        # macOS/Linux
powershell -File infra\development\scripts\windows\setup-dev.ps1   # Windows
```

See `README.md` for what this does and how to run the app afterward. Re-run it any time after
pulling changes; it's idempotent.

Sanity check before writing anything:

```bash
uv run ruff check . && uv run mypy . && uv run pytest
cd ui && npx ng test --watch=false && npx ng build
```

## 2. Branching and PRs

- **`dev` is the trunk.** Create feature branches from `dev`, and open PRs against `dev` (not
  `main`, and not another feature branch).
- **`main` is production.** It only moves forward via a deliberate `dev` -> `main` PR when the
  accumulated work on `dev` is actually ready to ship. Don't propose merging a feature branch
  into `main` directly.
- Branch names: `feat/<short-description>`, `fix/<short-description>`, `chore/<short-description>`,
  `docs/<short-description>`, `infra/<short-description>` - matches the commit prefixes below.
- Commit messages and PR titles follow [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `chore:`, `ci:`, `docs:`, `infra:`) - `CHANGELOG.md` and any future
  auto-generated release notes depend on this being consistent.
- Opening a PR against `dev` picks up `.github/PULL_REQUEST_TEMPLATE.md` automatically. Fill in
  the test plan for real - don't leave checkboxes unchecked as a TODO.
- CI (`.github/workflows/ci.yml`) runs backend (ruff, mypy, pytest) and frontend (ng test, ng
  build) checks on every PR. Both must pass before merging.

## 3. Architecture at a glance

- **Backend** (`app/`): FastAPI. `POST /api/chat/stream` streams the assistant's reply over
  Server-Sent Events (`event: delta` / `error` / `done`); `POST`/`GET /api/uploads` handles chat
  image uploads. Stateless by design - no per-request state is shared across instances, which
  matters once this runs as more than one ECS task.
- **LLM providers** (`app/chat/providers/`): `OllamaChatService` and `OpenAiChatService`, both
  behind the `get_chat_service()` factory in `app/chat/service.py` - that factory is the swap
  point for adding another provider later. OpenAI is bring-your-own-key (the UI's Settings panel
  sends the key with each request; nothing is stored server-side).
- **Frontend** (`ui/`): Angular, standalone components, signals for state (no NgRx/service
  subjects). `ChatResponder` (`ui/src/app/chat-responder.ts`) is the frontend's equivalent swap
  point - `HttpChatResponder` talks to the real backend, `MockChatResponder` is a fallback/test
  double. Both stream multiple chunks rather than returning one value; `ChatService` accumulates
  them into the assistant message as they arrive.
- **Memory**: two tiers, both server-side and scoped per account. Short-term (per-conversation)
  memory is `Conversation`/`Message` rows in Postgres (`app/db/models/chat.py`), assembled back
  into context for each reply by `app/chat/history.py`. Long-term (cross-conversation) memory
  lives in Qdrant behind the `MemoryStore` interface (`app/core/memory.py`) - `app/memory/`
  extracts durable facts from a conversation and retrieves them for a new one; `MEMORY_ENABLED`
  is a kill switch, and `app/memory/qdrant_store.py` is the only file that knows it's Qdrant, so
  swapping the store later doesn't touch the rest of the app.
- **Migrations**: `alembic/` - `uv run alembic revision --autogenerate -m "..."` after changing a
  model, then `uv run alembic upgrade head`. `app/db/session.py`'s `create_all` still runs at
  startup for local/test convenience; a real deploy's schema is Alembic's migration history.
- **Infra** (`infra/`): `infra/Dockerfile` is the one backend image definition, used by both
  `infra/development/docker-compose.yml` (local dev) and the AWS deploy in `infra/production/`
  (Terraform - see its own README).

## 4. Known gotchas

- **Ollama from inside a container**: the backend's default `OLLAMA_BASE_URL` assumes a
  host-native Ollama install. The dev Docker Compose stack points the containerized backend at
  `http://host.docker.internal:11434` instead - see the comment in
  `infra/development/docker-compose.yml` if this needs to change.
- **`uv run` vs the venv binary in Docker**: `infra/Dockerfile`'s `CMD` invokes `.venv/bin/uvicorn`
  directly, not `uv run uvicorn`. The latter re-syncs the environment (including dev-only
  packages) against the lockfile on every container start, which needs network access and adds
  startup latency - a real bug caught while building the infra, not a style preference.
- **No mixed content in production without a custom domain**: `infra/production/static_site.tf`
  routes both the UI and `/api/*` through one CloudFront distribution specifically so the ALB
  (which has no cert) is never called directly from the browser. Don't add a second, separate
  CloudFront distribution or point the UI at the ALB's own domain - see that file's comments.
- **Long-term memory needs an embedding model actually pulled**: `OLLAMA_EMBEDDING_MODEL`
  (default `nomic-embed-text`) isn't auto-pulled - run `ollama pull nomic-embed-text` (or set the
  env var to a model you've already pulled, e.g. `mxbai-embed-large`) or extraction/retrieval
  will silently no-op (logged, not raised - see `app/memory/service.py`).

## 5. Definition of Done (per PR)

1. `uv run ruff check .`, `uv run mypy .`, `uv run pytest` all pass (backend changes).
2. `npx ng test --watch=false`, `npx ng build` both pass (frontend changes).
3. New behavior has a test - unit tests for logic, at minimum a manual verification note in the
   PR's test plan for anything that needs a real browser or a real external service (Ollama,
   OpenAI) to observe.
4. `CHANGELOG.md`'s `[Unreleased]` section is updated for anything a user of the app would
   notice. Version bumps (`pyproject.toml`, `ui/package.json`) follow semver independently per
   package - a new user-facing capability is a MINOR bump, infra/tooling-only changes usually
   don't need one.
