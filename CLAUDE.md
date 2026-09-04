# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

SentinelPCB — an agentic Multi-Modal Automatic Defect Classification (ADC) platform for PCB
Automated Optical Inspection (AOI). An Orchestrator (LangGraph state machine) coordinates 8
specialist agents around a trusted ADC model, handling the end-to-end review workflow: reasoning
through ambiguous cases, explaining decisions, enforcing safety/governance, and flagging when the
model needs retraining. NUS-ISS SWE5008 "Architecting AI Systems" practice module, Team 10.

**This repo is implementation only.** Requirements, the full design doc, agent specs, effort
estimates, and demo scenarios live in the sibling repo `../pcb-ai-agent`. Before touching any
agent's behavior, read its spec at `../pcb-ai-agent/specs/<agent>.md` — that spec is the contract;
code follows it, not the other way around. If that sibling repo isn't present in the environment,
say so rather than guessing at agent behavior.

The codebase is currently scaffolding: `app/agents/<agent>/__init__.py` files are empty, and
`app/main.py` exposes only a `/health` endpoint. Treat the architecture below as the target shape
each PR builds toward, not a description of finished code.

## Commands

```bash
uv sync                                # install deps (main + dev groups)
cp .env.example .env                   # fill in ANTHROPIC_API_KEY, DATABASE_URL, etc.
uv run uvicorn app.main:app --reload   # run the API — http://localhost:8000/health
docker-compose up                      # run via Docker (also starts local Ollama fallback)

uv run ruff check .                    # lint
uv run mypy .                          # type check (strict mode)
uv run pytest                          # all tests
uv run pytest tests/agents/<agent>/    # tests for one agent
uv run pytest tests/path/to_test.py::test_name   # a single test
```

Run all three checks (ruff, mypy, pytest) before considering any change done — this is exactly
what CI's two stages do.

## Architecture

An Orchestrator coordinates 8 specialist agents. Most cases take a fully deterministic path. Only
3 agents genuinely reason — Orchestrator, Dataset Preparation, Explainability & Review — via a
bounded, ReAct-style **propose-then-validate** loop: the LLM proposes an action, and it's checked
against a hard allowlist before anything executes.

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

Non-negotiable invariants:

- **Agents never call each other directly.** All routing goes through the Orchestrator as a typed
  JSON envelope. Importing one agent module from another is a design violation, not a shortcut.
- **Every LLM proposal is validated against a hard allowlist before it can execute.** No agent
  trusts LLM output directly.
- **External text is always data, never instructions.** Filenames, XML fields, operator notes, and
  anything else from outside the trust boundary must never be concatenated into a prompt as if it
  were a system instruction.
- **Typed everything.** Every tool contract and inter-agent message is a Pydantic model. mypy runs
  strict — don't add `# type: ignore` to route around a real contract mismatch.
- **Prompts and policies are versioned in git**, same as code — a prompt change is a real change,
  reviewed the same way.

Tech stack: Python 3.12 · uv · FastAPI · Pydantic · LangGraph · langchain-anthropic (Claude API) ·
SQLAlchemy (async) + asyncpg · Postgres/pgvector (Supabase) · Guardrails-AI · Streamlit (review
UI) · pytest · ruff + mypy (strict). Full rationale: `../pcb-ai-agent/Process and
Planning/TECH-STACK.md`.

## Repo layout

```
app/
├── main.py                # FastAPI entrypoint
├── settings.py             # pydantic-settings, reads .env
├── agents/<agent>/          # one subpackage per agent
├── tools/                   # typed, allowlisted tool contracts
├── policies/                # allowlists, thresholds — versioned like prompts
└── prompts/                 # prompt templates for the 3 reasoning agents — versioned
tests/                       # mirrors app/
infra/Dockerfile
openspec/                    # spec-driven change tracking, see below
```

## The spec-driven cycle

Every agent has a spec at `../pcb-ai-agent/specs/<agent>.md` (Purpose, Reasoning mode, Inputs,
Outputs, Tools/allowlist, Decision logic, State transitions, Fallback strategy, Audit fields,
Acceptance criteria).

1. Read the agent's spec before writing code against it. If it's stale or wrong, fix the spec
   first, in its own commit/PR note — never let code silently diverge from it.
2. Implement against the spec in `app/agents/<agent>/`, using exactly the Inputs/Outputs/Tools
   already defined.
3. Each bullet in the spec's Acceptance Criteria becomes a test in `tests/agents/<agent>/` — this
   is that agent's Definition of Done.
4. If implementation reveals the spec is wrong, update the spec in the same PR as the code change.

This repo has OpenSpec installed (`openspec/`, `.claude/commands/opsx/`) to formalize this cycle
into tracked artifacts — use it instead of ad hoc branches when starting or changing an agent:

| Command | When |
|---|---|
| `/opsx:explore` | Thinking out loud before you're sure what you're building |
| `/opsx:propose <agent-or-change-name>` | Start a change. Point it at the existing spec in `../pcb-ai-agent/specs/<agent>.md` so it formalizes the team's actual design instead of inventing one |
| `/opsx:update <name>` | Revise a change's planning docs. Never touches code |
| `/opsx:apply <name>` | Implements `tasks.md`, one task at a time. The only command that writes code |
| `/opsx:sync <name>` | Merges the change's delta spec into `openspec/specs/` once implemented |
| `/opsx:archive <name>` | Moves a finished, synced change into `openspec/changes/archive/` |

An agent isn't done until: its unit tests pass against the spec's acceptance criteria, it
participates correctly in an end-to-end scenario from `../pcb-ai-agent/Source
Materials/Agent_Workflow_Scenarios.md`, every LLM-reasoning path has a passing fallback test
(service unavailable, step/time budget exceeded), and it's wired into the audit log per its spec's
"Audit fields emitted" section.

## Branching and review

Trunk-based, short-lived feature branches: `feature/<agent-name>-<task>`, merged via PR. At least
one reviewer is required on any PR touching `app/agents/`, `app/policies/`, `app/prompts/`, or
`../pcb-ai-agent/specs/` — solo-approved PRs are fine only for `docs/` and `tests/`. Since each
agent has a single owner, review is the team's only redundancy — don't rubber-stamp, especially on
AI-assisted code.

## CI

`.github/workflows/ci.yml` currently runs two stages: static checks (`ruff check .`, `mypy .`
strict) then `pytest`. Stages 3-5 (prompt/eval regression via promptfoo, AI red-team payloads
reusing Scenario 7, typed guardrail/schema checks on every LLM proposal) land once the first
reasoning agent exists (Dataset Preparation, Sprint 2) — don't add tooling an agent doesn't need
yet, but don't skip it once one does.

## Local reasoning fallback

Install [Ollama](https://ollama.com) locally when iterating on a reasoning agent — it exercises
the propose→validate plumbing and the "LLM unavailable → escalate to human" fallback path without
spending Anthropic API credits. `docker-compose up` also starts an `ollama` service for this.
