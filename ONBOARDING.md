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
4. starts the `db` and `qdrant` containers:
   `docker compose -f infra/development/docker-compose.yml --env-file .env up -d --wait db qdrant`
5. `npm install` in `ui/`
6. runs the backend checks (`ruff`, `mypy`, `pytest`) and the UI unit tests

It finishes with `Setup complete.` and a reminder of the run commands. If it fails partway, see
[Troubleshooting](#8-troubleshooting) and re-run it.

---

## 4. Configure `.env`

The script created `.env` from `.env.example` with working defaults. The **only** value you may
need to set by hand:

| Key | Set it when | Value |
|---|---|---|
| `OPENAI_API_KEY` | always - chat, the agents, memory embeddings and Agent 2 all use it | your **own** OpenAI API key (`sk-...`). `.env` is git-ignored. Chat and the agents refuse to run without it. |

Leave these alone - they already point at the local containers:
`DATABASE_URL`, `QDRANT_URL`, `JWT_SECRET_KEY` (auto-generated in step 3).
Do not set `OPENAI_BASE_URL` unless you are deliberately pointing at another OpenAI-compatible
endpoint; unset, calls go to OpenAI.

**Coming from the LiteLLM-era setup?** Delete the `OPENAI_BASE_URL=http://localhost:4000/v1` line,
put your real key in `OPENAI_API_KEY` (it used to hold `sk-litellm-dev`), and remove
`LITELLM_OPENAI_API_KEY` / `LITELLM_MASTER_KEY`. Then drop the old container:
`docker compose -f infra/development/docker-compose.yml --env-file .env down --remove-orphans`.

---

## 5. Start the app

One command starts everything - Docker infra (already up from step 3, idempotent), the backend,
the inference service, and the UI, each with live reload:

```bash
bash infra/development/scripts/unix/start-dev.sh                    # macOS/Linux - one terminal
powershell -File infra\development\scripts\windows\start-dev.ps1    # Windows - opens 3 windows
```

It also points the backend at the inference service for you (`INFERENCE_BASE_URL` in `.env`), so
`inspect_image`/the Work tab/the Models tab all work without extra setup - the inference service
itself boots without real models (`inference/models.toml` unfilled is fine; classification calls
just won't return real predictions until you fill it in). Ctrl+C stops the app processes; the
Docker services keep running.

Prefer to run things by hand (fewer moving parts to reason about, or you only need one piece)?

```bash
# terminal 1 - backend
uv run uvicorn app.main:app --reload        # http://localhost:8000

# terminal 2 - UI
cd ui && npm start                          # http://localhost:4200

# terminal 3 - inference (optional - only if your work touches classification)
cd inference && uv run uvicorn inference_service.main:app --app-dir src --port 8001 --reload
```

Stop the Docker services when you're done for the day:

```bash
docker compose -f infra/development/docker-compose.yml --env-file .env down
```

---

## 6. Verify it works

1. Open http://localhost:4200, register an account (the **first** account ever created becomes
   Admin automatically), and send a chat message. If the reply streams back token by token, the
   whole stack is healthy: backend, Postgres and Qdrant (and OpenAI, if you set a key).
2. Spot checks:
   ```bash
   curl http://localhost:8000/health              # backend -> {"status":"ok"} style response
   ```
3. Qdrant ships its own web dashboard, no extra setup: open http://localhost:6333/dashboard to
   browse collections/points directly.

---

## 7. Run only your section (optional)

`db` + `qdrant` cover most backend work. Add a Compose profile only if your area
needs it:

| Working on | Extra setup |
|---|---|
| Backend / chat / memory / agents | nothing - the default stack covers it |
| UI | `npm start` is the real loop; `docker compose ... --profile ui up -d` only for a container smoke test |
| Inference service (`inference/`) | `start-dev` (step 5) already runs it natively; fill in real Hugging Face repos in `inference/models.toml` for real predictions, or work in `inference/` with its own README. `docker compose -f infra/development/docker-compose.yml --env-file .env --profile inference up -d` for a container smoke test instead |
| LangFuse tracing for the chat agents | `docker compose -f infra/development/docker-compose.yml --env-file .env --profile langfuse up -d --wait`, then set `LANGFUSE_ENABLED=True` in `.env` and restart the backend. Auto-provisions an org/project/API key on first boot - log in at http://localhost:3000 with `dev@sentinelchat.local` / `sentinelchat-dev-password`. |

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
| `port is already allocated` / `address already in use` | Something else holds one of the ports below. Stop it, or stop a stale stack: `docker compose -f infra/development/docker-compose.yml --env-file .env down`. |
| OpenAI chat returns a 401 / auth error | `OPENAI_API_KEY` in `.env` is blank or not a valid `sk-...` key. Fix it and restart the backend. |
| OpenAI calls fail with `Connection refused` on `localhost:4000`, or `No connected db` | Your `.env` still has the LiteLLM-era `OPENAI_BASE_URL`. Delete that line (see "Coming from the LiteLLM-era setup" above) and restart the backend. |
| Ran a `docker compose` command by hand and `${...}` values came out blank | You likely dropped `--env-file .env` from the command. Without it, Compose can't find the repo-root `.env` for this file's `${...}` substitutions and silently treats them as blank - always include `--env-file .env` (run from the repo root) with any `docker compose -f infra/development/docker-compose.yml ...` command. |
| Long-term memory silently does nothing | Check `OPENAI_API_KEY` is set and `MEMORY_ENABLED=True` in `.env`. |
| Anything half-installed | The script is idempotent - just run it again. |

---

## Ports used

| Port | Service | Started by |
|---|---|---|
| 8000 | backend (uvicorn) | you, terminal 1 |
| 4200 | UI (`npm start`) | you, terminal 2 |
| 5433 | Postgres (`5433` -> container `5432`) | Compose (`db`) |
| 6333 / 6334 | Qdrant REST / gRPC (dashboard at `6333/dashboard`) | Compose (`qdrant`) |
| 8001 | inference service | you (`start-dev`, terminal 3, or Compose `inference` profile) |
| 3000 | LangFuse tracing UI | Compose (`langfuse-web`), profile only |

---

## Where to go next

- [`README.md`](README.md) - what the product is, the dependency table
- [`DEVELOPMENT.md`](DEVELOPMENT.md) - architecture, module boundaries, branching/PR workflow, known gotchas
- [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) - what to type and click to trigger each behaviour
- [`docs/DEMO.md`](docs/DEMO.md) - a rehearsed step-by-step demo script (with the sample data to use)
- [`docs/REVIEW_DRIFT_RETRAINING.md`](docs/REVIEW_DRIFT_RETRAINING.md) - Qdrant schema, the review -> drift -> retraining chain, API and tool reference
- [`infra/production/README.md`](infra/production/README.md) - what the AWS deployment looks like
