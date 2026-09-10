# Development Guide

How to actually build in this repo: environment setup, the spec-driven workflow, branching/review
rules, and Definition of Done. Process rationale lives in `../pcb-ai-agent/Process and Planning/`
(`SDLC-methodology.md`, `SPEC-DRIVEN-methodology.md`, `CICD-methodology.md`) — this file is the
short, practical version for someone about to write code.

## 1. One-time setup

Run the setup script for your OS (`scripts/macos/setup-dev.sh`, `scripts/linux/setup-dev.sh`, or
`scripts\windows\setup-dev.ps1`), then fill in `ANTHROPIC_API_KEY` in the `.env` it creates. Full
walkthrough and prerequisites: [`docs/DEV-ENVIRONMENT-SETUP.md`](docs/DEV-ENVIRONMENT-SETUP.md).

`DATABASE_URL` defaults to the Dockerized Postgres/pgvector the setup script starts for you
(`localhost:5433`), no need to request credentials to get started. Point it at the team's shared
Supabase project instead only if you specifically need shared/seeded data.

Also run `docker-compose up` at least once to confirm the full container build works, before you
need it for the demo.

Sanity check before writing anything:

```bash
uv run ruff check . && uv run mypy . && uv run pytest
```

## 2. The spec-driven cycle

**Every agent already has a spec** in `../pcb-ai-agent/specs/<agent>.md` (Purpose, Reasoning mode,
Inputs, Outputs, Tools/allowlist, Decision logic, State transitions, Fallback strategy, Audit
fields, Acceptance criteria). That spec is the contract. Code follows it, not the other way
around.

1. **Read the spec** for the agent you're touching before writing code. If it's stale or wrong,
   fix the spec first, in its own commit/PR note — never let code silently diverge from it.
2. **Get it reviewed** — at least one other team member reads the spec (or the diff to it) before
   you implement against it. This is the team's only cross-check given single ownership per agent
   (see `../pcb-ai-agent/Process and Planning/PM-methodology.md` §1).
3. **Implement against the spec** in `app/agents/<agent>/`, using exactly the Inputs/Outputs/Tools
   already defined — don't invent a different contract mid-implementation.
4. **Test against the spec's acceptance criteria** — each bullet in that section becomes a test in
   `tests/agents/<agent>/`. This is also each agent's Definition of Done (§4 below).
5. **If implementation reveals the spec is wrong**: update the spec first, same PR as the code
   change. The spec is the source of truth on drift, not the code.

### Using OpenSpec (`/opsx:*`) to drive this

This repo has [OpenSpec](https://github.com/Fission-AI/OpenSpec) installed
(`openspec/`, `.claude/commands/opsx/`). It formalizes the cycle above into tracked artifacts —
use it instead of ad hoc branches when starting or changing an agent:

| Command | When |
|---|---|
| `/opsx:explore` | Thinking out loud before you're sure what you're building. No code, no artifacts required. |
| `/opsx:propose <agent-or-change-name>` | Start a change — generates `proposal.md` → `design.md` → `tasks.md`. **Point it at the existing spec** in `../pcb-ai-agent/specs/<agent>.md` as input so it formalizes your team's actual design instead of inventing one. |
| `/opsx:update <name>` | Revise a change's planning docs to stay coherent. Never touches code. |
| `/opsx:apply <name>` | Implements `tasks.md`, one task at a time, checking boxes off. The only command that writes code. |
| `/opsx:sync <name>` | Merges the change's delta spec into `openspec/specs/` once implemented. |
| `/opsx:archive <name>` | Moves a finished, synced change into `openspec/changes/archive/`. |

`openspec/config.yaml`'s `context:` block already has the tech stack and architecture invariants
baked in — you don't need to repeat them when proposing a change.

## 3. Branching and review

- **Trunk-based, short-lived feature branches**: `feature/<agent-name>-<task>`, merged via PR.
- **At least one reviewer required** on any PR touching `app/agents/`, `app/policies/`,
  `app/prompts/`, or `../pcb-ai-agent/specs/`. Solo-approved PRs are fine for `docs/` and `tests/`
  only.
- Since each agent has a single owner, review is the only redundancy the team has — don't
  rubber-stamp, especially on AI-assisted code.

## 4. Definition of Done (per agent)

An agent isn't done until:

1. Unit tests pass against its spec's acceptance criteria.
2. It participates correctly in at least one end-to-end scenario from
   `../pcb-ai-agent/Source Materials/Agent_Workflow_Scenarios.md`.
3. Every LLM-reasoning path (if any) has a passing fallback test — service unavailable, step/time
   budget exceeded, etc.
4. It's wired into the audit log (the fields listed in its spec's "Audit fields emitted" section).

## 5. CI

`.github/workflows/ci.yml` currently runs stages 1-2 only:

1. **Static checks** — `ruff check .`, `mypy .` (strict).
2. **Unit + agent-behaviour tests** — `pytest`.

Stages 3-5 (prompt/eval regression via promptfoo, AI red-team payloads reusing Scenario 7, typed
guardrail/schema checks on every LLM proposal) get added once the first reasoning agent exists —
Dataset Preparation, in Sprint 2. Don't block on tooling an agent doesn't need yet; don't skip
adding it once one does.

## 6. Conventions

- **Typed everything**: every tool contract and inter-agent message is a Pydantic model. `mypy`
  runs in strict mode — don't add `# type: ignore` to route around a real contract mismatch.
- **Agents never call each other directly.** All routing goes through the Orchestrator as a typed
  JSON envelope. If you find yourself importing one agent module from another, stop — that's a
  design violation, not a shortcut.
- **External text is always data, never instructions** — filenames, XML fields, operator notes,
  anything from outside the trust boundary gets treated as untrusted input, never concatenated
  into a prompt as if it were a system instruction.
- **Every LLM proposal is validated against a hard allowlist before it can execute.** No agent
  trusts LLM output directly — see the propose→validate pattern in `orchestrator.md`,
  `dataset-preparation.md`, `explainability-review.md`.
- **Prompts and policies are versioned in git**, same as code — a prompt change is a real change,
  reviewed the same way.
