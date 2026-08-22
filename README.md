# SentinelPCB

Agentic Multi-Modal Automatic Defect Classification (ADC) platform for PCB Automated Optical
Inspection (AOI). A small set of specialized agents wraps a trusted PCB feature-detection model,
coordinating the review workflow: assemble a sample, validate it, run inference, and decide
auto-accept vs. escalate to a human reviewer.

Built for the NUS-ISS SWE5008 "Architecting AI Systems" Practice Module, Team 10 (Chong Loh Loy
Fatt, Kenny Lau Jia Xu, Wang Ze Yu).

> Project planning docs (proposal, PRD, scenarios) live in the gitignored `planning/` folder.
> **This repo's `docs/`** holds documentation tied to the codebase itself — **`docs/USER-GUIDE.md`**
> for how to run and use the app, `docs/AGENT-DESIGN.md`, the living as-built design doc that
> tracks what's actually implemented vs. still planned, and `docs/MODEL-TRAINING.md` (replacing
> the placeholder defect classifier). The trusted model's source repo is the sibling
> `pcb-inspect-ai` (see `models/README.md`).

## Architecture, honestly

One image submitted = one persisted `Workflow`, moving through
`RECEIVED → PREPARING → QUALITY_CHECK → INFERENCE → POLICY_DECISION →` either
`ACCEPTED → LEARNING_QUEUE` or `EXPLANATION → HUMAN_REVIEW` — see `app/agents/orchestrator/runner.py`.
Every component in that path is real and DB-backed today: Dataset Preparation, Dataset Quality,
and Multi-Modal Inference (a real ONNX feature detector plus a deterministic placeholder defect
classifier) are deterministic subagents; the Orchestrator's accept/escalate decision and
Explainability & Review's templated report (backed by Case Context, a two-source RAG tool) are
coded as agents but run **no live LLM** — deliberately, so Continuous Monitoring & Drift's
agent/policy-health metrics exist before any genuinely agentic reasoning does. Model Lifecycle is
still an empty stub, and `HUMAN_REVIEW` is still a dead end (no reviewer-action endpoint, so
escalated cases can't yet feed back into the learning queue).

Full status per component, including what's deliberately deferred: `docs/AGENT-DESIGN.md`.

## Tech stack

**Backend:** Python 3.12 · [uv](https://docs.astral.sh/uv/) · FastAPI · Pydantic ·
ONNX Runtime (trusted feature-detection model) · Pillow/NumPy · SQLAlchemy (async) + asyncpg ·
Postgres/pgvector (local via `docker compose up db`, or Supabase-hosted) · sentence-transformers
(local embeddings for Case Context RAG — pulls in torch) · pytest · ruff + mypy (strict).

**Frontend:** Angular (Vite/esbuild-based `@angular/build:application` builder) · Tailwind CSS v4 ·
Vitest.

**Planned, not yet used by any agent:** LangGraph + langchain-anthropic (LLM reasoning),
Langsmith (observability), Guardrails-AI.

## Repo layout

```
app/
├── main.py                        # FastAPI entrypoint - /workflows, /observability/metrics
├── settings.py                    # pydantic-settings, reads .env
├── db/                            # SQLAlchemy models (Workflow, AuditEvent, Explanation,
│                                   #   RemediationDoc) + async session/engine (real)
├── services/embeddings.py         # EmbeddingProvider abstraction (local sentence-transformers)
├── agents/orchestrator/           # deterministic routing + policy decision, no LLM (real)
├── agents/dataset_preparation/    # assembles a SamplePackage (real, always succeeds)
├── agents/dataset_quality/        # rule-based validation (real)
├── agents/multi_modal_inference/  # ONNX feature detector + placeholder defect classifier (real)
├── agents/explainability_review/  # templated report + case_context.py RAG tool, no LLM (real)
├── agents/continuous_monitoring_drift/  # model + agent/policy health metrics (real)
├── agents/model_lifecycle/        # designed, not implemented yet (empty stub)
├── policies/                      # hard allowlist + thresholds, versioned like prompts (real)
└── tools/                         # per-agent tool contracts (empty - none built yet)
tests/                             # mirrors app/ - DB-backed tests need `docker compose up db`
models/                            # pcb_feature_detector.onnx (gitignored) + labels.json
simulation/                        # simulate_line.py - stands in for the AOI camera/PLC edge
scripts/                           # seed_remediation_docs.py
                                    # macos/, linux/, windows/ - per-OS setup-dev + build-prod
ui/                                # Angular review UI - see ui/README.md
infra/Dockerfile                   # backend image
docs/                              # AGENT-DESIGN.md (as-built), MODEL-TRAINING.md
openspec/                          # spec-driven change tracking - see DEVELOPMENT.md
```

## Quickstart

```bash
bash scripts/macos/setup-dev.sh              # macOS
bash scripts/linux/setup-dev.sh              # Linux
powershell -File scripts\windows\setup-dev.ps1  # Windows
```

Installs backend + UI dependencies, creates `.env` from `.env.example`, checks whether the
ADC model is present, and runs the backend test suite (DB-backed tests skip cleanly unless
Postgres is up — `docker compose up db`). Then, in two terminals:

```bash
uv run uvicorn app.main:app --reload   # http://localhost:8000
cd ui && npm start                     # http://localhost:4200
```

Open `http://localhost:4200`, upload a PCB image, and run an inspection. Or drive it headlessly:

```bash
uv run python simulation/simulate_line.py --once
```

**The ADC model isn't in git** (80MB ONNX binary). If `models/pcb_feature_detector.onnx` is
missing, everything except Multi-Modal Inference still works (`rejected_quality` cases, all unit
tests except the model-dependent ones, which skip automatically) - see `models/README.md` to
generate it.

### Docker

```bash
docker-compose up --build   # backend (:8000) + ui (:4200) + db (:5433) + ollama (:11434)
```

### Production build

```bash
bash scripts/macos/build-prod.sh [tag]              # macOS
bash scripts/linux/build-prod.sh [tag]              # Linux
powershell -File scripts\windows\build-prod.ps1 [tag]  # Windows
```

Runs backend + UI checks, then builds `sentinel-pcb-backend:<tag>` and
`sentinel-pcb-ui:<tag>` Docker images (defaults to `latest`).

### Checks

```bash
uv run ruff check .
uv run mypy .
uv run pytest

cd ui && npx ng test --watch=false
```

## Contributing / how to develop

See **[DEVELOPMENT.md](DEVELOPMENT.md)** — environment setup, the spec-driven workflow (including
the `/opsx:*` OpenSpec commands), CI expectations, and how to add or change an agent.
