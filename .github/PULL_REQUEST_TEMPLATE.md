## What

<!-- One or two sentences: what changed and why. -->

## Spec impact

- [ ] No spec change needed
- [ ] Spec updated in this PR (`../pcb-ai-agent/specs/<agent>.md`) — link/summarize the diff

## Checklist

- [ ] `uv run ruff check . && uv run mypy . && uv run pytest` passes locally
- [ ] Tests added/updated against the spec's acceptance criteria (if touching an agent)
- [ ] Audit log fields updated if this changes what an agent emits
- [ ] Docs updated (`docs/AGENT-DESIGN.md`, `README.md`, etc.) if this changes status/behavior

## Review

<!-- Required for changes under app/agents/, app/policies/, app/prompts/, or ../pcb-ai-agent/specs/. -->
