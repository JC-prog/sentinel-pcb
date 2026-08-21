# Contributing

This is a NUS-ISS SWE5008 team project (Team 10) — contributions in practice means the four of us,
but the same rules apply to anyone else working in this repo.

For environment setup, the spec-driven workflow, branching/review rules, and Definition of Done,
see [DEVELOPMENT.md](DEVELOPMENT.md). This file covers the mechanics of submitting a change.

## Before you start

1. Read the spec for the agent/area you're touching (see `DEVELOPMENT.md` §2) — code follows the
   spec, not the other way around.
2. Create a short-lived branch off `main`: `feature/<agent-name>-<task>` (or `fix/...`,
   `chore/...`, `docs/...`).

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/), lowercase, imperative mood:

```
feat: add Queue/Board/Trace/History views with Angular routing
fix: correct policy threshold for low-confidence escalation
docs: update AGENT-DESIGN status for continuous_monitoring_drift
chore: bump ruff and mypy versions
```

## Before opening a PR

```bash
uv run ruff check . && uv run mypy . && uv run pytest
```

CI (`.github/workflows/ci.yml`) runs the same checks — a PR that fails locally will fail there too.

## PR review

- At least one reviewer is required on any PR touching `app/agents/`, `app/policies/`,
  `app/prompts/`, or specs in `../pcb-ai-agent/specs/`.
- Solo-approved PRs are fine for `docs/` and `tests/`-only changes.
- If implementation revealed the spec was wrong, update the spec in the same PR — don't let code
  and spec silently diverge.

## Reporting bugs / proposing changes

Open a GitHub issue, or start with `/opsx:explore` or `/opsx:propose` (see DEVELOPMENT.md §2) if
the change needs design work before code.
