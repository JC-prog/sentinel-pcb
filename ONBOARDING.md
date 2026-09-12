# Onboarding: dev environment setup

The exact steps to get a working local environment, in order. For the architecture, the
branching/PR workflow, and *why* things are set up this way, read
[`DEVELOPMENT.md`](DEVELOPMENT.md) afterward.

Total time: ~15 minutes, most of it Docker pulling images on the first run.

---

## 1. Install prerequisites

The setup script in step 3 checks for each of these and stops with an install hint if one is
missing, but install them first to save a round trip.

| Software | Version | Install |
|---|---|---|
| **uv** | latest | macOS: `brew install uv` &nbsp;·&nbsp; Linux: `curl -LsSf https://astral.sh/uv/install.sh \| sh` &nbsp;·&nbsp; Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| **Docker** | any recent, with Compose v2 | [Docker Desktop](https://docs.docker.com/get-docker/) (macOS/Windows) or Docker Engine + `docker compose` plugin (Linux). **Start it and leave it running.** |
| **Node.js + npm** | 22 (CI uses 22; ≥20 works) | [nodejs.org](https://nodejs.org/), `brew install node`, or `nvm install 22` |
| **Git** | any recent | your package manager |

uv fetches Python 3.12 itself, so you do **not** need a separate Python install.

Optional, only for the "Local LLM" option or Ollama embeddings:

- **[Ollama](https://ollama.com)**, then pull the models you intend to use:
  ```bash
  ollama pull llama3.2          # default local chat model
  ollama pull nomic-embed-text  # default local long-term-memory embedding model
  ```

---

## 2. Clone

```bash
git clone <repo-url>
cd sentinel-pcb
```

---

## 3. Run the setup script

```bash
bash infra/development/scripts/unix/setup-dev.sh                    # macOS/Linux
powershell -File infra\development\scripts\windows\setup-dev.ps1    # Windows
```

It is idempotent (safe to re-run after every `git pull`). It:

1. `uv sync` - backend Python deps
2. copies `.env.example` to `.env` if you don't have one, and generates `JWT_SECRET_KEY` in it
3. checks Docker is installed and running
4. starts the `db`, `qdrant`, and `litellm` containers:
   `docker compose -f infra/development/docker-compose.yml up -d --wait db qdrant litellm`
5. checks for Ollama (optional - just prints whether it found one)
6. `npm install` in `ui/`
7. runs the backend checks (`ruff`, `mypy`, `pytest`) and the UI unit tests

It finishes with `Setup complete.` and a reminder of the run commands. If it fails partway, see
[Troubleshooting](#8-troubleshooting) and re-run it.

---

## 4. Configure `.env`

The script created `.env` from `.env.example` with working defaults. The **only** value you may
need to set by hand:

| Key | Set it when | Value |
|---|---|---|
| `LITELLM_OPENAI_API_KEY` | your work touches the OpenAI provider, the Explainability & Review Agent, or OpenAI embeddings | your **own** OpenAI API key. Only your local `litellm` container reads it, and `.env` is git-ignored. Leave blank to work Ollama-only (OpenAI calls just 401). |

Leave these alone - they already point at the local containers:
`OPENAI_BASE_URL`, `OPENAI_API_KEY` (the proxy's dev master key), `DATABASE_URL`, `QDRANT_URL`,
`OLLAMA_BASE_URL`, `JWT_SECRET_KEY` (auto-generated in step 3).

Optional: to use a shared team LiteLLM proxy instead of your own container, point
`OPENAI_BASE_URL` at it and set `OPENAI_API_KEY` to the key from the team vault. See
[`infra/litellm/README.md`](infra/litellm/README.md).

---

## 5. Start the app

The Docker services (`db`, `qdrant`, `litellm`) are already up from step 3. In two terminals:

```bash
# terminal 1 - backend
uv run uvicorn app.main:app --reload        # http://localhost:8000

# terminal 2 - UI
cd ui && npm start                          # http://localhost:4200
```

Stop the Docker services when you're done for the day:

```bash
docker compose -f infra/development/docker-compose.yml down
```

---

## 6. Verify it works

1. Open http://localhost:4200, register an account (the **first** account ever created becomes
   Admin automatically), and send a chat message. If the reply streams back token by token, the
   whole stack is healthy: backend, Postgres, Qdrant, and the LiteLLM proxy.
2. Spot checks:
   ```bash
   curl http://localhost:8000/health              # backend -> {"status":"ok"} style response
   curl http://localhost:4000/health/liveliness   # LiteLLM proxy -> 200
   ```

---

## 7. Run only your section (optional)

`db` + `qdrant` + `litellm` cover most backend work. Add a Compose profile only if your area
needs it:

| Working on | Extra setup |
|---|---|
| Backend / chat / memory / agents | nothing - the default stack covers it |
| UI | `npm start` is the real loop; `docker compose ... --profile ui up -d` only for a container smoke test |
| Inference service (`inference/`) | fill in real Hugging Face repos in `inference/models.toml`, then `docker compose -f infra/development/docker-compose.yml --profile inference up -d`, or work in `inference/` with its own README |
| Explainability & Review Agent data | seed its embedded Qdrant with `scripts/explainability_agent/generate_telemetry.py` and `scripts/explainability_agent/populate_qdrant.py` - see [`DEVELOPMENT.md`](DEVELOPMENT.md) |

`docker compose ... --profile full up -d` runs everything.

---

## 8. Before you write code

```bash
uv run ruff check . && uv run mypy . && uv run pytest
cd ui && npx ng test --watch=false && npx ng build
```

Branch from `dev` (the trunk), open PRs against `dev`. `main` is production. See
[`DEVELOPMENT.md`](DEVELOPMENT.md) for the full workflow and the Definition of Done.

---

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| Script: `Docker daemon is not running` | Start Docker Desktop (macOS: `open -a Docker`) or `sudo systemctl start docker` (Linux), wait for it to be ready, re-run the script. |
| `port is already allocated` / `address already in use` | Something else holds one of the ports below. Stop it, or stop a stale stack: `docker compose -f infra/development/docker-compose.yml down`. |
| Backend won't start, `litellm` unhealthy | `docker compose -f infra/development/docker-compose.yml logs litellm`. The container is healthy without a key; it only needs `LITELLM_OPENAI_API_KEY` to actually reach OpenAI. |
| OpenAI chat returns a 401 / auth error | Set `LITELLM_OPENAI_API_KEY` in `.env` and `docker compose -f infra/development/docker-compose.yml up -d --force-recreate litellm`. |
| Long-term memory silently does nothing | Using Ollama embeddings? Run `ollama pull nomic-embed-text`. Or disable it with `MEMORY_ENABLED=False` in `.env`. |
| First Explainability Agent request is very slow | Expected - its CLIP image model (~350 MB, pulls `torch`) downloads on first use, not at install. |
| Anything half-installed | The script is idempotent - just run it again. |

---

## Ports used

| Port | Service | Started by |
|---|---|---|
| 8000 | backend (uvicorn) | you, terminal 1 |
| 4200 | UI (`npm start`) | you, terminal 2 |
| 4000 | LiteLLM proxy | Compose (`litellm`) |
| 5433 | Postgres (`5433` -> container `5432`) | Compose (`db`) |
| 6333 / 6334 | Qdrant REST / gRPC | Compose (`qdrant`) |
| 8001 | inference service | Compose (`inference`), profile only |

---

## Where to go next

- [`README.md`](README.md) - what the product is, the dependency table
- [`DEVELOPMENT.md`](DEVELOPMENT.md) - architecture, module boundaries, branching/PR workflow, known gotchas
- [`infra/litellm/README.md`](infra/litellm/README.md) - why the app never calls `api.openai.com` directly; per-developer key vs one production key
- [`infra/production/README.md`](infra/production/README.md) - what the AWS deployment looks like
