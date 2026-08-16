# SentinelPCB

Agentic Multi-Modal Automatic Defect Classification (ADC) platform for PCB Automated Optical
Inspection (AOI). A small set of specialized agents wraps a trusted PCB feature-detection model,
coordinating the review workflow: assemble a sample, validate it, run inference, and decide
auto-accept vs. escalate to a human reviewer.

Built for the NUS-ISS SWE5008 "Architecting AI Systems" Practice Module, Team 10 (Chong Loh Loy
Fatt, Kenny Lau Jia Xu, Wang Ze Yu).

> Project planning and design docs live in **this repo's `docs/`** — proposal, design doc, and
> `docs/ADC-Group-Report-Team10-v1.md`, which doubles as the living design doc and tracks what's
> actually implemented vs. still planned. The trusted model's source repo is the sibling
> `pcb-inspect-ai` (see `models/README.md`).

## Architecture, honestly

Three components reason (Orchestrator, Explainability & Review, Continuous Monitoring & Drift);
four are deterministic services the agents call (Dataset Preparation, Dataset Quality,
Multi-Modal Inference, Model Lifecycle). Right now, only the deterministic backbone is built and
tested end to end: **ingest → Dataset Preparation → Dataset Quality → Multi-Modal Inference →
Orchestrator's threshold decision → auto-accept / escalate**. The reasoning agents, the human
review handoff, and the retraining loop are designed but not yet implemented.

Full status per component: `docs/ADC-Group-Report-Team10-v1.md` §2-4 (uses a ✅/⚠️/❌ legend so
it's clear what's real vs. aspirational).

## Tech stack

**Backend:** Python 3.12 · [uv](https://docs.astral.sh/uv/) · FastAPI · Pydantic ·
ONNX Runtime (trusted model inference) · Pillow/NumPy · SQLAlchemy (async) + asyncpg ·
Postgres/pgvector (Supabase, provisioned, not yet wired) · pytest · ruff + mypy (strict).

**Frontend:** Angular (Vite/esbuild-based `@angular/build:application` builder) · Tailwind CSS v4 ·
Vitest.

**Planned, not yet used by any agent:** LangGraph + langchain-anthropic (LLM reasoning),
Langsmith (observability), Guardrails-AI.

## Repo layout

```
app/
├── main.py                        # FastAPI entrypoint, POST /inspections
├── settings.py                    # pydantic-settings, reads .env
├── agents/orchestrator/           # deterministic routing + policy decision (real)
├── agents/dataset_preparation/    # assembles a SamplePackage (real)
├── agents/dataset_quality/        # rule-based validation (real)
├── agents/multi_modal_inference/  # ONNX Runtime wrapper around the trusted model (real)
├── agents/{explainability_review,continuous_monitoring_drift,model_lifecycle,case_context}/
│                                   # designed, not implemented yet (empty)
├── policies/                      # hard allowlist + thresholds, versioned like prompts (real)
└── tools/                         # per-agent tool contracts (empty - none built yet)
tests/                             # mirrors app/
models/                            # pcb_feature_detector.onnx (gitignored) + labels.json
simulation/                        # simulate_line.py - stands in for the AOI camera/PLC edge
ui/                                # Angular review UI - see ui/README.md
infra/Dockerfile                   # backend image
scripts/                           # setup-dev.sh, build-prod.sh
docs/                              # proposal, design doc, group report (design doc + tracker)
openspec/                          # spec-driven change tracking - see DEVELOPMENT.md
```

## Quickstart

```bash
bash scripts/setup-dev.sh
```

Installs backend + UI dependencies, creates `.env` from `.env.example`, checks whether the
ADC model is present, and runs the backend test suite. Then, in two terminals:

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
docker-compose up --build   # backend (:8000) + ui (:4200) + ollama (:11434)
```

### Production build

```bash
bash scripts/build-prod.sh [tag]
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
