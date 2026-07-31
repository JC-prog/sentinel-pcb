# SentinelPCB

Agentic Multi-Modal Automatic Defect Classification (ADC) platform for PCB Automated Optical
Inspection (AOI). A team of specialized agents wraps the trusted ADC model, coordinating the
end-to-end review workflow, reasoning through ambiguous cases, explaining decisions, enforcing
safety/governance, and proactively flagging when the model needs retraining.

Built for the NUS-ISS SWE5008 "Architecting AI Systems" Practice Module, Team 10 (Chong Loh Loy
Fatt, Kenny Lau Jia Xu, Wang Ze Yu).

> Project planning — requirements, the full design doc, agent specs, effort estimates, demo
> scenarios — lives in the sibling `pcb-ai-agent` repo, not here. This repo is the implementation.
> See **`../pcb-ai-agent/specs/<agent>.md`** before touching any agent's behavior.

## Architecture, in one paragraph

An Orchestrator (LangGraph state machine) coordinates 8 specialist agents. Most cases take a
fully deterministic path. Only 3 agents genuinely reason — Orchestrator, Dataset Preparation,
Explainability & Review — via a bounded, ReAct-style **propose-then-validate** loop: the LLM
proposes an action, and it's checked against a hard allowlist before anything executes. Agents
never call each other directly; the Orchestrator routes every hand-off as a typed JSON envelope.

| Agent | Reasoning mode |
|---|---|
| Orchestrator | Hybrid — deterministic by default, reasons on ambiguous/conflicting cases |
| Dataset Preparation | Hybrid — deterministic matching, reasons only when matching is inconclusive |
| Case Context | Retrieval + light synthesis (embedding search) — no generative LLM |
| Dataset Quality | Deterministic |
| Multi-Modal Inference | Deterministic; may emit a `REQUEST_REPREPARATION` signal |
| Explainability & Review | Hybrid — RAG narrative + self-critique/grounding check |
| Model Lifecycle | Deterministic (safety-critical; no LLM in the approval path) |
| Continuous Monitoring & Drift | Autonomous, scheduled — deterministic thresholds trigger an LLM-drafted proposal |

## Tech stack

Python 3.12 · [uv](https://docs.astral.sh/uv/) · FastAPI · Pydantic · LangGraph ·
langchain-anthropic (Claude API) · SQLAlchemy (async) + asyncpg · Postgres/pgvector (Supabase) ·
Guardrails-AI · Streamlit (review UI) · pytest · ruff + mypy (strict).

Full rationale for each choice: `../pcb-ai-agent/Process and Planning/TECH-STACK.md`.

## Repo layout

```
app/
├── main.py              # FastAPI entrypoint
├── settings.py           # pydantic-settings, reads .env
├── agents/<agent>/        # one subpackage per agent
├── tools/                 # typed, allowlisted tool contracts
├── policies/              # allowlists, thresholds — versioned like prompts
└── prompts/               # prompt templates for the 3 reasoning agents — versioned
tests/                     # mirrors app/
infra/
└── Dockerfile
docs/                      # code-level docs (this repo only — not project planning)
openspec/                  # spec-driven change tracking — see DEVELOPMENT.md
```

## Quickstart

```bash
uv sync                          # install dependencies
cp .env.example .env             # fill in ANTHROPIC_API_KEY, DATABASE_URL, etc.
uv run uvicorn app.main:app --reload   # http://localhost:8000/health
```

Or via Docker:

```bash
docker-compose up
```

Running tests / checks:

```bash
uv run ruff check .
uv run mypy .
uv run pytest
```

## Contributing / how to develop

See **[DEVELOPMENT.md](DEVELOPMENT.md)** — environment setup, the spec-driven workflow (including
the `/opsx:*` OpenSpec commands), CI expectations, and how to add or change an agent.
